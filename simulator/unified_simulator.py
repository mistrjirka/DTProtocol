from __future__ import annotations

import heapq
import math
import random
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol, Sequence, Tuple

from model import (
    BROADCAST,
    DTPK_CRYST_HEADER,
    DTPK_GENERIC_HEADER,
    LCMM_OVERHEAD,
    MAC_OVERHEAD,
    MAX_PACKET_SIZE,
    NEIGHBOR_RECORD_SIZE,
    Metrics,
    Packet,
    Profile,
)
from node import Node
from cpp_backend import CppNodeProcess, Tx

ENV_PRIORITY = 0
RF_PRIORITY = 10
PROTOCOL_PRIORITY = 20


@dataclass(frozen=True)
class Waypoint:
    t_ms: float
    x: float
    y: float


@dataclass
class UnifiedLink:
    a: int
    b: int
    loss: float = 0.0
    ack_loss: Optional[float] = None
    latency_ms: float = 25.0
    jitter_ms: float = 5.0
    up: bool = True
    max_range: Optional[float] = None
    epoch: int = 0

    def other(self, node_id: int) -> int:
        if node_id == self.a:
            return self.b
        if node_id == self.b:
            return self.a
        raise KeyError(node_id)


class BackendAdapter(Protocol):
    name: str

    def add_node(self, sim: "UnifiedSimulator", node_id: int, *, seed: int, k_limit: int) -> Any: ...
    def fail_node(self, sim: "UnifiedSimulator", node_id: int) -> None: ...
    def recover_node(self, sim: "UnifiedSimulator", node_id: int, *, seed: int, k_limit: int) -> None: ...
    def routes(self, sim: "UnifiedSimulator", node_id: int) -> Dict[int, Tuple[int, int]]: ...
    def send(self, sim: "UnifiedSimulator", node_id: int, target: int, payload: bytes, timeout_ms: int, e2e_ack: bool) -> Optional[int]: ...
    def close(self, sim: "UnifiedSimulator") -> None: ...


class PythonAdapter:
    """Fast theoretical DTPK/LCMM model on the shared physical engine."""

    name = "python"

    def add_node(self, sim: "UnifiedSimulator", node_id: int, *, seed: int, k_limit: int) -> Node:
        node = Node(sim, node_id)
        sim.nodes[node_id] = node
        sim.node_epoch[node_id] = 0
        sim.schedule(0, node.start)
        return node

    def fail_node(self, sim: "UnifiedSimulator", node_id: int) -> None:
        node: Node = sim.nodes[node_id]
        node.up = False
        node.generation += 1
        node.reset_runtime()

    def recover_node(self, sim: "UnifiedSimulator", node_id: int, *, seed: int, k_limit: int) -> None:
        node: Node = sim.nodes[node_id]
        node.start()

    def routes(self, sim: "UnifiedSimulator", node_id: int) -> Dict[int, Tuple[int, int]]:
        node: Node = sim.nodes[node_id]
        if not node.up or node.crashed:
            return {}
        return {dest: (route.next_hop, route.distance) for dest, route in node.routes.items()}

    def send(self, sim: "UnifiedSimulator", node_id: int, target: int, payload: bytes,
             timeout_ms: int, e2e_ack: bool) -> Optional[int]:
        node: Node = sim.nodes[node_id]
        return node.send_data(target, payload_size=len(payload), e2e_ack=e2e_ack, timeout_ms=timeout_ms)

    def close(self, sim: "UnifiedSimulator") -> None:
        return


