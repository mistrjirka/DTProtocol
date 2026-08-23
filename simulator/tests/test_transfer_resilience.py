from __future__ import annotations

import pathlib
import random
import struct
import sys
from typing import Dict, List, Tuple

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cpp_backend import CppNetwork, CppNodeProcess

pytestmark = pytest.mark.skipif(
    not CppNodeProcess.available(),
    reason="host C++ node not built",
)

LCMM_HEADER_BYTES = 3
DTPK_SEQ_REQ = 0x46
DTPK_FRAGMENT = 0x47
FRAGMENT_INDEX_OFFSET = LCMM_HEADER_BYTES + 13


def _random_payload(seed: int, size: int) -> bytes:
    generator = random.Random(seed)
    return bytes(generator.getrandbits(8) for _ in range(size))


def _is_fragment(payload: bytes) -> bool:
    return (
        len(payload) > FRAGMENT_INDEX_OFFSET
        and payload[LCMM_HEADER_BYTES] == DTPK_FRAGMENT
    )


def _received_payloads(net: CppNetwork, node_id: int) -> List[bytes]:
    return [
        b"" if values[3] == "-" else bytes.fromhex(values[3])
        for kind, values in net.nodes[node_id].events
        if kind == "APP_RX"
    ]


def _run_until(net: CppNetwork, predicate, deadline_ms: float) -> bool:
    while net.now < deadline_ms:
        if predicate():
            return True
        net.run(min(deadline_ms, net.now + 25.0))
    return predicate()


def _inject_when_receiving(
    net: CppNetwork,
    receiver: int,
    sender: int,
    wire_target: int,
    frame: bytes,
) -> None:
    deadline = net.now + 20_000
    while net.now <= deadline:
        before = len(net.nodes[receiver].events)
        txs = net.nodes[receiver].inject(
            net.now, sender, wire_target, frame
        )
        accepted = [
            values
            for kind, values in net.nodes[receiver].events[before:]
            if kind == "INJECTED"
        ]
        assert accepted
        if accepted[-1][0] == "1":
            net._handle_txs(receiver, txs, net.now)
            return
        net.run(min(deadline, net.now + 25.0))
    raise AssertionError("receiver never returned to an RX window")


class DestinationRebootNetwork(CppNetwork):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.rebooted = False

    def _firmware_deliver(
        self,
        sender,
        receiver,
        receiver_epoch,
        wire_target,
        payload,
    ):
        super()._firmware_deliver(
            sender,
            receiver,
            receiver_epoch,
            wire_target,
            payload,
        )
        if (
            not self.rebooted
            and sender == 1
            and receiver == 2
            and _is_fragment(payload)
            and payload[FRAGMENT_INDEX_OFFSET] == 2
        ):
            self.rebooted = True
            self.set_node_up(2, False, reason="destination-mid-multipart")
            self.schedule(
                500,
                lambda: self.set_node_up(
                    2, True, reason="destination-mid-multipart"
                ),
                priority=self.ENV_PRIORITY,
            )


class SourceRebootNetwork(CppNetwork):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.rebooted = False

    def _start_tx(self, sender, tx, at):
        result = super()._start_tx(sender, tx, at)
        if (
            not self.rebooted
            and sender == 1
            and tx.target == 2
            and _is_fragment(tx.payload)
            and tx.payload[FRAGMENT_INDEX_OFFSET] == 2
        ):
            self.rebooted = True
            # Preserve a genuine partial destination assembly, then lose all
            # source-side multipart/retry state in the reboot.
            delay = self.airtime_ms(8 + len(tx.payload)) + 1.0
            self.schedule(
                delay,
                lambda: self.set_node_up(
                    1, False, reason="source-mid-multipart"
                ),
                priority=self.ENV_PRIORITY,
            )
            self.schedule(
                delay + 500,
                lambda: self.set_node_up(
                    1, True, reason="source-mid-multipart"
                ),
                priority=self.ENV_PRIORITY,
            )
        return result


