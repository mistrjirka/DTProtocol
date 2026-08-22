from __future__ import annotations

import hashlib
import math
import random
from collections import defaultdict
from dataclasses import dataclass
from typing import DefaultDict, List, Optional, Tuple

from cpp_sim_adapter import CppSimNetwork
from model import MAC_OVERHEAD, MAX_PACKET_SIZE
from simulator import Simulator


@dataclass
class MediumMetrics:
    collision_drops: int = 0
    half_duplex_drops: int = 0


class KeyedEnvironmentMixin:
    """Common physical additions used by both Python and C++ protocol backends.

    Environment randomness is keyed to physical events rather than one global
    draw order. Optional contention records the same RF TX intervals for both
    backends and applies LoRa's broadcast/half-duplex nature independently of
    MAC addressing.
    """

    def _init_shared_environment(self, *, radio_contention: bool = False) -> None:
        self._environment_ordinals: DefaultDict[Tuple, int] = defaultdict(int)
        self.radio_contention = bool(radio_contention)
        self.medium_metrics = MediumMetrics()
        self._medium_tx: List[dict] = []

    # ------------------------------------------------------------------
    # Stable environment randomness
    # ------------------------------------------------------------------
    def _keyed_rng(self, namespace: str, *parts) -> random.Random:
        key = (namespace, *parts)
        ordinal = self._environment_ordinals[key]
        self._environment_ordinals[key] += 1
        encoded = "|".join(
            [str(self.seed), namespace, *(str(part) for part in parts), str(ordinal)]
        ).encode("utf-8")
        digest = hashlib.blake2b(encoded, digest_size=16).digest()
        return random.Random(int.from_bytes(digest, "big"))

    @staticmethod
    def _time_key(value: float) -> int:
        return int(round(float(value) * 1000.0))

    def jittered_latency(self, link) -> float:
        if link.jitter_ms <= 0:
            return link.latency_ms
        a, b = sorted((link.a, link.b))
        rng = self._keyed_rng("latency", a, b, self._time_key(self.now))
        return max(0.0, rng.gauss(link.latency_ms, link.jitter_ms))

    def sample_link_loss(self, sender: int, receiver: int, *, ack: bool = False) -> bool:
        link = self.get_link(sender, receiver)
        if link is None:
            return True
        if self._consume_drop(sender, receiver):
            return True
        probability = link.ack_loss if ack and link.ack_loss is not None else link.loss
        rng = self._keyed_rng(
            "ack-loss" if ack else "data-loss",
            sender,
            receiver,
            self._time_key(self.now),
        )
        return rng.random() < probability

    # ------------------------------------------------------------------
    # Shared same-channel contention / hidden terminals
    # ------------------------------------------------------------------
    def _record_medium_tx(self, sender: int, start_ms: float, end_ms: float) -> None:
        if not self.radio_contention:
            return
        # Keep enough history for any in-flight frame. LoRa packets here are far
        # shorter than this window, while pruning prevents unbounded test memory.
        cutoff = float(start_ms) - 10_000.0
        self._medium_tx[:] = [tx for tx in self._medium_tx if tx["end"] >= cutoff]
        self._medium_tx.append(
            {"sender": int(sender), "start": float(start_ms), "end": float(end_ms)}
        )

    def _ever_in_range(self, a: int, b: int, start_ms: float, end_ms: float) -> bool:
        """Exact minimum-distance test for piecewise-linear trajectories."""
        limit = self._range_limit(a, b)
        if limit is None:
            return self.get_link(a, b) is not None
        if end_ms <= start_ms:
            return self.in_range_now(a, b, start_ms)

        times = {float(start_ms), float(end_ms)}
        times.update(self._trajectory_breakpoints(a, start_ms, end_ms))
        times.update(self._trajectory_breakpoints(b, start_ms, end_ms))
        ordered = sorted(times)
        limit_sq = limit * limit

        for left, right in zip(ordered, ordered[1:]):
            ax0, ay0 = self.position_at(a, left)
            bx0, by0 = self.position_at(b, left)
            ax1, ay1 = self.position_at(a, right)
            bx1, by1 = self.position_at(b, right)
            rx0, ry0 = ax0 - bx0, ay0 - by0
            rvx = (ax1 - bx1) - rx0
            rvy = (ay1 - by1) - ry0
            vv = rvx * rvx + rvy * rvy
            if vv <= 1e-30:
                u = 0.0
            else:
                u = max(0.0, min(1.0, -(rx0 * rvx + ry0 * rvy) / vv))
            rx = rx0 + u * rvx
            ry = ry0 + u * rvy
            if rx * rx + ry * ry <= limit_sq + 1e-12:
                return True
        return self._distance_sq(a, b, end_ms) <= limit_sq + 1e-12

    def _medium_interference(
        self,
        sender: int,
        receiver: int,
        start_ms: float,
        end_ms: float,
    ) -> Optional[str]:
        if not self.radio_contention:
            return None

        for tx in self._medium_tx:
            other = tx["sender"]
            # Skip this frame's own emission record.
            if (
                other == sender
                and abs(tx["start"] - start_ms) < 1e-9
                and abs(tx["end"] - end_ms) < 1e-9
            ):
                continue
            overlap_start = max(float(start_ms), tx["start"])
            overlap_end = min(float(end_ms), tx["end"])
            if overlap_start >= overlap_end:
                continue

            if other == receiver:
                return "half-duplex"

            link = self.get_link(other, receiver)
            if link is None or not link.up:
                continue
            if self._ever_in_range(other, receiver, overlap_start, overlap_end):
                return "collision"
        return None

    def frame_path_valid(self, *args, **kwargs):
        valid, reason = super().frame_path_valid(*args, **kwargs)
        if not valid or not self.radio_contention:
            return valid, reason

        sender = int(args[0])
        receiver = int(args[1])
        start_ms = float(args[2])
        end_ms = float(args[3])
        interference = self._medium_interference(sender, receiver, start_ms, end_ms)
        if interference == "collision":
            self.medium_metrics.collision_drops += 1
            self.log("radio_collision", sender=sender, node=receiver)
            return False, "collision"
        if interference == "half-duplex":
            self.medium_metrics.half_duplex_drops += 1
            self.log("half_duplex_drop", sender=sender, node=receiver)
            return False, "half-duplex"
        return True, "ok"


