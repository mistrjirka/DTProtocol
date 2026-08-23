from __future__ import annotations

"""Hardware/timing fidelity overlay for SharedPythonNetwork.

This backend is the production-like Python reference. In addition to the shared
RF/CCA behavior it models:

* SX1262 continuous-RX semantics and the explicit post-callback RadioLib refresh;
* RX_DONE only for frames the current no-capture medium could actually decode;
* production LCMM hop deadlines derived from the local DTPK request timeout/3;
* v2 liveness maintenance: suspect/probe/repair, but topology withdrawal only
  after the hard no-valid-packet timeout.
"""

import math
from typing import Dict

from model import DTPK_CRYST_REQ_SIZE, Packet, TxRequest, next_sequence
from radio_timing import (
    rssi_cca_duration_ms,
    rx_rearm_after_read_ms,
    tx_startup_ms,
)
from shared_backends import MobileAwareNode, SharedPythonNetwork
from simulator import Simulator


class TimedV2Node(MobileAwareNode):
    """Python node with the maintenance cadence/invariants of production v2."""

    LIVENESS_SUSPECT_MS = 30_000.0
    LIVENESS_PROBE_COOLDOWN_MS = 10_000.0
    MAINTENANCE_PERIOD_MS = 1_000.0

    def __init__(self, sim, node_id: int, *, mobile_hint: bool = False):
        super().__init__(sim, node_id, mobile_hint=mobile_hint)
        self.last_liveness_probe: Dict[int, float] = {}

    def reset_runtime(self) -> None:
        super().reset_runtime()
        self.last_liveness_probe.clear()

    def link_retry_timeout_ms(self, packet) -> float:
        helper = getattr(self.sim, "_remaining_hop_timeout_ms", None)
        if helper is not None:
            return helper(self.id, packet)
        return super().link_retry_timeout_ms(packet)

    def start(self) -> None:
        # Mirrors Node.start(), except production DTPK runs liveness/assembly/
        # sequence maintenance every 1 s rather than expiry/3.
        self.up = True
        self.crashed = False
        self.generation += 1

        if self.profile.sequence_numbers:
            self.origin_sequence = next_sequence(self.origin_sequence)

        self.sim.log(
            "node_up",
            node=self.id,
            origin_sequence=self.origin_sequence,
        )
        self.schedule_cryst("startup")

        gen = self.generation
        if self.profile.periodic_cryst_ms:
            self.sim.schedule(
                self.profile.periodic_cryst_ms,
                self._periodic_cryst,
                gen,
            )
        if self.profile.hello_period_ms:
            first = self.sim.rng.uniform(100, self._effective_hello_period_ms())
            self.sim.schedule(first, self._periodic_hello, gen)
        if self.profile.origin_seq_period_ms:
            self.sim.schedule(
                self.profile.origin_seq_period_ms,
                self._origin_sequence_tick,
                gen,
            )
        if self.profile.neighbor_expiry_ms:
            self.sim.schedule(
                self.MAINTENANCE_PERIOD_MS,
                self._expiry_tick,
                gen,
            )

    def _queue_liveness_probe(self, neighbor: int) -> None:
        # C++ sendCrystRequest() coalesces one unsent direct request per
        # neighbor. Match it so suspicion cannot create a control burst.
        if any(
            queued.packet.kind == "CRYST_REQ"
            and queued.next_hop == neighbor
            for queued in self.txq
        ):
            return
        request = Packet(
            "CRYST_REQ",
            self.next_packet_id(),
            original_sender=self.id,
            final_target=neighbor,
            wire_dtpk_size=DTPK_CRYST_REQ_SIZE,
            sender_sequence=self.origin_sequence,
            route_version=self.route_version,
        )
        self.sim.metrics.cryst_req_tx += 1
        self.enqueue(
            TxRequest(
                request,
                neighbor,
                lcmm_ack=True,
                timeout_ms=3_000,
                priority=True,
            )
        )

    def _expiry_tick(self, gen: int) -> None:
        if gen != self.generation or not self.up or self.crashed:
            return

        hard_expiry = self._effective_neighbor_expiry_ms()
        stale = []
        for neighbor, last in list(self.last_heard.items()):
            age = self.sim.now - last
            if age >= hard_expiry:
                stale.append(neighbor)
                continue
            if (
                self.profile.state_digest_requests
                and age >= self.LIVENESS_SUSPECT_MS
            ):
                last_probe = self.last_liveness_probe.get(neighbor, -1e30)
                if (
                    self.sim.now - last_probe
                    >= self.LIVENESS_PROBE_COOLDOWN_MS
                ):
                    self._queue_liveness_probe(neighbor)
                    self.last_liveness_probe[neighbor] = self.sim.now

        changed_contribution = False
        for neighbor in stale:
            self.last_heard.pop(neighbor, None)
            self.last_liveness_probe.pop(neighbor, None)
            self.neighbor_cryst_state.pop(neighbor, None)
            if neighbor in self.routes_by_neighbor:
                del self.routes_by_neighbor[neighbor]
                changed_contribution = True

        if changed_contribution:
            changed_best = self.rebuild_routes()
            if changed_best:
                self.schedule_cryst("neighbor_expired")

        self._expire_fragment_assemblies()
        self.sim.schedule(
            self.MAINTENANCE_PERIOD_MS,
            self._expiry_tick,
            gen,
        )

    def receive(self, packet, previous_hop: int) -> None:
        # Base receive() records every valid direct protocol frame as liveness.
        super().receive(packet, previous_hop)
        if previous_hop in self.last_heard:
            self.last_liveness_probe.pop(previous_hop, None)