class RouteFailoverNetwork(CppNetwork):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cut = False

    def _firmware_deliver(
        self,
        sender,
        receiver,
        receiver_epoch,
        wire_target,
        payload,
    ):
        super()._firmware_deliver(
            sender,
            receiver,
            receiver_epoch,
            wire_target,
            payload,
        )
        if (
            not self.cut
            and sender == 1
            and receiver == 2
            and _is_fragment(payload)
            and payload[FRAGMENT_INDEX_OFFSET] == 1
        ):
            self.cut = True
            self.set_link(1, 2, False)


def test_destination_reboot_mid_multipart_requests_and_receives_all_missing_fragments():
    payload = _random_payload(0xD351, 1000)
    with DestinationRebootNetwork(seed=93_001, tick_ms=25) as net:
        net.add_node(1)
        net.add_node(2)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)
        net.run(60_000)

        packet_id = net.send(
            1, 2, payload, timeout_ms=300_000, e2e_ack=True
        )
        assert packet_id != 0
        assert _run_until(
            net,
            lambda: _received_payloads(net, 2) == [payload]
            and any(result == 1 for result, _ping in net.app_acks(1)),
            net.now + 400_000,
        )
        assert net.rebooted
        assert _received_payloads(net, 2) == [payload]
        assert sum(result == 1 for result, _ping in net.app_acks(1)) == 1
        assert net.routes(1).get(2) == (2, 1)
        assert net.routes(2).get(1) == (1, 1)


def test_source_reboot_mid_multipart_never_partially_delivers_and_new_incarnation_recovers():
    payload = _random_payload(0xD352, 1000)
    with SourceRebootNetwork(seed=93_002, tick_ms=25) as net:
        net.add_node(1)
        net.add_node(2)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)
        net.run(60_000)

        assert net.send(
            1, 2, payload, timeout_ms=300_000, e2e_ack=True
        ) != 0
        net.run(net.now + 150_000)
        assert net.rebooted
        assert _received_payloads(net, 2) == []

        assert _run_until(
            net,
            lambda: net.routes(1).get(2) == (2, 1),
            net.now + 120_000,
        )
        replacement = b"after-source-reboot"
        assert net.send(
            1,
            2,
            replacement,
            timeout_ms=60_000,
            e2e_ack=True,
        ) != 0
        assert _run_until(
            net,
            lambda: _received_payloads(net, 2) == [replacement]
            and any(result == 1 for result, _ping in net.app_acks(1)),
            net.now + 100_000,
        )
        assert _received_payloads(net, 2) == [replacement]


def test_multipart_selectively_repairs_over_alternate_route_after_first_path_fails():
    payload = _random_payload(0xD353, 1800)
    with RouteFailoverNetwork(seed=93_003, tick_ms=25) as net:
        for node_id in (1, 2, 3, 4):
            net.add_node(node_id)
        for left, right in ((1, 2), (2, 4), (1, 3), (3, 4)):
            net.add_link(left, right, latency_ms=0, jitter_ms=0)
        net.run(120_000)
        assert net.routes(1).get(4) == (2, 2)

        assert net.send(
            1, 4, payload, timeout_ms=500_000, e2e_ack=True
        ) != 0
        assert _run_until(
            net,
            lambda: _received_payloads(net, 4) == [payload]
            and any(result == 1 for result, _ping in net.app_acks(1)),
            net.now + 600_000,
        )
        assert net.cut
        assert net.routes(1).get(4) == (3, 2)
        assert _received_payloads(net, 4) == [payload]
        assert sum(result == 1 for result, _ping in net.app_acks(1)) == 1


def test_three_simultaneous_multipart_senders_recover_from_two_assembly_slots():
    payloads: Dict[int, bytes] = {
        sender: _random_payload(0xD400 + sender, 1000)
        for sender in (1, 2, 3)
    }
    with CppNetwork(seed=93_004, tick_ms=25) as net:
        for node_id in (1, 2, 3, 4):
            net.add_node(node_id)
        for sender in (1, 2, 3):
            net.add_link(sender, 4, latency_ms=0, jitter_ms=0)
        net.run(120_000)

        for sender in (1, 2, 3):
            assert net.send(
                sender,
                4,
                payloads[sender],
                timeout_ms=600_000,
                e2e_ack=True,
            ) != 0

        assert _run_until(
            net,
            lambda: len(_received_payloads(net, 4)) == 3
            and all(
                any(result == 1 for result, _ping in net.app_acks(sender))
                for sender in (1, 2, 3)
            ),
            net.now + 800_000,
        )
        assert set(_received_payloads(net, 4)) == set(payloads.values())
        for sender in (1, 2, 3):
            assert sum(
                result == 1 for result, _ping in net.app_acks(sender)
            ) == 1


