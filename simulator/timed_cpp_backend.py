from __future__ import annotations

"""Timing overlay for the real-C++ subprocess backend.

The host fake MAC intentionally keeps the firmware call interface synchronous.
That means it cannot reproduce a *busy* production RSSI CCA without turning the
subprocess protocol into a coroutine: on hardware ``MAC::sendData()`` remains in
continuous RX for ~20 ms, can latch DIO1, and may then return a transient busy
result to LCMM.

For clear-channel and non-contention experiments we can still reproduce the
important wall-clock ordering exactly enough to compare Python and C++ protocol
behavior:

    firmware sendData call
      -> 3-sample RSSI CCA
      -> RadioLib/SX1262 TX setup
      -> LoRa RF airtime
      -> finishTransmit + continuous-RX re-arm
      -> firmware TX-done callback

and on reception:

    RF end / RX_DONE
      -> RadioLib/MAC IRQ + packet-buffer SPI reads
      -> firmware RX callback
      -> (immediate outbound ACK/TX, or RX re-arm)

The overlay is deliberately conservative during the synthetic clear CCA: the
fake subprocess has already entered its SENDING state, so the simulated radio is
considered unavailable for reception.  Therefore this backend is *not* the MAC
capacity oracle under ``radio_contention=True``; SharedPythonNetwork remains the
production-CCA model for those experiments.
"""

from typing import Dict

from environment import MAC_OVERHEAD
from radio_timing import (
    rssi_cca_duration_ms,
    rx_packet_read_ms,
    rx_rearm_after_read_ms,
    rx_rearm_ms,
    tx_startup_ms,
)
from shared_backends import SharedCppNetwork


