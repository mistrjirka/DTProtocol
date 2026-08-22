from __future__ import annotations
import heapq
import math
import random
from collections import deque
from typing import Callable, Dict, List, Optional, Tuple
from model import *
from node import Node
class Simulator:
    def __init__(
        self,
        seed: int = 1,
        profile: Profile | None = None,
        sf: int = 9,
        bandwidth_hz: int = 125_000,
        coding_rate_denominator: int = 7,
    ):
        self.rng = random.Random(seed)
        self.seed = seed
        self.profile = profile or Profile.current()
        self.sf = sf
        self.bandwidth_hz = bandwidth_hz
        self.cr_den = coding_rate_denominator
        self.now = 0.0
        self._seq = 0
        self._events: List[Tuple[float, int, Callable, tuple]] = []
        self.nodes: Dict[int, Node] = {}
        self.links: Dict[frozenset[int], Link] = {}
        self.metrics = Metrics()
        self.trace: List[dict] = []

    def log(self, event: str, **fields) -> None:
        self.trace.append({"t_ms": round(self.now, 3), "event": event, **fields})

    def schedule(self, delay_ms: float, fn: Callable, *args) -> None:
        self._seq += 1
        heapq.heappush(self._events, (self.now + max(0.0, delay_ms), self._seq, fn, args))

    def schedule_at(self, when_ms: float, fn: Callable, *args) -> None:
        self._seq += 1
        heapq.heappush(self._events, (float(when_ms), self._seq, fn, args))

    def run(self, until_ms: float) -> None:
        while self._events and self._events[0][0] <= until_ms:
            t, _, fn, args = heapq.heappop(self._events)
            self.now = t
            fn(*args)
        self.now = float(until_ms)

    def add_node(self, node_id: int, start: bool = True) -> "Node":
        node = Node(self, node_id)
        self.nodes[node_id] = node
        if start:
            self.schedule(0, node.start)
        return node

    def add_link(
        self,
        a: int,
        b: int,
        *,
        loss: float = 0.0,
        ack_loss: Optional[float] = None,
        latency_ms: float = 25.0,
        jitter_ms: float = 5.0,
        up: bool = True,
    ) -> None:
        self.links[frozenset((a, b))] = Link(a, b, loss, ack_loss, latency_ms, jitter_ms, up)

    def set_link(self, a: int, b: int, up: bool) -> None:
        link = self.links[frozenset((a, b))]
        link.up = up
        self.log("link", a=a, b=b, up=up)

    def reboot_node(self, node_id: int, downtime_ms: float = 500.0) -> None:
        node = self.nodes[node_id]
        node.up = False
        node.generation += 1
        node.reset_runtime()
        self.log("node_down", node=node_id)
        self.schedule(downtime_ms, node.start)

    def neighbors(self, node_id: int, only_up: bool = True) -> List[int]:
        out = []
        for link in self.links.values():
            if node_id not in (link.a, link.b):
                continue
            if only_up and not link.up:
                continue
            other = link.other(node_id)
            if only_up and (not self.nodes[node_id].up or not self.nodes[other].up):
                continue
            out.append(other)
        return out

    def get_link(self, a: int, b: int) -> Optional[Link]:
        return self.links.get(frozenset((a, b)))

    def _jittered_latency(self, link: Link) -> float:
        if link.jitter_ms <= 0:
            return link.latency_ms
        return max(0.0, self.rng.gauss(link.latency_ms, link.jitter_ms))

    def airtime_ms(self, payload_bytes: int) -> float:
        # LoRa explicit-header airtime approximation, CRC enabled, preamble 8.
        pl = max(0, payload_bytes)
        sf = self.sf
        bw = self.bandwidth_hz
        de = 1 if sf >= 11 and bw == 125_000 else 0
        ih = 0
        crc = 1
        cr = max(1, self.cr_den - 4)  # 4/5 -> 1, 4/7 -> 3
        tsym = (2**sf) / bw
        tpreamble = (8 + 4.25) * tsym
        denom = 4 * (sf - 2 * de)
        num = 8 * pl - 4 * sf + 28 + 16 * crc - 20 * ih
        payload_sym = 8 + max(math.ceil(num / denom) * (cr + 4), 0)
        return (tpreamble + payload_sym * tsym) * 1000.0

    def _frame_bytes(self, packet: Packet) -> int:
        if packet.kind == "CRYST":
            dtpk = DTPK_CRYST_HEADER + len(packet.advertisements) * NEIGHBOR_RECORD_SIZE
        elif packet.wire_dtpk_size:
            dtpk = packet.wire_dtpk_size
        else:
            dtpk = DTPK_GENERIC_HEADER + packet.payload_size
        return MAC_OVERHEAD + LCMM_OVERHEAD + dtpk

    def transmit(
        self,
        sender_id: int,
        target: Optional[int],
        packet: Packet,
        reliable: bool,
        on_complete: Callable[[bool], None],
        attempt: int = 1,
    ) -> None:
        sender = self.nodes[sender_id]
        if not sender.up or sender.crashed:
            self.schedule(0, on_complete, False)
            return

        frame_bytes = self._frame_bytes(packet)
        if frame_bytes > MAX_PACKET_SIZE:
            self.metrics.oversize_drops += 1
            self.log("oversize_drop", node=sender_id, kind=packet.kind, bytes=frame_bytes)
            self.schedule(0, on_complete, False)
            return

        # MAC::sendData currently returns success even when state==SENDING.
        if self.now < sender.radio_busy_until:
            if self.profile.mac_busy_silent_drop:
                self.metrics.silent_busy_drops += 1
                self.log("busy_silent_drop", node=sender_id, kind=packet.kind, target=target)
                if reliable:
                    self.schedule(sender.link_retry_timeout_ms(packet), self._retry_or_finish,
                                  sender_id, target, packet, reliable, on_complete, attempt)
                else:
                    self.schedule(0, on_complete, True)
                return
            delay = sender.radio_busy_until - self.now
            self.schedule(delay, self.transmit, sender_id, target, packet, reliable, on_complete, attempt)
            return

        airtime = self.airtime_ms(frame_bytes)
        sender.radio_busy_until = self.now + airtime
        self.metrics.radio_data_frames += 1
        self.metrics.bytes_on_air += frame_bytes
        if target is None:
            self.metrics.broadcasts += 1
            if packet.kind == "CRYST":
                self.metrics.cryst_tx += 1
            self.log("tx_broadcast", node=sender_id, kind=packet.kind, bytes=frame_bytes)
            for nb in self.neighbors(sender_id):
                link = self.get_link(sender_id, nb)
                assert link is not None
                if self.rng.random() < link.loss:
                    self.metrics.link_loss_drops += 1
                    continue
                delay = airtime + self._jittered_latency(link)
                self.schedule(delay, self._deliver, nb, sender_id, packet.clone(), False, None)
            self.schedule(0 if self.profile.noack_releases_lcmm_immediately else airtime, on_complete, True)
            return

        self.metrics.unicast_attempts += 1
        link = self.get_link(sender_id, target)
        if link is None or not link.up or not self.nodes[target].up:
            self.log("tx_no_link", node=sender_id, target=target, kind=packet.kind, attempt=attempt)
            if reliable:
                self.schedule(sender.link_retry_timeout_ms(packet), self._retry_or_finish,
                              sender_id, target, packet, reliable, on_complete, attempt)
            else:
                self.schedule(0 if self.profile.noack_releases_lcmm_immediately else airtime, on_complete, True)
            return

        data_lost = self.rng.random() < link.loss
        self.log("tx_unicast", node=sender_id, target=target, kind=packet.kind,
                 reliable=reliable, attempt=attempt, lost=data_lost)
        if data_lost:
            self.metrics.link_loss_drops += 1
            if reliable:
                self.schedule(sender.link_retry_timeout_ms(packet), self._retry_or_finish,
                              sender_id, target, packet, reliable, on_complete, attempt)
            else:
                self.schedule(0 if self.profile.noack_releases_lcmm_immediately else airtime, on_complete, True)
            return

        delay = airtime + self._jittered_latency(link)
        if reliable:
            self.schedule(delay, self._deliver, target, sender_id, packet.clone(), True,
                          (sender_id, target, packet, on_complete, attempt))
        else:
            self.schedule(delay, self._deliver, target, sender_id, packet.clone(), False, None)
            self.schedule(0 if self.profile.noack_releases_lcmm_immediately else airtime, on_complete, True)

    def _deliver(self, receiver_id: int, previous_hop: int, packet: Packet,
                 reliable: bool, ack_context) -> None:
        receiver = self.nodes[receiver_id]
        if not receiver.up or receiver.crashed:
            return
        if reliable:
            sender_id, target, original_packet, on_complete, attempt = ack_context
            link = self.get_link(sender_id, target)
            if link is None or not link.up:
                return
            # LCMM receiver sends link ACK first; upper layer is delivered after TX completion.
            ack_bytes = MAC_OVERHEAD + 1 + 2  # approximate MAC + LCMM ACK type/id
            ack_airtime = self.airtime_ms(ack_bytes)
            self.metrics.radio_link_ack_frames += 1
            self.metrics.bytes_on_air += ack_bytes
            ack_loss = link.ack_loss if link.ack_loss is not None else link.loss
            lost = self.rng.random() < ack_loss
            upper_delay = ack_airtime
            self.schedule(upper_delay, receiver.receive, packet, previous_hop)
            if lost:
                self.metrics.link_loss_drops += 1
                self.schedule(receiver.link_retry_timeout_ms(packet), self._retry_or_finish,
                              sender_id, target, original_packet, True, on_complete, attempt)
            else:
                ack_delay = ack_airtime + self._jittered_latency(link)
                self.schedule(ack_delay, on_complete, True)
        else:
            receiver.receive(packet, previous_hop)

    def _retry_or_finish(self, sender_id: int, target: Optional[int], packet: Packet,
                         reliable: bool, on_complete: Callable[[bool], None], attempt: int) -> None:
        if attempt >= self.profile.max_lcmm_attempts:
            on_complete(False)
        else:
            self.transmit(sender_id, target, packet, reliable, on_complete, attempt + 1)

    def shortest_distances(self) -> Dict[int, Dict[int, int]]:
        result: Dict[int, Dict[int, int]] = {}
        for source in self.nodes:
            if not self.nodes[source].up:
                result[source] = {}
                continue
            dist = {source: 0}
            q = deque([source])
            while q:
                u = q.popleft()
                for v in self.neighbors(u):
                    if v not in dist:
                        dist[v] = dist[u] + 1
                        q.append(v)
            result[source] = dist
        return result

    def audit(self) -> dict:
        truth = self.shortest_distances()
        missing, stale, wrong_distance, loops = [], [], [], []
        stretch_values = []
        for node_id, node in self.nodes.items():
            if not node.up or node.crashed:
                continue
            for dest, d in truth[node_id].items():
                if dest == node_id:
                    continue
                route = node.routes.get(dest)
                if route is None:
                    missing.append((node_id, dest, d))
                else:
                    if route.distance != d:
                        wrong_distance.append((node_id, dest, route.distance, d))
                    path, status = self.follow_route(node_id, dest)
                    if status == "ok":
                        stretch_values.append((len(path) - 1) / d)
                    elif status == "loop":
                        loops.append((node_id, dest, path))
            for dest in list(node.routes):
                if dest not in truth[node_id]:
                    stale.append((node_id, dest))
        self.metrics.loops_observed += len(loops)
        self.metrics.stale_route_observations += len(stale)
        return {
            "missing": missing,
            "stale": stale,
            "wrong_distance": wrong_distance,
            "loops": loops,
            "mean_stretch": (sum(stretch_values) / len(stretch_values)) if stretch_values else None,
            "correct": not (missing or stale or wrong_distance or loops),
        }

    def follow_route(self, start: int, dest: int, max_steps: Optional[int] = None) -> Tuple[List[int], str]:
        max_steps = max_steps or (len(self.nodes) + 2)
        path = [start]
        seen = {start}
        cur = start
        for _ in range(max_steps):
            if cur == dest:
                return path, "ok"
            node = self.nodes.get(cur)
            if node is None or not node.up or node.crashed:
                return path, "dead"
            route = node.routes.get(dest)
            if route is None:
                return path, "missing"
            nxt = route.next_hop
            link = self.get_link(cur, nxt)
            if link is None or not link.up:
                path.append(nxt)
                return path, "broken"
            if nxt in seen:
                path.append(nxt)
                return path, "loop"
            seen.add(nxt)
            path.append(nxt)
            cur = nxt
        return path, "limit"

    def summary(self) -> dict:
        return {
            "seed": self.seed,
            "profile": self.profile.name,
            "time_ms": self.now,
            "audit": self.audit(),
            "metrics": self.metrics.__dict__.copy(),
            "routes": {
                nid: {d: {"next": r.next_hop, "distance": r.distance} for d, r in sorted(n.routes.items())}
                for nid, n in sorted(self.nodes.items())
            },
            "crashed": [nid for nid, n in self.nodes.items() if n.crashed],
        }