class QueuedRouteChangeNetwork(CppNetwork):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cut = False

    def _firmware_deliver(
        self,
        sender,
        receiver,
        receiver_epoch,
        wire_target,
        payload,
    ):
        super()._firmware_deliver(
            sender,
            receiver,
            receiver_epoch,
            wire_target,
            payload,
        )
        if (
            not self.cut
            and sender == 2
            and receiver == 4
            and len(payload) > LCMM_HEADER_BYTES
            and payload[LCMM_HEADER_BYTES] == 0x41
        ):
            # The first payload has reached the destination, but its E2E ACK now
            # cannot cross the old first hop. A second source packet will wait
            # behind that E2E transaction while the selected route changes.
            self.cut = True
            self.set_link(1, 2, False)


def test_packet_queued_before_route_change_refreshes_next_hop_when_gate_opens():
    with QueuedRouteChangeNetwork(seed=93_005, tick_ms=25) as net:
        for node_id in (1, 2, 3, 4):
            net.add_node(node_id)
        for left, right in ((1, 2), (2, 4), (1, 3), (3, 4)):
            net.add_link(left, right, latency_ms=0, jitter_ms=0)
        net.run(120_000)
        assert net.routes(1).get(4) == (2, 2)

        first = b"first-before-route-change"
        assert net.send(
            1, 4, first, timeout_ms=150_000, e2e_ack=True
        ) != 0
        assert _run_until(
            net,
            lambda: _received_payloads(net, 4) == [first],
            net.now + 30_000,
        )
        assert net.cut
        assert net.routes(1).get(4) == (2, 2)

        second = b"queued-before-new-next-hop"
        assert net.send(
            1, 4, second, timeout_ms=300_000, e2e_ack=True
        ) != 0

        assert _run_until(
            net,
            lambda: net.routes(1).get(4) == (3, 2),
            net.now + 180_000,
        )
        assert _run_until(
            net,
            lambda: _received_payloads(net, 4) == [first, second]
            and len(net.app_acks(1)) >= 2
            and net.app_acks(1)[-1][0] == 1,
            net.now + 300_000,
        ), (net.nodes[1].events, net.nodes[4].events, net.routes(1))

        assert _received_payloads(net, 4) == [first, second]
        assert [result for result, _ping in net.app_acks(1)] == [1, 1]


class LongerReplacementRouteNetwork(CppNetwork):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cut = False
        self.seq_req_frames: List[Tuple[int, int, int]] = []

    def _start_tx(self, sender, tx, at):
        if (
            len(tx.payload) > LCMM_HEADER_BYTES
            and tx.payload[LCMM_HEADER_BYTES] == DTPK_SEQ_REQ
        ):
            # LCMM packet type zero is a one-shot DATA_NOACK frame. Record the
            # selected next hop as well so directed repair remains exercised.
            self.seq_req_frames.append((sender, tx.target, tx.payload[0]))
        return super()._start_tx(sender, tx, at)

    def _firmware_deliver(
        self,
        sender,
        receiver,
        receiver_epoch,
        wire_target,
        payload,
    ):
        super()._firmware_deliver(
            sender,
            receiver,
            receiver_epoch,
            wire_target,
            payload,
        )
        if (
            not self.cut
            and sender == 1
            and receiver == 2
            and _is_fragment(payload)
            and payload[FRAGMENT_INDEX_OFFSET] == 1
        ):
            self.cut = True
            self.set_link(1, 2, False)


