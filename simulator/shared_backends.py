from __future__ import annotations

import hashlib
import math
import random
from collections import defaultdict
from dataclasses import dataclass, replace
from typing import DefaultDict, List, Optional, Tuple

from cpp_sim_adapter import CppSimNetwork
from model import LCMM_OVERHEAD, MAC_OVERHEAD, MAX_PACKET_SIZE, NEIGHBOR_RECORD_SIZE
from simulator import Simulator


@dataclass
class MediumMetrics:
    collision_drops: int = 0
    half_duplex_drops: int = 0


class KeyedEnvironmentMixin:
    """Keyed environment randomness + common same-channel contention."""

    def _init_shared_environment(self, *, radio_contention: bool = False) -> None:
        self._environment_ordinals: DefaultDict[Tuple, int] = defaultdict(int)
        self.radio_contention = bool(radio_contention)
        self.medium_metrics = MediumMetrics()
        self._medium_tx: List[dict] = []

    def _keyed_rng(self, namespace: str, *parts) -> random.Random:
        key = (namespace, *parts)
        ordinal = self._environment_ordinals[key]
        self._environment_ordinals[key] += 1
        encoded = "|".join(
            [str(self.seed), namespace, *(str(p) for p in parts), str(ordinal)]
        ).encode()
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
        p = link.ack_loss if ack and link.ack_loss is not None else link.loss
        rng = self._keyed_rng(
            "ack-loss" if ack else "data-loss", sender, receiver,
            self._time_key(self.now),
        )
        return rng.random() < p

    def _record_medium_tx(self, sender: int, start_ms: float, end_ms: float) -> None:
        if not self.radio_contention:
            return
        cutoff = float(start_ms) - 10_000.0
        self._medium_tx[:] = [x for x in self._medium_tx if x["end"] >= cutoff]
        self._medium_tx.append(
            {"sender": int(sender), "start": float(start_ms), "end": float(end_ms)}
        )

    def _ever_in_range(self, a: int, b: int, start_ms: float, end_ms: float) -> bool:
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
            ax0, ay0 = self.position_at(a, left); bx0, by0 = self.position_at(b, left)
            ax1, ay1 = self.position_at(a, right); bx1, by1 = self.position_at(b, right)
            rx0, ry0 = ax0 - bx0, ay0 - by0
            rvx, rvy = (ax1 - bx1) - rx0, (ay1 - by1) - ry0
            vv = rvx * rvx + rvy * rvy
            u = 0.0 if vv <= 1e-30 else max(
                0.0, min(1.0, -(rx0 * rvx + ry0 * rvy) / vv)
            )
            rx, ry = rx0 + u * rvx, ry0 + u * rvy
            if rx * rx + ry * ry <= limit_sq + 1e-12:
                return True
        return self._distance_sq(a, b, end_ms) <= limit_sq + 1e-12

    def _medium_interference(self, sender: int, receiver: int,
                             start_ms: float, end_ms: float) -> Optional[str]:
        if not self.radio_contention:
            return None
        for tx in self._medium_tx:
            other = tx["sender"]
            if (other == sender and abs(tx["start"] - start_ms) < 1e-9 and
                    abs(tx["end"] - end_ms) < 1e-9):
                continue
            left, right = max(start_ms, tx["start"]), min(end_ms, tx["end"])
            if left >= right:
                continue
            if other == receiver:
                return "half-duplex"
            link = self.get_link(other, receiver)
            if link is not None and link.up and self._ever_in_range(other, receiver, left, right):
                return "collision"
        return None

    def frame_path_valid(self, *args, **kwargs):
        valid, reason = super().frame_path_valid(*args, **kwargs)
        if not valid or not self.radio_contention:
            return valid, reason
        sender, receiver = int(args[0]), int(args[1])
        start_ms, end_ms = float(args[2]), float(args[3])
        interference = self._medium_interference(sender, receiver, start_ms, end_ms)
        if interference == "collision":
            self.medium_metrics.collision_drops += 1
            return False, "collision"
        if interference == "half-duplex":
            self.medium_metrics.half_duplex_drops += 1
            return False, "half-duplex"
        return True, "ok"


