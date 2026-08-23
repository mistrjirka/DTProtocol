from __future__ import annotations

from collections import defaultdict, deque
from typing import Dict, List, Optional, Set, Tuple, TYPE_CHECKING

from model import *

if TYPE_CHECKING:
    from simulator import Simulator


class Node:
    def __init__(self, sim: Simulator, node_id: int):
        self.sim = sim
        self.id = node_id
        self.profile = sim.profile
        self.up = False
        self.crashed = False
        self.generation = 0
        self.packet_counter = 0

        self.routes_by_neighbor: Dict[int, Dict[int, AdvertisedRoute]] = {}
        self.routes: Dict[int, Route] = {}
        # Feasible distance is based on values this node has advertised, not
        # merely values it once selected internally.
        self.feasibility: Dict[int, FeasibilityState] = {}
        self.origin_sequence = 0
        self.route_version = 0
        # Direct neighbour -> (neighbour incarnation/origin sequence, state version)
        self.neighbor_cryst_state: Dict[int, Tuple[int, int]] = {}
        # Direct neighbour -> transactional CRYST-v2 chunk assembly.
        self.cryst_assemblies: Dict[int, CrystAssemblyState] = {}

        self.seq_requests_seen: Set[Tuple[int, int, int, int]] = set()
        # destination -> (requested generation, number of emitted retries)
        self.pending_seq_requests: Dict[int, Tuple[int, int]] = {}
        self.pending_cryst_requests: Set[int] = set()

        self.session_active = False
        self.session_seen: Set[int] = set()
        self.session_deadline = 0.0
        self.session_token = 0
        self.cryst_scheduled = False
        self.cryst_token = 0
        self.last_heard: Dict[int, float] = {}

        self.txq: Deque[TxRequest] = deque()
        self.lcmm_busy = False
        self.repair_burst = 0
        self.radio_busy_until = 0.0
        # packet id, source incarnation, destination, deadline
        self.waiting_e2e: Optional[Tuple[int, int, int, float]] = None
        self.wait_token = 0
        self.delivered_ids: Set[Tuple[int, int, int]] = set()
        # One bounded source message and a bounded number of destination
        # assemblies mirror the production C++ memory policy.
        self.multipart_send: Optional[dict] = None
        self.fragment_assemblies: Dict[Tuple[int, int, int], dict] = {}
        self.crashed_reason: Optional[str] = None

    def reset_runtime(self) -> None:
        self.packet_counter = 0
        self.routes_by_neighbor.clear()
        self.routes.clear()
        self.feasibility.clear()
        self.route_version = 0
        self.neighbor_cryst_state.clear()
        self.cryst_assemblies.clear()
        self.seq_requests_seen.clear()
        self.pending_seq_requests.clear()
        self.pending_cryst_requests.clear()
        self.session_active = False
        self.session_seen.clear()
        self.session_deadline = 0
        self.cryst_scheduled = False
        self.last_heard.clear()
        self.txq.clear()
        self.lcmm_busy = False
        self.repair_burst = 0
        self.radio_busy_until = 0
        self.waiting_e2e = None
        self.delivered_ids.clear()
        self.multipart_send = None
        self.fragment_assemblies.clear()
        self.crashed = False
        self.crashed_reason = None

    def _effective_hello_period_ms(self) -> float:
        period = float(self.profile.hello_period_ms or 1)
        # The conservative 1% fallback cannot physically sustain a 10 s HELLO
        # at SF9/BW125 once HELLO/CRYST airtime is included. Keep correctness
        # identical; only slow the maintenance clock for low-duty operation.
        duty = float(getattr(self.sim, "duty_cycle_percent", 0.0))
        if 0.0 < duty <= 1.0:
            period = max(period, 60_000.0)
        return period

    def _effective_neighbor_expiry_ms(self) -> float:
        expiry = float(self.profile.neighbor_expiry_ms or 1)
        duty = float(getattr(self.sim, "duty_cycle_percent", 0.0))
        if 0.0 < duty <= 1.0:
            expiry = max(expiry, 3.0 * self._effective_hello_period_ms())
        return expiry

    def start(self) -> None:
        self.up = True
        self.crashed = False
        self.generation += 1

        if self.profile.sequence_numbers:
            # Persistent incarnation in the model. Real firmware should reserve
            # sequence ranges / use a boot counter so reboot never moves backward.
            self.origin_sequence = next_sequence(self.origin_sequence)
        if self.profile.state_digest_requests and self.route_version == 0:
            # Production DTPK starts each incarnation at route version 1.
            self.route_version = 1

        self.sim.log(
            "node_up",
            node=self.id,
            origin_sequence=self.origin_sequence,
        )
        self.schedule_cryst("startup")

        gen = self.generation
        if self.profile.periodic_cryst_ms:
            self.sim.schedule(
                self.profile.periodic_cryst_ms,
                self._periodic_cryst,
                gen,
            )
        if self.profile.hello_period_ms:
            first = self.sim.rng.uniform(100, self._effective_hello_period_ms())
            self.sim.schedule(first, self._periodic_hello, gen)
        if self.profile.origin_seq_period_ms:
            self.sim.schedule(
                self.profile.origin_seq_period_ms,
                self._origin_sequence_tick,
                gen,
            )
        if self.profile.neighbor_expiry_ms:
            self.sim.schedule(
                self._effective_neighbor_expiry_ms() / 3,
                self._expiry_tick,
                gen,
            )

    def crash(self, reason: str) -> None:
        if self.crashed:
            return
        self.crashed = True
        self.crashed_reason = reason
        self.sim.metrics.crashes += 1
        self.sim.log("crash", node=self.id, reason=reason)

    # ------------------------------------------------------------------
    # Control-plane timers
    # ------------------------------------------------------------------
    def _periodic_cryst(self, gen: int) -> None:
        if gen != self.generation or not self.up or self.crashed:
            return
        self.schedule_cryst("periodic")
        self.sim.schedule(
            self.profile.periodic_cryst_ms or 1,
            self._periodic_cryst,
            gen,
        )

    def _periodic_hello(self, gen: int) -> None:
        if gen != self.generation or not self.up or self.crashed:
            return

        # HELLO is replaceable liveness state. A queued CRYST snapshot must not
        # suppress it: during rapid multi-chunk convergence snapshots can be
        # superseded continuously, while HELLO is the bounded direct-neighbour
        # liveness guarantee.
        if not any(
            queued.packet.kind == "HELLO"
            for queued in self.txq
        ):
            packet = Packet(
                "HELLO",
                self.next_packet_id(),
                wire_dtpk_size=DTPK_HELLO_SIZE,
                sender_sequence=self.origin_sequence,
                route_version=self.route_version,
            )
            self.sim.metrics.hello_tx += 1
            self.enqueue(TxRequest(packet, None, lcmm_ack=False))

        period = self._effective_hello_period_ms()
        jitter = max(0.0, min(0.95, self.profile.hello_jitter_fraction))
        if jitter:
            high = 1.0 if getattr(self, "mobile_hint", False) else 1.0 + jitter
            period *= self.sim.rng.uniform(1.0 - jitter, high)
        self.sim.schedule(period, self._periodic_hello, gen)

    def _origin_sequence_tick(self, gen: int) -> None:
        if gen != self.generation or not self.up or self.crashed:
            return
        self.origin_sequence = next_sequence(self.origin_sequence)
        self.schedule_cryst("origin_sequence")
        self.sim.schedule(
            self.profile.origin_seq_period_ms or 1,
            self._origin_sequence_tick,
            gen,
        )

    def _expiry_tick(self, gen: int) -> None:
        if gen != self.generation or not self.up or self.crashed:
            return
        expiry = self._effective_neighbor_expiry_ms()
        stale = [
            neighbor
            for neighbor, last in self.last_heard.items()
            if self.sim.now - last >= expiry
        ]
        changed_contribution = False
        for neighbor in stale:
            self.last_heard.pop(neighbor, None)
            # We no longer possess this neighbour's full advertised state. A
            # returning HELLO must therefore request it even if version numbers
            # happen to match an earlier incarnation.
            self.neighbor_cryst_state.pop(neighbor, None)
            self.cryst_assemblies.pop(neighbor, None)
            if neighbor in self.routes_by_neighbor:
                del self.routes_by_neighbor[neighbor]
                changed_contribution = True

        if changed_contribution:
            changed_best = self.rebuild_routes()
            if changed_best:
                self.schedule_cryst("neighbor_expired")
        self._expire_fragment_assemblies()
        self.sim.schedule(expiry / 3, self._expiry_tick, gen)

    def schedule_cryst(self, reason: str) -> None:
        if not self.up or self.crashed or self.cryst_scheduled:
            return
        self.cryst_scheduled = True
        self.cryst_token += 1
        token = self.cryst_token
        upper = self.profile.cryst_jitter_max_ms
        if upper is None:
            upper = self.profile.k_limit_ms
        high = max(self.profile.cryst_jitter_min_ms + 1, upper)
        delay = self.sim.rng.uniform(self.profile.cryst_jitter_min_ms, high)
        self.sim.log(
            "cryst_schedule",
            node=self.id,
            reason=reason,
            delay=round(delay, 2),
        )
        self.sim.schedule(delay, self._emit_cryst, token)

    # ------------------------------------------------------------------
    # Route advertisement / feasibility
    # ------------------------------------------------------------------
    def _update_feasibility_from_advertisement(self) -> None:
        if not self.profile.feasibility_condition:
            return
        for dest, route in self.routes.items():
            if route.distance >= ROUTE_INFINITY:
                continue
            state = self.feasibility.get(dest)
            if state is None:
                self.feasibility[dest] = FeasibilityState(
                    route.sequence,
                    route.distance,
                )
            elif sequence_newer(route.sequence, state.sequence):
                self.feasibility[dest] = FeasibilityState(
                    route.sequence,
                    route.distance,
                )
                self.sim.metrics.sequence_resets += 1
            elif (
                route.sequence == state.sequence
                and route.distance < state.feasible_distance
            ):
                self.feasibility[dest] = FeasibilityState(
                    state.sequence,
                    route.distance,
                )

    def _emit_cryst(self, token: int) -> None:
        if token != self.cryst_token or not self.up or self.crashed:
            return
        self.cryst_scheduled = False
        self._update_feasibility_from_advertisement()

        ads: List[AdvertisedRoute] = []
        if self.profile.advertise_self_route:
            ads.append(
                AdvertisedRoute(
                    dest=self.id,
                    via=self.id,
                    distance=0,
                    sequence=self.origin_sequence,
                )
            )
        ads.extend(
            AdvertisedRoute(
                dest=dest,
                via=route.next_hop,
                distance=route.distance,
                sequence=route.sequence,
            )
            for dest, route in sorted(self.routes.items())
        )

        record_size = (
            NEIGHBOR_RECORD_V2_SIZE
            if self.profile.sequence_numbers
            else NEIGHBOR_RECORD_SIZE
        )
        header_size = (
            DTPK_CRYST_V2_HEADER
            if self.profile.state_digest_requests
            else DTPK_CRYST_HEADER
        )

        if self.profile.state_digest_requests:
            # Mirror the production C++ 255-byte radio limit. The previous
            # theoretical backend emitted one oversized full vector, silently
            # capping stable line topologies at roughly one CRYST frame.
            max_dtpk_bytes = MAX_PACKET_SIZE - MAC_OVERHEAD - LCMM_OVERHEAD
            max_records = (max_dtpk_bytes - header_size) // record_size
            if max_records <= 0:
                return
            chunks = [
                ads[offset : offset + max_records]
                for offset in range(0, len(ads), max_records)
            ] or [[]]
            chunk_count = len(chunks)
            for chunk_index, chunk_ads in enumerate(chunks):
                packet = Packet(
                    "CRYST",
                    self.next_packet_id(),
                    advertisements=tuple(chunk_ads),
                    wire_dtpk_size=header_size + len(chunk_ads) * record_size,
                    cryst_record_size=record_size,
                    sender_sequence=self.origin_sequence,
                    route_version=self.route_version,
                    chunk_index=chunk_index,
                    chunk_count=chunk_count,
                )
                self.enqueue(TxRequest(packet, None, lcmm_ack=False))
            return

        packet = Packet(
            "CRYST",
            self.next_packet_id(),
            advertisements=tuple(ads),
            wire_dtpk_size=header_size + len(ads) * record_size,
            cryst_record_size=record_size,
            sender_sequence=self.origin_sequence,
            route_version=self.route_version,
        )
        self.enqueue(TxRequest(packet, None, lcmm_ack=False))

    def next_packet_id(self) -> int:
        self.packet_counter = (self.packet_counter + 1) & 0xFFFF
        if self.packet_counter == 0:
            self.packet_counter = 1
        return self.packet_counter

    # ------------------------------------------------------------------
    # TX queue / reliability
    # ------------------------------------------------------------------
    def enqueue(self, req: TxRequest) -> None:
        # HELLO and CRYST are replaceable full-state control. Under duty/backoff
        # pressure, retaining old snapshots only delays the newest route state.
        if req.packet.kind == "HELLO":
            if any(
                queued.packet.kind == "HELLO"
                for queued in self.txq
            ):
                return
        elif req.packet.kind == "CRYST" and req.packet.chunk_index == 0:
            # The first chunk supersedes older snapshots; later chunks from the
            # same snapshot must remain queued behind it. Preserve HELLO because
            # liveness must not depend on completing a churn-heavy snapshot.
            self.txq = deque(
                queued
                for queued in self.txq
                if queued.packet.kind != "CRYST"
            )
        elif req.packet.kind == "SEQ_REQ":
            for queued in self.txq:
                if (
                    queued.packet.kind != "SEQ_REQ"
                    or queued.packet.final_target != req.packet.final_target
                ):
                    continue
                if sequence_newer(
                    queued.packet.requested_sequence,
                    req.packet.requested_sequence,
                ):
                    return
                if sequence_newer(
                    req.packet.requested_sequence,
                    queued.packet.requested_sequence,
                ):
                    queued.packet = req.packet
                    queued.next_hop = req.next_hop
                    queued.lcmm_ack = req.lcmm_ack
                else:
                    # A periodic flood escape must upgrade an already queued
                    # directed repair instead of being hidden by coalescing.
                    flood = (
                        queued.packet.repair_flood
                        or req.packet.repair_flood
                    )
                    queued.packet.repair_flood = flood
                    queued.next_hop = None if flood else req.next_hop
                    queued.lcmm_ack = False if flood else req.lcmm_ack
                queued.priority = queued.priority or req.priority
                return

        # Preserve FIFO order inside each priority class. Selection, rather
        # than front insertion, gives responses precedence and a fairness quota
        # to repair traffic without reversing packets or starving snapshots.
        self.txq.append(req)
        self.sim.metrics.max_queue = max(
            self.sim.metrics.max_queue,
            len(self.txq),
        )
        self.sim.schedule(0, self.pump)

    @staticmethod
    def _tx_queue_class(req: TxRequest) -> int:
        if req.packet.kind in ("ACK", "NACK", "FRAGMENT_STATUS"):
            return 2
        if req.priority or req.packet.kind in (
            "CRYST_REQ", "SEQ_REQ", "FRAGMENT_QUERY"
        ):
            return 1
        return 0

    def _select_tx_index(self) -> Optional[int]:
        response = repair = normal = None
        for index, req in enumerate(self.txq):
            if self.waiting_e2e is not None and self.profile.global_e2e_gate:
                if not (
                    self.profile.ack_bypasses_e2e_gate
                    and req.packet.kind in (
                        "ACK", "NACK", "CRYST", "HELLO", "CRYST_REQ",
                        "SEQ_REQ", "DATA_FRAGMENT", "FRAGMENT_STATUS",
                        "FRAGMENT_QUERY"
                    )
                ):
                    continue
            queue_class = self._tx_queue_class(req)
            if queue_class == 2 and response is None:
                response = index
            elif queue_class == 1 and repair is None:
                repair = index
            elif queue_class == 0 and normal is None:
                normal = index
        if response is not None:
            return response
        if repair is not None and (normal is None or self.repair_burst < 4):
            return repair
        if normal is not None:
            return normal
        return repair

    def _pop_tx_index(self, index: int) -> TxRequest:
        self.txq.rotate(-index)
        req = self.txq.popleft()
        self.txq.rotate(index)
        return req

    def pump(self) -> None:
        if not self.up or self.crashed:
            return
        self._pump_multipart()
        if self.lcmm_busy or not self.txq:
            return
        index = self._select_tx_index()
        if index is None:
            return
        req = self._pop_tx_index(index)
        queue_class = self._tx_queue_class(req)
        if queue_class == 1:
            self.repair_burst = min(255, self.repair_burst + 1)
        elif queue_class == 0:
            self.repair_burst = 0
        if req.lcmm_ack:
            self.lcmm_busy = True

        def completed(success: bool) -> None:
            if req.lcmm_ack:
                self.lcmm_busy = False
                if success and req.next_hop is not None:
                    # A returned link ACK proves bidirectional reachability of
                    # the direct next hop, regardless of packet kind.
                    self.last_heard[req.next_hop] = self.sim.now
                    last_probe = getattr(self, "last_liveness_probe", None)
                    if last_probe is not None:
                        last_probe.pop(req.next_hop, None)
            if not success:
                self.sim.log(
                    "hop_fail",
                    node=self.id,
                    target=req.next_hop,
                    kind=req.packet.kind,
                )
            if (
                req.packet.kind in ("DATA_FRAGMENT", "FRAGMENT_QUERY")
                and self.multipart_send is not None
                and self.multipart_send["id"] == req.packet.packet_id
                and self.multipart_send["source_sequence"]
                == req.packet.sender_sequence
                and req.packet.original_sender == self.id
                and req.packet.final_target == self.multipart_send["target"]
            ):
                self._schedule_multipart_query(self._multipart_query_delay_ms())
            if req.dtpk_ack and success:
                self.waiting_e2e = (
                    req.packet.packet_id,
                    req.packet.sender_sequence,
                    req.packet.final_target or 0,
                    self.sim.now + req.timeout_ms,
                )
                self.wait_token += 1
                wait_token = self.wait_token
                self.sim.schedule(
                    req.timeout_ms,
                    self._e2e_timeout,
                    wait_token,
                    req.packet.packet_id,
                    req.packet.sender_sequence,
                )
            self._pump_multipart()
            self.sim.schedule(0, self.pump)

        self.sim.transmit(
            self.id,
            req.next_hop,
            req.packet,
            req.lcmm_ack,
            completed,
        )
        if not req.lcmm_ack and self.profile.noack_releases_lcmm_immediately:
            self.sim.schedule(0, self.pump)

    def link_retry_timeout_ms(self, packet: Packet) -> float:
        return (
            1_650.0
            + self.sim.airtime_ms(self.sim._frame_bytes(packet))
            + self.sim.rng.uniform(25.0, 250.0)
        )

    def _e2e_timeout(
        self, token: int, packet_id: int, source_sequence: int
    ) -> None:
        if token != self.wait_token or self.waiting_e2e is None:
            return
        if self.waiting_e2e[:2] != (packet_id, source_sequence):
            return
        self.waiting_e2e = None
        if (
            self.multipart_send is not None
            and self.multipart_send["id"] == packet_id
            and self.multipart_send["source_sequence"] == source_sequence
        ):
            self.multipart_send = None
        self.sim.metrics.e2e_failure += 1
        self.sim.log("e2e_timeout", node=self.id, packet_id=packet_id)
        self.sim.schedule(0, self.pump)

    # ------------------------------------------------------------------
    # Receive dispatch
    # ------------------------------------------------------------------
    def receive(self, packet: Packet, previous_hop: int) -> None:
        if not self.up or self.crashed:
            return

        # Any valid directly received protocol frame is a liveness observation.
        self.last_heard[previous_hop] = self.sim.now

        if packet.kind == "HELLO":
            self.receive_hello(packet, previous_hop)
            return
        if packet.kind == "CRYST":
            self.receive_cryst(packet, previous_hop)
            return
        if packet.kind == "SEQ_REQ":
            self.receive_seq_req(packet, previous_hop)
            return

        if packet.final_target == self.id:
            if packet.kind == "DATA":
                self.receive_data_local(packet, previous_hop)
            elif packet.kind == "DATA_FRAGMENT":
                self.receive_fragment_local(packet, previous_hop)
            elif packet.kind == "FRAGMENT_STATUS":
                self.receive_fragment_status_local(packet)
            elif packet.kind == "FRAGMENT_QUERY":
                self.receive_fragment_query_local(packet, previous_hop)
            elif packet.kind == "ACK":
                self.receive_ack_local(packet, True)
            elif packet.kind == "NACK":
                self.receive_ack_local(packet, False)
            elif packet.kind == "CRYST_REQ":
                self.receive_cryst_req(packet, previous_hop)
            return

        self.forward(packet, previous_hop)

    # ------------------------------------------------------------------
    # HELLO / state repair
    # ------------------------------------------------------------------
    def request_cryst(self, neighbor: int) -> None:
        if neighbor in self.pending_cryst_requests or any(
            queued.packet.kind == "CRYST_REQ"
            and queued.next_hop == neighbor
            for queued in self.txq
        ):
            return
        self.pending_cryst_requests.add(neighbor)
        delay = self.sim.rng.uniform(25.0, 500.0)
        self.sim.schedule(delay, self._enqueue_cryst_request, neighbor)

    def _enqueue_cryst_request(self, neighbor: int) -> None:
        self.pending_cryst_requests.discard(neighbor)
        if not self.up or self.crashed:
            return
        request = Packet(
            "CRYST_REQ",
            self.next_packet_id(),
            original_sender=self.id,
            final_target=neighbor,
            wire_dtpk_size=DTPK_CRYST_REQ_SIZE,
            sender_sequence=self.origin_sequence,
            route_version=self.route_version,
        )
        self.sim.metrics.cryst_req_tx += 1
        self.enqueue(
            TxRequest(request, neighbor, lcmm_ack=True, priority=True)
        )

    def receive_hello(self, packet: Packet, previous_hop: int) -> None:
        self.sim.metrics.hello_rx += 1
        incoming_state = (packet.sender_sequence, packet.route_version)
        known_state = self.neighbor_cryst_state.get(previous_hop)

        contribution = self.routes_by_neighbor.get(previous_hop)
        reincarnated = (
            known_state is not None
            and known_state[0] != packet.sender_sequence
        )
        if contribution is None or reincarnated:
            # A new incarnation invalidates all old indirect state from this
            # neighbour immediately; keep only the directly observed route.
            contribution = {}
            self.routes_by_neighbor[previous_hop] = contribution
            if reincarnated:
                self.neighbor_cryst_state.pop(previous_hop, None)
                self.cryst_assemblies.pop(previous_hop, None)

        direct = AdvertisedRoute(
            previous_hop,
            self.id,
            1,
            packet.sender_sequence,
        )
        contribution_changed = contribution.get(previous_hop) != direct
        if contribution_changed:
            contribution[previous_hop] = direct

        best_changed = self.rebuild_routes() if (contribution_changed or reincarnated) else False
        if best_changed:
            self.schedule_cryst("hello_neighbor_change")

        known_state = self.neighbor_cryst_state.get(previous_hop)
        self.sim.log(
            "hello_rx",
            node=self.id,
            sender=previous_hop,
            sender_sequence=packet.sender_sequence,
            advertised_version=packet.route_version,
            known_state=known_state,
            best_changed=best_changed,
        )

        if self.profile.state_digest_requests and known_state != incoming_state:
            self.request_cryst(previous_hop)

    def receive_cryst_req(self, packet: Packet, previous_hop: int) -> None:
        """Apply the requester's direct-state digest, then answer with state.

        The request is reliable at LCMM. Therefore one received mobile HELLO and
        the resulting request/response exchange can establish both direct
        routes without making the mobility hint part of route semantics.
        """
        self.sim.metrics.cryst_req_rx += 1
        incoming_state = (packet.sender_sequence, packet.route_version)
        known_state = self.neighbor_cryst_state.get(previous_hop)

        accept_digest = False
        invalidate_indirect = False
        if known_state is None:
            accept_digest = True
            invalidate_indirect = True
        elif packet.sender_sequence == known_state[0]:
            accept_digest = (
                packet.route_version == known_state[1]
                or ((packet.route_version - known_state[1]) & 0xFFFFFFFF)
                < 0x80000000
            )
        elif sequence_newer(packet.sender_sequence, known_state[0]):
            accept_digest = True
            invalidate_indirect = True

        contribution = self.routes_by_neighbor.get(previous_hop)
        contribution_changed = False
        if accept_digest:
            if contribution is None or invalidate_indirect:
                contribution = {}
                self.routes_by_neighbor[previous_hop] = contribution
            direct = AdvertisedRoute(
                previous_hop,
                self.id,
                1,
                packet.sender_sequence,
            )
            contribution_changed = contribution.get(previous_hop) != direct
            contribution[previous_hop] = direct
            if invalidate_indirect:
                self.neighbor_cryst_state.pop(previous_hop, None)
                self.cryst_assemblies.pop(previous_hop, None)

        if contribution_changed:
            if self.rebuild_routes():
                self.schedule_cryst("cryst_request_direct_route")

        # The requester already learned our digest from HELLO, and accepting
        # this request establishes its direct route here. Both route changes
        # schedule snapshots; a reverse request would create a priority ping-pong
        # that starves the requested snapshots.
        self.schedule_cryst("state_request")

    # ------------------------------------------------------------------
    # Sequence repair: reliable directed request, broadcast only as fallback
    # ------------------------------------------------------------------
    def _sequence_request_next_hop(
        self,
        dest: int,
        *,
        avoid: Optional[int] = None,
    ) -> Optional[int]:
        candidates = []
        for router, contribution in self.routes_by_neighbor.items():
            if router == avoid:
                continue
            advertised = contribution.get(dest)
            if advertised is None or advertised.distance >= ROUTE_INFINITY:
                continue
            candidates.append(
                (advertised.sequence, advertised.distance, router)
            )
        if not candidates:
            return None

        state = self.feasibility.get(dest)
        feasible = []
        for sequence, distance, router in candidates:
            route = Route(router, router, distance, sequence)
            if self._is_feasible(dest, route):
                feasible.append((sequence, distance, router))
        if feasible:
            return min(feasible, key=lambda item: (item[1], item[2]))[2]

        newest = candidates[0][0]
        for sequence, _distance, _router in candidates[1:]:
            if sequence_newer(sequence, newest):
                newest = sequence
        candidates.sort(
            key=lambda item: (
                0 if item[0] == newest else 1,
                item[1],
                item[2],
            )
        )
        return candidates[0][2]

    def request_sequence(self, dest: int, requested_sequence: int) -> None:
        if not self.profile.seqno_requests or dest == self.id:
            return

        requested_sequence &= 0xFFFF
        existing = self.pending_seq_requests.get(dest)
        if existing is not None:
            if not sequence_newer(requested_sequence, existing[0]):
                return
        self.pending_seq_requests[dest] = (requested_sequence, 0)
        self._retry_sequence_request(dest, requested_sequence)

    def _retry_sequence_request(
        self, dest: int, requested_sequence: int
    ) -> None:
        if not self.up or self.crashed:
            return
        pending = self.pending_seq_requests.get(dest)
        if pending is None or pending[0] != requested_sequence:
            return

        route = self.routes.get(dest)
        if route is not None and not sequence_newer(
            requested_sequence, route.sequence
        ):
            self.pending_seq_requests.pop(dest, None)
            return

        known = any(
            dest in contribution
            for contribution in self.routes_by_neighbor.values()
        )
        if route is None and not known:
            self.pending_seq_requests.pop(dest, None)
            return

        flood = (pending[1] + 1) % 8 == 0
        next_hop = None if flood else self._sequence_request_next_hop(dest)

        request = Packet(
            "SEQ_REQ",
            self.next_packet_id(),
            original_sender=self.id,
            final_target=dest,
            wire_dtpk_size=DTPK_SEQ_REQ_SIZE,
            requested_sequence=requested_sequence,
            repair_flood=next_hop is None,
            hop_limit=self.profile.seqno_request_hop_limit,
        )
        key = (
            self.id,
            request.packet_id,
            dest,
            request.requested_sequence,
        )
        self.seq_requests_seen.add(key)
        self.sim.metrics.seq_req_tx += 1
        self.sim.log(
            "seq_req_tx",
            node=self.id,
            dest=dest,
            requested=request.requested_sequence,
            attempt=pending[1] + 1,
            directed=next_hop is not None,
        )
        self.enqueue(
            TxRequest(
                request,
                next_hop,
                lcmm_ack=next_hop is not None,
                timeout_ms=3_000,
                priority=True,
            )
        )

        attempts = min(pending[1] + 1, 65_535)
        self.pending_seq_requests[dest] = (requested_sequence, attempts)
        shift = min(max(attempts - 1, 0), 4)
        delay = min(
            self.profile.seqno_request_cooldown_ms * (1 << shift),
            self.profile.seqno_request_max_cooldown_ms,
        )
        self.sim.schedule(
            delay,
            self._retry_sequence_request,
            dest,
            requested_sequence,
        )

    def receive_seq_req(self, packet: Packet, previous_hop: int) -> None:
        self.sim.metrics.seq_req_rx += 1
        origin = packet.original_sender if packet.original_sender is not None else -1
        dest = packet.final_target if packet.final_target is not None else -1
        key = (origin, packet.packet_id, dest, packet.requested_sequence)
        if key in self.seq_requests_seen:
            self.sim.metrics.seq_req_duplicates += 1
            return
        self.seq_requests_seen.add(key)

        if dest == self.id:
            if sequence_newer(packet.requested_sequence, self.origin_sequence):
                self.origin_sequence = packet.requested_sequence
            self.sim.metrics.seq_req_satisfied += 1
            self.sim.log(
                "seq_req_satisfied",
                node=self.id,
                requester=origin,
                sequence=self.origin_sequence,
            )
            self.schedule_cryst("sequence_request")
            return

        if packet.hop_limit <= 1:
            return
        next_hop = (
            None
            if packet.repair_flood
            else self._sequence_request_next_hop(dest, avoid=previous_hop)
        )
        forwarded = packet.clone()
        if next_hop is None:
            forwarded.repair_flood = True
        forwarded.hop_limit -= 1
        self.sim.metrics.seq_req_tx += 1
        self.enqueue(
            TxRequest(
                forwarded,
                next_hop,
                lcmm_ack=next_hop is not None,
                timeout_ms=3_000,
                priority=True,
            )
        )

    # ------------------------------------------------------------------
    # CRYST receive
    # ------------------------------------------------------------------
    @staticmethod
    def _version_newer(candidate: int, current: int) -> bool:
        candidate &= 0xFFFFFFFF
        current &= 0xFFFFFFFF
        return candidate != current and ((candidate - current) & 0xFFFFFFFF) < 0x80000000

    def _expire_cryst_assembly(
        self,
        previous_hop: int,
        sender_sequence: int,
        route_version: int,
        chunk_count: int,
    ) -> None:
        assembly = self.cryst_assemblies.get(previous_hop)
        if assembly is None:
            return
        if (
            assembly.sender_sequence != sender_sequence
            or assembly.route_version != route_version
            or assembly.chunk_count != chunk_count
        ):
            return
        age = self.sim.now - assembly.last_update_ms
        if age >= DTPK_CRYST_ASSEMBLY_EXPIRY_MS:
            self.cryst_assemblies.pop(previous_hop, None)
            return
        self.sim.schedule(
            DTPK_CRYST_ASSEMBLY_EXPIRY_MS - age,
            self._expire_cryst_assembly,
            previous_hop,
            sender_sequence,
            route_version,
            chunk_count,
        )

    def _reassemble_cryst_v2(
        self, packet: Packet, previous_hop: int
    ) -> Optional[Tuple[AdvertisedRoute, ...]]:
        if (
            packet.chunk_count <= 0
            or packet.chunk_count > DTPK_MAX_CRYST_CHUNKS
            or packet.chunk_index < 0
            or packet.chunk_index >= packet.chunk_count
        ):
            return None

        applied = self.neighbor_cryst_state.get(previous_hop)
        if applied is not None:
            if packet.sender_sequence == applied[0]:
                if packet.route_version == applied[1]:
                    return None
                if not self._version_newer(packet.route_version, applied[1]):
                    return None
            elif not sequence_newer(packet.sender_sequence, applied[0]):
                return None

        assembly = self.cryst_assemblies.get(previous_hop)
        different = (
            assembly is None
            or assembly.sender_sequence != packet.sender_sequence
            or assembly.route_version != packet.route_version
            or assembly.chunk_count != packet.chunk_count
        )
        if assembly is not None and different:
            if packet.sender_sequence == assembly.sender_sequence:
                if (
                    packet.route_version != assembly.route_version
                    and not self._version_newer(
                        packet.route_version, assembly.route_version
                    )
                ):
                    return None
            elif not sequence_newer(
                packet.sender_sequence, assembly.sender_sequence
            ):
                return None
        if different:
            assembly = CrystAssemblyState(
                sender_sequence=packet.sender_sequence,
                route_version=packet.route_version,
                chunk_count=packet.chunk_count,
                last_update_ms=self.sim.now,
                chunks={},
            )
            self.cryst_assemblies[previous_hop] = assembly
            self.sim.schedule(
                DTPK_CRYST_ASSEMBLY_EXPIRY_MS,
                self._expire_cryst_assembly,
                previous_hop,
                packet.sender_sequence,
                packet.route_version,
                packet.chunk_count,
            )

        assembly.last_update_ms = self.sim.now
        assembly.chunks.setdefault(packet.chunk_index, packet.advertisements)
        if len(assembly.chunks) != assembly.chunk_count:
            return None

        advertisements = tuple(
            advertisement
            for chunk_index in range(assembly.chunk_count)
            for advertisement in assembly.chunks[chunk_index]
        )
        self.cryst_assemblies.pop(previous_hop, None)
        return advertisements

    def receive_cryst(self, packet: Packet, previous_hop: int) -> None:
        self.sim.metrics.cryst_rx += 1

        advertisements = packet.advertisements
        sender_sequence = packet.sender_sequence
        if self.profile.state_digest_requests:
            reassembled = self._reassemble_cryst_v2(packet, previous_hop)
            if reassembled is None:
                return
            advertisements = reassembled
        elif self.profile.session_gc_enabled:
            if not self.session_active:
                self.session_active = True
                self.session_seen.clear()
            self.session_seen.add(previous_hop)
            self.session_deadline = self.sim.now + self.profile.k_limit_ms
            self.session_token += 1
            token = self.session_token
            self.sim.schedule(
                self.profile.k_limit_ms,
                self._session_timeout,
                token,
            )

        advertised_me = any(
            advertisement.dest == self.id
            for advertisement in advertisements
        )

        if self.profile.sequence_numbers and not self.profile.state_digest_requests:
            sender_sequence = 0
            for advertisement in advertisements:
                if (
                    advertisement.dest == previous_hop
                    and advertisement.via == previous_hop
                    and advertisement.distance == 0
                ):
                    sender_sequence = advertisement.sequence
                    break

        incoming: Dict[int, AdvertisedRoute] = {
            previous_hop: AdvertisedRoute(
                previous_hop,
                self.id,
                1,
                sender_sequence,
            )
        }

        for advertisement in advertisements:
            if (
                self.profile.sequence_numbers
                and advertisement.dest == previous_hop
            ):
                continue
            if advertisement.dest == self.id:
                continue
            # Legacy profiles still model split horizon. v4 crystallized state
            # no longer carries a `via` field; feasibility is the loop-safety
            # invariant for that protocol.
            if (
                not self.profile.state_digest_requests
                and advertisement.via == self.id
            ):
                continue

            distance = advertisement.distance + 1
            if self.profile.distance_uint8_wrap:
                distance &= 0xFF
            elif self.profile.max_metric is not None:
                distance = min(distance, self.profile.max_metric)
            if self.profile.sequence_numbers and distance >= ROUTE_INFINITY:
                continue

            incoming[advertisement.dest] = AdvertisedRoute(
                advertisement.dest,
                previous_hop,
                distance,
                advertisement.sequence,
            )

        old = self.routes_by_neighbor.get(previous_hop)
        contribution_changed = old != incoming
        self.routes_by_neighbor[previous_hop] = incoming
        best_changed = self.rebuild_routes()
        if self.profile.state_digest_requests:
            self.neighbor_cryst_state[previous_hop] = (
                packet.sender_sequence,
                packet.route_version,
            )

        should_send = (
            (self.profile.cryst_missing_self_reply and not advertised_me)
            or (self.profile.propagate_on_route_change and best_changed)
        )
        self.sim.log(
            "cryst_rx",
            node=self.id,
            sender=previous_hop,
            contribution_changed=contribution_changed,
            best_changed=best_changed,
            should_send=should_send,
            chunk_count=packet.chunk_count,
        )
        if should_send:
            self.schedule_cryst("cryst_update")

    def _session_timeout(self, token: int) -> None:
        if (
            token != self.session_token
            or not self.session_active
            or not self.up
            or self.crashed
        ):
            return
        if self.sim.now + 1e-9 < self.session_deadline:
            return

        removed = (
            [
                neighbor
                for neighbor in self.routes_by_neighbor
                if neighbor not in self.session_seen
            ]
            if self.profile.session_gc_enabled
            else []
        )
        for neighbor in removed:
            del self.routes_by_neighbor[neighbor]
        self.session_active = False
        self.session_seen.clear()

        if removed:
            changed = self.rebuild_routes()
            if changed:
                self.schedule_cryst("cryst_gc")

    # ------------------------------------------------------------------
    # Route selection
    # ------------------------------------------------------------------
    def _route_candidates(self) -> Dict[int, List[Route]]:
        candidates: Dict[int, List[Route]] = defaultdict(list)
        for router, contribution in self.routes_by_neighbor.items():
            for dest, advertisement in contribution.items():
                if dest == self.id:
                    continue
                candidates[dest].append(
                    Route(
                        router,
                        advertisement.via,
                        advertisement.distance,
                        advertisement.sequence,
                    )
                )
        return candidates

    def _is_feasible(self, dest: int, route: Route) -> bool:
        if route.distance >= ROUTE_INFINITY:
            return False
        state = self.feasibility.get(dest)
        if state is None:
            return True
        if sequence_newer(route.sequence, state.sequence):
            return True
        if route.sequence != state.sequence:
            return False
        neighbour_metric = max(0, route.distance - 1)
        return neighbour_metric < state.feasible_distance

    def _rebuild_feasible(self) -> Tuple[Dict[int, Route], Dict[int, List[Route]]]:
        candidates = self._route_candidates()
        new: Dict[int, Route] = {}
        blocked: Dict[int, List[Route]] = defaultdict(list)

        for dest, routes in candidates.items():
            finite = [route for route in routes if route.distance < ROUTE_INFINITY]
            feasible: List[Route] = []
            for route in finite:
                if self._is_feasible(dest, route):
                    feasible.append(route)
                else:
                    blocked[dest].append(route)
                    self.sim.metrics.feasibility_rejects += 1
            if feasible:
                # Sequence numbers decide feasibility; they are not a metric.
                # Among safe candidates, choose the lowest cost deterministically.
                new[dest] = min(
                    feasible,
                    key=lambda route: (route.distance, route.next_hop),
                )

        return new, blocked

    def rebuild_routes(self) -> bool:
        old = self.routes
        blocked: Dict[int, List[Route]] = {}

        if self.profile.feasibility_condition:
            new, blocked = self._rebuild_feasible()
        else:
            candidates = self._route_candidates()
            new = {
                dest: min(routes, key=lambda route: (route.distance, route.next_hop))
                for dest, routes in candidates.items()
            }

        changed = new != old
        self.routes = new
        if changed:
            self.route_version = (self.route_version + 1) & 0xFFFFFFFF
            if self.route_version == 0:
                self.route_version = 1
            self.sim.metrics.route_changes += 1

        # Feasibility provides safety by refusing a route that could point back
        # into the forwarding DAG. If that refusal is the only reason a known
        # destination vanished, explicitly ask the origin for a new generation.
        if self.profile.seqno_requests:
            for dest, rejected in blocked.items():
                if dest in new or not rejected:
                    continue
                state = self.feasibility.get(dest)
                if state is None:
                    continue
                self.request_sequence(dest, next_sequence(state.sequence))

        self.sim.metrics.max_route_entries = max(
            self.sim.metrics.max_route_entries,
            len(new),
        )
        self.sim.metrics.max_contributions = max(
            self.sim.metrics.max_contributions,
            sum(len(value) for value in self.routes_by_neighbor.values()),
        )
        return changed

    # ------------------------------------------------------------------
    # Data plane
    # ------------------------------------------------------------------
    @staticmethod
    def _fragment_count(total_size: int) -> int:
        if total_size <= 0 or total_size > DTPK_MAX_MESSAGE_SIZE:
            return 0
        return (total_size + DTPK_FRAGMENT_PAYLOAD_SIZE - 1) // DTPK_FRAGMENT_PAYLOAD_SIZE

    @staticmethod
    def _fragment_payload_bytes(total_size: int, index: int) -> int:
        count = Node._fragment_count(total_size)
        if count == 0 or index < 0 or index >= count:
            return 0
        offset = index * DTPK_FRAGMENT_PAYLOAD_SIZE
        return min(DTPK_FRAGMENT_PAYLOAD_SIZE, total_size - offset)

    def _multipart_query_delay_ms(self) -> float:
        if self.multipart_send is None:
            return float(DTPK_FRAGMENT_QUERY_INTERVAL_MS)
        route = self.routes.get(self.multipart_send["target"])
        hops = max(1, route.distance if route is not None else 1)
        return float(
            DTPK_FRAGMENT_QUERY_INTERVAL_MS
            + (2 * hops - 1) * DTPK_FRAGMENT_RELAY_HOP_BUDGET_MS
        )

    def _schedule_multipart_query(self, delay_ms: float) -> None:
        state = self.multipart_send
        if state is None:
            return
        state["query_token"] += 1
        token = state["query_token"]
        state["next_query_at"] = self.sim.now + delay_ms
        self.sim.schedule(
            delay_ms,
            self._multipart_query_tick,
            state["id"],
            state["source_sequence"],
            token,
        )

    def _multipart_query_tick(
        self, packet_id: int, source_sequence: int, token: int
    ) -> None:
        state = self.multipart_send
        if (
            state is None
            or state["id"] != packet_id
            or state["source_sequence"] != source_sequence
            or state["query_token"] != token
        ):
            return
        self._pump_multipart()
        self.sim.schedule(0, self.pump)

    def _queue_multipart_fragment(self, index: int, retransmit: bool) -> bool:
        state = self.multipart_send
        if state is None:
            return False
        route = self.routes.get(state["target"])
        payload_bytes = self._fragment_payload_bytes(state["total_size"], index)
        if route is None or payload_bytes <= 0:
            return False
        packet = Packet(
            "DATA_FRAGMENT",
            state["id"],
            original_sender=self.id,
            final_target=state["target"],
            payload_size=payload_bytes,
            e2e_ack_requested=True,
            sender_sequence=state["source_sequence"],
            wire_dtpk_size=DTPK_FRAGMENT_HEADER + payload_bytes,
            total_size=state["total_size"],
            fragment_index=index,
            fragment_count=state["fragment_count"],
            hop_limit=DTPK_DEFAULT_HOP_LIMIT,
        )
        self.sim.metrics.fragments_tx += 1
        if retransmit:
            self.sim.metrics.fragment_retransmits += 1
        self.sim.log(
            "fragment_enqueue",
            node=self.id,
            packet_id=state["id"],
            index=index,
            retransmit=retransmit,
        )
        self.enqueue(
            TxRequest(
                packet,
                route.next_hop,
                lcmm_ack=True,
                timeout_ms=10_000,
            )
        )
        return True

    def _queue_fragment_query(self) -> bool:
        state = self.multipart_send
        if state is None:
            return False
        route = self.routes.get(state["target"])
        if route is None:
            return False
        query_id = state["next_query_id"]
        state["next_query_id"] = (query_id + 1) & 0xFFFF or 1
        packet = Packet(
            "FRAGMENT_QUERY",
            state["id"],
            original_sender=self.id,
            final_target=state["target"],
            e2e_ack_requested=True,
            sender_sequence=state["source_sequence"],
            wire_dtpk_size=DTPK_FRAGMENT_QUERY_SIZE,
            total_size=state["total_size"],
            fragment_count=state["fragment_count"],
            query_id=query_id,
            hop_limit=DTPK_DEFAULT_HOP_LIMIT,
        )
        self.sim.metrics.fragment_query_tx += 1
        self.enqueue(
            TxRequest(
                packet,
                route.next_hop,
                lcmm_ack=True,
                timeout_ms=5_000,
                priority=True,
            )
        )
        return True

    def _pump_multipart(self) -> None:
        state = self.multipart_send
        if state is None or self.lcmm_busy:
            return
        if any(
            queued.packet.kind in ("DATA_FRAGMENT", "FRAGMENT_QUERY")
            and queued.packet.packet_id == state["id"]
            and queued.packet.sender_sequence == state["source_sequence"]
            for queued in self.txq
        ):
            return
        if state["next_initial"] < state["fragment_count"]:
            index = state["next_initial"]
            if self._queue_multipart_fragment(index, False):
                state["next_initial"] += 1
            return
        if state["retransmit"]:
            index = min(state["retransmit"])
            if self._queue_multipart_fragment(index, True):
                state["retransmit"].remove(index)
            return
        if self.sim.now + 1e-9 >= state["next_query_at"]:
            self._queue_fragment_query()

    def _expire_fragment_assemblies(self) -> None:
        stale = [
            key
            for key, assembly in self.fragment_assemblies.items()
            if self.sim.now - assembly["last_update"]
            >= DTPK_FRAGMENT_ASSEMBLY_EXPIRY_MS
        ]
        for key in stale:
            del self.fragment_assemblies[key]
            self.sim.metrics.fragment_assemblies_expired += 1

    def _queue_fragment_status(
        self, assembly: dict, previous_hop: int, query_id: int
    ) -> None:
        missing = tuple(
            index
            for index in range(assembly["fragment_count"])
            if index not in assembly["received"]
        )
        bitmap_bytes = (assembly["fragment_count"] + 7) // 8
        packet = Packet(
            "FRAGMENT_STATUS",
            assembly["id"],
            original_sender=self.id,
            final_target=assembly["original_sender"],
            sender_sequence=assembly["source_sequence"],
            wire_dtpk_size=DTPK_FRAGMENT_STATUS_HEADER + bitmap_bytes,
            fragment_count=assembly["fragment_count"],
            query_id=query_id,
            missing_fragments=missing,
            hop_limit=DTPK_DEFAULT_HOP_LIMIT,
        )
        self.sim.metrics.fragment_status_tx += 1
        self.enqueue(
            TxRequest(packet, previous_hop, lcmm_ack=True, priority=True)
        )

    def _queue_message_ack(self, packet: Packet, previous_hop: int) -> None:
        target = packet.original_sender
        if target is None:
            return
        ack = Packet(
            "ACK",
            packet.packet_id,
            original_sender=self.id,
            final_target=target,
            sender_sequence=packet.sender_sequence,
            wire_dtpk_size=DTPK_GENERIC_HEADER,
            hop_limit=DTPK_DEFAULT_HOP_LIMIT,
        )
        self.enqueue(
            TxRequest(ack, previous_hop, lcmm_ack=True, priority=True)
        )

    def receive_fragment_local(self, packet: Packet, previous_hop: int) -> None:
        source = packet.original_sender if packet.original_sender is not None else -1
        key = (source, packet.sender_sequence, packet.packet_id)
        count = self._fragment_count(packet.total_size)
        expected = self._fragment_payload_bytes(
            packet.total_size, packet.fragment_index
        )
        if (
            count == 0
            or packet.fragment_count != count
            or expected != packet.payload_size
        ):
            return
        if key in self.delivered_ids:
            self._queue_message_ack(packet, previous_hop)
            return

        assembly = self.fragment_assemblies.get(key)
        if assembly is None:
            if len(self.fragment_assemblies) >= DTPK_MAX_FRAGMENT_ASSEMBLIES:
                oldest = min(
                    self.fragment_assemblies,
                    key=lambda item: self.fragment_assemblies[item]["last_update"],
                )
                del self.fragment_assemblies[oldest]
                self.sim.metrics.fragment_assemblies_expired += 1
            assembly = {
                "id": packet.packet_id,
                "source_sequence": packet.sender_sequence,
                "original_sender": source,
                "total_size": packet.total_size,
                "fragment_count": count,
                "received": set(),
                "last_update": self.sim.now,
                "last_hop": previous_hop,
            }
            self.fragment_assemblies[key] = assembly
        if (
            assembly["total_size"] != packet.total_size
            or assembly["fragment_count"] != count
        ):
            return
        assembly["last_update"] = self.sim.now
        assembly["last_hop"] = previous_hop
        assembly["received"].add(packet.fragment_index)

        if len(assembly["received"]) == count:
            del self.fragment_assemblies[key]
            logical = Packet(
                "DATA",
                packet.packet_id,
                original_sender=source,
                final_target=self.id,
                payload_size=packet.total_size,
                e2e_ack_requested=True,
                sender_sequence=packet.sender_sequence,
                wire_dtpk_size=DTPK_GENERIC_HEADER + packet.total_size,
                hop_limit=DTPK_DEFAULT_HOP_LIMIT,
            )
            self.sim.metrics.multipart_messages_completed += 1
            self.receive_data_local(logical, previous_hop)
            return
        if packet.fragment_index + 1 == count:
            self._queue_fragment_status(assembly, previous_hop, 0)

    def receive_fragment_query_local(
        self, packet: Packet, previous_hop: int
    ) -> None:
        source = packet.original_sender if packet.original_sender is not None else -1
        key = (source, packet.sender_sequence, packet.packet_id)
        count = self._fragment_count(packet.total_size)
        if count == 0 or packet.fragment_count != count:
            return
        if key in self.delivered_ids:
            self._queue_message_ack(packet, previous_hop)
            return
        assembly = self.fragment_assemblies.get(key)
        if assembly is None:
            assembly = {
                "id": packet.packet_id,
                "source_sequence": packet.sender_sequence,
                "original_sender": source,
                "total_size": packet.total_size,
                "fragment_count": count,
                "received": set(),
                "last_update": self.sim.now,
                "last_hop": previous_hop,
            }
        else:
            assembly["last_update"] = self.sim.now
            assembly["last_hop"] = previous_hop
        self._queue_fragment_status(assembly, previous_hop, packet.query_id)

    def receive_fragment_status_local(self, packet: Packet) -> None:
        state = self.multipart_send
        if (
            state is None
            or packet.packet_id != state["id"]
            or packet.sender_sequence != state["source_sequence"]
            or packet.original_sender != state["target"]
            or packet.fragment_count != state["fragment_count"]
            or packet.query_id == state["last_status_query_id"]
        ):
            return
        state["last_status_query_id"] = packet.query_id
        state["retransmit"].update(
            index
            for index in packet.missing_fragments
            if 0 <= index < state["fragment_count"]
        )
        self._pump_multipart()
        self.sim.schedule(0, self.pump)

    def send_data(
        self,
        target: int,
        payload_size: int = 16,
        e2e_ack: bool = True,
        timeout_ms: Optional[int] = None,
    ) -> Optional[int]:
        if not self.up or self.crashed:
            return None
        route = self.routes.get(target)
        if route is None:
            self.sim.metrics.route_misses += 1
            self.sim.log("route_miss", node=self.id, target=target)
            return None
        if payload_size < 0:
            return None

        pid = self.next_packet_id()
        if payload_size <= DTPK_SINGLE_PAYLOAD_SIZE:
            packet = Packet(
                "DATA",
                pid,
                original_sender=self.id,
                final_target=target,
                payload_size=payload_size,
                e2e_ack_requested=e2e_ack,
                sender_sequence=self.origin_sequence,
                wire_dtpk_size=DTPK_GENERIC_HEADER + payload_size,
                hop_limit=DTPK_DEFAULT_HOP_LIMIT,
            )
            self.enqueue(
                TxRequest(
                    packet,
                    route.next_hop,
                    lcmm_ack=True,
                    dtpk_ack=e2e_ack,
                    timeout_ms=timeout_ms or self.profile.e2e_timeout_ms,
                )
            )
            return pid

        count = self._fragment_count(payload_size)
        if count == 0 or self.multipart_send is not None:
            self.sim.metrics.oversize_drops += 1
            return None
        requested = timeout_ms or self.profile.e2e_timeout_ms
        hops = max(1, route.distance)
        minimum = (
            15_000
            + DTPK_FRAGMENT_QUERY_INTERVAL_MS
            + (count + 1) * DTPK_FRAGMENT_SOURCE_HOP_BUDGET_MS
            + (2 * hops + 1) * DTPK_FRAGMENT_RELAY_HOP_BUDGET_MS
        )
        effective_timeout = max(requested, minimum)
        self.multipart_send = {
            "id": pid,
            "source_sequence": self.origin_sequence,
            "target": target,
            "total_size": payload_size,
            "fragment_count": count,
            "next_initial": 0,
            "retransmit": set(),
            "next_query_at": self.sim.now + DTPK_FRAGMENT_QUERY_INTERVAL_MS,
            "next_query_id": 1,
            "last_status_query_id": -1,
            "query_token": 0,
        }
        self.waiting_e2e = (
            pid,
            self.origin_sequence,
            target,
            self.sim.now + effective_timeout,
        )
        self.wait_token += 1
        token = self.wait_token
        self.sim.schedule(
            effective_timeout,
            self._e2e_timeout,
            token,
            pid,
            self.origin_sequence,
        )
        self.sim.metrics.multipart_messages_started += 1
        self._pump_multipart()
        self.sim.schedule(0, self.pump)
        return pid

    def receive_data_local(self, packet: Packet, previous_hop: int) -> None:
        key = (
            packet.original_sender or -1,
            packet.sender_sequence,
            packet.packet_id,
        )
        duplicate = key in self.delivered_ids
        if duplicate:
            self.sim.metrics.duplicate_app += 1
        if not duplicate or not self.profile.duplicate_suppression:
            self.sim.metrics.delivered_app += 1
        self.delivered_ids.add(key)
        self.sim.log(
            "app_rx",
            node=self.id,
            sender=packet.original_sender,
            packet_id=packet.packet_id,
            duplicate=duplicate,
            wire_dtpk_size=packet.wire_dtpk_size,
        )

        if not packet.e2e_ack_requested:
            return

        target = packet.original_sender
        if target is None:
            return
        route = self.routes.get(target)
        if route is None and self.profile.ack_null_route_crash:
            self.crash("sendAckPacket null routing dereference")
            return
        self._queue_message_ack(packet, previous_hop)

    def receive_ack_local(self, packet: Packet, positive: bool) -> None:
        if (
            self.waiting_e2e is None
            or self.waiting_e2e[:2]
            != (packet.packet_id, packet.sender_sequence)
        ):
            self.sim.log(
                "unexpected_e2e",
                node=self.id,
                packet_id=packet.packet_id,
                kind=packet.kind,
            )
            return

        self.waiting_e2e = None
        if (
            self.multipart_send is not None
            and self.multipart_send["id"] == packet.packet_id
            and self.multipart_send["source_sequence"]
            == packet.sender_sequence
        ):
            self.multipart_send = None
        self.wait_token += 1
        if positive or self.profile.nack_is_success:
            self.sim.metrics.e2e_success += 1
            self.sim.log(
                "e2e_success",
                node=self.id,
                packet_id=packet.packet_id,
                via=packet.kind,
            )
        else:
            self.sim.metrics.e2e_failure += 1
            self.sim.log(
                "e2e_nack",
                node=self.id,
                packet_id=packet.packet_id,
            )
        self.sim.schedule(0, self.pump)

    def forward(self, packet: Packet, previous_hop: int) -> None:
        if packet.final_target is None:
            return
        if packet.hop_limit <= 1:
            self.sim.metrics.route_misses += 1
            if packet.kind in ("DATA", "DATA_FRAGMENT") and packet.original_sender is not None:
                nack = Packet(
                    "NACK", packet.packet_id,
                    original_sender=self.id,
                    final_target=packet.original_sender,
                    sender_sequence=packet.sender_sequence,
                    wire_dtpk_size=DTPK_GENERIC_HEADER,
                    hop_limit=DTPK_DEFAULT_HOP_LIMIT,
                )
                self.sim.metrics.nacks += 1
                self.enqueue(TxRequest(
                    nack, previous_hop, lcmm_ack=True, priority=True
                ))
            return
        route = self.routes.get(packet.final_target)
        if route is None:
            self.sim.metrics.route_misses += 1
            if packet.kind in ("DATA", "DATA_FRAGMENT") and packet.original_sender is not None:
                nack = Packet(
                    "NACK",
                    packet.packet_id,
                    original_sender=self.id,
                    final_target=packet.original_sender,
                    sender_sequence=packet.sender_sequence,
                    wire_dtpk_size=DTPK_GENERIC_HEADER,
                    hop_limit=DTPK_DEFAULT_HOP_LIMIT,
                )
                self.sim.metrics.nacks += 1
                self.enqueue(
                    TxRequest(
                        nack,
                        previous_hop,
                        lcmm_ack=True,
                        priority=True,
                    )
                )
            return

        forwarded = packet.clone()
        forwarded.hop_limit -= 1
        if self.profile.forwarding_size_bug:
            forwarded.wire_dtpk_size = (
                packet.wire_dtpk_size
                or DTPK_GENERIC_HEADER + packet.payload_size
            ) + LCMM_RX_HEADER

        self.enqueue(
            TxRequest(
                forwarded,
                route.next_hop,
                lcmm_ack=self.profile.relay_lcmm_ack,
                priority=packet.kind in (
                    "ACK", "NACK", "CRYST_REQ",
                    "FRAGMENT_STATUS", "FRAGMENT_QUERY"
                ),
            )
        )