def test_sequence_repair_can_install_longer_route_during_active_multipart():
    # The original two-hop path disappears after fragment 1. The surviving path
    # is three hops, so feasibility requires an active SEQ_REQ repair wave. That
    # control traffic must not wait until the application transaction times out.
    payload = _random_payload(0xD354, 1800)
    with LongerReplacementRouteNetwork(seed=93_006, tick_ms=25) as net:
        for node_id in (1, 2, 3, 4, 5):
            net.add_node(node_id)
        for left, right in (
            (1, 2),
            (2, 4),
            (1, 3),
            (3, 5),
            (5, 4),
        ):
            net.add_link(left, right, latency_ms=0, jitter_ms=0)
        net.run(180_000)
        assert net.routes(1).get(4) == (2, 2)

        assert net.send(
            1, 4, payload, timeout_ms=700_000, e2e_ack=True
        ) != 0
        assert _run_until(
            net,
            lambda: _received_payloads(net, 4) == [payload]
            and any(result == 1 for result, _ping in net.app_acks(1)),
            net.now + 800_000,
        ), (net.routes(1), net.nodes[1].events, net.nodes[4].events)
        assert net.cut
        assert net.routes(1).get(4) == (3, 3)
        assert _received_payloads(net, 4) == [payload]
        assert sum(result == 1 for result, _ping in net.app_acks(1)) == 1
        assert net.seq_req_frames, "the longer route must require sequence repair"
        assert all(
            lcmm_type == 0
            for _sender, _target, lcmm_type in net.seq_req_frames
        )
        assert any(
            target != 0
            for _sender, target, _lcmm_type in net.seq_req_frames
        )


class SingleFirstHopFailoverNetwork(CppNetwork):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cut = False
        self.source_attempts = 0
        self.source_routers: List[int] = []

    def _start_tx(self, sender, tx, at):
        packet_type = (
            tx.payload[LCMM_HEADER_BYTES]
            if len(tx.payload) > LCMM_HEADER_BYTES
            else None
        )
        if sender == 1 and packet_type == 0x41:
            self.source_attempts += 1
            self.source_routers.append(tx.target)
        if (
            not self.cut
            and sender == 1
            and tx.target == 2
            and packet_type == 0x41
        ):
            self.cut = True
            self.set_link(1, 2, False)
        return super()._start_tx(sender, tx, at)


class SingleDownstreamFailoverNetwork(CppNetwork):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cut = False
        self.source_routers: List[int] = []

    def _start_tx(self, sender, tx, at):
        packet_type = (
            tx.payload[LCMM_HEADER_BYTES]
            if len(tx.payload) > LCMM_HEADER_BYTES
            else None
        )
        if sender == 1 and packet_type == 0x41:
            self.source_routers.append(tx.target)
        if (
            not self.cut
            and sender == 2
            and tx.target == 4
            and packet_type == 0x41
        ):
            self.cut = True
            self.set_link(2, 4, False)
        return super()._start_tx(sender, tx, at)


def test_single_frame_retries_same_identity_after_first_hop_route_fails():
    payload = b"single-first-hop-failover"
    with SingleFirstHopFailoverNetwork(seed=93_007, tick_ms=25) as net:
        for node_id in (1, 2, 3, 4):
            net.add_node(node_id)
        for left, right in ((1, 2), (2, 4), (1, 3), (3, 4)):
            net.add_link(left, right, latency_ms=0, jitter_ms=0)
        net.run(120_000)
        assert net.routes(1).get(4) == (2, 2)

        assert net.send(
            1, 4, payload, timeout_ms=400_000, e2e_ack=True
        ) != 0
        assert _run_until(
            net,
            lambda: _received_payloads(net, 4) == [payload]
            and any(result == 1 for result, _ping in net.app_acks(1)),
            net.now + 500_000,
        )
        assert net.cut
        # One failed LCMM exchange is not authoritative topology withdrawal.
        # The transaction must use the feasible alternate immediately, while
        # the suspect selected route may remain until hard liveness expiry.
        assert net.routes(1).get(4) in {(2, 2), (3, 2)}
        assert 2 in net.source_routers and 3 in net.source_routers
        assert _received_payloads(net, 4) == [payload]
        assert sum(result == 1 for result, _ping in net.app_acks(1)) == 1
        assert net.source_attempts > 1