class SharedPythonNetwork(KeyedEnvironmentMixin, Simulator):
    def __init__(
        self,
        *args,
        radio_contention: bool = False,
        duty_cycle_percent: float = 0.0,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        duty = float(duty_cycle_percent)
        if duty < 0.0 or duty > 100.0:
            raise ValueError("duty_cycle_percent must be between 0 and 100")
        self.duty_cycle_percent = duty

        expiry = self.profile.neighbor_expiry_ms
        if duty > 0.0 and expiry is not None:
            full_airtime = self.airtime_ms(MAX_PACKET_SIZE)
            off_time = full_airtime * (100.0 / duty - 1.0)
            hello_period = float(self.profile.hello_period_ms or 0)
            hello_gap = hello_period * (1.0 + self.profile.hello_jitter_fraction)
            safe_expiry = int(math.ceil(off_time + hello_gap + 1000.0))
            if safe_expiry > expiry:
                self.profile = replace(
                    self.profile,
                    neighbor_expiry_ms=safe_expiry,
                )

        self._init_shared_environment(radio_contention=radio_contention)

    def _frame_bytes(self, packet):
        if packet.kind == "CRYST" and packet.wire_dtpk_size:
            return MAC_OVERHEAD + LCMM_OVERHEAD + int(packet.wire_dtpk_size)
        base = super()._frame_bytes(packet)
        if packet.kind != "CRYST":
            return base
        record_size = getattr(packet, "cryst_record_size", NEIGHBOR_RECORD_SIZE)
        return base + len(packet.advertisements) * (
            int(record_size) - NEIGHBOR_RECORD_SIZE
        )

    def transmit(self, sender_id, target, packet, reliable, on_complete, attempt=1):
        wait = self.transmit_wait_ms(sender_id)
        if wait > 1e-9:
            self.note_regulatory_deferral(sender_id)
            self.schedule(
                wait,
                self.transmit,
                sender_id,
                target,
                packet,
                reliable,
                on_complete,
                attempt,
            )
            return

        sender = self.nodes[sender_id]
        frame_bytes = self._frame_bytes(packet)
        actual_start = (
            self.node_up.get(sender_id, False)
            and sender.up
            and not sender.crashed
            and frame_bytes <= MAX_PACKET_SIZE
            and self.now >= sender.radio_busy_until
        )
        if actual_start:
            airtime = self.airtime_ms(frame_bytes)
            self.account_transmission(sender_id, self.now, self.now + airtime)
            self._record_medium_tx(sender_id, self.now, self.now + airtime)
        return super().transmit(sender_id, target, packet, reliable, on_complete, attempt)

    def _deliver(self, receiver_id, previous_hop, packet, reliable, ack_context,
                 receiver_epoch=None):
        receiver = self.nodes[receiver_id]
        if (
            not receiver.up
            or receiver.crashed
            or not self.node_up.get(receiver_id, False)
            or (
                receiver_epoch is not None
                and self.node_epoch.get(receiver_id, 0) != receiver_epoch
            )
        ):
            self.rf_metrics.firmware_epoch_drops += 1
            return

        self.rf_metrics.rf_delivered += 1
        if not reliable:
            receiver.receive(packet, previous_hop)
            return

        sender_id, target, original_packet, on_complete, attempt = ack_context
        link = self.get_link(receiver_id, sender_id)
        if link is None:
            self.schedule(
                receiver.link_retry_timeout_ms(packet),
                self._retry_or_finish,
                sender_id,
                target,
                original_packet,
                True,
                on_complete,
                attempt,
            )
            return

        if self.transmit_wait_ms(receiver_id) > 1e-9:
            self.note_regulatory_deferral(receiver_id)
            receiver.receive(packet, previous_hop)
            self.schedule(
                self.nodes[sender_id].link_retry_timeout_ms(original_packet),
                self._retry_or_finish,
                sender_id,
                target,
                original_packet,
                True,
                on_complete,
                attempt,
            )
            return

        ack_bytes = MAC_OVERHEAD + 1 + 2
        ack_airtime = self.airtime_ms(ack_bytes)
        ack_start = self.now
        ack_end = ack_start + ack_airtime
        self.account_transmission(receiver_id, ack_start, ack_end)
        self._record_medium_tx(receiver_id, ack_start, ack_end)
        self.metrics.radio_link_ack_frames += 1
        self.metrics.bytes_on_air += ack_bytes
        self.rf_metrics.tx_frames += 1

        ack_epochs = self.capture_frame_epochs(receiver_id, sender_id)
        lost = self.sample_link_loss(receiver_id, sender_id, ack=True)
        if lost:
            self.metrics.link_loss_drops += 1
            self.rf_metrics.rf_loss_drops += 1

        self.schedule_at(
            ack_end,
            self._python_ack_rf_complete,
            receiver_id,
            sender_id,
            packet,
            previous_hop,
            original_packet,
            target,
            on_complete,
            attempt,
            ack_start,
            ack_end,
            *ack_epochs,
            lost,
            self.jittered_latency(link),
            priority=self.RADIO_PRIORITY,
        )


class SharedCppNetwork(KeyedEnvironmentMixin, CppSimNetwork):
    def __init__(self, *args, radio_contention: bool = False,
                 duty_cycle_percent: float = 0.0, **kwargs):
        super().__init__(
            *args,
            duty_cycle_percent=duty_cycle_percent,
            **kwargs,
        )
        self._init_shared_environment(radio_contention=radio_contention)

    def _start_tx(self, sender, tx, at):
        if self.transmit_wait_ms(sender, at) <= 1e-6:
            airtime = self.airtime_ms(MAC_OVERHEAD + len(tx.payload))
            self._record_medium_tx(sender, at, at + airtime)
        return super()._start_tx(sender, tx, at)


__all__ = ["SharedPythonNetwork", "SharedCppNetwork", "MediumMetrics"]
