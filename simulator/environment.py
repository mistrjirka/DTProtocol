from __future__ import annotations

import heapq
import math
import random
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple


MAC_OVERHEAD = 8


@dataclass(frozen=True)
class Waypoint:
    t_ms: float
    x: float
    y: float


@dataclass
class RadioLink:
    a: int
    b: int
    loss: float = 0.0
    ack_loss: Optional[float] = None
    # This is an optional synthetic delay after RF completion, not LoRa
    # propagation. Real propagation is microseconds at ordinary LoRa ranges and
    # is negligible beside the packet airtime, so physical simulations default
    # to zero. Tests may still inject latency explicitly.
    latency_ms: float = 0.0
    jitter_ms: float = 0.0
    up: bool = True
    epoch: int = 0
    max_range: Optional[float] = None

    def other(self, node: int) -> int:
        if node == self.a:
            return self.b
        if node == self.b:
            return self.a
        raise KeyError(node)


@dataclass
class RfMetrics:
    tx_frames: int = 0
    rf_receivers_considered: int = 0
    rf_delivered: int = 0
    rf_loss_drops: int = 0
    rf_range_drops: int = 0
    rf_epoch_drops: int = 0
    firmware_epoch_drops: int = 0
    regulatory_deferrals: int = 0
    regulatory_airtime_ms: float = 0.0


