from __future__ import annotations

"""Hardware/timing fidelity overlay for SharedPythonNetwork.

This backend is the production-like Python reference. In addition to the shared
RF/CCA behavior it models:

* SX1262 continuous-RX semantics and the explicit post-callback RadioLib refresh;
* RX_DONE only for frames the current no-capture medium could actually decode;
* production LCMM hop deadlines derived from the local DTPK request timeout/3,
  rather than the older fixed ~1.65 s theoretical retry delay.
"""

import math

from radio_timing import (
    rssi_cca_duration_ms,
    rx_rearm_after_read_ms,
    tx_startup_ms,
)
from shared_backends import SharedPythonNetwork
from simulator import Simulator


class TimedSharedPythonNetwork(SharedPythonNetwork):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Source DATA uses the application/DTPK timeout. Relayed DATA and
        # protocol ACK/NACK use production's fixed 5000 ms request timeout;
        # CRYST_REQ uses 3000 ms. Keys are 16-bit packet identities and are
        # naturally overwritten after wrap rather than growing per RF retry.
        self._source_request_timeout_ms: dict[tuple[int, int], int] = {}
        self._hop_deadline_ms: dict[tuple[int, str, int, int], float] = {}

    @staticmethod
    def _deadline_key(sender_id: int, packet) -> tuple[int, str, int, int]:
        origin = (
            int(packet.original_sender)
            if packet.original_sender is not None
            else int(sender_id)
        )
        return (
            int(sender_id),
            str(packet.kind),
            origin,
            int(packet.packet_id) & 0xFFFF,
        )

    def _production_hop_timeout_base_ms(self, sender_id: int, packet) -> int:
        """LCMM timeout argument used by production for this local hop.

        DTPK passes request.timeout/3 to LCMM::sendPacketSingle(). The C++
        division is integer division and clamps non-positive values to 1 ms.
        """
        if packet.kind == "CRYST_REQ":
            request_timeout = 3000
        elif (
            packet.kind == "DATA"
            and packet.original_sender == sender_id
        ):
            request_timeout = self._source_request_timeout_ms.get(
                (int(sender_id), int(packet.packet_id) & 0xFFFF),
                int(self.profile.e2e_timeout_ms),
            )
        else:
            # Relayed DATA and routed ACK/NACK are queued with 5000 ms in the
            # production DTPK implementation. Other reliable kinds currently
            # fall through to the same safe production default.
            request_timeout = 5000
        request_timeout = int(request_timeout)
        return max(1, request_timeout // 3 if request_timeout > 0 else 1)

    def _remaining_hop_timeout_ms(self, sender_id: int, packet) -> float:
        key = self._deadline_key(sender_id, packet)
        deadline = self._hop_deadline_ms.get(key)
        if deadline is not None:
            return max(1.0, float(deadline) - float(self.now))

        # Fallback for a direct helper call outside the normal timed TX path.
        frame_bytes = self._frame_bytes(packet)
        return float(
            self._production_hop_timeout_base_ms(sender_id, packet)
            + math.ceil(self.airtime_ms(frame_bytes))
        )

    def add_node(self, node_id: int, *args, **kwargs):
        node = super().add_node(node_id, *args, **kwargs)
        # Simulator internals ask the transmitting Node for its LCMM retry
        # delay. Bind that query to the absolute production-equivalent deadline
        # maintained by this timed backend.
        node.link_retry_timeout_ms = (
            lambda packet, nid=int(node_id): self._remaining_hop_timeout_ms(
                nid, packet
            )
        )
        return node

    def send(
        self,
        node_id: int,
        target: int,
        payload: bytes = b"hello",
        timeout_ms: int = 10000,
        e2e_ack: bool = True,
    ) -> int:
        packet_id = super().send(
            node_id,
            target,
            payload,
            timeout_ms,
            e2e_ack,
        )
        # enqueue() schedules pump() for a future event turn, so this mapping is
        # installed before the first CCA/TX can consume it. Packet id 0 is a
        # valid Python-model id; storing a failed route-miss entry is harmless
        # and will be overwritten by the next wrapped id.
        self._source_request_timeout_ms[
            (int(node_id), int(packet_id) & 0xFFFF)
        ] = int(timeout_ms)
        return packet_id

    def _transmit_after_cca(
        self, sender_id, target, packet, reliable, on_complete, attempt
    ):
        if reliable:
            frame_bytes = self._frame_bytes(packet)
            # Production starts its timeout budget before MAC::sendData(); after
            # sendData returns it subtracts the time spent in CCA/setup. Thus the
            # absolute deadline is requestStart + hopTimeout + ceil(data airtime).
            request_start = (
                float(self.now)
                - rssi_cca_duration_ms()
                - tx_startup_ms(frame_bytes)
            )
            deadline = (
                request_start
                + self._production_hop_timeout_base_ms(sender_id, packet)
                + math.ceil(self.airtime_ms(frame_bytes))
            )
            self._hop_deadline_ms[
                self._deadline_key(sender_id, packet)
            ] = deadline
        return super()._transmit_after_cca(
            sender_id,
            target,
            packet,
            reliable,
            on_complete,
            attempt,
        )

    def _mark_post_read_rearm(self, node_id: int) -> None:
        node = self.nodes[node_id]
        node.radio_busy_until = max(
            float(node.radio_busy_until), self.now + rx_rearm_after_read_ms()
        )

    def _medium_tx_decodable_without_capture(self, tx: dict, receiver: int) -> bool:
        """Whether this abstract RF frame could have produced RX_DONE.

        This intentionally uses the simulator's current pessimistic collision
        model: any audible overlap corrupts the frame. It is still more faithful
        than the old shortcut that converted every reachable frame end into
        RX_DONE, including collided frames and frames whose preamble began while
        the receiver was transmitting/re-arming.
        """
        sender = int(tx["sender"])
        if sender == receiver:
            return False
        start = float(tx["start"])
        end = float(tx["end"])
        link = self.get_link(sender, receiver)
        if (
            link is None
            or not link.up
            or not self.node_up.get(sender, False)
            or not self.node_up.get(receiver, False)
            or not self.stays_in_range(sender, receiver, start, end)
        ):
            return False

        node = self.nodes.get(receiver)
        if node is None or not node.up or node.crashed:
            return False
        # SX1262 must be in RX when the desired preamble starts. The timed
        # Python radio_busy_until covers TX and explicit restart gaps, not the
        # host-side packet-buffer read while Rx Continuous remains active.
        if float(node.radio_busy_until) > start + 1e-9:
            return False

        for other in self._medium_tx:
            if other is tx:
                continue
            left = max(start, float(other["start"]))
            right = min(end, float(other["end"]))
            if left >= right:
                continue
            other_sender = int(other["sender"])
            if other_sender == receiver:
                return False
            other_link = self.get_link(other_sender, receiver)
            if (
                other_link is not None
                and other_link.up
                and self.node_up.get(other_sender, False)
                and self._ever_in_range(other_sender, receiver, left, right)
            ):
                return False
        return True

    def _rx_completion_during_cca(
        self, receiver: int, cca_start_ms: float, cca_end_ms: float
    ) -> bool:
        if not self.radio_contention:
            return False
        for tx in self._medium_tx:
            end = float(tx["end"])
            if not (float(cca_start_ms) < end <= float(cca_end_ms)):
                continue
            if self._medium_tx_decodable_without_capture(tx, receiver):
                return True
        return False

    def _deliver_after_read(
        self,
        receiver_id,
        previous_hop,
        packet,
        reliable,
        ack_context,
        receiver_epoch=None,
    ):
        # DATA_NOACK / HELLO / CRYST etc. return from LCMM/MAC callback without
        # beginning a TX, so MAC::loop executes its explicit startReceive()
        # refresh even though SX1262 Rx Continuous was already active.
        if not reliable:
            receiver = self.nodes[receiver_id]
            if (
                receiver.up
                and not receiver.crashed
                and self.node_up.get(receiver_id, False)
                and (
                    receiver_epoch is None
                    or self.node_epoch.get(receiver_id, 0) == receiver_epoch
                )
            ):
                self._mark_post_read_rearm(receiver_id)
        return super()._deliver_after_read(
            receiver_id,
            previous_hop,
            packet,
            reliable,
            ack_context,
            receiver_epoch,
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
        # If MAC policy already prevents an ACK attempt, production LCMM delivers
        # DATA upward and MAC::loop performs the explicit RX refresh.
        regulatory_wait = Simulator.transmit_wait_ms(self, receiver_id)
        carrier_wait = max(
            0.0, self._carrier_backoff_until.get(receiver_id, 0.0) - self.now
        )
        if regulatory_wait > 1e-9 or carrier_wait > 1e-9:
            receiver = self.nodes[receiver_id]
            if (
                receiver.up
                and not receiver.crashed
                and self.node_up.get(receiver_id, False)
            ):
                self._mark_post_read_rearm(receiver_id)
        return super()._schedule_ack_cca(
            receiver_id,
            previous_hop,
            packet,
            sender_id,
            target,
            original_packet,
            on_complete,
            attempt,
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
        busy_before = self.medium_metrics.cca_busy
        super()._finish_ack_cca(
            receiver_id,
            previous_hop,
            packet,
            sender_id,
            target,
            original_packet,
            on_complete,
            attempt,
            cca_start,
        )
        if self.medium_metrics.cca_busy > busy_before:
            receiver = self.nodes[receiver_id]
            if (
                receiver.up
                and not receiver.crashed
                and self.node_up.get(receiver_id, False)
            ):
                # The busy path delivered DATA upward and returned from the MAC
                # RX callback without starting an ACK transmission.
                self._mark_post_read_rearm(receiver_id)

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
        # A topology object disappearing between DATA RX and ACK start is rare,
        # but the parent treats it as a failed ACK and delivers DATA upward.
        # That path also needs the normal post-callback RX refresh.
        missing_link = self.get_link(receiver_id, sender_id) is None
        if missing_link:
            receiver = self.nodes[receiver_id]
            if (
                receiver.up
                and not receiver.crashed
                and self.node_up.get(receiver_id, False)
            ):
                self._mark_post_read_rearm(receiver_id)
        return super()._start_ack_tx(
            receiver_id,
            previous_hop,
            packet,
            sender_id,
            target,
            original_packet,
            on_complete,
            attempt,
        )

    def _complete_ack_receive(self, sender_id, expected_epoch, on_complete) -> None:
        sender = self.nodes[sender_id]
        if (
            self.node_up.get(sender_id, False)
            and self.node_epoch.get(sender_id, 0) == expected_epoch
            and sender.up
            and not sender.crashed
        ):
            # LCMM::handleACK runs inside the RX callback. Only after it returns
            # does MAC::loop execute the explicit continuous-RX refresh.
            self._mark_post_read_rearm(sender_id)
        return super()._complete_ack_receive(
            sender_id, expected_epoch, on_complete
        )


__all__ = ["TimedSharedPythonNetwork"]