class CppProcessAdapter:
    """Production DTPK/CrystDatabase/LCMM, one process per emulated MCU."""

    name = "cpp"

    def __init__(self, binary: Optional[str] = None):
        self.binary = binary

    def add_node(self, sim: "UnifiedSimulator", node_id: int, *, seed: int, k_limit: int) -> CppNodeProcess:
        node = CppNodeProcess(node_id, seed=seed, k_limit=k_limit, binary=self.binary)
        sim.nodes[node_id] = node
        sim.node_epoch[node_id] = 0
        sim.cpp_node_up[node_id] = True
        sim.cpp_boot_time[node_id] = sim.now
        return node

    def fail_node(self, sim: "UnifiedSimulator", node_id: int) -> None:
        sim.cpp_node_up[node_id] = False
        node: CppNodeProcess = sim.nodes[node_id]
        node.close()

    def recover_node(self, sim: "UnifiedSimulator", node_id: int, *, seed: int, k_limit: int) -> None:
        sim.nodes[node_id] = CppNodeProcess(node_id, seed=seed, k_limit=k_limit, binary=self.binary)
        sim.cpp_node_up[node_id] = True
        sim.cpp_boot_time[node_id] = sim.now

    def routes(self, sim: "UnifiedSimulator", node_id: int) -> Dict[int, Tuple[int, int]]:
        if not sim.cpp_node_up.get(node_id, False):
            return {}
        node: CppNodeProcess = sim.nodes[node_id]
        return node.routes(sim._cpp_local_time(node_id))

    def send(self, sim: "UnifiedSimulator", node_id: int, target: int, payload: bytes,
             timeout_ms: int, e2e_ack: bool) -> Optional[int]:
        if not sim.cpp_node_up.get(node_id, False):
            return None
        node: CppNodeProcess = sim.nodes[node_id]
        return node.send(sim._cpp_local_time(node_id), target, payload, timeout_ms, e2e_ack)

    def close(self, sim: "UnifiedSimulator") -> None:
        for node in sim.nodes.values():
            if isinstance(node, CppNodeProcess):
                node.close()