def test_single_frame_recovers_when_failure_occurs_after_first_hop_ack():
    payload = b"single-downstream-failover"
    with SingleDownstreamFailoverNetwork(seed=93_008, tick_ms=25) as net:
        for node_id in (1, 2, 3, 4):
            net.add_node(node_id)
        for left, right in ((1, 2), (2, 4), (1, 3), (3, 4)):
            net.add_link(left, right, latency_ms=0, jitter_ms=0)
        net.run(120_000)
        assert net.routes(1).get(4) == (2, 2)

        assert net.send(
            1, 4, payload, timeout_ms=400_000, e2e_ack=True
        ) != 0
        assert _run_until(
            net,
            lambda: _received_payloads(net, 4) == [payload]
            and any(result == 1 for result, _ping in net.app_acks(1)),
            net.now + 500_000,
        )
        assert net.cut
        assert net.routes(1).get(4) in {(2, 2), (3, 2)}
        assert 2 in net.source_routers and 3 in net.source_routers
        assert _received_payloads(net, 4) == [payload]
        assert sum(result == 1 for result, _ping in net.app_acks(1)) == 1


class LostFirstMessageAckNetwork(CppNetwork):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ack_attempts_dropped = 0
        self.source_lcmm_ids: List[int] = []
        self.source_dtpk_ids: List[int] = []

    def _start_tx(self, sender, tx, at):
        packet_type = (
            tx.payload[LCMM_HEADER_BYTES]
            if len(tx.payload) > LCMM_HEADER_BYTES
            else None
        )
        if sender == 1 and packet_type == 0x41:
            self.source_lcmm_ids.append(
                int.from_bytes(tx.payload[1:3], "little")
            )
            self.source_dtpk_ids.append(
                int.from_bytes(tx.payload[4:6], "little")
            )
        if (
            sender == 2
            and tx.target == 1
            and packet_type == 0x42
            and self.ack_attempts_dropped < 5
        ):
            self.drop_next_frames(2, 1, 1)
            self.ack_attempts_dropped += 1
        return super()._start_tx(sender, tx, at)


def test_lost_complete_e2e_ack_retries_same_identity_and_delivers_once():
    payload = b"lost-first-whole-message-ack"
    with LostFirstMessageAckNetwork(seed=93_009, tick_ms=25) as net:
        net.add_node(1)
        net.add_node(2)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)
        net.run(60_000)

        assert net.send(
            1, 2, payload, timeout_ms=180_000, e2e_ack=True
        ) != 0
        assert _run_until(
            net,
            lambda: _received_payloads(net, 2) == [payload]
            and any(result == 1 for result, _ping in net.app_acks(1)),
            net.now + 240_000,
        )
        assert net.ack_attempts_dropped == 5
        assert _received_payloads(net, 2) == [payload]
        assert len(set(net.source_lcmm_ids)) >= 2
        assert len(set(net.source_dtpk_ids)) == 1
        assert [result for result, _ping in net.app_acks(1)] == [1]


def test_ack_callback_can_reentrantly_enqueue_next_message():
    first = b"first-before-reentrant-callback"
    second = b"second-from-ack-callback"
    with CppNetwork(seed=93_010, tick_ms=25) as net:
        net.add_node(1)
        net.add_node(2)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)
        net.run(60_000)

        assert net.send_chain(
            1,
            2,
            first,
            second,
            timeout_ms=90_000,
        ) != 0
        assert _run_until(
            net,
            lambda: _received_payloads(net, 2) == [first, second]
            and any(
                kind == "CHAIN_ACK" and int(values[0]) == 1
                for kind, values in net.nodes[1].events
            ),
            net.now + 180_000,
        ), (net.nodes[1].events, net.nodes[2].events)
        assert _received_payloads(net, 2) == [first, second]
        assert any(
            kind == "CHAINID" and int(values[0]) != 0
            for kind, values in net.nodes[1].events
        )