class TimedSharedCppNetwork(SharedCppNetwork):
    """Real C++ DTPK/LCMM with documented SX1262/RadioLib wall-clock timing."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._radio_rx_ready_at: Dict[int, float] = {}

    def add_node(self, node_id: int, **kwargs) -> None:
        super().add_node(node_id, **kwargs)
        self._radio_rx_ready_at[node_id] = self.now

    def _on_environment_node_down(self, node_id: int) -> None:
        self._radio_rx_ready_at[node_id] = float("inf")
        super()._on_environment_node_down(node_id)

    def _on_environment_node_up(self, node_id: int) -> None:
        super()._on_environment_node_up(node_id)
        self._radio_rx_ready_at[node_id] = self.now

    def frame_start_valid(self, sender: int, receiver: int) -> bool:
        if not super().frame_start_valid(sender, receiver):
            return False
        return self.now + 1e-9 >= self._radio_rx_ready_at.get(receiver, 0.0)

    def _start_tx(self, sender, tx, at):
        """Schedule clear-channel CCA + TX setup before physical RF begins."""
        if not self.node_up.get(sender, False):
            return

        # The fake MAC and environment both enforce optional strict duty policy.
        # This is only a safety net against small adapter/numerical drift.
        wait = self.transmit_wait_ms(sender, at)
        if wait > 1e-6:
            self.note_regulatory_deferral(sender)
            self.schedule_at(
                at + wait,
                self._start_tx,
                sender,
                tx,
                at + wait,
                priority=self.RADIO_PRIORITY,
            )
            return

        frame_bytes = MAC_OVERHEAD + len(tx.payload)
        cca_ms = rssi_cca_duration_ms()
        setup_ms = tx_startup_ms(frame_bytes)
        rf_start = float(at) + cca_ms + setup_ms
        airtime = self.airtime_ms(frame_bytes)
        rf_end = rf_start + airtime
        rx_ready = rf_end + rx_rearm_ms()

        # The subprocess fake MAC already considers itself SENDING.  Treat the
        # radio as unavailable until the real MAC would have completed TX and
        # restored continuous RX.
        self._radio_rx_ready_at[sender] = max(
            self._radio_rx_ready_at.get(sender, 0.0), rx_ready
        )
        self.medium_metrics.cca_scans += 1
        self.medium_metrics.cca_clear += 1

        self.schedule_at(
            rf_start,
            self._begin_rf_tx,
            sender,
            tx,
            rf_start,
            rf_end,
            rx_ready,
            priority=self.RADIO_PRIORITY,
        )

    def _begin_rf_tx(self, sender, tx, rf_start, rf_end, rx_ready):
        if not self.node_up.get(sender, False):
            return

        self.rf_metrics.tx_frames += 1
        sender_epoch = self.node_epoch.get(sender, 0)
        self.account_transmission(sender, rf_start, rf_end)
        self._record_medium_tx(sender, rf_start, rf_end)

        # hostsim::phy_done changes fake-MAC state back to RECEIVING and invokes
        # the LCMM/DTPK owner callback. Production does that only after
        # finishTransmit()+startReceive(), so issue PHYDONE at rx_ready.
        self.schedule_at(
            rx_ready,
            self._phy_done,
            sender,
            sender_epoch,
            tx.token,
            priority=self.RADIO_PRIORITY,
        )

        receivers = self.linked_nodes(sender) if tx.target == 0 else [tx.target]
        for receiver in receivers:
            self.rf_metrics.rf_receivers_considered += 1
            if not self.frame_start_valid(sender, receiver):
                continue

            link = self.get_link(sender, receiver)
            if link is None:
                continue
            sender_ep, receiver_ep, link_ep = self.capture_frame_epochs(
                sender, receiver
            )
            if self.sample_link_loss(sender, receiver):
                self.rf_metrics.rf_loss_drops += 1
                continue

            wire_target = 0 if tx.target == 0 else receiver
            self.schedule_at(
                rf_end,
                self._rf_complete_timed,
                sender,
                sender_ep,
                receiver,
                receiver_ep,
                wire_target,
                link_ep,
                rf_start,
                rf_end,
                self.jittered_latency(link),
                tx.payload,
                priority=self.RADIO_PRIORITY,
            )

    def _rf_complete_timed(
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
        valid, reason = self.frame_path_valid(
            sender,
            receiver,
            rf_start,
            rf_end,
            sender_epoch,
            receiver_epoch,
            link_epoch,
        )
        if not valid:
            if reason == "range":
                self.rf_metrics.rf_range_drops += 1
            else:
                self.rf_metrics.rf_epoch_drops += 1
            return

        frame_bytes = MAC_OVERHEAD + len(payload)
        read_done = rf_end + latency_ms + rx_packet_read_ms(frame_bytes)
        # Until the packet buffer/IRQ sequence has completed the receiver cannot
        # safely begin another modeled frame.
        self._radio_rx_ready_at[receiver] = max(
            self._radio_rx_ready_at.get(receiver, 0.0), read_done
        )
        self.schedule_at(
            read_done,
            self._firmware_deliver_after_read,
            sender,
            receiver,
            receiver_epoch,
            wire_target,
            payload,
            priority=self.RADIO_PRIORITY,
        )

    def _firmware_deliver_after_read(
        self,
        sender: int,
        receiver: int,
        receiver_epoch: int,
        wire_target: int,
        payload: bytes,
    ) -> None:
        if (
            not self.node_up.get(receiver, False)
            or self.node_epoch.get(receiver, 0) != receiver_epoch
        ):
            self.rf_metrics.firmware_epoch_drops += 1
            return

        # CppSimNetwork owns MCU-local millis() semantics.
        txs = self.nodes[receiver].inject(
            self._local_time(receiver), sender, wire_target, payload
        )
        self.rf_metrics.rf_delivered += 1

        if txs:
            # A reliable DATA callback can synchronously start its link ACK.
            # _start_tx() below replaces the read deadline with the full
            # CCA/setup/TX/re-arm deadline.
            self._handle_txs(receiver, txs, self.now)
        else:
            # No immediate TX: production MAC::loop calls startReceive() after
            # the RX callback returns.  Keep the radio unavailable through that
            # documented SPI + STBY_RC->RX interval.
            self._radio_rx_ready_at[receiver] = max(
                self._radio_rx_ready_at.get(receiver, 0.0),
                self.now + rx_rearm_after_read_ms(),
            )


__all__ = ["TimedSharedCppNetwork"]
