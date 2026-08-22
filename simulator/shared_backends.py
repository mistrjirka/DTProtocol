from __future__ import annotations

import hashlib
import math
import random
from collections import defaultdict
from dataclasses import dataclass, replace
from typing import DefaultDict, List, Optional, Tuple

from cpp_sim_adapter import CppSimNetwork
from model import LCMM_OVERHEAD, MAC_OVERHEAD, MAX_PACKET_SIZE, NEIGHBOR_RECORD_SIZE
from node import Node
from radio_timing import (
    RSSI_CCA_SAMPLE_SPACING_MS,
    RSSI_CCA_SAMPLES,
    rssi_cca_duration_ms,
    rssi_sample_offsets_ms,
    rx_packet_read_ms,
    rx_rearm_ms,
    tx_startup_ms,
)
from simulator import Simulator


@dataclass
class MediumMetrics:
    collision_drops: int = 0
    half_duplex_drops: int = 0
    cca_scans: int = 0
    cca_busy: int = 0
    cca_clear: int = 0
    cca_backoff_ms: float = 0.0


class KeyedEnvironmentMixin:
    """Keyed randomness + common medium/CCA behavior.

    The production MAC keeps the SX1262 in continuous RX while taking three
    instantaneous-RSSI samples over roughly 20 ms. A detected carrier causes a
    randomized 25-250 ms backoff. This mixin models that physical observation
    window independently from routing behavior so the Python theoretical backend
    and C++ adapter share the same RF history/collision predicates.
    """

    CARRIER_BACKOFF_MIN_MS = 25.0
    CARRIER_BACKOFF_MAX_MS = 250.0

    def _init_shared_environment(self, *, radio_contention: bool = False) -> None:
        self._environment_ordinals: DefaultDict[Tuple, int] = defaultdict(int)
        self.radio_contention = bool(radio_contention)
        self.medium_metrics = MediumMetrics()
        self._medium_tx: List[dict] = []
        self._carrier_backoff_until: dict[int, float] = {}
        self._cca_pending_until: dict[int, float] = {}

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
        rng = self._keyed_rng(
            "ack-loss" if ack else "data-loss", sender, receiver,
            self._time_key(self.now),
        )
        return self._sample_link_loss_with_rng(
            link,
            sender,
            receiver,
            ack=ack,
            rng=rng,
        )

    def transmit_wait_ms(self, node_id: int, at_ms: Optional[float] = None) -> float:
        """Combine optional regulatory spacing with MAC carrier backoff."""
        at = self.now if at_ms is None else float(at_ms)
        regulatory = super().transmit_wait_ms(node_id, at)
        carrier = max(0.0, self._carrier_backoff_until.get(node_id, 0.0) - at)
        return max(float(regulatory), carrier)

    def _record_medium_tx(self, sender: int, start_ms: float, end_ms: float) -> None:
        if not self.radio_contention:
            return
        cutoff = float(start_ms) - 10_000.0
        self._medium_tx[:] = [x for x in self._medium_tx if x["end"] >= cutoff]
        self._medium_tx.append(
            {"sender": int(sender), "start": float(start_ms), "end": float(end_ms)}
        )

    def _carrier_backoff_ms(self, node_id: int, observed_at_ms: float) -> float:
        rng = self._keyed_rng(
            "carrier-backoff", int(node_id), self._time_key(observed_at_ms)
        )
        return rng.uniform(self.CARRIER_BACKOFF_MIN_MS, self.CARRIER_BACKOFF_MAX_MS)

    def _medium_energy_at(self, receiver: int, at_ms: float) -> bool:
        """Whether an abstract reachable transmission contributes RSSI energy.

        Links in the shared simulator are already the abstraction for RF reach.
        Therefore a transmission is considered CCA-visible only when the sender
        has an up/in-range physical link to the sensing node. Hidden terminals
        remain hidden by construction.
        """
        if not self.radio_contention:
            return False
        if not self.node_up.get(receiver, False):
            return False
        at = float(at_ms)
        for tx in self._medium_tx:
            other = int(tx["sender"])
            if other == receiver:
                continue
            if not (float(tx["start"]) <= at < float(tx["end"])):
                continue
            link = self.get_link(other, receiver)
            if link is None or not link.up:
                continue
            if not self.node_up.get(other, False):
                continue
            if self.in_cca_range_now(other, receiver, at):
                return True
        return False

    def _rx_completion_during_cca(
        self, receiver: int, cca_start_ms: float, cca_end_ms: float
    ) -> bool:
        if not self.radio_contention:
            return False
        for tx in self._medium_tx:
            other = int(tx["sender"])
            if other == receiver:
                continue
            end = float(tx["end"])
            if not (float(cca_start_ms) < end <= float(cca_end_ms)):
                continue
            link = self.get_link(other, receiver)
            if link is None or not link.up:
                continue
            if (
                self.node_up.get(other, False)
                and self.node_up.get(receiver, False)
                and self.in_range_now(other, receiver, end)
            ):
                # Production MAC checks the shared DIO1/RX_DONE wake flag
                # between samples. A completed decodable frame therefore aborts
                # CCA even if its RF energy fell entirely between sample instants.
                return True
        return False

    def _rssi_cca_busy(self, node_id: int, cca_start_ms: float) -> bool:
        offsets = rssi_sample_offsets_ms(
            RSSI_CCA_SAMPLES, RSSI_CCA_SAMPLE_SPACING_MS
        )
        cca_end = float(cca_start_ms) + rssi_cca_duration_ms(
            RSSI_CCA_SAMPLES, RSSI_CCA_SAMPLE_SPACING_MS
        )
        return (
            any(
                self._medium_energy_at(node_id, float(cca_start_ms) + offset)
                for offset in offsets
            )
            or self._rx_completion_during_cca(node_id, cca_start_ms, cca_end)
        )

    def _ever_in_range(
        self,
        a: int,
        b: int,
        start_ms: float,
        end_ms: float,
        range_kind: str = "decode",
    ) -> bool:
        limit = self._range_limit(a, b, range_kind)
        if limit is None:
            return self.get_link(a, b) is not None
        if end_ms <= start_ms:
            return self.in_range_now(a, b, start_ms, range_kind)
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
            if (
                link is not None
                and link.up
                and self._ever_in_range(
                    other, receiver, left, right, "interference"
                )
            ):
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