class HeldOwnAckNetwork(CppNetwork):
    """Hold only the ACK addressed to node 2 at relay 3."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.held = []

    def _rf_complete(
        self,
        sender,
        sender_epoch,
        receiver,
        receiver_epoch,
        wire_target,
        link_epoch,
        rf_start,
        rf_end,
        latency_ms,
        payload,
    ):
        is_ack_to_two = (
            sender == 3
            and receiver == 2
            and len(payload) >= LCMM_HEADER_BYTES + 11
            and payload[LCMM_HEADER_BYTES] == 0x42
            and int.from_bytes(payload[10:12], "little") == 2
        )
        if is_ack_to_two:
            self.held.append(
                (sender, receiver, receiver_epoch, wire_target, payload)
            )
            return
        return super()._rf_complete(
            sender,
            sender_epoch,
            receiver,
            receiver_epoch,
            wire_target,
            link_epoch,
            rf_start,
            rf_end,
            latency_ms,
            payload,
        )

    def release(self):
        pending = self.held
        self.held = []
        for sender, receiver, receiver_epoch, wire_target, payload in pending:
            if (
                self.node_up.get(receiver, False)
                and self.node_epoch.get(receiver, 0) == receiver_epoch
            ):
                txs = self.nodes[receiver].inject(
                    self.now, sender, wire_target, payload
                )
                self._handle_txs(receiver, txs, self.now)


def test_relay_service_is_not_blocked_by_relays_own_pending_e2e_ack():
    with HeldOwnAckNetwork(seed=96_001, tick_ms=25) as net:
        for node_id in (1, 2, 3, 4):
            net.add_node(node_id)
        for left, right in ((1, 2), (2, 3), (3, 4)):
            net.add_link(left, right, latency_ms=0, jitter_ms=0)
        net.run(120_000)

        own_payload = b"node2-own-transaction"
        assert net.send(
            2, 4, own_payload, timeout_ms=180_000, e2e_ack=True
        ) != 0
        assert _run_until(
            net,
            lambda: _received_payloads(net, 4) == [own_payload]
            and bool(net.held),
            net.now + 90_000,
        )
        assert net.app_acks(2) == []

        relayed_payload = b"node1-through-busy-relay"
        assert net.send(
            1, 4, relayed_payload, timeout_ms=180_000, e2e_ack=True
        ) != 0
        assert _run_until(
            net,
            lambda: relayed_payload in _received_payloads(net, 4)
            and any(result == 1 for result, _ping in net.app_acks(1)),
            net.now + 60_000,
        ), (net.nodes[1].events, net.nodes[2].events, net.nodes[4].events)
        assert net.app_acks(2) == []

        net.release()
        assert _run_until(
            net,
            lambda: any(result == 1 for result, _ping in net.app_acks(2)),
            net.now + 60_000,
        )
        assert _received_payloads(net, 4) == [own_payload, relayed_payload]


class LostEndToEndAckNetwork(CppNetwork):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dropped_ack_attempts = 0
        self.source_data_ids: List[int] = []

    def _start_tx(self, sender, tx, at):
        packet_type = (
            tx.payload[LCMM_HEADER_BYTES]
            if len(tx.payload) > LCMM_HEADER_BYTES
            else None
        )
        if sender == 1 and tx.target == 2 and packet_type == 0x41:
            if len(tx.payload) >= LCMM_HEADER_BYTES:
                self.source_data_ids.append(
                    int.from_bytes(tx.payload[1:3], "little")
                )
        if (
            sender == 2
            and tx.target == 1
            and packet_type == 0x42
            and self.dropped_ack_attempts < 5
        ):
            self.drop_next_frames(2, 1, 1)
            self.dropped_ack_attempts += 1
        return super()._start_tx(sender, tx, at)


def test_lost_complete_e2e_ack_transaction_retries_same_data_identity_once():
    payload = b"deliver-once-when-first-e2e-ack-is-lost"
    with LostEndToEndAckNetwork(seed=93_009, tick_ms=25) as net:
        net.add_node(1)
        net.add_node(2)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)
        net.run(60_000)

        assert net.send(
            1, 2, payload, timeout_ms=180_000, e2e_ack=True
        ) != 0
        assert _run_until(
            net,
            lambda: _received_payloads(net, 2) == [payload]
            and any(result == 1 for result, _ping in net.app_acks(1)),
            net.now + 220_000,
        ), (net.nodes[1].events, net.nodes[2].events)

        assert net.dropped_ack_attempts == 5
        assert _received_payloads(net, 2) == [payload]
        assert sum(result == 1 for result, _ping in net.app_acks(1)) == 1
        # LCMM uses a fresh link transaction ID, while DTPK reuses its original
        # application identity. At least two source transactions prove the E2E
        # retry happened; exactly-once delivery proves replay suppression held.
        assert len(set(net.source_data_ids)) >= 2


def test_unrelated_high_rate_traffic_cannot_evict_active_retry_identity():
    original = b"original-replay-window"
    with LostFirstMessageAckNetwork(
        seed=93_011,
        tick_ms=5,
        sf=7,
    ) as net:
        for node_id in (1, 2, 3):
            net.add_node(node_id)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)
        net.add_link(3, 2, latency_ms=0, jitter_ms=0)
        net.run(80_000)

        assert net.send(
            1, 2, original, timeout_ms=180_000, e2e_ack=True
        ) != 0
        assert _run_until(
            net,
            lambda: _received_payloads(net, 2) == [original]
            and net.ack_attempts_dropped == 5,
            net.now + 30_000,
        )

        # More than the former global 64-entry ring receives unrelated traffic
        # before source 1's whole-message retry. A per-source replay window must
        # retain source 1 independently.
        source_sequence = net._node_origin_sequence[3]
        for index in range(96):
            # Feed valid over-the-air DATA identities directly rather than
            # violating node 3's bounded local-application admission contract.
            # All 96 reach node 2 before source 1's >=35 s message retry.
            frame = (
                struct.pack("<BH", 0, 0xA000 + index)
                + struct.pack(
                    "<BHHHHBB",
                    0x41,
                    0xB000 + index,
                    source_sequence,
                    3,
                    2,
                    0,
                    255,
                )
                + bytes([index])
            )
            _inject_when_receiving(net, 2, 3, 2, frame)
        assert len(_received_payloads(net, 2)) == 97
        net.run(net.now + 70_000)

        received = _received_payloads(net, 2)
        assert len(received) == 97
        assert received.count(original) == 1
        assert [result for result, _ping in net.app_acks(1)] == [1]


class NewNeighborRepairNetwork(CppNetwork):
    """Hide one alternate neighbor until an active transfer needs it."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cut = False
        self.hold_control = True
        self.hello_from_three = None
        self.cryst_requests_to_three: List[float] = []
        self.source_data_routers: List[int] = []

    @staticmethod
    def _packet_type(payload: bytes):
        return payload[LCMM_HEADER_BYTES] if len(payload) > LCMM_HEADER_BYTES else None

    def _start_tx(self, sender, tx, at):
        packet_type = self._packet_type(tx.payload)
        if sender == 3 and packet_type == 0x44:
            self.hello_from_three = tx.payload
        if sender == 1 and tx.target == 3 and packet_type == 0x45:
            self.cryst_requests_to_three.append(self.now)
        if sender == 1 and packet_type == 0x41:
            self.source_data_routers.append(tx.target)
        if (
            not self.cut
            and sender == 2
            and tx.target == 4
            and packet_type == 0x41
        ):
            self.cut = True
            self.set_link(2, 4, False)
        return super()._start_tx(sender, tx, at)

    def _rf_complete(
        self,
        sender,
        sender_epoch,
        receiver,
        receiver_epoch,
        wire_target,
        link_epoch,
        rf_start,
        rf_end,
        latency_ms,
        payload,
    ):
        if (
            self.hold_control
            and sender == 3
            and receiver == 1
            and self._packet_type(payload) in (0x40, 0x44)
        ):
            return
        return super()._rf_complete(
            sender,
            sender_epoch,
            receiver,
            receiver_epoch,
            wire_target,
            link_epoch,
            rf_start,
            rf_end,
            latency_ms,
            payload,
        )


