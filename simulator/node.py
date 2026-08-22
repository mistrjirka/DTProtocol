from __future__ import annotations
from collections import defaultdict, deque
from typing import Dict, Optional, Set, Tuple, TYPE_CHECKING
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
        self.waiting_e2e: Optional[Tuple[int, int, float]] = None  # packet id, target, deadline
        self.wait_token = 0
        self.delivered_ids: Set[Tuple[int, int]] = set()
        self.crashed_reason: Optional[str] = None

    def reset_runtime(self) -> None:
        self.routes_by_neighbor.clear()
        self.routes.clear()
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
        self.sim.log("node_up", node=self.id)
        self.schedule_cryst("startup")
        if self.profile.periodic_cryst_ms:
            gen = self.generation
            self.sim.schedule(self.profile.periodic_cryst_ms, self._periodic_cryst, gen)
        if self.profile.neighbor_expiry_ms:
            gen = self.generation
            self.sim.schedule(self.profile.neighbor_expiry_ms / 3, self._expiry_tick, gen)

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
        self.sim.schedule(self.profile.periodic_cryst_ms or 1, self._periodic_cryst, gen)

    def _expiry_tick(self, gen: int) -> None:
        if gen != self.generation or not self.up or self.crashed:
            return
        expiry = self.profile.neighbor_expiry_ms
        assert expiry is not None
        stale = [n for n, last in self.last_heard.items() if self.sim.now - last >= expiry]
        changed = False
        for n in stale:
            self.last_heard.pop(n, None)
            if n in self.routes_by_neighbor:
                del self.routes_by_neighbor[n]
                changed = True
        if changed:
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
        high = max(self.profile.cryst_jitter_min_ms + 1, self.profile.k_limit_ms)
        delay = self.sim.rng.uniform(self.profile.cryst_jitter_min_ms, high)
        self.sim.log("cryst_schedule", node=self.id, reason=reason, delay=round(delay, 2))
        self.sim.schedule(delay, self._emit_cryst, token)

    def _emit_cryst(self, token: int) -> None:
        if token != self.cryst_token or not self.up or self.crashed:
            return
        self.cryst_scheduled = False
        ads = tuple(
            AdvertisedRoute(dest=d, via=r.next_hop, distance=r.distance)
            for d, r in sorted(self.routes.items())
        )
        p = Packet("CRYST", self.next_packet_id(), advertisements=ads,
                   wire_dtpk_size=DTPK_CRYST_HEADER + len(ads) * NEIGHBOR_RECORD_SIZE)
        self.enqueue(TxRequest(p, None, lcmm_ack=False))

    def next_packet_id(self) -> int:
        pid = self.packet_counter & 0xFFFF
        self.packet_counter = (self.packet_counter + 1) & 0xFFFF
        return pid

    def enqueue(self, req: TxRequest) -> None:
        if req.priority or req.packet.kind in ("ACK", "NACK"):
            self.txq.appendleft(req)
        else:
            self.txq.append(req)
        self.sim.metrics.max_queue = max(self.sim.metrics.max_queue, len(self.txq))
        self.sim.schedule(0, self.pump)

    def pump(self) -> None:
        if not self.up or self.crashed or self.lcmm_busy or not self.txq:
            return
        req = self.txq[0]
        if self.waiting_e2e is not None and self.profile.global_e2e_gate:
            if not (self.profile.ack_bypasses_e2e_gate and req.packet.kind in ("ACK", "NACK", "CRYST")):
                return
        self.txq.popleft()
        if req.lcmm_ack:
            self.lcmm_busy = True

        def completed(success: bool) -> None:
            if req.lcmm_ack:
                self.lcmm_busy = False
            if not success:
                self.sim.log("hop_fail", node=self.id, target=req.next_hop, kind=req.packet.kind)
            if req.dtpk_ack and success:
                self.waiting_e2e = (req.packet.packet_id, req.packet.final_target or 0,
                                    self.sim.now + req.timeout_ms)
                self.wait_token += 1
                tok = self.wait_token
                self.sim.schedule(req.timeout_ms, self._e2e_timeout, tok, req.packet.packet_id)
            self.sim.schedule(0, self.pump)

        self.sim.transmit(self.id, req.next_hop, req.packet, req.lcmm_ack, completed)
        if not req.lcmm_ack and self.profile.noack_releases_lcmm_immediately:
            self.sim.schedule(0, self.pump)

    def link_retry_timeout_ms(self, packet: Packet) -> float:
        # C++ DTPK gives LCMM roughly timeout/(3+0.1), then LCMM adds airtime.
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
        self.last_heard[previous_hop] = self.sim.now
        if not self.session_active:
            self.session_active = True
            self.session_seen.clear()
        self.session_seen.add(previous_hop)
        self.session_deadline = self.sim.now + self.profile.k_limit_ms
        self.session_token += 1
        tok = self.session_token
        self.sim.schedule(self.profile.k_limit_ms, self._session_timeout, tok)

        advertised_me = any(ad.dest == self.id for ad in packet.advertisements)
        incoming: Dict[int, AdvertisedRoute] = {
            previous_hop: AdvertisedRoute(previous_hop, self.id, 1)
        }
        for ad in packet.advertisements:
            # This mirrors: if (packet[i].id == myId || packet[i].from == myId) continue;
            if ad.dest == self.id or ad.via == self.id:
                continue
            distance = ad.distance + 1
            if self.profile.distance_uint8_wrap:
                distance &= 0xFF
            elif self.profile.max_metric is not None:
                distance = min(distance, self.profile.max_metric)
            incoming[ad.dest] = AdvertisedRoute(ad.dest, previous_hop, distance)

        old = self.routes_by_neighbor.get(previous_hop)
        contribution_changed = old != incoming
        self.routes_by_neighbor[previous_hop] = incoming
        best_changed = self.rebuild_routes()
        should_send = (not advertised_me) or (self.profile.propagate_on_route_change and best_changed)
        self.sim.log("cryst_rx", node=self.id, sender=previous_hop,
                     advertised_me=advertised_me, contribution_changed=contribution_changed,
                     best_changed=best_changed, should_send=should_send)
        if should_send:
            self.schedule_cryst("cryst_update")

    def _session_timeout(self, token: int) -> None:
        if token != self.session_token or not self.session_active or not self.up or self.crashed:
            return
        if self.sim.now + 1e-9 < self.session_deadline:
            return
        removed = [n for n in self.routes_by_neighbor if n not in self.session_seen] if self.profile.session_gc_enabled else []
        for n in removed:
            del self.routes_by_neighbor[n]
        self.session_active = False
        self.session_seen.clear()
        if removed:
            changed = self.rebuild_routes()
            self.sim.log("cryst_gc", node=self.id, removed=removed, best_changed=changed)
            if changed:
                self.schedule_cryst("cryst_gc")

    def rebuild_routes(self) -> bool:
        old = self.routes
        candidates: Dict[int, List[Route]] = defaultdict(list)
        for router, contribution in self.routes_by_neighbor.items():
            for dest, ad in contribution.items():
                if dest == self.id:
                    continue
                candidates[dest].append(Route(router, ad.via, ad.distance))
        new: Dict[int, Route] = {}
        for dest, routes in candidates.items():
            # Deterministic tie break; C++ unordered iteration makes equal-cost tie selection unstable.
            new[dest] = min(routes, key=lambda r: (r.distance, r.next_hop))
        changed = new != old
        self.routes = new
        if changed:
            self.sim.metrics.route_changes += 1
        self.sim.metrics.max_route_entries = max(self.sim.metrics.max_route_entries, len(new))
        self.sim.metrics.max_contributions = max(
            self.sim.metrics.max_contributions,
            sum(len(v) for v in self.routes_by_neighbor.values()),
        )
        return changed

    def send_data(self, target: int, payload_size: int = 16, e2e_ack: bool = True,
                  timeout_ms: Optional[int] = None) -> Optional[int]:
        if not self.up or self.crashed:
            return None
        route = self.routes.get(target)
        if route is None:
            self.sim.metrics.route_misses += 1
            self.sim.log("route_miss", node=self.id, target=target)
            return None
        pid = self.next_packet_id()
        p = Packet("DATA", pid, original_sender=self.id, final_target=target,
                   payload_size=payload_size,
                   wire_dtpk_size=DTPK_GENERIC_HEADER + payload_size)
        self.enqueue(TxRequest(p, route.next_hop, lcmm_ack=True, dtpk_ack=e2e_ack,
                               timeout_ms=timeout_ms or self.profile.e2e_timeout_ms))
        return pid

    def receive_data_local(self, packet: Packet, previous_hop: int) -> None:
        key = (packet.original_sender or -1, packet.packet_id)
        duplicate = key in self.delivered_ids
        if duplicate:
            self.sim.metrics.duplicate_app += 1
        if not duplicate or not self.profile.duplicate_suppression:
            self.sim.metrics.delivered_app += 1
        self.delivered_ids.add(key)
        self.sim.log("app_rx", node=self.id, sender=packet.original_sender,
                     packet_id=packet.packet_id, duplicate=duplicate,
                     wire_dtpk_size=packet.wire_dtpk_size)

        target = packet.original_sender
        if target is None:
            return
        route = self.routes.get(target)
        if route is None and self.profile.ack_null_route_crash:
            # C++ uses `from` as whereToSend, but then dereferences routing in a debug print.
            self.crash("sendAckPacket null routing dereference")
            return
        ack = Packet("ACK", packet.packet_id, original_sender=self.id,
                     final_target=target, wire_dtpk_size=DTPK_GENERIC_HEADER)
        self.enqueue(TxRequest(ack, previous_hop, lcmm_ack=True, priority=True))

    def receive_ack_local(self, packet: Packet, positive: bool) -> None:
        if self.waiting_e2e is None or self.waiting_e2e[0] != packet.packet_id:
            self.sim.log("unexpected_e2e", node=self.id, packet_id=packet.packet_id,
                         kind=packet.kind)
            return
        self.waiting_e2e = None
        self.wait_token += 1
        if positive or self.profile.nack_is_success:
            self.sim.metrics.e2e_success += 1
            self.sim.log("e2e_success", node=self.id, packet_id=packet.packet_id,
                         via=packet.kind)
        else:
            self.sim.metrics.e2e_failure += 1
            self.sim.log("e2e_nack", node=self.id, packet_id=packet.packet_id)
        self.sim.schedule(0, self.pump)

    def forward(self, packet: Packet, previous_hop: int) -> None:
        if packet.final_target is None:
            return
        route = self.routes.get(packet.final_target)
        if route is None:
            self.sim.metrics.route_misses += 1
            if packet.kind == "DATA" and packet.original_sender is not None:
                nack = Packet("NACK", packet.packet_id, original_sender=self.id,
                              final_target=packet.original_sender,
                              wire_dtpk_size=DTPK_GENERIC_HEADER)
                self.sim.metrics.nacks += 1
                self.enqueue(TxRequest(nack, previous_hop, lcmm_ack=True, priority=True))
            return
        fwd = packet.clone()
        if self.profile.forwarding_size_bug:
            fwd.wire_dtpk_size = (packet.wire_dtpk_size or DTPK_GENERIC_HEADER + packet.payload_size) + LCMM_RX_HEADER
        reliable = self.profile.relay_lcmm_ack
        self.enqueue(TxRequest(fwd, route.next_hop, lcmm_ack=reliable,
                               priority=packet.kind in ("ACK", "NACK")))
