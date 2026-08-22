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

        self.seq_requests_seen: Set[Tuple[int, int, int, int]] = set()
        self.seq_request_last: Dict[int, float] = {}
        self.seq_request_max_seen: Dict[int, int] = {}

        self.session_active = False
        self.session_seen: Set[int] = set()
        self.session_deadline = 0.0
        self.session_token = 0
        self.cryst_scheduled = False
        self.cryst_token = 0
        self.last_heard: Dict[int, float] = {}

        self.txq: Deque[TxRequest] = deque()
        self.lcmm_busy = False
        self.radio_busy_until = 0.0
        self.waiting_e2e: Optional[Tuple[int, int, float]] = None
        self.wait_token = 0
        self.delivered_ids: Set[Tuple[int, int]] = set()
        self.crashed_reason: Optional[str] = None

    def reset_runtime(self) -> None:
        self.routes_by_neighbor.clear()
        self.routes.clear()
        self.feasibility.clear()
        self.route_version = 0
        self.neighbor_cryst_state.clear()
        self.seq_requests_seen.clear()
        self.seq_request_last.clear()
        self.seq_request_max_seen.clear()
        self.session_active = False
        self.session_seen.clear()
        self.session_deadline = 0
        self.cryst_scheduled = False
        self.last_heard.clear()
        self.txq.clear()
        self.lcmm_busy = False
        self.radio_busy_until = 0
        self.waiting_e2e = None
        self.delivered_ids.clear()
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

        # HELLO is replaceable maintenance state. Do not build a backlog while
        # regulation says the node cannot transmit, and do not queue it behind a
        # CRYST snapshot that already carries stronger routing-state identity.
        if (
            self.sim.transmit_wait_ms(self.id) <= 1e-9
            and not any(
                queued.packet.kind in ("HELLO", "CRYST")
                for queued in self.txq
            )
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
            period *= self.sim.rng.uniform(1.0 - jitter, 1.0 + jitter)
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
            if neighbor in self.routes_by_neighbor:
                del self.routes_by_neighbor[neighbor]
                changed_contribution = True

        if changed_contribution:
            changed_best = self.rebuild_routes()
            if changed_best:
                self.schedule_cryst("neighbor_expired")
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
        pid = self.packet_counter & 0xFFFF
        self.packet_counter = (self.packet_counter + 1) & 0xFFFF
        return pid

    # ------------------------------------------------------------------
    # TX queue / reliability
    # ------------------------------------------------------------------
    def enqueue(self, req: TxRequest) -> None:
        # HELLO and CRYST are replaceable full-state control. Under duty/backoff
        # pressure, retaining old snapshots only delays the newest route state.
        if req.packet.kind == "HELLO":
            if any(
                queued.packet.kind in ("HELLO", "CRYST")
                for queued in self.txq
            ):
                return
        elif req.packet.kind == "CRYST":
            self.txq = deque(
                queued
                for queued in self.txq
                if queued.packet.kind not in ("HELLO", "CRYST")
            )

        if req.priority or req.packet.kind in ("ACK", "NACK"):
            self.txq.appendleft(req)
        else:
            self.txq.append(req)
        self.sim.metrics.max_queue = max(
            self.sim.metrics.max_queue,
            len(self.txq),
        )
        self.sim.schedule(0, self.pump)

    def pump(self) -> None:
        if not self.up or self.crashed or self.lcmm_busy or not self.txq:
            return
        req = self.txq[0]
        if self.waiting_e2e is not None and self.profile.global_e2e_gate:
            if not (
                self.profile.ack_bypasses_e2e_gate
                and req.packet.kind in (
                    "ACK", "NACK", "CRYST", "HELLO", "CRYST_REQ", "SEQ_REQ"
                )
            ):
                return
        self.txq.popleft()
        if req.lcmm_ack:
            self.lcmm_busy = True

        def completed(success: bool) -> None:
            if req.lcmm_ack:
                self.lcmm_busy = False
            if not success:
                self.sim.log(
                    "hop_fail",
                    node=self.id,
                    target=req.next_hop,
                    kind=req.packet.kind,
                )
            if req.dtpk_ack and success:
                self.waiting_e2e = (
                    req.packet.packet_id,
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
                )
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
        return 1_650.0 + self.sim.airtime_ms(self.sim._frame_bytes(packet))

    def _e2e_timeout(self, token: int, packet_id: int) -> None:
        if token != self.wait_token or self.waiting_e2e is None:
            return
        if self.waiting_e2e[0] != packet_id:
            return
        self.waiting_e2e = None
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
            elif packet.kind == "ACK":
                self.receive_ack_local(packet, True)
            elif packet.kind == "NACK":
                self.receive_ack_local(packet, False)
            elif packet.kind == "CRYST_REQ":
                self.sim.metrics.cryst_req_rx += 1
                self.schedule_cryst("state_request")
            return

        self.forward(packet, previous_hop)

    # ------------------------------------------------------------------
    # HELLO / state repair
    # ------------------------------------------------------------------
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
            request = Packet(
                "CRYST_REQ",
                self.next_packet_id(),
                original_sender=self.id,
                final_target=previous_hop,
                wire_dtpk_size=DTPK_CRYST_REQ_SIZE,
            )
            self.sim.metrics.cryst_req_tx += 1
            self.enqueue(
                TxRequest(
                    request,
                    previous_hop,
                    lcmm_ack=True,
                    priority=True,
                )
            )

    # ------------------------------------------------------------------
    # Bounded sequence request: liveness counterpart of feasibility
    # ------------------------------------------------------------------
    def request_sequence(self, dest: int, requested_sequence: int) -> None:
        if not self.profile.seqno_requests or dest == self.id:
            return

        last = self.seq_request_last.get(dest, -1e30)
        max_seen = self.seq_request_max_seen.get(dest, -1)
        if (
            requested_sequence <= max_seen
            and self.sim.now - last < self.profile.seqno_request_cooldown_ms
        ):
            return

        self.seq_request_last[dest] = self.sim.now
        self.seq_request_max_seen[dest] = max(max_seen, requested_sequence)
        request = Packet(
            "SEQ_REQ",
            self.next_packet_id(),
            original_sender=self.id,
            final_target=dest,
            wire_dtpk_size=DTPK_SEQ_REQ_SIZE,
            requested_sequence=requested_sequence & 0xFFFF,
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
        )
        self.enqueue(TxRequest(request, None, lcmm_ack=False, priority=True))

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
        forwarded = packet.clone()
        forwarded.hop_limit -= 1
        self.sim.metrics.seq_req_tx += 1
        self.enqueue(TxRequest(forwarded, None, lcmm_ack=False, priority=True))

    # ------------------------------------------------------------------
    # CRYST receive
    # ------------------------------------------------------------------
    def receive_cryst(self, packet: Packet, previous_hop: int) -> None:
        self.sim.metrics.cryst_rx += 1

        if self.profile.state_digest_requests:
            self.neighbor_cryst_state[previous_hop] = (
                packet.sender_sequence,
                packet.route_version,
            )

        if self.profile.session_gc_enabled:
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
            for advertisement in packet.advertisements
        )

        sender_sequence = packet.sender_sequence
        if self.profile.sequence_numbers and not self.profile.state_digest_requests:
            sender_sequence = 0
            for advertisement in packet.advertisements:
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

        for advertisement in packet.advertisements:
            if (
                self.profile.sequence_numbers
                and advertisement.dest == previous_hop
            ):
                continue
            if advertisement.dest == self.id or advertisement.via == self.id:
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
                # Destination generation is freshness, not a metric. A newer
                # feasible generation supersedes all older candidates even when
                # the older path is shorter. Only compare distance/next-hop
                # within the selected newest generation.
                newest = feasible[0].sequence
                for route in feasible[1:]:
                    if sequence_newer(route.sequence, newest):
                        newest = route.sequence
                same_generation = [
                    route for route in feasible if route.sequence == newest
                ]
                new[dest] = min(
                    same_generation,
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
            self.route_version = (self.route_version + 1) & 0xFFFF
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

        pid = self.next_packet_id()
        packet = Packet(
            "DATA",
            pid,
            original_sender=self.id,
            final_target=target,
            payload_size=payload_size,
            wire_dtpk_size=DTPK_GENERIC_HEADER + payload_size,
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

    def receive_data_local(self, packet: Packet, previous_hop: int) -> None:
        key = (packet.original_sender or -1, packet.packet_id)
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

        target = packet.original_sender
        if target is None:
            return
        route = self.routes.get(target)
        if route is None and self.profile.ack_null_route_crash:
            self.crash("sendAckPacket null routing dereference")
            return

        ack = Packet(
            "ACK",
            packet.packet_id,
            original_sender=self.id,
            final_target=target,
            wire_dtpk_size=DTPK_GENERIC_HEADER,
        )
        self.enqueue(
            TxRequest(
                ack,
                previous_hop,
                lcmm_ack=True,
                priority=True,
            )
        )

    def receive_ack_local(self, packet: Packet, positive: bool) -> None:
        if self.waiting_e2e is None or self.waiting_e2e[0] != packet.packet_id:
            self.sim.log(
                "unexpected_e2e",
                node=self.id,
                packet_id=packet.packet_id,
                kind=packet.kind,
            )
            return

        self.waiting_e2e = None
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
        route = self.routes.get(packet.final_target)
        if route is None:
            self.sim.metrics.route_misses += 1
            if packet.kind == "DATA" and packet.original_sender is not None:
                nack = Packet(
                    "NACK",
                    packet.packet_id,
                    original_sender=self.id,
                    final_target=packet.original_sender,
                    wire_dtpk_size=DTPK_GENERIC_HEADER,
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
                priority=packet.kind in ("ACK", "NACK", "CRYST_REQ"),
            )
        )