def test_failed_transfer_can_request_new_neighbor_snapshot_and_avoid_failed_router():
    payload = b"needs-new-neighbor-snapshot"
    with NewNeighborRepairNetwork(seed=93_012, tick_ms=25) as net:
        for node_id in (1, 2, 3, 4):
            net.add_node(node_id)
        for left, right in ((1, 2), (2, 4), (1, 3), (3, 4)):
            net.add_link(left, right, latency_ms=0, jitter_ms=0)
        net.run(120_000)
        assert net.hello_from_three is not None
        assert net.routes(1).get(4) == (2, 2)

        assert net.send(
            1, 4, payload, timeout_ms=160_000, e2e_ack=True
        ) != 0
        assert _run_until(net, lambda: net.cut, net.now + 30_000)

        # Reveal only node 3's HELLO during the active transaction. Node 1 has a
        # direct route to 3 but lacks its snapshot/route to 4. A failed transfer
        # makes this CRYST_REQ urgent rather than deferring it to the E2E timeout.
        txs = net.nodes[1].inject(
            net.now, 3, 0, net.hello_from_three
        )
        net._handle_txs(1, txs, net.now)
        assert _run_until(
            net,
            lambda: bool(net.cryst_requests_to_three),
            net.now + 20_000,
        )
        assert net.cryst_requests_to_three[0] < 160_000

        net.hold_control = False
        assert _run_until(
            net,
            lambda: _received_payloads(net, 4) == [payload]
            and any(result == 1 for result, _ping in net.app_acks(1)),
            net.now + 120_000,
        ), (net.nodes[1].events, net.nodes[4].events)
        assert net.cut
        assert 2 in net.source_data_routers
        assert 3 in net.source_data_routers
        assert _received_payloads(net, 4) == [payload]
        assert [result for result, _ping in net.app_acks(1)] == [1]


