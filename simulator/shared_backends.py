from __future__ import annotations

import hashlib
import random
from collections import defaultdict
from dataclasses import dataclass
from typing import DefaultDict, Dict, List, Optional, Tuple

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

    Node/link state histories are kept here as physical history. This matters
    for collisions: a failure that happens *after* two frames already interfered
    must not retroactively make that past interference disappear, while a link
    that was down for the whole overlap must not become an interferer just
    because it recovered before decode completion.
    """

    def _init_shared_environment(self, *, radio_contention: bool = False) -> None:
        self._environment_ordinals: DefaultDict[Tuple, int] = defaultdict(int)
        self.radio_contention = bool(radio_contention)
        self.medium_metrics = MediumMetrics()
        self._medium_tx: List[dict] = []
        self._node_state_history: Dict[int, List[Tuple[float, bool]]] = {}
        self._link_state_history: Dict[frozenset[int], List[Tuple[float, bool]]] = {}

    # ------------------------------------------------------------------
    # Physical state history. These overrides remain protocol-independent.
    # ------------------------------------------------------------------
    def register_node(self, node_id, *, up=True, position=(0.0, 0.0)):
        result = super().register_node(node_id, up=up, position=position)
        self._node_state_history.setdefault(node_id, []).append((float(self.now), bool(up)))
        return result

    def set_node_up(self, node_id: int, up: bool, *, reason: str = "environment") -> None:
        before = self.node_up.get(node_id, False)
        super().set_node_up(node_id, up, reason=reason)
        after = self.node_up.get(node_id, False)
        if before != after:
            self._node_state_history.setdefault(node_id, []).append((float(self.now), after))

    def add_link(self, a: int, b: int, **kwargs) -> None:
        super().add_link(a, b, **kwargs)
        link = self.get_link(a, b)
        assert link is not None
        self._link_state_history.setdefault(frozenset((a, b)), []).append(
            (float(self.now), bool(link.up))
        )

    def set_link(self, a: int, b: int, up: bool) -> None:
        link = self.get_link(a, b)
        before = None if link is None else bool(link.up)
        super().set_link(a, b, up)
        link = self.get_link(a, b)
        after = None if link is None else bool(link.up)
        if before != after and after is not None:
            self._link_state_history.setdefault(frozenset((a, b)), []).append(
                (float(self.now), after)
            )

    @staticmethod
    def _state_at(history: List[Tuple[float, bool]], t_ms: float, default: bool = False) -> bool:
        state = default
        for when, value in history:
            if when > t_ms + 1e-12:
                break
            state = value
        return state

    def _node_up_at(self, node_id: int, t_ms: float) -> bool:
        return self._state_at(self._node_state_history.get(node_id, []), t_ms, False)

    def _link_up_at(self, a: int, b: int, t_ms: float) -> bool:
        return self._state_at(
            self._link_state_history.get(frozenset((a, b)), []), t_ms, False
        )

    @staticmethod
    def _history_breakpoints(history: List[Tuple[float, bool]], start_ms: float, end_ms: float):
        for when, _value in history:
            if start_ms < when < end_ms:
                yield when

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

    def _interferer_active_during(
        self,
        interferer: int,
        receiver: int,
        start_ms: float,
        end_ms: float,
    ) -> bool:
        """True iff RF from interferer can reach receiver at any overlap instant.

        Failure/link transitions are split into constant-state intervals. Motion
        is then checked analytically inside each interval. This prevents state at
        decode time from rewriting what physically happened earlier in a frame.
        """
        if self.get_link(interferer, receiver) is None or end_ms <= start_ms:
            return False
        times = {float(start_ms), float(end_ms)}
        times.update(
            self._history_breakpoints(
                self._node_state_history.get(interferer, []), start_ms, end_ms
            )
        )
        times.update(
            self._history_breakpoints(
                self._node_state_history.get(receiver, []), start_ms, end_ms
            )
        )
        times.update(
            self._history_breakpoints(
                self._link_state_history.get(frozenset((interferer, receiver)), []),
                start_ms,
                end_ms,
            )
        )
        times.update(self._trajectory_breakpoints(interferer, start_ms, end_ms))
        times.update(self._trajectory_breakpoints(receiver, start_ms, end_ms))
        ordered = sorted(times)

        for left, right in zip(ordered, ordered[1:]):
            if right <= left:
                continue
            midpoint = (left + right) * 0.5
            if not self._node_up_at(interferer, midpoint):
                continue
            if not self._node_up_at(receiver, midpoint):
                continue
            if not self._link_up_at(interferer, receiver, midpoint):
                continue
            if self._ever_in_range(interferer, receiver, left, right):
                return True
        return False

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
                # If the desired frame itself is path-valid, the receiver's node
                # epoch did not change during it. Any overlapping local TX is a
                # genuine half-duplex receive loss.
                return "half-duplex"

            if self._interferer_active_during(other, receiver, overlap_start, overlap_end):
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
            metrics = getattr(self, "metrics", None)
            if metrics is not None and hasattr(metrics, "collision_drops"):
                metrics.collision_drops += 1
            self.log("radio_collision", sender=sender, node=receiver)
            return False, "collision"
        if interference == "half-duplex":
            self.medium_metrics.half_duplex_drops += 1
            metrics = getattr(self, "metrics", None)
            if metrics is not None and hasattr(metrics, "half_duplex_drops"):
                metrics.half_duplex_drops += 1
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
