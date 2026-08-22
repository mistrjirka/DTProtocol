from __future__ import annotations

"""Receive-state timing/fidelity overlay for SharedPythonNetwork.

SharedPythonNetwork models production RSSI CCA, TX setup, airtime, RX-buffer SPI
reads and successful link-ACK ordering. This wrapper keeps the final RadioLib
``startReceive()`` interval visible after callbacks and tightens the RX_DONE
semantics used by CCA: a frame completion only behaves like a hardware RX_DONE
when the current pessimistic no-capture medium could actually have decoded it.
"""

from radio_timing import rx_rearm_after_read_ms
from shared_backends import SharedPythonNetwork
from simulator import Simulator


class TimedSharedPythonNetwork(SharedPythonNetwork):
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
        # SX1262 must be in RX when the desired preamble starts. radio_busy_until
        # records TX/read/re-arm intervals in the timed Python backend.
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
        # beginning a TX, so MAC::loop immediately executes startReceive().
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
        # DATA upward and MAC::loop re-arms RX after the callback.
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
        # That path also needs the normal post-callback RX re-arm.
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
            # does MAC::loop restore continuous RX.
            self._mark_post_read_rearm(sender_id)
        return super()._complete_ack_receive(
            sender_id, expected_epoch, on_complete
        )


__all__ = ["TimedSharedPythonNetwork"]
