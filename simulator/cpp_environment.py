from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from cpp_backend import CppNetwork, Tx, lora_airtime_ms, MAC_OVERHEAD


@dataclass(frozen=True)
class Waypoint:
    t_ms: float
    x: float
    y: float


@dataclass
class CppRfMetrics:
    tx_frames: int = 0
    rf_receivers_considered: int = 0
    rf_delivered: int = 0
    rf_loss_drops: int = 0
    rf_range_drops: int = 0
    rf_epoch_drops: int = 0
    firmware_epoch_drops: int = 0


class MovingCppNetwork(CppNetwork):
    """Real-C++ network backend with continuous geometry.

    DTProtocol/LCMM still run inside one real C++ process per node.  This class
    owns the external physical environment.  Motion and node/link failures are
    therefore independent of firmware state and may invalidate a frame at any
    point during its airtime.

    Motion is piecewise-linear.  For one linear segment the squared distance
    between two moving nodes is convex, so the maximum separation over that
    segment is at an endpoint.  Checking the union of both nodes' waypoint
    times therefore determines exactly whether the pair stayed within range for
    the whole frame; no mobility sampling interval is involved.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.trajectories: Dict[int, List[Waypoint]] = {}
        self.link_max_range: Dict[frozenset[int], Optional[float]] = {}
        self.rf_metrics = CppRfMetrics()

    def add_node(
        self,
        node_id: int,
        *,
        seed: Optional[int] = None,
        k_limit: int = 20,
        position: Tuple[float, float] = (0.0, 0.0),
    ) -> None:
        super().add_node(node_id, seed=seed, k_limit=k_limit)
        self.trajectories[node_id] = [Waypoint(0.0, float(position[0]), float(position[1]))]

    def add_link(
        self,
        a: int,
        b: int,
        *,
        loss: float = 0.0,
        latency_ms: float = 5.0,
        up: bool = True,
        max_range: Optional[float] = None,
    ) -> None:
        super().add_link(a, b, loss=loss, latency_ms=latency_ms, up=up)
        self.link_max_range[frozenset((a, b))] = None if max_range is None else float(max_range)

    def set_trajectory(
        self,
        node_id: int,
        points: Sequence[Tuple[float, float, float] | Waypoint],
    ) -> None:
        if not points:
            raise ValueError("trajectory must contain at least one waypoint")
        converted = [
            p if isinstance(p, Waypoint) else Waypoint(float(p[0]), float(p[1]), float(p[2]))
            for p in points
        ]
        converted.sort(key=lambda p: p.t_ms)
        # Same-time points are ambiguous for a continuous trajectory.  Keep the
        # last one explicitly so the environment definition remains deterministic.
        dedup: List[Waypoint] = []
        for p in converted:
            if dedup and p.t_ms == dedup[-1].t_ms:
                dedup[-1] = p
            else:
                dedup.append(p)
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
        # Trajectories are small in tests; linear search keeps the code obvious.
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
        return self.link_max_range.get(frozenset((a, b)))

    def _distance_sq(self, a: int, b: int, t_ms: float) -> float:
        ax, ay = self.position_at(a, t_ms)
        bx, by = self.position_at(b, t_ms)
        return (ax - bx) ** 2 + (ay - by) ** 2

    def _trajectory_breakpoints(self, node_id: int, start_ms: float, end_ms: float) -> Iterable[float]:
        for p in self.trajectories.get(node_id, ()):
            if start_ms < p.t_ms < end_ms:
                yield p.t_ms

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
        # On each interval between these breakpoints relative position is
        # linear; ||relative_position||^2 is convex and therefore maximized at
        # an interval endpoint.  The union below is an exact whole-airtime test.
        return all(self._distance_sq(a, b, t) <= limit_sq + 1e-12 for t in times)

    def _in_range_now(self, a: int, b: int, at: float) -> bool:
        limit = self._range_limit(a, b)
        return limit is None or self._distance_sq(a, b, at) <= limit * limit + 1e-12

    def _start_tx(self, sender: int, tx: Tx, at: float) -> None:
        if not self.node_up.get(sender, False):
            return

        self.rf_metrics.tx_frames += 1
        sender_epoch = self.node_epoch[sender]
        airtime = lora_airtime_ms(MAC_OVERHEAD + len(tx.payload))
        rf_end = at + airtime

        # The transmitting radio gets its own IRQ independently of reception.
        self._schedule(rf_end, 10, self._phy_done, sender, sender_epoch, tx.token)

        if tx.target == 0:
            receivers = [
                link.other(sender)
                for link in self.links.values()
                if sender in (link.a, link.b)
            ]
        else:
            receivers = [tx.target]

        for receiver in receivers:
            self.rf_metrics.rf_receivers_considered += 1
            link = self._link(sender, receiver)
            if (
                not link
                or not link.up
                or not self.node_up.get(receiver, False)
                or not self._in_range_now(sender, receiver, at)
            ):
                continue

            link_epoch = link.epoch
            receiver_epoch = self.node_epoch[receiver]
            if self._consume_drop(sender, receiver) or self.env_rng.random() < link.loss:
                self.rf_metrics.rf_loss_drops += 1
                continue

            wire_target = 0 if tx.target == 0 else receiver
            self._schedule(
                rf_end,
                10,
                self._rf_complete,
                sender,
                sender_epoch,
                receiver,
                receiver_epoch,
                wire_target,
                link_epoch,
                at,
                rf_end,
                link.latency_ms,
                tx.payload,
            )

    def _rf_complete(
        self,
        sender: int,
        sender_epoch: int,
        receiver: int,
        receiver_epoch: int,
        wire_target: int,
        link_epoch: int,
        rf_start: float,
        rf_end: float,
        latency_ms: float,
        payload: bytes,
    ) -> None:
        link = self._link(sender, receiver)
        # Link/power failures are external epoch changes.  A down->up transition
        # entirely inside one frame still changes the epoch and invalidates it.
        if (
            not link
            or not link.up
            or link.epoch != link_epoch
            or not self.node_up.get(sender, False)
            or self.node_epoch[sender] != sender_epoch
            or not self.node_up.get(receiver, False)
            or self.node_epoch[receiver] != receiver_epoch
        ):
            self.rf_metrics.rf_epoch_drops += 1
            return
        if not self.stays_in_range(sender, receiver, rf_start, rf_end):
            self.rf_metrics.rf_range_drops += 1
            return

        # RF is already captured at this point.  A later link-state change must
        # not retroactively erase the frame, but a receiver reboot before its
        # firmware sees the frame must invalidate the stale delivery.
        self._schedule(
            self.now + latency_ms,
            10,
            self._firmware_deliver,
            sender,
            receiver,
            receiver_epoch,
            wire_target,
            payload,
        )

    def _firmware_deliver(
        self,
        sender: int,
        receiver: int,
        receiver_epoch: int,
        wire_target: int,
        payload: bytes,
    ) -> None:
        if (
            not self.node_up.get(receiver, False)
            or self.node_epoch[receiver] != receiver_epoch
        ):
            self.rf_metrics.firmware_epoch_drops += 1
            return
        txs = self.nodes[receiver].inject(self.now, sender, wire_target, payload)
        self.rf_metrics.rf_delivered += 1
        self._handle_txs(receiver, txs, self.now)