class EnvironmentKernel:
    """Shared deterministic physical environment for every protocol backend.

    The Python theoretical model and the real-C++ subprocess backend both use
    this event queue, topology, trajectories, node/link epochs, airtime model,
    environment RNG and optional regulatory TX policy. Protocol code is
    deliberately outside this class.

    Priority convention for events at the same timestamp:
      0  environment changes (power/link failures, recovery)
      10 RF/PHY completion and delivery
      20 protocol timers/ticks

    Therefore a failure scheduled exactly at a PHY boundary is applied before
    the protocol/radio completion callback, independent of insertion order.
    """

    ENV_PRIORITY = 0
    RADIO_PRIORITY = 10
    PROTOCOL_PRIORITY = 20

    def __init__(
        self,
        seed: int = 1,
        *,
        sf: int = 9,
        bandwidth_hz: int = 125_000,
        coding_rate_denominator: int = 7,
        duty_cycle_percent: float = 0.0,
    ) -> None:
        self.seed = seed
        self.env_rng = random.Random(seed ^ 0xE17A_5EED)
        self.sf = sf
        self.bandwidth_hz = bandwidth_hz
        self.cr_den = coding_rate_denominator

        duty = float(duty_cycle_percent)
        if duty < 0.0 or duty > 100.0:
            raise ValueError("duty_cycle_percent must be between 0 and 100")
        self.duty_cycle_percent = duty
        # Regulatory availability is a property of the RF environment, not of
        # volatile firmware state. It therefore survives simulated reboots.
        self._tx_policy_until: Dict[int, float] = {}

        self.now = 0.0
        self._seq = 0
        self._events: List[Tuple[float, int, int, Callable, tuple]] = []

        self.links: Dict[frozenset[int], RadioLink] = {}
        self.node_up: Dict[int, bool] = {}
        self.node_epoch: Dict[int, int] = {}
        self.trajectories: Dict[int, List[Waypoint]] = {}

        self.link_max_range: Dict[frozenset[int], Optional[float]] = {}
        self.drop_next: Dict[Tuple[int, int], int] = {}
        self.rf_metrics = RfMetrics()
        self.trace: List[dict] = []

    def log(self, event: str, **fields) -> None:
        self.trace.append({"t_ms": round(self.now, 3), "event": event, **fields})

    # ------------------------------------------------------------------
    # Event queue
    # ------------------------------------------------------------------
    def schedule(
        self,
        delay_ms: float,
        fn: Callable,
        *args,
        priority: int = PROTOCOL_PRIORITY,
    ) -> None:
        self.schedule_at(self.now + max(0.0, float(delay_ms)), fn, *args, priority=priority)

    def schedule_at(
        self,
        when_ms: float,
        fn: Callable,
        *args,
        priority: int = PROTOCOL_PRIORITY,
    ) -> None:
        self._seq += 1
        heapq.heappush(
            self._events,
            (float(when_ms), int(priority), self._seq, fn, args),
        )

    def run_events(self, until_ms: float) -> None:
        until = float(until_ms)
        while self._events and self._events[0][0] <= until:
            when, _priority, _seq, fn, args = heapq.heappop(self._events)
            self.now = when
            fn(*args)
        self.now = until

    def run(self, until_ms: float) -> None:
        self.run_events(until_ms)

    # ------------------------------------------------------------------
    # Shared non-blocking regulatory TX policy
    # ------------------------------------------------------------------
    def transmit_wait_ms(self, node_id: int, at_ms: Optional[float] = None) -> float:
        if self.duty_cycle_percent <= 0.0:
            return 0.0
        at = self.now if at_ms is None else float(at_ms)
        return max(0.0, self._tx_policy_until.get(node_id, 0.0) - at)

    def account_transmission(
        self,
        node_id: int,
        start_ms: float,
        end_ms: float,
    ) -> None:
        if self.duty_cycle_percent <= 0.0:
            return
        airtime = max(0.0, float(end_ms) - float(start_ms))
        if airtime <= 0.0:
            return
        period = airtime * (100.0 / self.duty_cycle_percent)
        next_allowed = float(start_ms) + period
        self._tx_policy_until[node_id] = max(
            self._tx_policy_until.get(node_id, 0.0), next_allowed
        )
        self.rf_metrics.regulatory_airtime_ms += airtime

    def note_regulatory_deferral(self, node_id: int) -> None:
        self.rf_metrics.regulatory_deferrals += 1
        self.log(
            "regulatory_defer",
            node=node_id,
            wait_ms=round(self.transmit_wait_ms(node_id), 3),
        )

    # ------------------------------------------------------------------
    # Nodes and failures
    # ------------------------------------------------------------------
    def register_node(
        self,
        node_id: int,
        *,
        up: bool = True,
        position: Tuple[float, float] = (0.0, 0.0),
    ) -> None:
        self.node_up[node_id] = bool(up)
        self.node_epoch.setdefault(node_id, 0)
        self._tx_policy_until.setdefault(node_id, 0.0)
        self.trajectories.setdefault(
            node_id,
            [Waypoint(0.0, float(position[0]), float(position[1]))],
        )

    def set_node_up(self, node_id: int, up: bool, *, reason: str = "environment") -> None:
        old = self.node_up.get(node_id, False)
        up = bool(up)
        if old == up:
            return
        self.node_up[node_id] = up
        self.node_epoch[node_id] = self.node_epoch.get(node_id, 0) + 1
        self.log("node_up" if up else "node_down", node=node_id, reason=reason)
        if up:
            self._on_environment_node_up(node_id)
        else:
            self._on_environment_node_down(node_id)

    def fail_node_at(self, when_ms: float, node_id: int) -> None:
        self.schedule_at(
            when_ms,
            self.set_node_up,
            node_id,
            False,
            priority=self.ENV_PRIORITY,
        )

    def recover_node_at(self, when_ms: float, node_id: int) -> None:
        self.schedule_at(
            when_ms,
            self.set_node_up,
            node_id,
            True,
            priority=self.ENV_PRIORITY,
        )

    def _on_environment_node_down(self, node_id: int) -> None:
        pass

    def _on_environment_node_up(self, node_id: int) -> None:
        pass

    # ------------------------------------------------------------------
    # Links
    # ------------------------------------------------------------------
    def add_link(
        self,
        a: int,
        b: int,
        *,
        loss: float = 0.0,
        ack_loss: Optional[float] = None,
        latency_ms: float = 0.0,
        jitter_ms: float = 0.0,
        up: bool = True,
        max_range: Optional[float] = None,
    ) -> None:
        key = frozenset((a, b))
        self.links[key] = RadioLink(
            a=a,
            b=b,
            loss=float(loss),
            ack_loss=ack_loss,
            latency_ms=float(latency_ms),
            jitter_ms=float(jitter_ms),
            up=bool(up),
            max_range=None if max_range is None else float(max_range),
        )
        self.link_max_range[key] = None if max_range is None else float(max_range)

    def get_link(self, a: int, b: int) -> Optional[RadioLink]:
        return self.links.get(frozenset((a, b)))

    def _link(self, a: int, b: int) -> Optional[RadioLink]:
        return self.get_link(a, b)

    def set_link(self, a: int, b: int, up: bool) -> None:
        link = self.get_link(a, b)
        if link is None:
            raise KeyError((a, b))
        up = bool(up)
        if link.up != up:
            link.up = up
            link.epoch += 1
            self.log("link", a=a, b=b, up=up)

    def set_link_at(self, when_ms: float, a: int, b: int, up: bool) -> None:
        self.schedule_at(
            when_ms,
            self.set_link,
            a,
            b,
            up,
            priority=self.ENV_PRIORITY,
        )

    def linked_nodes(self, node_id: int) -> List[int]:
        out: List[int] = []
        for link in self.links.values():
            if node_id in (link.a, link.b):
                out.append(link.other(node_id))
        return out

    def neighbors(self, node_id: int, only_up: bool = True) -> List[int]:
        out: List[int] = []
        for link in self.links.values():
            if node_id not in (link.a, link.b):
                continue
            other = link.other(node_id)
            if only_up:
                if not link.up:
                    continue
                if not self.node_up.get(node_id, False) or not self.node_up.get(other, False):
                    continue
                if not self.in_range_now(node_id, other):
                    continue
            out.append(other)
        return out

    # ------------------------------------------------------------------
    # Continuous piecewise-linear motion
    # ------------------------------------------------------------------
    def set_trajectory(
        self,
        node_id: int,
        points: Sequence[Tuple[float, float, float] | Waypoint],
    ) -> None:
        if not points:
            raise ValueError("trajectory must contain at least one waypoint")
        converted = [
            p if isinstance(p, Waypoint)
            else Waypoint(float(p[0]), float(p[1]), float(p[2]))
            for p in points
        ]
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
                return (
                    left.x + alpha * (right.x - left.x),
                    left.y + alpha * (right.y - left.y),
                )
        return (points[-1].x, points[-1].y)

    def _range_limit(self, a: int, b: int) -> Optional[float]:
        key = frozenset((a, b))
        if key in self.link_max_range:
            return self.link_max_range[key]
        link = self.get_link(a, b)
        return link.max_range if link else None

    def _distance_sq(self, a: int, b: int, t_ms: float) -> float:
        ax, ay = self.position_at(a, t_ms)
        bx, by = self.position_at(b, t_ms)
        return (ax - bx) ** 2 + (ay - by) ** 2

    def _trajectory_breakpoints(
        self,
        node_id: int,
        start_ms: float,
        end_ms: float,
    ) -> Iterable[float]:
        for point in self.trajectories.get(node_id, ()):
            if start_ms < point.t_ms < end_ms:
                yield point.t_ms

    def stays_in_range(self, a: int, b: int, start_ms: float, end_ms: float) -> bool:
        limit = self._range_limit(a, b)
        if limit is None:
            return True
        if end_ms < start_ms:
            start_ms, end_ms = end_ms, start_ms
        times = {float(start_ms), float(end_ms)}
        times.update(self._trajectory_breakpoints(a, start_ms, end_ms))
        times.update(self._trajectory_breakpoints(b, start_ms, end_ms))
        limit_sq = limit * limit
        # Relative motion is linear between breakpoints, so squared separation
        # is convex on each segment. Its maximum on a closed segment is at an
        # endpoint; checking every breakpoint is therefore exact for the
        # "remain in range for the whole frame" predicate.
        return all(self._distance_sq(a, b, t) <= limit_sq + 1e-12 for t in times)

    def in_range_now(self, a: int, b: int, at_ms: Optional[float] = None) -> bool:
        t = self.now if at_ms is None else float(at_ms)
        limit = self._range_limit(a, b)
        return limit is None or self._distance_sq(a, b, t) <= limit * limit + 1e-12

    # ------------------------------------------------------------------
    # RF helpers shared by Python and C++ adapters
    # ------------------------------------------------------------------
    def symbol_time_ms(self) -> float:
        if self.bandwidth_hz <= 0 or self.sf < 5 or self.sf > 12:
            return 0.0
        return (2**self.sf) / self.bandwidth_hz * 1000.0

    def cad_duration_ms(self, symbols: float = 4.0, post_symbols: float = 0.5) -> float:
        """SX126x CAD listening + post-processing duration approximation.

        Semtech describes CAD as scanning the configured number of LoRa symbols
        followed by roughly half a symbol of correlation/post-processing. This
        helper models duration only; detection probability belongs to the
        higher-level RF/capture model.
        """
        return max(0.0, float(symbols) + float(post_symbols)) * self.symbol_time_ms()

    def airtime_ms(self, payload_bytes: int) -> float:
        """LoRa time-on-air matching the RadioLib/SX126x equation.

        DTProtocol uses explicit LoRa headers, CRC on, preamble 8 and supplies
        coding rate as the denominator (5..8). Auto-LDRO follows RadioLib:
        enable when symbol length is >=16 ms. SF5/6 use the SX126x-specific
        preamble and payload constants rather than the SF7-12 formula.
        """
        pl = max(0, int(payload_bytes))
        sf = int(self.sf)
        bw_hz = int(self.bandwidth_hz)
        if bw_hz <= 0 or sf < 5 or sf > 12:
            return 0.0

        symbol_ms = (2**sf) / bw_hz * 1000.0
        low_data_rate_optimize = symbol_ms >= 16.0
        preamble_extra = 6.25 if sf <= 6 else 4.25
        sf_coefficient2 = 0 if sf <= 6 else 8
        divisor = 4 * (sf - (2 if low_data_rate_optimize else 0))

        # Same constants as MathExtension.timeOnAir(): LoRa CRC contributes 16
        # bits and explicit-header mode contributes 20 to the numerator.
        bit_count = 8 * pl + 16 - 4 * sf + sf_coefficient2 + 20
        bit_count = max(bit_count, 0)
        pre_coded_symbols = math.ceil(bit_count / divisor)
        symbols = (
            8.0 + preamble_extra + 8.0 +
            pre_coded_symbols * int(self.cr_den)
        )
        return symbols * symbol_ms

    def jittered_latency(self, link: RadioLink) -> float:
        if link.jitter_ms <= 0:
            return link.latency_ms
        return max(0.0, self.env_rng.gauss(link.latency_ms, link.jitter_ms))

    def drop_next_frames(self, sender: int, receiver: int, count: int = 1) -> None:
        key = (sender, receiver)
        self.drop_next[key] = self.drop_next.get(key, 0) + int(count)

    def _consume_drop(self, sender: int, receiver: int) -> bool:
        key = (sender, receiver)
        if self.drop_next.get(key, 0) <= 0:
            return False
        self.drop_next[key] -= 1
        return True

    def sample_link_loss(self, sender: int, receiver: int, *, ack: bool = False) -> bool:
        link = self.get_link(sender, receiver)
        if link is None:
            return True
        if self._consume_drop(sender, receiver):
            return True
        probability = link.ack_loss if ack and link.ack_loss is not None else link.loss
        return self.env_rng.random() < probability

    def frame_start_valid(self, sender: int, receiver: int) -> bool:
        link = self.get_link(sender, receiver)
        return bool(
            link
            and link.up
            and self.node_up.get(sender, False)
            and self.node_up.get(receiver, False)
            and self.in_range_now(sender, receiver)
        )

    def capture_frame_epochs(self, sender: int, receiver: int) -> Tuple[int, int, int]:
        link = self.get_link(sender, receiver)
        return (
            self.node_epoch.get(sender, 0),
            self.node_epoch.get(receiver, 0),
            link.epoch if link else -1,
        )

    def frame_path_valid(
        self,
        sender: int,
        receiver: int,
        start_ms: float,
        end_ms: float,
        sender_epoch: int,
        receiver_epoch: int,
        link_epoch: int,
    ) -> Tuple[bool, str]:
        link = self.get_link(sender, receiver)
        if (
            link is None
            or not link.up
            or link.epoch != link_epoch
            or not self.node_up.get(sender, False)
            or self.node_epoch.get(sender, 0) != sender_epoch
            or not self.node_up.get(receiver, False)
            or self.node_epoch.get(receiver, 0) != receiver_epoch
        ):
            return False, "epoch"
        if not self.stays_in_range(sender, receiver, start_ms, end_ms):
            return False, "range"
        return True, "ok"
