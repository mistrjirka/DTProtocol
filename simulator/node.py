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
        self.feasibility: Dict[int, FeasibilityState] = {}
        self.origin_sequence = 0

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

    def start(self) -> None:
        self.up = True
        self.crashed = False
        self.generation += 1

        if self.profile.sequence_numbers:
            # The Python model keeps the node object across simulated reboots, so
            # this models a persistent boot generation. The embedded C++ backend
            # will use a persistence hook for the same property.
            self.origin_sequence = (self.origin_sequence + 1) & 0xFFFF
            if self.origin_sequence == 0:
                self.origin_sequence = 1

        self.sim.log("node_up", node=self.id)
        self.schedule_cryst("startup")

        gen = self.generation
        if self.profile.periodic_cryst_ms:
            self.sim.schedule(
                self.profile.periodic_cryst_ms,
                self._periodic_cryst,
                gen,
            )
        if self.profile.origin_seq_period_ms:
            self.sim.schedule(
                self.profile.origin_seq_period_ms,
                self._origin_sequence_tick,
                gen,
            )
        if self.profile.neighbor_expiry_ms:
            self.sim.schedule(
                self.profile.neighbor_expiry_ms / 3,
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

    def _periodic_cryst(self, gen: int) -> None:
        if gen != self.generation or not self.up or self.crashed:
            return
        self.schedule_cryst("periodic")
        self.sim.schedule(
            self.profile.periodic_cryst_ms or 1,
            self._periodic_cryst,
            gen,
        )

    def _origin_sequence_tick(self, gen: int) -> None:
        if gen != self.generation or not self.up or self.crashed:
            return
        self.origin_sequence = (self.origin_sequence + 1) & 0xFFFF
        if self.origin_sequence == 0:
            self.origin_sequence = 1
        self.schedule_cryst("origin_sequence")
        self.sim.schedule(
            self.profile.origin_seq_period_ms or 1,
            self._origin_sequence_tick,
            gen,
        )

    def _expiry_tick(self, gen: int) -> None:
        if gen != self.generation or not self.up or self.crashed:
            return
        expiry = self.profile.neighbor_expiry_ms
        assert expiry is not None
        stale = [
            n for n, last in self.last_heard.items()
            if self.sim.now - last >= expiry
        ]
        changed = False
        for neighbor in stale:
            self.last_heard.pop(neighbor, None)
            if neighbor in self.routes_by_neighbor:
                del self.routes_by_neighbor[neighbor]
                changed = True
        if changed:
            if self.rebuild_routes():
                self.schedule_cryst("neighbor_expired")
        self.sim.schedule(expiry / 3, self._expiry_tick, gen)

    def schedule_cryst(self, reason: str) -> None:
        if not self.up or self.crashed or self.cryst_scheduled:
            return
        self.cryst_scheduled = True
        self.cryst_token += 1
        token = self.cryst_token
        high = max(self.profile.cryst_jitter_min_ms + 1, self.profile.k_limit_ms)
        delay = self.sim.rng.uniform(self.profile.cryst_jitter_min_ms, high)
        self.sim.log(
            "cryst_schedule",
            node=self.id,
            reason=reason,
            delay=round(delay, 2),
        )
        self.sim.schedule(delay, self._emit_cryst, token)

    def _emit_cryst(self, token: int) -> None:
        if token != self.cryst_token or not self.up or self.crashed:
            return
        self.cryst_scheduled = False

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
        packet = Packet(
            "CRYST",
            self.next_packet_id(),
            advertisements=tuple(ads),
            wire_dtpk_size=DTPK_CRYST_HEADER + len(ads) * record_size,
            cryst_record_size=record_size,
        )
        self.enqueue(TxRequest(packet, None, lcmm_ack=False))

    def next_packet_id(self) -> int:
        pid = self.packet_counter & 0xFFFF
        self.packet_counter = (self.packet_counter + 1) & 0xFFFF
        return pid

    def enqueue(self, req: TxRequest) -> None:
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
                and req.packet.kind in ("ACK", "NACK", "CRYST")
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
                token = self.wait_token
                self.sim.schedule(
                    req.timeout_ms,
                    self._e2e_timeout,
                    token,
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
        if (
            not req.lcmm_ack
            and self.profile.noack_releases_lcmm_immediately
        ):
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

    def receive(self, packet: Packet, previous_hop: int) -> None:
        if not self.up or self.crashed:
            return

        # Liveness is a property of hearing a valid direct RF neighbor, not of
        # participating in a particular crystallization wave.
        self.last_heard[previous_hop] = self.sim.now

        if packet.kind == "CRYST":
            self.receive_cryst(packet, previous_hop)
            return

        if packet.final_target == self.id:
            if packet.kind == "DATA":
                self.receive_data_local(packet, previous_hop)
            elif packet.kind == "ACK":
                self.receive_ack_local(packet, True)
            elif packet.kind == "NACK":
                self.receive_ack_local(packet, False)
            return

        self.forward(packet, previous_hop)

    def receive_cryst(self, packet: Packet, previous_hop: int) -> None:
        self.sim.metrics.cryst_rx += 1

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
            ad.dest == self.id
            for ad in packet.advertisements
        )

        sender_sequence = 0
        if self.profile.sequence_numbers:
            for ad in packet.advertisements:
                if (
                    ad.dest == previous_hop
                    and ad.via == previous_hop
                    and ad.distance == 0
                ):
                    sender_sequence = ad.sequence
                    break

        incoming: Dict[int, AdvertisedRoute] = {
            previous_hop: AdvertisedRoute(
                previous_hop,
                self.id,
                1,
                sender_sequence,
            )
        }

        for ad in packet.advertisements:
            # Sender's explicit self route became the direct candidate above.
            if self.profile.sequence_numbers and ad.dest == previous_hop:
                continue
            # Broadcast split horizon: each receiver drops a route whose selected
            # next hop at the sender was the receiver itself.
            if ad.dest == self.id or ad.via == self.id:
                continue

            distance = ad.distance + 1
            if self.profile.distance_uint8_wrap:
                distance &= 0xFF
            elif self.profile.max_metric is not None:
                distance = min(distance, self.profile.max_metric)

            # 255 is an explicit infinity in v2 and is never selected.
            if (
                self.profile.sequence_numbers
                and distance >= ROUTE_INFINITY
            ):
                continue

            incoming[ad.dest] = AdvertisedRoute(
                ad.dest,
                previous_hop,
                distance,
                ad.sequence,
            )

        old = self.routes_by_neighbor.get(previous_hop)
        contribution_changed = old != incoming
        self.routes_by_neighbor[previous_hop] = incoming
        best_changed = self.rebuild_routes()

        should_send = (
            (not advertised_me)
            or (
                self.profile.propagate_on_route_change
                and best_changed
            )
        )
        self.sim.log(
            "cryst_rx",
            node=self.id,
            sender=previous_hop,
            advertised_me=advertised_me,
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
            self.sim.log(
                "cryst_gc",
                node=self.id,
                removed=removed,
                best_changed=changed,
            )
            if changed:
                self.schedule_cryst("cryst_gc")

    def _route_candidates(self) -> Dict[int, List[Route]]:
        candidates: Dict[int, List[Route]] = defaultdict(list)
        for router, contribution in self.routes_by_neighbor.items():
            for dest, ad in contribution.items():
                if dest == self.id:
                    continue
                candidates[dest].append(
                    Route(
                        router,
                        ad.via,
                        ad.distance,
                        ad.sequence,
                    )
                )
        return candidates

    @staticmethod
    def _newest_sequence(routes: List[Route]) -> int:
        newest = routes[0].sequence
        for route in routes[1:]:
            if sequence_newer(route.sequence, newest):
                newest = route.sequence
        return newest

    def _rebuild_feasible(self, old: Dict[int, Route]) -> Dict[int, Route]:
        candidates = self._route_candidates()
        new: Dict[int, Route] = {}

        for dest, routes in candidates.items():
            routes = [
                route
                for route in routes
                if route.distance < ROUTE_INFINITY
            ]
            if not routes:
                continue

            state = self.feasibility.get(dest)
            current = old.get(dest)

            if state is None:
                newest = self._newest_sequence(routes)
                same_generation = [
                    route
                    for route in routes
                    if route.sequence == newest
                ]
                chosen = min(
                    same_generation,
                    key=lambda route: (route.distance, route.next_hop),
                )
                self.feasibility[dest] = FeasibilityState(
                    newest,
                    chosen.distance,
                )
                new[dest] = chosen
                continue

            newer = [
                route
                for route in routes
                if sequence_newer(route.sequence, state.sequence)
            ]
            if newer:
                newest = self._newest_sequence(newer)
                fresh = [
                    route
                    for route in newer
                    if route.sequence == newest
                ]
                chosen = min(
                    fresh,
                    key=lambda route: (route.distance, route.next_hop),
                )
                self.feasibility[dest] = FeasibilityState(
                    newest,
                    chosen.distance,
                )
                self.sim.metrics.sequence_resets += 1
                new[dest] = chosen
                continue

            same = [
                route
                for route in routes
                if route.sequence == state.sequence
            ]

            # The selected successor may report a worse metric without raising
            # feasible distance. Switching to another successor is allowed only
            # if that neighbor advertises a metric strictly below our FD. With
            # unit link cost its advertised metric is local_distance - 1.
            selected_candidate = None
            if current is not None and current.sequence == state.sequence:
                selected_candidate = next(
                    (
                        route
                        for route in same
                        if route.next_hop == current.next_hop
                    ),
                    None,
                )

            feasible_successors = [
                route
                for route in same
                if max(0, route.distance - 1) < state.feasible_distance
            ]

            valid = list(feasible_successors)
            if (
                selected_candidate is not None
                and selected_candidate not in valid
            ):
                valid.append(selected_candidate)

            if not valid:
                if same:
                    self.sim.metrics.feasibility_rejects += len(same)
                continue

            chosen = min(
                valid,
                key=lambda route: (route.distance, route.next_hop),
            )
            self.feasibility[dest] = FeasibilityState(
                state.sequence,
                min(state.feasible_distance, chosen.distance),
            )
            new[dest] = chosen

        return new

    def rebuild_routes(self) -> bool:
        old = self.routes

        if self.profile.feasibility_condition:
            new = self._rebuild_feasible(old)
        else:
            candidates = self._route_candidates()
            new: Dict[int, Route] = {}
            for dest, routes in candidates.items():
                new[dest] = min(
                    routes,
                    key=lambda route: (route.distance, route.next_hop),
                )

        changed = new != old
        self.routes = new
        if changed:
            self.sim.metrics.route_changes += 1
        self.sim.metrics.max_route_entries = max(
            self.sim.metrics.max_route_entries,
            len(new),
        )
        self.sim.metrics.max_contributions = max(
            self.sim.metrics.max_contributions,
            sum(len(value) for value in self.routes_by_neighbor.values()),
        )
        return changed

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
        if (
            self.waiting_e2e is None
            or self.waiting_e2e[0] != packet.packet_id
        ):
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
                priority=packet.kind in ("ACK", "NACK"),
            )
        )