class MobileAwareNode(Node):
    """Node whose own mobility label only changes its HELLO cadence.

    The hint is intentionally local-only: it is not transmitted and cannot
    change another node's feasibility condition, route metric, next-hop choice,
    expiry, or loop-prevention state. A wrong label therefore affects airtime
    and discovery latency only.
    """

    def __init__(self, sim, node_id: int, *, mobile_hint: bool = False):
        super().__init__(sim, node_id)
        self.mobile_hint = bool(mobile_hint)

    def _effective_hello_period_ms(self) -> float:
        configured = self.profile.hello_period_ms
        if self.mobile_hint:
            connected = bool(self.last_heard)
            if (
                not connected
                and self.profile.mobile_discovery_hello_period_ms is not None
            ):
                configured = self.profile.mobile_discovery_hello_period_ms
            elif self.profile.mobile_hello_period_ms is not None:
                configured = self.profile.mobile_hello_period_ms
        period = float(configured or 1)

        # Preserve the optional strict-duty simulator behavior. Practical mode
        # has duty_cycle_percent=0 and therefore gets the normal clocks.
        duty = float(getattr(self.sim, "duty_cycle_percent", 0.0))
        if 0.0 < duty <= 1.0:
            period = max(period, 60_000.0)
        return period


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

    def add_node(
        self,
        node_id: int,
        start: bool = True,
        *,
        position: Tuple[float, float] = (0.0, 0.0),
        mobile_hint: bool = False,
    ) -> MobileAwareNode:
        node = MobileAwareNode(self, node_id, mobile_hint=mobile_hint)
        self.nodes[node_id] = node
        self.register_node(node_id, up=True, position=position)
        if start:
            self.schedule(0, node.start, priority=self.PROTOCOL_PRIORITY)
        return node

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

    def _schedule_data_cca(
        self, sender_id, target, packet, reliable, on_complete, attempt
    ) -> None:
        sender = self.nodes[sender_id]
        if (
            not self.node_up.get(sender_id, False)
            or not sender.up
            or sender.crashed
        ):
            self.schedule(0, on_complete, False)
            return

        regulatory_wait = Simulator.transmit_wait_ms(self, sender_id)
        if regulatory_wait > 1e-9:
            self.note_regulatory_deferral(sender_id)
        wait = self.transmit_wait_ms(sender_id)
        earliest = max(
            self.now + wait,
            float(sender.radio_busy_until),
            self._cca_pending_until.get(sender_id, 0.0),
        )
        if earliest > self.now + 1e-9:
            self.schedule_at(
                earliest,
                self._schedule_data_cca,
                sender_id,
                target,
                packet,
                reliable,
                on_complete,
                attempt,
                priority=self.RADIO_PRIORITY,
            )
            return

        cca_start = self.now
        cca_end = cca_start + rssi_cca_duration_ms()
        self._cca_pending_until[sender_id] = cca_end
        self.medium_metrics.cca_scans += 1
        self.schedule_at(
            cca_end,
            self._finish_data_cca,
            sender_id,
            target,
            packet,
            reliable,
            on_complete,
            attempt,
            cca_start,
            priority=self.RADIO_PRIORITY,
        )

    def _finish_data_cca(
        self, sender_id, target, packet, reliable, on_complete, attempt, cca_start
    ) -> None:
        sender = self.nodes[sender_id]
        if (
            not self.node_up.get(sender_id, False)
            or not sender.up
            or sender.crashed
        ):
            self._cca_pending_until.pop(sender_id, None)
            self.schedule(0, on_complete, False)
            return

        if self._rssi_cca_busy(sender_id, cca_start):
            self.medium_metrics.cca_busy += 1
            backoff = self._carrier_backoff_ms(sender_id, self.now)
            self.medium_metrics.cca_backoff_ms += backoff
            next_try = self.now + backoff
            self._carrier_backoff_until[sender_id] = next_try
            self._cca_pending_until[sender_id] = next_try
            self.schedule_at(
                next_try,
                self._schedule_data_cca,
                sender_id,
                target,
                packet,
                reliable,
                on_complete,
                attempt,
                priority=self.RADIO_PRIORITY,
            )
            return

        self.medium_metrics.cca_clear += 1
        frame_bytes = self._frame_bytes(packet)
        rf_start = self.now + tx_startup_ms(frame_bytes)
        self._cca_pending_until[sender_id] = rf_start
        self.schedule_at(
            rf_start,
            self._transmit_after_cca,
            sender_id,
            target,
            packet,
            reliable,
            on_complete,
            attempt,
            priority=self.RADIO_PRIORITY,
        )

    def _transmit_after_cca(
        self, sender_id, target, packet, reliable, on_complete, attempt
    ):
        self._cca_pending_until.pop(sender_id, None)
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
            rf_end = self.now + airtime
            self.account_transmission(sender_id, self.now, rf_end)
            self._record_medium_tx(sender_id, self.now, rf_end)
            completion = on_complete
            if not reliable:
                # Production no-ACK LCMM is released by MAC's TX-done callback
                # only after finishTransmit() and continuous RX have been
                # restored. Base Simulator fires at RF end, so delay the owner
                # callback by the modeled re-arm interval.
                completion = lambda ok: self.schedule(
                    rx_rearm_ms(),
                    on_complete,
                    ok,
                    priority=self.RADIO_PRIORITY,
                )
            result = super().transmit(
                sender_id, target, packet, reliable, completion, attempt
            )
            sender.radio_busy_until = max(
                float(sender.radio_busy_until), rf_end + rx_rearm_ms()
            )
            return result
        return super().transmit(
            sender_id, target, packet, reliable, on_complete, attempt
        )

    def transmit(self, sender_id, target, packet, reliable, on_complete, attempt=1):
        # Regulatory spacing and carrier backoff are both MAC policy waits and
        # neither consumes an LCMM RF attempt. Once allowed, model the actual
        # blocking three-sample RSSI CCA before RadioLib/SX1262 TX setup.
        return self._schedule_data_cca(
            sender_id, target, packet, reliable, on_complete, attempt
        )

    def _schedule_ack_cca(
        self,
        receiver_id,
        previous_hop,
        packet,
        sender_id,
        target,
        original_packet,
        on_complete,
        attempt,
    ) -> None:
        receiver = self.nodes[receiver_id]
        if (
            not receiver.up
            or receiver.crashed
            or not self.node_up.get(receiver_id, False)
        ):
            self.rf_metrics.firmware_epoch_drops += 1
            return

        # Production LCMM does not queue a link ACK when MAC policy refuses it;
        # it delivers the DATA upward and lets the original sender retry.
        regulatory_wait = Simulator.transmit_wait_ms(self, receiver_id)
        if regulatory_wait > 1e-9:
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

        backoff_wait = max(
            0.0, self._carrier_backoff_until.get(receiver_id, 0.0) - self.now
        )
        if backoff_wait > 1e-9:
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

        cca_start = self.now
        cca_end = cca_start + rssi_cca_duration_ms()
        self._cca_pending_until[receiver_id] = cca_end
        self.medium_metrics.cca_scans += 1
        self.schedule_at(
            cca_end,
            self._finish_ack_cca,
            receiver_id,
            previous_hop,
            packet,
            sender_id,
            target,
            original_packet,
            on_complete,
            attempt,
            cca_start,
            priority=self.RADIO_PRIORITY,
        )

    def _finish_ack_cca(
        self,
        receiver_id,
        previous_hop,
        packet,
        sender_id,
        target,
        original_packet,
        on_complete,
        attempt,
        cca_start,
    ) -> None:
        receiver = self.nodes[receiver_id]
        if (
            not receiver.up
            or receiver.crashed
            or not self.node_up.get(receiver_id, False)
        ):
            self._cca_pending_until.pop(receiver_id, None)
            self.rf_metrics.firmware_epoch_drops += 1
            return

        if self._rssi_cca_busy(receiver_id, cca_start):
            self.medium_metrics.cca_busy += 1
            backoff = self._carrier_backoff_ms(receiver_id, self.now)
            self.medium_metrics.cca_backoff_ms += backoff
            self._carrier_backoff_until[receiver_id] = self.now + backoff
            self._cca_pending_until.pop(receiver_id, None)
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

        self.medium_metrics.cca_clear += 1
        ack_bytes = MAC_OVERHEAD + 1 + 2
        ack_start = self.now + tx_startup_ms(ack_bytes)
        self._cca_pending_until[receiver_id] = ack_start
        self.schedule_at(
            ack_start,
            self._start_ack_tx,
            receiver_id,
            previous_hop,
            packet,
            sender_id,
            target,
            original_packet,
            on_complete,
            attempt,
            priority=self.RADIO_PRIORITY,
        )

    def _start_ack_tx(
        self,
        receiver_id,
        previous_hop,
        packet,
        sender_id,
        target,
        original_packet,
        on_complete,
        attempt,
    ) -> None:
        self._cca_pending_until.pop(receiver_id, None)
        receiver = self.nodes[receiver_id]
        if (
            not receiver.up
            or receiver.crashed
            or not self.node_up.get(receiver_id, False)
        ):
            self.rf_metrics.firmware_epoch_drops += 1
            return

        link = self.get_link(receiver_id, sender_id)
        if link is None:
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
        receiver.radio_busy_until = max(
            float(receiver.radio_busy_until), ack_end + rx_rearm_ms()
        )
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

        # Two independent events follow ACK RF completion in production:
        # 1) the ACK transmitter finishes TX, re-arms RX, then LCMM invokes the
        #    deferred DTPK data callback;
        # 2) the original DATA sender receives/reads the ACK and completes its
        #    waiting LCMM attempt. Do not collapse these into one timestamp.
        self.schedule_at(
            ack_end + rx_rearm_ms(),
            self._deliver_after_ack_tx,
            receiver_id,
            previous_hop,
            packet,
            ack_epochs[0],
            priority=self.RADIO_PRIORITY,
        )
        self.schedule_at(
            ack_end,
            self._finish_ack_rf,
            receiver_id,
            sender_id,
            original_packet,
            target,
            on_complete,
            attempt,
            ack_start,
            ack_end,
            *ack_epochs,
            lost,
            self.jittered_latency(link),
            ack_bytes,
            priority=self.RADIO_PRIORITY,
        )

    def _deliver_after_ack_tx(
        self, receiver_id, previous_hop, packet, ack_sender_epoch
    ) -> None:
        receiver = self.nodes[receiver_id]
        if (
            self.node_up.get(receiver_id, False)
            and self.node_epoch.get(receiver_id, 0) == ack_sender_epoch
            and receiver.up
            and not receiver.crashed
        ):
            receiver.receive(packet, previous_hop)
        else:
            self.rf_metrics.firmware_epoch_drops += 1

    def _finish_ack_rf(
        self, receiver_id, sender_id, original_packet, target, on_complete,
        attempt, ack_start, ack_end, ack_sender_epoch, ack_receiver_epoch,
        link_epoch, random_lost, latency_ms, ack_bytes
    ) -> None:
        valid, reason = self.frame_path_valid(
            receiver_id,
            sender_id,
            ack_start,
            ack_end,
            ack_sender_epoch,
            ack_receiver_epoch,
            link_epoch,
        )
        if not valid:
            if reason == "range":
                self.rf_metrics.rf_range_drops += 1
            else:
                self.rf_metrics.rf_epoch_drops += 1

        if random_lost or not valid:
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

        self.schedule(
            latency_ms + rx_packet_read_ms(ack_bytes),
            self._complete_ack_receive,
            sender_id,
            ack_receiver_epoch,
            on_complete,
            priority=self.RADIO_PRIORITY,
        )

    def _complete_ack_receive(
        self, sender_id, expected_epoch, on_complete
    ) -> None:
        sender = self.nodes[sender_id]
        if (
            not self.node_up.get(sender_id, False)
            or self.node_epoch.get(sender_id, 0) != expected_epoch
            or not sender.up
            or sender.crashed
        ):
            self.rf_metrics.firmware_epoch_drops += 1
            return
        on_complete(True)

    def _deliver(self, receiver_id, previous_hop, packet, reliable, ack_context,
                 receiver_epoch=None):
        # RX_DONE only means the RF frame is complete. RadioLib still reads the
        # packet buffer, resets buffer pointers and clears IRQ state before MAC
        # can invoke LCMM. Model that frame-size-dependent SPI interval here.
        self.schedule(
            rx_packet_read_ms(self._frame_bytes(packet)),
            self._deliver_after_read,
            receiver_id,
            previous_hop,
            packet,
            reliable,
            ack_context,
            receiver_epoch,
            priority=self.RADIO_PRIORITY,
        )

    def _deliver_after_read(self, receiver_id, previous_hop, packet, reliable, ack_context,
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
        if self.get_link(receiver_id, sender_id) is None:
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

        self._schedule_ack_cca(
            receiver_id,
            previous_hop,
            packet,
            sender_id,
            target,
            original_packet,
            on_complete,
            attempt,
        )


class SharedCppNetwork(KeyedEnvironmentMixin, CppSimNetwork):
    """Real C++ protocol on the shared RF medium.

    Collision/half-duplex history is shared with Python. The subprocess fake MAC
    still begins a TX synchronously when firmware calls sendData(), so exact
    20-ms RSSI CCA/backoff is intentionally *not* synthesized here: doing so
    would make the subprocess think it was already transmitting while hardware
    should still be in RX. Contention studies that depend on CCA timing should
    use SharedPythonNetwork until the subprocess protocol gains a mid-send CCA
    handshake; protocol/serialization/failure tests remain valid here.
    """

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


__all__ = [
    "SharedPythonNetwork",
    "SharedCppNetwork",
    "MobileAwareNode",
    "MediumMetrics",
]