class FatalRelaySubmissionNetwork(CppNetwork):
    """Fail relay 2's first DATA submission after ACKing source 1."""

    MAC_SEND_RADIO_ERROR = 4

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.armed = False
        self.injected = False
        self.source_lcmm_id = None
        self.trigger_ack_token = None
        self.source_routers = []

    def _start_tx(self, sender, tx, at):
        packet_kind = (
            tx.payload[3]
            if len(tx.payload) > 3
            else None
        )
        if (
            self.armed
            and sender == 1
            and tx.target in (2, 4)
            and packet_kind == 0x41
        ):
            self.source_routers.append(tx.target)
            if tx.target == 2 and self.source_lcmm_id is None:
                self.source_lcmm_id = int.from_bytes(tx.payload[1:3], "little")
        if (
            self.armed
            and not self.injected
            and sender == 2
            and tx.target == 1
            and tx.payload
            and tx.payload[0] == 4
            and len(tx.payload) >= 3
            and self.source_lcmm_id is not None
            and int.from_bytes(tx.payload[1:3], "little") == self.source_lcmm_id
        ):
            self.trigger_ack_token = tx.token
        return super()._start_tx(sender, tx, at)

    def _phy_done(self, sender, sender_epoch, token):
        if (
            sender == 2
            and token == self.trigger_ack_token
            and not self.injected
        ):
            # PHY completion releases the received DTPK frame upward. Make the
            # immediately following relay submission fail before any LCMM ID or
            # relay-failure map entry can be created.
            self.nodes[2].force_next_send_result(self.MAC_SEND_RADIO_ERROR)
            self.injected = True
        return super()._phy_done(sender, sender_epoch, token)


def test_fatal_relay_submission_nacks_source_and_uses_alternate_route():
    payload = b"fatal-relay-submission-failover"
    with FatalRelaySubmissionNetwork(seed=96_002, tick_ms=25) as net:
        for node_id in (1, 2, 3, 4):
            net.add_node(node_id)
        for left, right in ((1, 2), (2, 3), (1, 4), (4, 3)):
            net.add_link(left, right, latency_ms=0, jitter_ms=0)
        net.run(120_000)
        assert net.routes(1).get(3) == (2, 2)

        net.armed = True
        assert net.send(
            1, 3, payload, timeout_ms=300_000, e2e_ack=True
        ) != 0
        assert _run_until(
            net,
            lambda: _received_payloads(net, 3) == [payload]
            and any(result == 1 for result, _ping in net.app_acks(1)),
            net.now + 360_000,
        ), (net.nodes[1].events, net.nodes[2].events, net.source_routers)
        assert net.injected
        assert 2 in net.source_routers and 4 in net.source_routers
        assert _received_payloads(net, 3) == [payload]
        assert sum(result == 1 for result, _ping in net.app_acks(1)) == 1
