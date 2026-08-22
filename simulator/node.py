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
        self.self_seqno = 1
        self.route_version = 0
        self.neighbor_cryst_version: Dict[int, int] = {}
        self.feasible_distance: Dict[int, Tuple[int, int]] = {}
        self.seq_requests_seen: Set[Tuple[int, int, int, int]] = set()
        self.seq_request_last: Dict[int, float] = {}
        self.seq_request_max_seen: Dict[int, int] = {}
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
        self.waiting_e2e: Dict[int, Tuple[int, float, int]] = {}
        self.e2e_start_reachable: Dict[int, bool] = {}
        self.wait_token = 0
        self.delivered_ids: Set[Tuple[int, int]] = set()
        self.forwarded_ids: Set[Tuple[str, int, int, int]] = set()
        self.crashed_reason: Optional[str] = None

    def reset_runtime(self) -> None:
        self.routes_by_neighbor.clear()
        self.routes.clear()
        self.session_active = False
        self.session_seen.clear()
        self.session_deadline = 0
        self.cryst_scheduled = False
        self.last_heard.clear()
        self.neighbor_cryst_version.clear()
        self.seq_requests_seen.clear()
        self.seq_request_last.clear()
        self.seq_request_max_seen.clear()
        self.txq.clear()
        self.lcmm_busy = False
        self.radio_busy_until = 0
        if self.waiting_e2e:
            self.sim.metrics.e2e_aborted_by_reboot += len(self.waiting_e2e)
        self.waiting_e2e.clear()
        self.e2e_start_reachable.clear()
        self.delivered_ids.clear()
        self.forwarded_ids.clear()
        self.crashed = False
        self.crashed_reason = None

    def start(self) -> None:
        if not self.sim.node_up.get(self.id, False):
            return
        self.up = True
        self.crashed = False
        self.generation += 1
        if self.profile.source_seqno and self.generation > 1:
            self.self_seqno += 1
        self.sim.log("node_up", node=self.id, self_seqno=self.self_seqno)
        self.schedule_cryst("startup")
        if self.profile.periodic_cryst_ms:
            gen = self.generation
            self.sim.schedule(self.profile.periodic_cryst_ms, self._periodic_cryst, gen)
        if self.profile.hello_period_ms:
            gen = self.generation
            self.sim.schedule(
                self.sim.rng.uniform(100, self.profile.hello_period_ms),
                self._periodic_hello,
                gen,
            )
        if self.profile.neighbor_expiry_ms:
            gen = self.generation
            self.sim.schedule(self.profile.neighbor_expiry_ms / 3, self._expiry_tick, gen)

    def crash(self, reason: str) -> None:
        if self.crashed:
            return
        self.crashed = True
        self.crashed_reason = reason
        self.sim.node_up[self.id] = False
        self.sim.node_epoch[self.id] = self.sim.node_epoch.get(self.id, 0) + 1
        self.sim.metrics.crashes += 1
        self.sim.log("crash", node=self.id, reason=reason)

    def _periodic_hello(self, gen: int) -> None:
        if gen != self.generation or not self.up or self.crashed:
            return
        p = Packet(
            "HELLO",
            self.next_packet_id(),
            wire_dtpk_size=5,
            sender_seqno=self.self_seqno,
            route_version=self.route_version,
        )
        self.sim.metrics.hello_tx += 1
        self.enqueue(TxRequest(p, None, lcmm_ack=False))
        period = float(self.profile.hello_period_ms or 1)
        jitter = max(0.0, min(0.95, self.profile.hello_jitter_fraction))
        if jitter:
            period *= self.sim.rng.uniform(1.0 - jitter, 1.0 + jitter)
        self.sim.schedule(period, self._periodic_hello, gen)

    def _periodic_cryst(self, gen: int) -> None:
        if gen != self.generation or not self.up or self.crashed:
            return
        if self.profile.source_seqno:
            self.self_seqno = (self.self_seqno + 1) & 0xFFFF
            if self.self_seqno == 0:
                self.self_seqno = 1
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
            self.neighbor_cryst_version.pop(n, None)
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
        if self.profile.feasibility_condition:
            for d, r in self.routes.items():
                old = self.feasible_distance.get(d)
                if old is None or r.seqno > old[0] or (r.seqno == old[0] and r.distance < old[1]):
                    self.feasible_distance[d] = (r.seqno, r.distance)
        ads = tuple(
            AdvertisedRoute(dest=d, via=r.next_hop, distance=r.distance, seqno=r.seqno)
            for d, r in sorted(self.routes.items())
        )
        p = Packet(
            "CRYST",
            self.next_packet_id(),
            advertisements=ads,
            wire_dtpk_size=DTPK_CRYST_HEADER + len(ads) * NEIGHBOR_RECORD_SIZE,
            sender_seqno=self.self_seqno,
            route_version=self.route_version,
        )
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
        if self.waiting_e2e and self.profile.global_e2e_gate:
            if not (
                self.profile.ack_bypasses_e2e_gate
                and req.packet.kind in ("ACK", "NACK", "CRYST")
            ):
                return
        self.txq.popleft()
        if req.lcmm_ack:
            self.lcmm_busy = True
        if req.dtpk_ack:
            self.wait_token += 1
            tok = self.wait_token
            self.waiting_e2e[req.packet.packet_id] = (
                req.packet.final_target or 0,
                self.sim.now + req.timeout_ms,
                tok,
            )
            if self.profile.e2e_timeout_uint32_underflow:
                elapsed, wrapped = self._sample_uint32_polling_countdown(req.timeout_ms)
                if wrapped:
                    self.sim.schedule(
                        elapsed,
                        self._e2e_timeout_underflow,
                        tok,
                        req.packet.packet_id,
                        wrapped,
                    )
                else:
                    self.sim.schedule(elapsed, self._e2e_timeout, tok, req.packet.packet_id)
            else:
                self.sim.schedule(req.timeout_ms, self._e2e_timeout, tok, req.packet.packet_id)

        generation = self.generation

        def completed(success: bool) -> None:
            if generation != self.generation:
                return
            if req.lcmm_ack:
                self.lcmm_busy = False
            if not success:
                self.sim.log("hop_fail", node=self.id, target=req.next_hop, kind=req.packet.kind)
            self.sim.schedule(0, self.pump)

        self.sim.transmit(self.id, req.next_hop, req.packet, req.lcmm_ack, completed)
        if not req.lcmm_ack and self.profile.noack_releases_lcmm_immediately:
            self.sim.schedule(0, self.pump)

    def link_retry_timeout_ms(self, packet: Packet) -> float:
        return 1_650.0 + self.sim.airtime_ms(self.sim._frame_bytes(packet))

    def _e2e_timeout_underflow(self, token: int, packet_id: int, wrapped: int) -> None:
        record = self.waiting_e2e.get(packet_id)
        if record is None or record[2] != token:
            return
        self.waiting_e2e[packet_id] = (record[0], self.sim.now + wrapped, token)
        self.sim.metrics.e2e_timeout_underflows += 1
        self.sim.log(
            "e2e_timeout_underflow",
            node=self.id,
            packet_id=packet_id,
            wrapped_remaining_ms=wrapped,
        )

    def _e2e_timeout(self, token: int, packet_id: int) -> None:
        record = self.waiting_e2e.get(packet_id)
        if record is None or record[2] != token:
            return
        del self.waiting_e2e[packet_id]
        self.sim.metrics.e2e_failure += 1
        started_reachable = self.e2e_start_reachable.pop(packet_id, None)
        if started_reachable is True:
            self.sim.metrics.e2e_failure_started_reachable += 1
        elif started_reachable is False:
            self.sim.metrics.e2e_failure_started_unreachable += 1
        self.sim.log(
            "e2e_timeout",
            node=self.id,
            packet_id=packet_id,
            started_reachable=started_reachable,
        )
        self.sim.schedule(0, self.pump)

    def receive(self, packet: Packet, previous_hop: int) -> None:
        if not self.sim.node_up.get(self.id, False):
            return
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
                self.schedule_cryst("cryst_version_request")
            return

        self.forward(packet, previous_hop)

    def receive_hello(self, packet: Packet, previous_hop: int) -> None:
        self.sim.metrics.hello_rx += 1
        self.last_heard[previous_hop] = self.sim.now
        contribution = self.routes_by_neighbor.get(previous_hop)
        changed = False
        if contribution is None:
            contribution = {}
            self.routes_by_neighbor[previous_hop] = contribution
        direct = AdvertisedRoute(previous_hop, self.id, 1, packet.sender_seqno, 0)
        if packet.sender_seqno >= self.seq_request_max_seen.get(previous_hop, 1 << 60):
            self.seq_request_last.pop(previous_hop, None)
            self.seq_request_max_seen.pop(previous_hop, None)
        if contribution.get(previous_hop) != direct:
            contribution[previous_hop] = direct
            changed = self.rebuild_routes()
        self.sim.log(
            "hello_rx",
            node=self.id,
            sender=previous_hop,
            best_changed=changed,
            advertised_version=packet.route_version,
            known_version=self.neighbor_cryst_version.get(previous_hop),
        )
        if changed:
            self.schedule_cryst("hello_neighbor_change")
        if (
            self.profile.state_digest_requests
            and self.neighbor_cryst_version.get(previous_hop) != packet.route_version
        ):
            req = Packet(
                "CRYST_REQ",
                self.next_packet_id(),
                original_sender=self.id,
                final_target=previous_hop,
                wire_dtpk_size=5,
            )
            self.sim.metrics.cryst_req_tx += 1
            self.enqueue(TxRequest(req, previous_hop, lcmm_ack=True, priority=True))

    def request_seqno(self, dest: int, requested_seqno: int) -> None:
        if not self.profile.seqno_requests or dest == self.id:
            return
        last = self.seq_request_last.get(dest, -1e30)
        max_seen = self.seq_request_max_seen.get(dest, -1)
        if (
            requested_seqno <= max_seen
            and self.sim.now - last < self.profile.seqno_request_cooldown_ms
        ):
            return
        self.seq_request_last[dest] = self.sim.now
        self.seq_request_max_seen[dest] = max(max_seen, requested_seqno)
        p = Packet(
            "SEQ_REQ",
            self.next_packet_id(),
            original_sender=self.id,
            final_target=dest,
            wire_dtpk_size=9,
            requested_seqno=requested_seqno,
            hop_limit=self.profile.seqno_request_hop_limit,
        )
        self.seq_requests_seen.add((self.id, p.packet_id, dest, requested_seqno))
        self.sim.metrics.seq_req_tx += 1
        self.sim.log("seq_req_tx", node=self.id, dest=dest, requested=requested_seqno)
        self.enqueue(TxRequest(p, None, lcmm_ack=False, priority=True))

    def receive_seq_req(self, packet: Packet, previous_hop: int) -> None:
        self.sim.metrics.seq_req_rx += 1
        origin = packet.original_sender if packet.original_sender is not None else -1
        dest = packet.final_target if packet.final_target is not None else -1
        key = (origin, packet.packet_id, dest, packet.requested_seqno)
        if key in self.seq_requests_seen:
            self.sim.metrics.seq_req_duplicates += 1
            return
        self.seq_requests_seen.add(key)
        self.seq_request_last[dest] = self.sim.now
        self.seq_request_max_seen[dest] = max(
            self.seq_request_max_seen.get(dest, -1), packet.requested_seqno
        )
        if dest == self.id:
            self.self_seqno = max(self.self_seqno, packet.requested_seqno)
            self.sim.metrics.seq_req_satisfied += 1
            self.sim.log(
                "seq_req_satisfied", node=self.id, requester=origin, seqno=self.self_seqno
            )
            self.schedule_cryst("seqno_request")
            return
        if packet.hop_limit <= 1:
            return
        fwd = packet.clone()
        fwd.hop_limit -= 1
        self.sim.metrics.seq_req_tx += 1
        self.enqueue(TxRequest(fwd, None, lcmm_ack=False, priority=True))

    def receive_cryst(self, packet: Packet, previous_hop: int) -> None:
        self.sim.metrics.cryst_rx += 1
        if self.profile.memory_leak_model:
            self.sim.metrics.leaked_heap_bytes += self.sim._frame_bytes(packet)
        self.last_heard[previous_hop] = self.sim.now
        self.neighbor_cryst_version[previous_hop] = packet.route_version
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
            previous_hop: AdvertisedRoute(
                previous_hop, self.id, 1, packet.sender_seqno, 0
            )
        }
        for ad in packet.advertisements:
            if ad.dest == self.id or ad.via == self.id:
                continue
            distance = ad.distance + 1
            if self.profile.distance_uint8_wrap:
                distance &= 0xFF
            elif self.profile.max_metric is not None:
                distance = min(distance, self.profile.max_metric)
            incoming[ad.dest] = AdvertisedRoute(
                ad.dest, previous_hop, distance, ad.seqno, ad.distance
            )

        old = self.routes_by_neighbor.get(previous_hop)
        contribution_changed = old != incoming
        self.routes_by_neighbor[previous_hop] = incoming
        best_changed = self.rebuild_routes()
        should_send = (not advertised_me) or (
            self.profile.propagate_on_route_change and best_changed
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
            [n for n in self.routes_by_neighbor if n not in self.session_seen]
            if self.profile.session_gc_enabled
            else []
        )
        for n in removed:
            del self.routes_by_neighbor[n]
        self.session_active = False
        self.session_seen.clear()
        if removed:
            changed = self.rebuild_routes()
            self.sim.log("cryst_gc", node=self.id, removed=removed, best_changed=changed)
            if changed:
                self.schedule_cryst("cryst_gc")

    def _feasible(self, dest: int, ad: AdvertisedRoute, router: int) -> bool:
        if not self.profile.feasibility_condition or dest == router:
            return True
        fd = self.feasible_distance.get(dest)
        if fd is None:
            return True
        metric = (
            ad.neighbor_metric
            if ad.neighbor_metric is not None
            else max(0, ad.distance - 1)
        )
        seq, fd_metric = fd
        ok = ad.seqno > seq or (ad.seqno == seq and metric < fd_metric)
        if not ok:
            self.sim.metrics.infeasible_updates += 1
        return ok

    def rebuild_routes(self) -> bool:
        old = self.routes
        candidates: Dict[int, List[Route]] = defaultdict(list)
        blocked: Dict[int, List[AdvertisedRoute]] = defaultdict(list)
        for router, contribution in self.routes_by_neighbor.items():
            for dest, ad in contribution.items():
                if dest == self.id:
                    continue
                if not self._feasible(dest, ad, router):
                    blocked[dest].append(ad)
                    continue
                metric = (
                    ad.neighbor_metric
                    if ad.neighbor_metric is not None
                    else max(0, ad.distance - 1)
                )
                candidates[dest].append(
                    Route(router, ad.via, ad.distance, ad.seqno, metric)
                )
        new: Dict[int, Route] = {}
        for dest, routes in candidates.items():
            new[dest] = min(routes, key=lambda r: (r.distance, r.next_hop))
        changed = new != old
        self.routes = new
        if changed:
            self.route_version = (self.route_version + 1) & 0xFFFF
            self.sim.metrics.route_changes += 1

        if self.profile.seqno_requests:
            for dest, ads in blocked.items():
                if dest in new:
                    continue
                fd = self.feasible_distance.get(dest)
                if fd is None:
                    continue
                newest_seen = max((ad.seqno for ad in ads), default=fd[0])
                self.request_seqno(dest, max(fd[0], newest_seen) + 1)

        self.sim.metrics.max_route_entries = max(
            self.sim.metrics.max_route_entries, len(new)
        )
        self.sim.metrics.max_contributions = max(
            self.sim.metrics.max_contributions,
            sum(len(v) for v in self.routes_by_neighbor.values()),
        )
        return changed

    def _sample_uint32_polling_countdown(self, timeout_ms: int) -> Tuple[float, int]:
        remaining = int(timeout_ms) & 0xFFFFFFFF
        elapsed = 0
        while remaining > 0:
            dt = self.sim.rng.randint(1, 8)
            elapsed += dt
            if dt == remaining:
                return float(elapsed), 0
            if dt > remaining:
                return float(elapsed), (remaining - dt) & 0xFFFFFFFF
            remaining -= dt
        return float(elapsed), 0

    def _physical_path_exists(self, target: int) -> bool:
        if target == self.id:
            return True
        seen = {self.id}
        q = deque([self.id])
        while q:
            here = q.popleft()
            for other in self.sim.neighbors(here):
                if other == target:
                    return True
                if other not in seen:
                    seen.add(other)
                    q.append(other)
        return False

    def send_data(
        self,
        target: int,
        payload_size: int = 16,
        e2e_ack: bool = True,
        timeout_ms: Optional[int] = None,
        callback_present: bool = False,
    ) -> Optional[int]:
        self.sim.metrics.send_calls += 1
        if not self.up or self.crashed or not self.sim.node_up.get(self.id, False):
            self.sim.metrics.send_while_node_down += 1
            return None
        route = self.routes.get(target)
        if route is None:
            self.sim.metrics.route_misses += 1
            self.sim.log("route_miss", node=self.id, target=target)
            if self.profile.send_no_route_null_callback_crash and not callback_present:
                self.crash("sendPacket null callback on missing route")
            return None
        self.sim.metrics.send_route_found += 1
        start_reachable = self._physical_path_exists(target)
        if start_reachable:
            self.sim.metrics.send_physically_reachable_at_start += 1
        else:
            self.sim.metrics.send_physically_unreachable_at_start += 1
        pid = self.next_packet_id()
        if e2e_ack:
            self.e2e_start_reachable[pid] = start_reachable
        p = Packet(
            "DATA",
            pid,
            original_sender=self.id,
            final_target=target,
            payload_size=payload_size,
            wire_dtpk_size=DTPK_GENERIC_HEADER + payload_size,
        )
        self.enqueue(
            TxRequest(
                p,
                route.next_hop,
                lcmm_ack=True,
                dtpk_ack=e2e_ack,
                timeout_ms=timeout_ms or self.profile.e2e_timeout_ms,
            )
        )
        return pid

    def receive_data_local(self, packet: Packet, previous_hop: int) -> None:
        if self.profile.memory_leak_model:
            self.sim.metrics.leaked_heap_bytes += self.sim._frame_bytes(packet)
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
        self.enqueue(TxRequest(ack, previous_hop, lcmm_ack=True, priority=True))

    def receive_ack_local(self, packet: Packet, positive: bool) -> None:
        if self.profile.memory_leak_model:
            self.sim.metrics.leaked_heap_bytes += self.sim._frame_bytes(packet)
        if packet.packet_id not in self.waiting_e2e:
            self.sim.log(
                "unexpected_e2e",
                node=self.id,
                packet_id=packet.packet_id,
                kind=packet.kind,
            )
            return
        del self.waiting_e2e[packet.packet_id]
        started_reachable = self.e2e_start_reachable.pop(packet.packet_id, None)
        if positive or self.profile.nack_is_success:
            self.sim.metrics.e2e_success += 1
            if started_reachable is True:
                self.sim.metrics.e2e_success_started_reachable += 1
            elif started_reachable is False:
                self.sim.metrics.e2e_success_started_unreachable += 1
            self.sim.log(
                "e2e_success",
                node=self.id,
                packet_id=packet.packet_id,
                via=packet.kind,
                started_reachable=started_reachable,
            )
        else:
            self.sim.metrics.e2e_failure += 1
            if started_reachable is True:
                self.sim.metrics.e2e_failure_started_reachable += 1
            elif started_reachable is False:
                self.sim.metrics.e2e_failure_started_unreachable += 1
            self.sim.log(
                "e2e_nack",
                node=self.id,
                packet_id=packet.packet_id,
                started_reachable=started_reachable,
            )
        self.sim.schedule(0, self.pump)

    def forward(self, packet: Packet, previous_hop: int) -> None:
        if packet.final_target is None:
            return
        fwd_key = (
            packet.kind,
            packet.original_sender if packet.original_sender is not None else -1,
            packet.packet_id,
            packet.final_target,
        )
        if self.profile.duplicate_suppression and fwd_key in self.forwarded_ids:
            self.sim.metrics.duplicate_forward_drops += 1
            self.sim.log(
                "duplicate_forward_drop",
                node=self.id,
                kind=packet.kind,
                packet_id=packet.packet_id,
                previous_hop=previous_hop,
            )
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
                    TxRequest(nack, previous_hop, lcmm_ack=True, priority=True)
                )
            return
        fwd = packet.clone()
        if self.profile.forwarding_size_bug:
            fwd.wire_dtpk_size = (
                packet.wire_dtpk_size
                or DTPK_GENERIC_HEADER + packet.payload_size
            ) + LCMM_RX_HEADER
        reliable = self.profile.relay_lcmm_ack
        if self.profile.duplicate_suppression:
            self.forwarded_ids.add(fwd_key)
        self.enqueue(
            TxRequest(
                fwd,
                route.next_hop,
                lcmm_ack=reliable,
                priority=packet.kind in ("ACK", "NACK"),
            )
        )