class UnifiedSimulator:
    """One event/environment/radio engine with interchangeable node backends.

    Environment failures use a separate RNG and priority-0 events, so they are
    independent of protocol state and occur before protocol/RF callbacks at an
    identical timestamp. Mobility is continuous piecewise-linear and is tested
    over the entire airtime interval rather than sampled at TX start.
    """

    def __init__(self, *, backend: str | BackendAdapter = "python", seed: int = 1,
                 profile: Optional[Profile] = None, tick_ms: float = 50.0,
                 cpp_binary: Optional[str] = None, sf: int = 9,
                 bandwidth_hz: int = 125_000, coding_rate_denominator: int = 7):
        if isinstance(backend, str):
            if backend == "python":
                backend = PythonAdapter()
            elif backend == "cpp":
                backend = CppProcessAdapter(cpp_binary)
            else:
                raise ValueError(f"unknown backend {backend!r}")
        self.adapter: BackendAdapter = backend
        self.profile = profile or Profile.current()
        self.rng = random.Random(seed)
        self.env_rng = random.Random(seed ^ 0xD7A551E9)
        self.seed = seed
        self.sf = sf
        self.bandwidth_hz = bandwidth_hz
        self.cr_den = coding_rate_denominator
        self.tick_ms = float(tick_ms)
        self.now = 0.0
        self._seq = 0
        self._events: List[Tuple[float, int, int, Callable, tuple]] = []
        self._cpp_ticks_scheduled_until = 0.0
        self.nodes: Dict[int, Any] = {}
        self.cpp_node_up: Dict[int, bool] = {}
        self.cpp_boot_time: Dict[int, float] = {}
        self.node_epoch: Dict[int, int] = {}
        self.forced_drops: Dict[Tuple[int, int], int] = {}
        self.links: Dict[frozenset[int], UnifiedLink] = {}
        self.trajectories: Dict[int, List[Waypoint]] = {}
        self.node_k_limit: Dict[int, int] = {}
        self.metrics = Metrics()
        self.trace: List[dict] = []

    # ---- event engine ----
    def log(self, event: str, **fields) -> None:
        self.trace.append({"t_ms": round(self.now, 3), "event": event, **fields})

    def _schedule_at(self, when_ms: float, priority: int, fn: Callable, *args) -> None:
        self._seq += 1
        heapq.heappush(self._events, (float(when_ms), int(priority), self._seq, fn, args))

    def schedule(self, delay_ms: float, fn: Callable, *args) -> None:
        self._schedule_at(self.now + max(0.0, delay_ms), PROTOCOL_PRIORITY, fn, *args)

    def schedule_at(self, when_ms: float, fn: Callable, *args) -> None:
        self._schedule_at(when_ms, PROTOCOL_PRIORITY, fn, *args)

    def schedule_environment_at(self, when_ms: float, fn: Callable, *args) -> None:
        self._schedule_at(when_ms, ENV_PRIORITY, fn, *args)

    def run(self, until_ms: float) -> None:
        if self.adapter.name == "cpp":
            self._schedule_cpp_ticks(until_ms)
        while self._events and self._events[0][0] <= until_ms:
            when, _, _, fn, args = heapq.heappop(self._events)
            self.now = when
            fn(*args)
        self.now = float(until_ms)

    # ---- shared topology/environment ----
    def add_node(self, node_id: int, *, position: Tuple[float, float] = (0.0, 0.0),
                 seed: Optional[int] = None, k_limit: int = 20) -> Any:
        if node_id in self.nodes:
            raise ValueError(f"node {node_id} already exists")
        self.trajectories[node_id] = [Waypoint(0.0, float(position[0]), float(position[1]))]
        self.node_k_limit[node_id] = int(k_limit)
        node_seed = self.seed * 1009 + node_id if seed is None else int(seed)
        return self.adapter.add_node(self, node_id, seed=node_seed, k_limit=int(k_limit))

    def add_link(self, a: int, b: int, *, loss: float = 0.0,
                 ack_loss: Optional[float] = None, latency_ms: float = 25.0,
                 jitter_ms: float = 5.0, up: bool = True,
                 max_range: Optional[float] = None) -> None:
        self.links[frozenset((a, b))] = UnifiedLink(
            a=a, b=b, loss=float(loss),
            ack_loss=None if ack_loss is None else float(ack_loss),
            latency_ms=float(latency_ms), jitter_ms=float(jitter_ms), up=bool(up),
            max_range=None if max_range is None else float(max_range))

    def get_link(self, a: int, b: int) -> Optional[UnifiedLink]:
        return self.links.get(frozenset((a, b)))

    def drop_next_frames(self, sender: int, receiver: int, count: int = 1) -> None:
        key = (int(sender), int(receiver))
        self.forced_drops[key] = self.forced_drops.get(key, 0) + max(0, int(count))

    def _consume_forced_drop(self, sender: int, receiver: int) -> bool:
        key = (sender, receiver)
        remaining = self.forced_drops.get(key, 0)
        if remaining <= 0:
            return False
        self.forced_drops[key] = remaining - 1
        return True

    def set_link_at(self, when_ms: float, a: int, b: int, up: bool) -> None:
        self.schedule_environment_at(when_ms, self._set_link_now, a, b, up)

    def set_link(self, a: int, b: int, up: bool) -> None:
        self._set_link_now(a, b, up)

    def _set_link_now(self, a: int, b: int, up: bool) -> None:
        link = self.get_link(a, b)
        if link is None:
            raise KeyError((a, b))
        link.up = bool(up)
        link.epoch += 1
        self.log("link", a=a, b=b, up=bool(up), epoch=link.epoch)

    def fail_node_at(self, when_ms: float, node_id: int) -> None:
        self.schedule_environment_at(when_ms, self._fail_node_now, node_id)

    def recover_node_at(self, when_ms: float, node_id: int) -> None:
        self.schedule_environment_at(when_ms, self._recover_node_now, node_id)

    def reboot_node(self, node_id: int, downtime_ms: float = 500.0) -> None:
        self._fail_node_now(node_id)
        self.schedule_environment_at(self.now + downtime_ms, self._recover_node_now, node_id)

    def _fail_node_now(self, node_id: int) -> None:
        if not self._node_is_up(node_id):
            return
        self.node_epoch[node_id] = self.node_epoch.get(node_id, 0) + 1
        self.adapter.fail_node(self, node_id)
        self.log("node_down", node=node_id, epoch=self.node_epoch[node_id])

    def _recover_node_now(self, node_id: int) -> None:
        self.node_epoch[node_id] = self.node_epoch.get(node_id, 0) + 1
        k_limit = self.node_k_limit.get(node_id, 20)
        seed = self.seed * 1009 + node_id + self.node_epoch[node_id]
        self.adapter.recover_node(self, node_id, seed=seed, k_limit=k_limit)
        self.log("node_up", node=node_id, epoch=self.node_epoch[node_id])

    def _node_is_up(self, node_id: int) -> bool:
        if node_id not in self.nodes:
            return False
        if self.adapter.name == "cpp":
            return self.cpp_node_up.get(node_id, False)
        node: Node = self.nodes[node_id]
        return bool(node.up and not node.crashed)

    def neighbors(self, node_id: int, only_up: bool = True) -> List[int]:
        out: List[int] = []
        for link in self.links.values():
            if node_id not in (link.a, link.b):
                continue
            other = link.other(node_id)
            if only_up and (not link.up or not self._node_is_up(node_id)
                            or not self._node_is_up(other)
                            or not self._in_range_now(node_id, other, self.now)):
                continue
            out.append(other)
        return out

    # ---- exact piecewise-linear mobility ----
    def set_trajectory(self, node_id: int,
                       points: Sequence[Tuple[float, float, float] | Waypoint]) -> None:
        if not points:
            raise ValueError("trajectory must contain at least one waypoint")
        converted = [p if isinstance(p, Waypoint)
                     else Waypoint(float(p[0]), float(p[1]), float(p[2])) for p in points]
        converted.sort(key=lambda p: p.t_ms)
        dedup: List[Waypoint] = []
        for point in converted:
            if dedup and point.t_ms == dedup[-1].t_ms:
                dedup[-1] = point
            else:
                dedup.append(point)
        self.trajectories[node_id] = dedup

    def add_waypoint(self, node_id: int, t_ms: float, x: float, y: float) -> None:
        points = list(self.trajectories.get(node_id, [Waypoint(0.0, 0.0, 0.0)]))
        points.append(Waypoint(float(t_ms), float(x), float(y)))
        self.set_trajectory(node_id, points)

    def position_at(self, node_id: int, t_ms: float) -> Tuple[float, float]:
        points = self.trajectories.get(node_id)
        if not points:
            return (0.0, 0.0)
        t = float(t_ms)
        if t <= points[0].t_ms:
            return (points[0].x, points[0].y)
        if t >= points[-1].t_ms:
            return (points[-1].x, points[-1].y)
        for left, right in zip(points, points[1:]):
            if left.t_ms <= t <= right.t_ms:
                if right.t_ms == left.t_ms:
                    return (right.x, right.y)
                alpha = (t - left.t_ms) / (right.t_ms - left.t_ms)
                return (left.x + alpha * (right.x - left.x),
                        left.y + alpha * (right.y - left.y))
        return (points[-1].x, points[-1].y)

    def _distance_sq(self, a: int, b: int, t_ms: float) -> float:
        ax, ay = self.position_at(a, t_ms)
        bx, by = self.position_at(b, t_ms)
        return (ax - bx) ** 2 + (ay - by) ** 2

    def _trajectory_breakpoints(self, node_id: int, start_ms: float,
                                end_ms: float) -> Iterable[float]:
        for point in self.trajectories.get(node_id, ()):
            if start_ms < point.t_ms < end_ms:
                yield point.t_ms

    def _in_range_now(self, a: int, b: int, at_ms: float) -> bool:
        link = self.get_link(a, b)
        if link is None or link.max_range is None:
            return True
        return self._distance_sq(a, b, at_ms) <= link.max_range ** 2 + 1e-12

    def stays_in_range(self, a: int, b: int, start_ms: float, end_ms: float) -> bool:
        link = self.get_link(a, b)
        if link is None or link.max_range is None:
            return True
        if end_ms < start_ms:
            start_ms, end_ms = end_ms, start_ms
        times = {float(start_ms), float(end_ms)}
        times.update(self._trajectory_breakpoints(a, start_ms, end_ms))
        times.update(self._trajectory_breakpoints(b, start_ms, end_ms))
        limit_sq = link.max_range ** 2
        # Relative position is linear between breakpoints; squared distance is
        # convex, so checking segment endpoints exactly bounds whole airtime.
        return all(self._distance_sq(a, b, t) <= limit_sq + 1e-12 for t in times)

    def _jittered_latency(self, link: UnifiedLink) -> float:
        if link.jitter_ms <= 0:
            return link.latency_ms
        return max(0.0, self.env_rng.gauss(link.latency_ms, link.jitter_ms))

    # ---- shared LoRa PHY helpers ----
    def airtime_ms(self, payload_bytes: int) -> float:
        pl, sf, bw = max(0, int(payload_bytes)), self.sf, self.bandwidth_hz
        de, ih, crc = (1 if sf >= 11 and bw == 125_000 else 0), 0, 1
        cr = max(1, self.cr_den - 4)
        tsym = (2 ** sf) / bw
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

    def _physical_snapshot_valid(self, sender: int, sender_epoch: int,
                                 receiver: int, receiver_epoch: int,
                                 link_epoch: int, rf_start: float,
                                 rf_end: float) -> bool:
        link = self.get_link(sender, receiver)
        return bool(link and link.up and link.epoch == link_epoch
                    and self._node_is_up(sender)
                    and self.node_epoch.get(sender, 0) == sender_epoch
                    and self._node_is_up(receiver)
                    and self.node_epoch.get(receiver, 0) == receiver_epoch
                    and self.stays_in_range(sender, receiver, rf_start, rf_end))

    # ---- Python model adapter: abstract LCMM atop shared PHY ----
    def transmit(self, sender_id: int, target: Optional[int], packet: Packet,
                 reliable: bool, on_complete: Callable[[bool], None],
                 attempt: int = 1) -> None:
        if self.adapter.name != "python":
            raise RuntimeError("transmit(Packet) is a Python-model backend API")
        sender: Node = self.nodes[sender_id]
        if not self._node_is_up(sender_id):
            self.schedule(0, on_complete, False)
            return
        frame_bytes = self._frame_bytes(packet)
        if frame_bytes > MAX_PACKET_SIZE:
            self.metrics.oversize_drops += 1
            self.schedule(0, on_complete, False)
            return
        if self.now < sender.radio_busy_until:
            if self.profile.mac_busy_silent_drop:
                self.metrics.silent_busy_drops += 1
                if reliable:
                    self.schedule(sender.link_retry_timeout_ms(packet), self._python_retry_or_finish,
                                  sender_id, target, packet, on_complete, attempt)
                else:
                    self.schedule(0, on_complete, True)
                return
            self.schedule(sender.radio_busy_until - self.now, self.transmit,
                          sender_id, target, packet, reliable, on_complete, attempt)
            return
        airtime = self.airtime_ms(frame_bytes)
        rf_start, rf_end = self.now, self.now + airtime
        sender.radio_busy_until = rf_end
        self.metrics.radio_data_frames += 1
        self.metrics.bytes_on_air += frame_bytes
        sender_epoch = self.node_epoch.get(sender_id, 0)

        if target is None:
            self.metrics.broadcasts += 1
            if packet.kind == "CRYST":
                self.metrics.cryst_tx += 1
            for receiver in self.neighbors(sender_id):
                link = self.get_link(sender_id, receiver)
                if link is None:
                    continue
                if self._consume_forced_drop(sender_id, receiver) or self.env_rng.random() < link.loss:
                    self.metrics.link_loss_drops += 1
                    continue
                self._schedule_at(rf_end, RF_PRIORITY, self._python_rf_complete,
                                  sender_id, sender_epoch, receiver,
                                  self.node_epoch.get(receiver, 0), link.epoch,
                                  rf_start, rf_end, packet.clone(), False, None)
            self._schedule_at(rf_start if self.profile.noack_releases_lcmm_immediately else rf_end,
                              PROTOCOL_PRIORITY, on_complete, True)
            return

        self.metrics.unicast_attempts += 1
        link = self.get_link(sender_id, target)
        if (link is None or not link.up or not self._node_is_up(target)
                or not self._in_range_now(sender_id, target, rf_start)):
            if reliable:
                self.schedule(sender.link_retry_timeout_ms(packet), self._python_retry_or_finish,
                              sender_id, target, packet, on_complete, attempt)
            else:
                self._schedule_at(rf_start if self.profile.noack_releases_lcmm_immediately else rf_end,
                                  PROTOCOL_PRIORITY, on_complete, True)
            return
        if self._consume_forced_drop(sender_id, target) or self.env_rng.random() < link.loss:
            self.metrics.link_loss_drops += 1
            if reliable:
                self.schedule(sender.link_retry_timeout_ms(packet), self._python_retry_or_finish,
                              sender_id, target, packet, on_complete, attempt)
            else:
                self._schedule_at(rf_start if self.profile.noack_releases_lcmm_immediately else rf_end,
                                  PROTOCOL_PRIORITY, on_complete, True)
            return
        ack_context = (sender_id, target, packet, on_complete, attempt) if reliable else None
        self._schedule_at(rf_end, RF_PRIORITY, self._python_rf_complete,
                          sender_id, sender_epoch, target,
                          self.node_epoch.get(target, 0), link.epoch,
                          rf_start, rf_end, packet.clone(), reliable, ack_context)
        if not reliable:
            self._schedule_at(rf_start if self.profile.noack_releases_lcmm_immediately else rf_end,
                              PROTOCOL_PRIORITY, on_complete, True)

    def _python_rf_complete(self, sender_id: int, sender_epoch: int,
                            receiver_id: int, receiver_epoch: int,
                            link_epoch: int, rf_start: float, rf_end: float,
                            packet: Packet, reliable: bool, ack_context) -> None:
        if not self._physical_snapshot_valid(sender_id, sender_epoch, receiver_id,
                                             receiver_epoch, link_epoch, rf_start, rf_end):
            if reliable and ack_context is not None:
                s, t, original, done, attempt = ack_context
                sender: Node = self.nodes[s]
                self.schedule(sender.link_retry_timeout_ms(original), self._python_retry_or_finish,
                              s, t, original, done, attempt)
            return
        link = self.get_link(sender_id, receiver_id)
        assert link is not None
        arrival = rf_end + self._jittered_latency(link)
        if not reliable:
            self._schedule_at(arrival, PROTOCOL_PRIORITY, self._python_receive_if_current,
                              receiver_id, receiver_epoch, packet, sender_id)
            return

        ack_bytes = MAC_OVERHEAD + LCMM_OVERHEAD
        ack_airtime = self.airtime_ms(ack_bytes)
        ack_start, ack_end = arrival, arrival + ack_airtime
        self.metrics.radio_link_ack_frames += 1
        self.metrics.bytes_on_air += ack_bytes
        self._schedule_at(ack_end, PROTOCOL_PRIORITY, self._python_receive_if_current,
                          receiver_id, receiver_epoch, packet, sender_id)
        s, t, original, done, attempt = ack_context
        reverse = self.get_link(receiver_id, sender_id)
        if (reverse is None or not reverse.up or not self._node_is_up(sender_id)
                or not self._node_is_up(receiver_id)
                or not self._in_range_now(receiver_id, sender_id, ack_start)):
            sender: Node = self.nodes[s]
            self.schedule(sender.link_retry_timeout_ms(original), self._python_retry_or_finish,
                          s, t, original, done, attempt)
            return
        ack_loss = reverse.ack_loss if reverse.ack_loss is not None else reverse.loss
        if self._consume_forced_drop(receiver_id, sender_id) or self.env_rng.random() < ack_loss:
            self.metrics.link_loss_drops += 1
            sender: Node = self.nodes[s]
            self.schedule(sender.link_retry_timeout_ms(original), self._python_retry_or_finish,
                          s, t, original, done, attempt)
            return
        self._schedule_at(ack_end, RF_PRIORITY, self._python_ack_rf_complete,
                          receiver_id, self.node_epoch.get(receiver_id, 0),
                          sender_id, self.node_epoch.get(sender_id, 0), reverse.epoch,
                          ack_start, ack_end, original, done, attempt, t)

    def _python_receive_if_current(self, receiver_id: int, receiver_epoch: int,
                                   packet: Packet, previous_hop: int) -> None:
        if not self._node_is_up(receiver_id) or self.node_epoch.get(receiver_id, 0) != receiver_epoch:
            return
        receiver: Node = self.nodes[receiver_id]
        receiver.receive(packet, previous_hop)

    def _python_ack_rf_complete(self, ack_sender: int, ack_sender_epoch: int,
                                ack_receiver: int, ack_receiver_epoch: int,
                                link_epoch: int, rf_start: float, rf_end: float,
                                original: Packet, done: Callable[[bool], None],
                                attempt: int, target: int) -> None:
        if self._physical_snapshot_valid(ack_sender, ack_sender_epoch, ack_receiver,
                                         ack_receiver_epoch, link_epoch, rf_start, rf_end):
            link = self.get_link(ack_sender, ack_receiver)
            assert link is not None
            self._schedule_at(rf_end + self._jittered_latency(link), PROTOCOL_PRIORITY, done, True)
            return
        sender: Node = self.nodes[ack_receiver]
        self.schedule(sender.link_retry_timeout_ms(original), self._python_retry_or_finish,
                      ack_receiver, target, original, done, attempt)

    def _python_retry_or_finish(self, sender_id: int, target: Optional[int],
                                packet: Packet, on_complete: Callable[[bool], None],
                                attempt: int) -> None:
        if attempt >= self.profile.max_lcmm_attempts:
            on_complete(False)
        else:
            self.transmit(sender_id, target, packet, True, on_complete, attempt + 1)

    # ---- C++ adapter: real LCMM emits raw frames onto same PHY ----
    def _cpp_local_time(self, node_id: int) -> float:
        # A real MCU's millis() restarts on reboot. Keep world time outside and
        # translate to each subprocess' local boot-relative clock.
        return max(0.0, self.now - self.cpp_boot_time.get(node_id, 0.0))

    def _schedule_cpp_ticks(self, until_ms: float) -> None:
        t = max(self._cpp_ticks_scheduled_until, self.now)
        while t + self.tick_ms <= until_ms:
            t += self.tick_ms
            self._schedule_at(t, PROTOCOL_PRIORITY, self._cpp_tick_all)
        self._cpp_ticks_scheduled_until = max(self._cpp_ticks_scheduled_until, t)

    def _cpp_tick_all(self) -> None:
        for node_id in sorted(self.nodes):
            if not self._node_is_up(node_id):
                continue
            node: CppNodeProcess = self.nodes[node_id]
            self._cpp_handle_txs(node_id, node.tick(self._cpp_local_time(node_id)))

    def _cpp_handle_txs(self, sender_id: int, txs: List[Tx]) -> None:
        for tx in txs:
            self._cpp_start_tx(sender_id, tx)

    def _cpp_start_tx(self, sender_id: int, tx: Tx) -> None:
        if not self._node_is_up(sender_id):
            return
        frame_bytes = MAC_OVERHEAD + len(tx.payload)
        if frame_bytes > MAX_PACKET_SIZE:
            self.metrics.oversize_drops += 1
            return
        self.metrics.radio_data_frames += 1
        self.metrics.bytes_on_air += frame_bytes
        if tx.target == BROADCAST:
            self.metrics.broadcasts += 1
        else:
            self.metrics.unicast_attempts += 1
        rf_start, rf_end = self.now, self.now + self.airtime_ms(frame_bytes)
        sender_epoch = self.node_epoch.get(sender_id, 0)
        self._schedule_at(rf_end, RF_PRIORITY, self._cpp_phy_done,
                          sender_id, sender_epoch, tx.token)
        receivers = ([link.other(sender_id) for link in self.links.values()
                      if sender_id in (link.a, link.b)] if tx.target == BROADCAST
                     else [tx.target])
        for receiver_id in receivers:
            link = self.get_link(sender_id, receiver_id)
            if (link is None or not link.up or not self._node_is_up(receiver_id)
                    or not self._in_range_now(sender_id, receiver_id, rf_start)):
                continue
            if self._consume_forced_drop(sender_id, receiver_id) or self.env_rng.random() < link.loss:
                self.metrics.link_loss_drops += 1
                continue
            wire_target = BROADCAST if tx.target == BROADCAST else receiver_id
            self._schedule_at(rf_end, RF_PRIORITY, self._cpp_rf_complete,
                              sender_id, sender_epoch, receiver_id,
                              self.node_epoch.get(receiver_id, 0), wire_target,
                              link.epoch, rf_start, rf_end, tx.payload)

    def _cpp_phy_done(self, sender_id: int, sender_epoch: int, token: int) -> None:
        if not self._node_is_up(sender_id) or self.node_epoch.get(sender_id, 0) != sender_epoch:
            return
        node: CppNodeProcess = self.nodes[sender_id]
        self._cpp_handle_txs(sender_id, node.phy_done(self._cpp_local_time(sender_id), token))

    def _cpp_rf_complete(self, sender_id: int, sender_epoch: int,
                         receiver_id: int, receiver_epoch: int, wire_target: int,
                         link_epoch: int, rf_start: float, rf_end: float,
                         payload: bytes) -> None:
        if not self._physical_snapshot_valid(sender_id, sender_epoch, receiver_id,
                                             receiver_epoch, link_epoch, rf_start, rf_end):
            return
        link = self.get_link(sender_id, receiver_id)
        assert link is not None
        self._schedule_at(rf_end + self._jittered_latency(link), PROTOCOL_PRIORITY,
                          self._cpp_deliver_if_current, sender_id, receiver_id,
                          receiver_epoch, wire_target, payload)

    def _cpp_deliver_if_current(self, sender_id: int, receiver_id: int,
                                receiver_epoch: int, wire_target: int,
                                payload: bytes) -> None:
        if not self._node_is_up(receiver_id) or self.node_epoch.get(receiver_id, 0) != receiver_epoch:
            return
        node: CppNodeProcess = self.nodes[receiver_id]
        txs = node.inject(self._cpp_local_time(receiver_id), sender_id, wire_target, payload)
        self._cpp_handle_txs(receiver_id, txs)

    # ---- backend-neutral scenario/audit API ----
    def routes(self, node_id: int) -> Dict[int, Tuple[int, int]]:
        return self.adapter.routes(self, node_id)

    def send(self, node_id: int, target: int, payload: bytes = b"hello",
             timeout_ms: int = 10_000, e2e_ack: bool = True) -> Optional[int]:
        return self.adapter.send(self, node_id, target, payload, timeout_ms, e2e_ack)

    def shortest_distances(self) -> Dict[int, Dict[int, int]]:
        from collections import deque
        result: Dict[int, Dict[int, int]] = {}
        for source in self.nodes:
            if not self._node_is_up(source):
                result[source] = {}
                continue
            dist, queue = {source: 0}, deque([source])
            while queue:
                here = queue.popleft()
                for other in self.neighbors(here):
                    if other not in dist:
                        dist[other] = dist[here] + 1
                        queue.append(other)
            result[source] = dist
        return result

    def audit(self) -> Dict[str, Any]:
        shortest = self.shortest_distances()
        missing, stale, wrong_distance, loops = [], [], [], []
        for source in sorted(self.nodes):
            if not self._node_is_up(source):
                continue
            table, reachable = self.routes(source), shortest[source]
            for dest, distance in reachable.items():
                if dest == source:
                    continue
                route = table.get(dest)
                if route is None:
                    missing.append((source, dest))
                elif route[1] != distance:
                    wrong_distance.append((source, dest, route[1], distance))
            for dest in table:
                if dest not in reachable:
                    stale.append((source, dest))
            for dest in table:
                seen, current = set(), source
                for _ in range(len(self.nodes) + 1):
                    if current == dest:
                        break
                    if current in seen:
                        loops.append((source, dest, tuple(sorted(seen))))
                        break
                    seen.add(current)
                    next_route = self.routes(current).get(dest)
                    if next_route is None:
                        break
                    current = next_route[0]
        return {"correct": not (missing or stale or wrong_distance or loops),
                "missing": missing, "stale": stale,
                "wrong_distance": wrong_distance, "loops": loops}

    def close(self) -> None:
        self.adapter.close(self)

    def __enter__(self) -> "UnifiedSimulator":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