class TimedSharedPythonNetwork(SharedPythonNetwork):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
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
        """LCMM timeout argument used by production for this local hop."""
        if packet.kind == "CRYST_REQ":
            request_timeout = 3000
        elif (
            packet.kind == "DATA_FRAGMENT"
            and packet.original_sender == sender_id
        ):
            request_timeout = 10_000
        elif packet.kind == "DATA" and packet.original_sender == sender_id:
            request_timeout = self._source_request_timeout_ms.get(
                (int(sender_id), int(packet.packet_id) & 0xFFFF),
                int(self.profile.e2e_timeout_ms),
            )
        else:
            request_timeout = 5000
        request_timeout = int(request_timeout)
        # Production bounds one LCMM link-ACK silence interval independently
        # from the full application deadline. A long logical timeout must not
        # monopolize the half-duplex radio after one lost link ACK.
        return min(3000, max(1, request_timeout if request_timeout > 0 else 1))

    def _remaining_hop_timeout_ms(self, sender_id: int, packet) -> float:
        key = self._deadline_key(sender_id, packet)
        deadline = self._hop_deadline_ms.get(key)
        if deadline is not None:
            return max(1.0, float(deadline) - float(self.now))
        frame_bytes = self._frame_bytes(packet)
        link_ack_frame_bytes = MAC_OVERHEAD + 3
        return float(
            self._production_hop_timeout_base_ms(sender_id, packet)
            + math.ceil(self.airtime_ms(frame_bytes))
            + math.ceil(self.airtime_ms(link_ack_frame_bytes))
        )

    def add_node(
        self,
        node_id: int,
        start: bool = True,
        *,
        position=(0.0, 0.0),
        mobile_hint: bool = False,
    ) -> TimedV2Node:
        # Recreate SharedPythonNetwork.add_node with the production-maintenance
        # node type installed before its t=0 start event is scheduled.
        node = TimedV2Node(self, node_id, mobile_hint=mobile_hint)
        self.nodes[node_id] = node
        self.register_node(node_id, up=True, position=position)
        if start:
            self.schedule(0, node.start, priority=self.PROTOCOL_PRIORITY)
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
        self._source_request_timeout_ms[
            (int(node_id), int(packet_id) & 0xFFFF)
        ] = int(timeout_ms)
        return packet_id

    def _transmit_after_cca(
        self, sender_id, target, packet, reliable, on_complete, attempt
    ):
        if reliable:
            frame_bytes = self._frame_bytes(packet)
            request_start = (
                float(self.now)
                - rssi_cca_duration_ms()
                - tx_startup_ms(frame_bytes)
            )
            deadline = (
                request_start
                + self._production_hop_timeout_base_ms(sender_id, packet)
                + math.ceil(self.airtime_ms(frame_bytes))
                + self.rng.uniform(25.0, 250.0)
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
                and self._ever_in_range(
                    other_sender, receiver, left, right, "interference"
                )
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
        missing_link = self.get_link(receiver_id, sender_id) is None
        if missing_link:
            receiver = self.nodes[receiver_id]
            if (
                receiver.up
                and not receiver.crashed
                and self.node_up.get(receiver_id, False)
            ):
                self._mark_post_read_rearm(receiver_id)

        # Production DTPK records every direct reliable LCMM transaction by
        # its next hop. A successful link ACK is bidirectional liveness evidence
        # regardless of whether the payload is DATA, ACK/NACK or control.
        def wrapped_complete(success: bool):
            if success:
                requester = self.nodes.get(sender_id)
                if requester is not None:
                    requester.last_heard[receiver_id] = self.now
                    if isinstance(requester, TimedV2Node):
                        requester.last_liveness_probe.pop(receiver_id, None)
            on_complete(success)

        return super()._start_ack_tx(
            receiver_id,
            previous_hop,
            packet,
            sender_id,
            target,
            original_packet,
            wrapped_complete,
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
            self._mark_post_read_rearm(sender_id)
        return super()._complete_ack_receive(
            sender_id, expected_epoch, on_complete
        )


__all__ = ["TimedSharedPythonNetwork", "TimedV2Node"]