class SharedPythonNetwork(KeyedEnvironmentMixin, Simulator):
    """Theoretical Python protocol on the common physical environment."""

    def __init__(self, *args, radio_contention: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self._init_shared_environment(radio_contention=radio_contention)

    def transmit(self, sender_id, target, packet, reliable, on_complete, attempt=1):
        sender = self.nodes[sender_id]
        frame_bytes = self._frame_bytes(packet)
        can_start_now = (
            self.node_up.get(sender_id, False)
            and sender.up
            and not sender.crashed
            and frame_bytes <= MAX_PACKET_SIZE
            and self.now >= sender.radio_busy_until
        )
        if can_start_now:
            airtime = self.airtime_ms(frame_bytes)
            self._record_medium_tx(sender_id, self.now, self.now + airtime)
        return super().transmit(
            sender_id, target, packet, reliable, on_complete, attempt
        )

    def _deliver(
        self,
        receiver_id,
        previous_hop,
        packet,
        reliable,
        ack_context,
        receiver_epoch=None,
    ):
        # The abstract LCMM ACK is not represented as a Packet/transmit() call in
        # Simulator, so expose its real RF airtime to the common medium here.
        receiver = self.nodes[receiver_id]
        if (
            reliable
            and receiver.up
            and not receiver.crashed
            and self.node_up.get(receiver_id, False)
            and (
                receiver_epoch is None
                or self.node_epoch.get(receiver_id, 0) == receiver_epoch
            )
            and self.get_link(receiver_id, previous_hop) is not None
        ):
            ack_airtime = self.airtime_ms(MAC_OVERHEAD + 3)
            self._record_medium_tx(receiver_id, self.now, self.now + ack_airtime)
        return super()._deliver(
            receiver_id,
            previous_hop,
            packet,
            reliable,
            ack_context,
            receiver_epoch,
        )


class SharedCppNetwork(KeyedEnvironmentMixin, CppSimNetwork):
    """Real C++ protocol subprocesses on the same physical environment."""

    def __init__(self, *args, radio_contention: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self._init_shared_environment(radio_contention=radio_contention)

    def _start_tx(self, sender, tx, at):
        airtime = self.airtime_ms(MAC_OVERHEAD + len(tx.payload))
        self._record_medium_tx(sender, at, at + airtime)
        return super()._start_tx(sender, tx, at)


__all__ = [
    "SharedPythonNetwork",
    "SharedCppNetwork",
    "KeyedEnvironmentMixin",
    "MediumMetrics",
]
