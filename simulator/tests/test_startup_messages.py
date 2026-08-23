from __future__ import annotations

import pathlib
import random
import struct
from collections import Counter
import sys
from typing import Callable, Dict, List, Tuple

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cpp_backend import CppNetwork, CppNodeProcess

pytestmark = pytest.mark.skipif(
    not CppNodeProcess.available(),
    reason="host C++ node not built",
)

DTPK_TYPES = {
    0x40: "CRYST",
    0x41: "DATA",
    0x42: "ACK",
    0x43: "NACK",
    0x44: "HELLO",
    0x45: "CRYST_REQ",
    0x46: "SEQ_REQ",
    0x47: "FRAGMENT",
    0x48: "FRAG_STATUS",
    0x49: "FRAG_QUERY",
}
LCMM_HEADER_SIZE = 3
DTPK_FLAG_E2E_ACK_REQUESTED = 0x01
DTPK_FLAG_COMPRESSED = 0x02
DTPK_FLAG_DEBUG_ECHO = 0x04


def _random_payload(seed: int, size: int) -> bytes:
    generator = random.Random(seed)
    return bytes(generator.getrandbits(8) for _ in range(size))


def _kind(payload: bytes) -> str:
    if len(payload) < LCMM_HEADER_SIZE + 1:
        return f"LCMM_{payload[0] if payload else 'empty'}"
    return DTPK_TYPES.get(payload[LCMM_HEADER_SIZE], "UNKNOWN")


def _received_payloads(net: CppNetwork, node_id: int) -> List[bytes]:
    return [
        b"" if values[3] == "-" else bytes.fromhex(values[3])
        for kind, values in net.nodes[node_id].events
        if kind == "APP_RX"
    ]


def _received_flags(net: CppNetwork, node_id: int) -> List[int]:
    return [
        int(values[4])
        for kind, values in net.nodes[node_id].events
        if kind == "APP_RX"
    ]


class HeldStartupControlNetwork(CppNetwork):
    """Delay selected control frames while allowing application traffic.

    PHY completion still happens at the sender. The receiver sees the retained
    frame only when ``release_all`` is called, modeling crossed/lost startup
    state without stalling the transmitting firmware's MAC state machine.
    """

    def __init__(
        self,
        *args,
        hold: Callable[[int, int, str], bool],
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.hold = hold
        self.held: List[Tuple[int, int, int, int, bytes]] = []
        self.trace: List[Tuple[float, str, int, int, str]] = []
        self.routed_frames: List[
            Tuple[float, int, int, str, int, int, int, int, int]
        ] = []

    def _start_tx(self, sender, tx, at):
        packet_kind = _kind(tx.payload)
        self.trace.append((self.now, "TX", sender, tx.target, packet_kind))
        if (
            packet_kind in {
                "DATA", "FRAGMENT", "FRAG_QUERY", "FRAG_STATUS", "ACK", "NACK"
            }
            and len(tx.payload) >= LCMM_HEADER_SIZE + 11
        ):
            self.routed_frames.append(
                (
                    self.now,
                    sender,
                    tx.target,
                    packet_kind,
                    tx.payload[12],
                    tx.payload[13],
                    int.from_bytes(tx.payload[8:10], "little"),
                    int.from_bytes(tx.payload[10:12], "little"),
                    int.from_bytes(tx.payload[4:6], "little"),
                )
            )
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
        packet_kind = _kind(payload)
        if self.hold(sender, receiver, packet_kind):
            self.held.append(
                (sender, receiver, receiver_epoch, wire_target, payload)
            )
            self.trace.append((self.now, "HOLD", sender, receiver, packet_kind))
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

    def release_all(self) -> None:
        pending = self.held
        self.held = []
        for sender, receiver, receiver_epoch, wire_target, payload in pending:
            if (
                not self.node_up.get(receiver, False)
                or self.node_epoch.get(receiver, 0) != receiver_epoch
            ):
                continue
            txs = self.nodes[receiver].inject(
                self.now, sender, wire_target, payload
            )
            self._handle_txs(receiver, txs, self.now)


def _run_until(net: CppNetwork, predicate, deadline_ms: float) -> bool:
    while net.now < deadline_ms:
        if predicate():
            return True
        net.run(min(deadline_ms, net.now + 25.0))
    return predicate()





class CrystResponseTraceNetwork(CppNetwork):
    def __init__(self, *args, drop_broadcast_cryst: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self.drop_broadcast_cryst = drop_broadcast_cryst
        self.cryst_frames: List[
            Tuple[float, int, int, int, int, int, int]
        ] = []

    def _start_tx(self, sender, tx, at):
        if _kind(tx.payload) == "CRYST" and len(tx.payload) >= 14:
            self.cryst_frames.append(
                (
                    self.now,
                    sender,
                    tx.target,
                    tx.payload[0],
                    int.from_bytes(tx.payload[10:12], "little"),
                    int.from_bytes(tx.payload[12:14], "little"),
                    int.from_bytes(tx.payload[1:3], "little"),
                )
            )
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
            self.drop_broadcast_cryst
            and wire_target == 0
            and _kind(payload) == "CRYST"
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


def _synthetic_empty_cryst_frame(
    lcmm_id: int,
    origin_sequence: int,
    route_version: int = 1,
) -> bytes:
    return struct.pack(
        "<BHBHIHH",
        0,  # LCMM DATA without link ACK for synthetic injection
        lcmm_id,
        0x40,
        origin_sequence,
        route_version,
        0,
        1,
    )


def _synthetic_cryst_request_frame(
    lcmm_id: int,
    origin_sequence: int,
    route_version: int = 1,
) -> bytes:
    return struct.pack(
        "<BHBHI",
        0,
        lcmm_id,
        0x45,
        origin_sequence,
        route_version,
    )


def test_direct_reliable_snapshot_converges_when_all_broadcast_cryst_is_lost():
    with CrystResponseTraceNetwork(
        seed=91_019,
        tick_ms=25,
        drop_broadcast_cryst=True,
    ) as net:
        net.add_node(1)
        net.add_node(2)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)

        assert _run_until(
            net,
            lambda: net.routes(1).get(2) == (2, 1)
            and net.routes(2).get(1) == (1, 1),
            90_000,
        ), (net.routes(1), net.routes(2), net.cryst_frames[-80:])

        direct = [frame for frame in net.cryst_frames if frame[2] != 0]
        assert direct
        assert all(frame[3] == 1 for frame in direct), direct
        # One HELLO + its request/response is sufficient: the request digest
        # establishes one direction and the direct snapshot establishes the other.
        assert {(frame[1], frame[2]) for frame in direct} & {(1, 2), (2, 1)}


def test_large_cryst_request_returns_every_chunk_directly_and_reliably():
    with CrystResponseTraceNetwork(seed=91_020, tick_ms=25) as net:
        net.add_node(1)

        # Seed 47 direct contributions without creating 47 host processes. A v4
        # CRYST header can carry at most 46 five-byte route records, so node 1's
        # requested response must contain exactly two chunks.
        for sender in range(2, 49):
            _inject_frame(
                net,
                1,
                sender,
                1,
                _synthetic_empty_cryst_frame(
                    0xA000 + sender, 0x1000 + sender
                ),
            )
        assert len(net.routes(1)) == 47

        net.cryst_frames.clear()
        _inject_frame(
            net,
            1,
            2,
            1,
            _synthetic_cryst_request_frame(0xBEEF, 0x1002),
        )
        # There is intentionally no host node 2 to acknowledge the response.
        # Each direct chunk therefore exercises all five LCMM attempts before
        # the next queued chunk can proceed.
        net.run(net.now + 90_000)

        direct = [
            frame
            for frame in net.cryst_frames
            if frame[1] == 1 and frame[2] == 2
        ]
        assert direct, net.cryst_frames[-100:]
        assert {frame[5] for frame in direct} == {2}
        assert {frame[4] for frame in direct} == {0, 1}
        assert all(frame[3] == 1 for frame in direct)
        attempts = Counter(frame[4] for frame in direct)
        assert attempts == Counter({0: 5, 1: 5})


def test_send_before_any_route_is_rejected_visibly_but_does_not_block_convergence():
    with CppNetwork(seed=91_000, tick_ms=25) as net:
        net.add_node(1)
        net.add_node(2)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)

        # There is no implicit unbounded pre-route application queue. The API
        # rejects immediately and invokes the supplied failure callback.
        assert net.send(
            1, 2, b"too-early", timeout_ms=30_000, e2e_ack=True
        ) == 0
        assert net.app_acks(1) == [(0, 0)]

        net.run(60_000)
        assert net.routes(1).get(2) == (2, 1)
        assert net.routes(2).get(1) == (1, 1)

        packet_id = net.send(
            1, 2, b"after-route", timeout_ms=30_000, e2e_ack=True
        )
        assert packet_id != 0
        assert _run_until(
            net,
            lambda: _received_payloads(net, 2) == [b"after-route"]
            and net.app_acks(1)[-1][0] == 1,
            net.now + 40_000,
        )


@pytest.mark.parametrize(
    "early_payload",
    [
        b"early-direct",
        _random_payload(0xE411, 1000),
        b"startup-compressible-state=online;" * 80,
    ],
    ids=["single-frame", "multipart", "compressed"],
)
def test_direct_data_during_asymmetric_crystallization_delivers_and_repairs_reverse_route(
    early_payload: bytes,
):
    # Node 1 hears node 2's HELLO, but node 2 cannot receive node 1's HELLO,
    # CRYST, or CRYST_REQ. Node 1 therefore has a direct route while node 2 has
    # not committed any reciprocal snapshot when DATA arrives.
    held_keys = {
        (1, 2, "HELLO"),
        (1, 2, "CRYST"),
        (1, 2, "CRYST_REQ"),
        (2, 1, "CRYST"),
    }
    with HeldStartupControlNetwork(
        seed=91_001,
        tick_ms=25,
        hold=lambda sender, receiver, packet_kind: (
            sender,
            receiver,
            packet_kind,
        )
        in held_keys,
    ) as net:
        net.add_node(1)
        net.add_node(2)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)

        assert _run_until(
            net,
            lambda: 2 in net.routes(1) and 1 not in net.routes(2),
            20_000,
        ), (net.routes(1), net.routes(2), net.trace[-40:])
        assert net.held, "the scenario must still have uncommitted startup state"

        packet_id = net.send(
            1,
            2,
            early_payload,
            timeout_ms=180_000,
            e2e_ack=True,
        )
        assert packet_id != 0
        assert _run_until(
            net,
            lambda: _received_payloads(net, 2) == [early_payload]
            and any(result == 1 for result, _ping in net.app_acks(1)),
            net.now + 240_000,
        ), (net.nodes[1].events, net.nodes[2].events, net.trace[-80:])

        # Direct application DATA now proves the reverse one-hop route even
        # before the delayed CRYST/CRYST_REQ frames are released.
        assert net.routes(2).get(1) == (1, 1)
        reverse_id = net.send(
            2,
            1,
            b"early-reply",
            timeout_ms=30_000,
            e2e_ack=True,
        )
        assert reverse_id != 0
        assert _run_until(
            net,
            lambda: _received_payloads(net, 1) == [b"early-reply"]
            and any(result == 1 for result, _ping in net.app_acks(2)),
            net.now + 40_000,
        ), (net.nodes[1].events, net.nodes[2].events, net.trace[-80:])

        # Release the startup state only after both application transactions
        # completed. Crystallization must still converge normally afterward.
        net.release_all()
        net.hold = lambda _s, _r, _k: False
        net.run(net.now + 30_000)
        assert net.routes(1).get(2) == (2, 1)
        assert net.routes(2).get(1) == (1, 1)
        assert not net.held
        assert all(result == 1 for result, _ping in net.app_acks(1))
        assert all(result == 1 for result, _ping in net.app_acks(2))




class DropHelloLearnedCrystRequestNetwork(CppNetwork):
    """Lose the request caused by the first HELLO, then allow DATA repair."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.first_hello_delivered = False
        self.dropped_requests = 0
        self.allowed_requests = 0
        self.allow_snapshot = False
        self.delivered_snapshots = 0

    def _rf_complete(
        self, sender, sender_epoch, receiver, receiver_epoch, wire_target,
        link_epoch, rf_start, rf_end, latency_ms, payload,
    ):
        packet_kind = _kind(payload)
        if sender == 1 and receiver == 2 and packet_kind == "HELLO":
            if self.first_hello_delivered and not self.allow_snapshot:
                return
            self.first_hello_delivered = True
        if (sender, receiver, packet_kind) == (1, 2, "CRYST") \
                and not self.allow_snapshot:
            return
        if (sender, receiver, packet_kind) == (2, 1, "CRYST_REQ"):
            if self.dropped_requests < 5:
                self.dropped_requests += 1
                return
            self.allowed_requests += 1
            self.allow_snapshot = True
        if (sender, receiver, packet_kind) == (1, 2, "CRYST") \
                and self.allow_snapshot:
            self.delivered_snapshots += 1
        return super()._rf_complete(
            sender, sender_epoch, receiver, receiver_epoch, wire_target,
            link_epoch, rf_start, rf_end, latency_ms, payload,
        )


def test_data_retries_request_lost_after_direct_route_was_learned_from_hello():
    with DropHelloLearnedCrystRequestNetwork(seed=91_019, tick_ms=25) as net:
        net.add_node(1)
        net.add_node(2)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)

        assert _run_until(
            net,
            lambda: net.first_hello_delivered
            and net.routes(2).get(1) == (1, 1)
            and net.routes(1).get(2) == (2, 1)
            and net.dropped_requests == 5,
            50_000,
        )
        assert net.allowed_requests == 0
        assert net.delivered_snapshots == 0

        payload = b"data-retries-hello-request"
        assert net.send(1, 2, payload, timeout_ms=90_000, e2e_ack=True) != 0
        assert _run_until(
            net,
            lambda: _received_payloads(net, 2) == [payload]
            and net.allowed_requests >= 1
            and net.delivered_snapshots >= 1
            and any(result == 1 for result, _ping in net.app_acks(1)),
            net.now + 120_000,
        ), (net.nodes[1].events, net.nodes[2].events)

        requests_after_commit = net.allowed_requests
        net.run(net.now + 25_000)
        assert net.allowed_requests == requests_after_commit
        assert net.routes(1).get(2) == (2, 1)
        assert net.routes(2).get(1) == (1, 1)


class DropFirstProvisionalCrystRequestNetwork(CppNetwork):
    """Lose one complete reliable CRYST_REQ transaction after DATA learning."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dropped_requests = 0
        self.allowed_requests = 0
        self.delivered_snapshots = 0
        self.allow_snapshot = False
        self.trace: List[Tuple[float, str, int, int, str]] = []

    def _start_tx(self, sender, tx, at):
        self.trace.append((self.now, "TX", sender, tx.target, _kind(tx.payload)))
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
        packet_kind = _kind(payload)
        # Prevent ordinary startup control from completing node 2's view before
        # the provisional DATA-driven repair path is exercised.
        if (
            sender == 1
            and receiver == 2
            and packet_kind in {"HELLO", "CRYST", "CRYST_REQ"}
            and not self.allow_snapshot
        ):
            return

        if sender == 2 and receiver == 1 and packet_kind == "CRYST_REQ":
            if self.dropped_requests < 5:
                self.dropped_requests += 1
                return
            self.allowed_requests += 1
            self.allow_snapshot = True

        if (
            sender == 1
            and receiver == 2
            and packet_kind == "CRYST"
            and self.allow_snapshot
        ):
            self.delivered_snapshots += 1

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


def test_same_incarnation_data_retries_lost_provisional_snapshot_request():
    with DropFirstProvisionalCrystRequestNetwork(
        seed=91_018, tick_ms=25
    ) as net:
        net.add_node(1)
        net.add_node(2)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)

        # Node 1 learns node 2 from node 2's ordinary startup traffic, while all
        # reciprocal state into node 2 is withheld.
        assert _run_until(
            net,
            lambda: 2 in net.routes(1) and 1 not in net.routes(2),
            20_000,
        )

        first = b"provisional-request-lost"
        assert net.send(
            1, 2, first, timeout_ms=60_000, e2e_ack=False
        ) != 0
        assert _run_until(
            net,
            lambda: _received_payloads(net, 2) == [first]
            and net.routes(2).get(1) == (1, 1)
            and net.dropped_requests == 5,
            net.now + 40_000,
        ), (net.trace[-120:], net.nodes[1].events, net.nodes[2].events)
        assert net.allowed_requests == 0
        assert net.delivered_snapshots == 0

        # The direct route is already provisional with the same incarnation.
        # A second valid DATA frame must nevertheless enqueue a fresh request.
        second = b"same-incarnation-retries-request"
        assert net.send(
            1, 2, second, timeout_ms=60_000, e2e_ack=False
        ) != 0
        assert _run_until(
            net,
            lambda: _received_payloads(net, 2) == [first, second]
            and net.allowed_requests >= 1
            and net.delivered_snapshots >= 1,
            net.now + 90_000,
        ), (net.trace[-180:], net.nodes[1].events, net.nodes[2].events)

        # Once a nonzero route version is committed, later matching HELLOs do
        # not continuously regenerate CRYST_REQ traffic.
        requests_after_commit = net.allowed_requests
        net.run(net.now + 25_000)
        assert net.allowed_requests == requests_after_commit
        assert net.routes(1).get(2) == (2, 1)
        assert net.routes(2).get(1) == (1, 1)


def test_no_e2e_early_data_still_installs_reverse_route_without_routed_ack():
    held_keys = {
        (1, 2, "HELLO"),
        (1, 2, "CRYST"),
        (1, 2, "CRYST_REQ"),
        (2, 1, "CRYST"),
    }
    with HeldStartupControlNetwork(
        seed=91_013,
        tick_ms=25,
        hold=lambda sender, receiver, packet_kind: (
            sender, receiver, packet_kind
        ) in held_keys,
    ) as net:
        net.add_node(1)
        net.add_node(2)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)
        assert _run_until(
            net,
            lambda: 2 in net.routes(1) and 1 not in net.routes(2),
            20_000,
        )

        payload = b"early-without-e2e-ack"
        assert net.send(
            1, 2, payload, timeout_ms=60_000, e2e_ack=False
        ) != 0
        assert _run_until(
            net,
            lambda: _received_payloads(net, 2) == [payload]
            and net.routes(2).get(1) == (1, 1),
            net.now + 60_000,
        )
        assert net.app_acks(1) == []
        assert not any(
            sender == 2 and target == 1 and kind == "ACK"
            for _time, sender, target, kind, *_rest in net.routed_frames
        )

        reply = b"reverse-after-no-e2e"
        assert net.send(
            2, 1, reply, timeout_ms=60_000, e2e_ack=True
        ) != 0
        assert _run_until(
            net,
            lambda: _received_payloads(net, 1) == [reply]
            and any(result == 1 for result, _ping in net.app_acks(2)),
            net.now + 90_000,
        )

        net.release_all()
        net.hold = lambda _sender, _receiver, _kind: False
        net.run(net.now + 30_000)
        assert net.routes(1).get(2) == (2, 1)
        assert net.routes(2).get(1) == (1, 1)


def test_reverse_application_traffic_succeeds_before_first_startup_ack_returns():
    held_keys = {
        (1, 2, "HELLO"),
        (1, 2, "CRYST"),
        (1, 2, "CRYST_REQ"),
        (2, 1, "CRYST"),
        (2, 1, "ACK"),
    }
    with HeldStartupControlNetwork(
        seed=91_014,
        tick_ms=25,
        hold=lambda sender, receiver, packet_kind: (
            sender, receiver, packet_kind
        ) in held_keys,
    ) as net:
        net.add_node(1)
        net.add_node(2)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)
        assert _run_until(
            net,
            lambda: 2 in net.routes(1) and 1 not in net.routes(2),
            20_000,
        )

        forward = b"forward-ack-held"
        assert net.send(
            1, 2, forward, timeout_ms=120_000, e2e_ack=True
        ) != 0
        assert _run_until(
            net,
            lambda: _received_payloads(net, 2) == [forward]
            and net.routes(2).get(1) == (1, 1)
            and any(
                sender == 2 and receiver == 1 and _kind(payload) == "ACK"
                for sender, receiver, _epoch, _wire, payload in net.held
            ),
            net.now + 60_000,
        )
        assert net.app_acks(1) == []

        reverse = b"reverse-before-forward-ack"
        assert net.send(
            2, 1, reverse, timeout_ms=120_000, e2e_ack=True
        ) != 0
        assert _run_until(
            net,
            lambda: _received_payloads(net, 1) == [reverse]
            and any(result == 1 for result, _ping in net.app_acks(2)),
            net.now + 90_000,
        )
        assert net.app_acks(1) == []

        net.release_all()
        net.hold = lambda _sender, _receiver, _kind: False
        assert _run_until(
            net,
            lambda: any(result == 1 for result, _ping in net.app_acks(1)),
            net.now + 60_000,
        )
        net.run(net.now + 30_000)
        assert net.routes(1).get(2) == (2, 1)
        assert net.routes(2).get(1) == (1, 1)


def test_known_indirect_route_carries_data_while_remaining_topology_converges():
    with CppNetwork(seed=91_002, tick_ms=25) as net:
        for node_id in (1, 2, 3, 4):
            net.add_node(node_id)
        for left, right in ((1, 2), (2, 3), (3, 4)):
            net.add_link(left, right, latency_ms=0, jitter_ms=0)

        assert _run_until(
            net,
            lambda: 3 in net.routes(1) and 4 not in net.routes(1),
            60_000,
        ), net.routes(1)

        packet_id = net.send(
            1,
            3,
            b"known-before-full-convergence",
            timeout_ms=60_000,
            e2e_ack=True,
        )
        assert packet_id != 0
        assert _run_until(
            net,
            lambda: _received_payloads(net, 3)
            == [b"known-before-full-convergence"]
            and any(result == 1 for result, _ping in net.app_acks(1)),
            net.now + 90_000,
        ), (net.nodes[1].events, net.nodes[3].events)

        # The data/ACK transaction must not block CRYST/HELLO/repair traffic.
        net.run(net.now + 60_000)
        assert net.routes(1).get(4) == (2, 3)
        assert net.routes(4).get(1) == (3, 3)



def test_mixed_application_backlog_does_not_starve_remaining_crystallization():
    with CppNetwork(seed=91_016, tick_ms=25) as net:
        for node_id in (1, 2, 3, 4):
            net.add_node(node_id)
        for left, right in ((1, 2), (2, 3), (3, 4)):
            net.add_link(left, right, latency_ms=0, jitter_ms=0)

        assert _run_until(
            net,
            lambda: 3 in net.routes(1) and 4 not in net.routes(1),
            90_000,
        ), {node: net.routes(node) for node in (1, 2, 3, 4)}

        payloads = [
            (b"startup-backlog-small", True),
            (b"A" * 1000, True),
            (_random_payload(0xE416, 1000), True),
            (b"startup-backlog-no-e2e", False),
        ]
        packet_ids = [
            net.send(
                1,
                3,
                payload,
                timeout_ms=500_000,
                e2e_ack=needs_ack,
            )
            for payload, needs_ack in payloads
        ]
        assert all(packet_id != 0 for packet_id in packet_ids), packet_ids

        assert _run_until(
            net,
            lambda: len(_received_payloads(net, 3)) == len(payloads)
            and sum(result == 1 for result, _ping in net.app_acks(1)) == 3,
            net.now + 600_000,
        ), (net.nodes[1].events, net.nodes[3].events, net.routes(1))
        assert Counter(_received_payloads(net, 3)) == Counter(
            payload for payload, _needs_ack in payloads
        )
        assert sum(result == 1 for result, _ping in net.app_acks(1)) == 3

        # The application backlog must not turn startup into a separate phase or
        # starve control traffic. The fourth node still propagates everywhere.
        assert _run_until(
            net,
            lambda: all(
                all(
                    destination == node or destination in net.routes(node)
                    for destination in (1, 2, 3, 4)
                )
                for node in (1, 2, 3, 4)
            ),
            net.now + 180_000,
        ), {node: net.routes(node) for node in (1, 2, 3, 4)}


def test_node_with_own_pending_e2e_transaction_still_relays_other_nodes_data():
    # Node 2 owns a transaction to node 4. Hold its routed E2E ACK at node 3 so
    # node 2 remains in the local application gate, then send unrelated DATA
    # from node 1 through node 2. A per-node source gate must never become a
    # transit-network gate.
    with HeldStartupControlNetwork(
        seed=91_017,
        tick_ms=25,
        hold=lambda sender, receiver, kind: (sender, receiver, kind)
        == (3, 2, "ACK"),
    ) as net:
        for node_id in (1, 2, 3, 4):
            net.add_node(node_id)
        for left, right in ((1, 2), (2, 3), (3, 4)):
            net.add_link(left, right, latency_ms=0, jitter_ms=0)
        net.run(120_000)

        own = b"node-2-own-transaction"
        assert net.send(
            2, 4, own, timeout_ms=180_000, e2e_ack=True
        ) != 0
        assert _run_until(
            net,
            lambda: _received_payloads(net, 4) == [own]
            and any(_kind(frame[4]) == "ACK" for frame in net.held),
            net.now + 90_000,
        )
        assert net.app_acks(2) == []

        transit = b"node-1-through-busy-node-2"
        assert net.send(
            1, 4, transit, timeout_ms=180_000, e2e_ack=True
        ) != 0
        assert _run_until(
            net,
            lambda: _received_payloads(net, 4) == [own, transit],
            net.now + 60_000,
        ), (net.nodes[1].events, net.nodes[2].events, net.nodes[4].events)

        net.hold = lambda _sender, _receiver, _kind_name: False
        net.release_all()
        assert _run_until(
            net,
            lambda: any(result == 1 for result, _ping in net.app_acks(2))
            and any(result == 1 for result, _ping in net.app_acks(1)),
            net.now + 180_000,
        )
        assert _received_payloads(net, 4) == [own, transit]


def test_application_debug_flag_survives_single_compressed_and_multipart_delivery():
    fixtures = (
        b"short-debug",
        b"repeat-me;" * 80,
        _random_payload(0xD38A, 1000),
    )
    with CppNetwork(seed=91_003, tick_ms=25) as net:
        net.add_node(1)
        net.add_node(2)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)
        net.run(60_000)
        assert net.routes(1).get(2) == (2, 1)

        for index, payload in enumerate(fixtures):
            packet_id = net.send(
                1,
                2,
                payload,
                timeout_ms=180_000,
                e2e_ack=True,
                # Unknown transport bits must be masked; only DEBUG_ECHO is
                # application-owned.
                application_flags=0xFF,
            )
            assert packet_id != 0
            target_count = index + 1
            assert _run_until(
                net,
                lambda: len(_received_payloads(net, 2)) == target_count
                and len(net.app_acks(1)) == target_count,
                net.now + 240_000,
            )

        assert _received_payloads(net, 2) == list(fixtures)
        for flags in _received_flags(net, 2):
            assert flags & DTPK_FLAG_DEBUG_ECHO
            assert flags & DTPK_FLAG_E2E_ACK_REQUESTED
            assert (flags & DTPK_FLAG_COMPRESSED) == 0
            assert (flags & ~(
                DTPK_FLAG_DEBUG_ECHO | DTPK_FLAG_E2E_ACK_REQUESTED
            )) == 0


class CrystObservingNetwork(CppNetwork):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.latest_cryst: Dict[int, Tuple[int, int, List[Tuple[int, int, int]]]] = {}

    def _start_tx(self, sender, tx, at):
        if _kind(tx.payload) == "CRYST" and len(tx.payload) >= 14:
            _type, origin, version, chunk_index, chunk_count = struct.unpack_from(
                "<BHIHH", tx.payload, LCMM_HEADER_SIZE
            )
            records = []
            offset = LCMM_HEADER_SIZE + 11
            while offset + 5 <= len(tx.payload):
                destination, sequence, distance = struct.unpack_from(
                    "<HHB", tx.payload, offset
                )
                records.append((destination, sequence, distance))
                offset += 5
            if chunk_index == 0:
                self.latest_cryst[sender] = (origin, version, records)
        return super()._start_tx(sender, tx, at)


def _cryst_frame(
    lcmm_id: int,
    origin: int,
    version: int,
    chunk_index: int,
    chunk_count: int,
    records: List[Tuple[int, int, int]],
) -> bytes:
    return (
        struct.pack("<BH", 0, lcmm_id)
        + struct.pack(
            "<BHIHH", 0x40, origin, version, chunk_index, chunk_count
        )
        + b"".join(struct.pack("<HHB", *record) for record in records)
    )


def test_committed_route_remains_usable_while_new_multichunk_snapshot_is_incomplete():
    with CrystObservingNetwork(seed=91_004, tick_ms=25) as net:
        for node_id in (1, 2, 3):
            net.add_node(node_id)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)
        net.add_link(2, 3, latency_ms=0, jitter_ms=0)
        net.run(60_000)
        assert net.routes(1).get(3) == (2, 2)
        assert 2 in net.latest_cryst

        origin, version, records = net.latest_cryst[2]
        route_to_three = next(record for record in records if record[0] == 3)
        synthetic_version = (version + 1) & 0xFFFFFFFF or 1

        first = _cryst_frame(
            0xA101,
            origin,
            synthetic_version,
            0,
            2,
            [route_to_three],
        )
        second = _cryst_frame(
            0xA102,
            origin,
            synthetic_version,
            1,
            2,
            [],
        )

        # Only chunk zero is delivered: the new snapshot is explicitly
        # incomplete, so the previously committed route must remain active.
        txs = net.nodes[1].inject(net.now, 2, 0, first)
        net._handle_txs(1, txs, net.now)
        assert net.routes(1).get(3) == (2, 2)

        payload = b"during-incomplete-multichunk-snapshot"
        packet_id = net.send(
            1, 3, payload, timeout_ms=60_000, e2e_ack=True
        )
        assert packet_id != 0
        assert _run_until(
            net,
            lambda: _received_payloads(net, 3) == [payload]
            and any(result == 1 for result, _ping in net.app_acks(1)),
            net.now + 90_000,
        )

        # Completing the transaction applies the new snapshot atomically; the
        # route remains correct and no partial state was ever exposed.
        txs = net.nodes[1].inject(net.now, 2, 0, second)
        net._handle_txs(1, txs, net.now)
        assert net.routes(1).get(3) == (2, 2)



def test_first_hop_relay_learns_source_route_before_forwarding_early_data():
    held_keys = {
        (1, 2, "HELLO"),
        (1, 2, "CRYST"),
        (1, 2, "CRYST_REQ"),
    }
    with HeldStartupControlNetwork(
        seed=91_005,
        tick_ms=25,
        hold=lambda sender, receiver, packet_kind: (
            sender,
            receiver,
            packet_kind,
        )
        in held_keys,
    ) as net:
        for node_id in (1, 2, 3):
            net.add_node(node_id)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)
        net.add_link(2, 3, latency_ms=0, jitter_ms=0)

        # Node 2 advertises node 3 to node 1, while all origin-state traffic
        # from node 1 to node 2 is withheld. The forward route exists, but the
        # relay has no route back to the source yet.
        assert _run_until(
            net,
            lambda: 3 in net.routes(1) and 1 not in net.routes(2),
            60_000,
        ), (net.routes(1), net.routes(2), net.routes(3), net.trace[-80:])

        payload = b"relay-learns-source-before-forwarding"
        packet_id = net.send(
            1, 3, payload, timeout_ms=90_000, e2e_ack=True
        )
        assert packet_id != 0
        assert _run_until(
            net,
            lambda: _received_payloads(net, 3) == [payload]
            and any(result == 1 for result, _ping in net.app_acks(1)),
            net.now + 120_000,
        ), (net.nodes[1].events, net.nodes[2].events, net.nodes[3].events)

        assert net.routes(2).get(1) == (1, 1)
        net.release_all()
        net.hold = lambda _sender, _receiver, _kind: False
        net.run(net.now + 60_000)
        assert net.routes(1).get(3) == (2, 2)
        assert net.routes(3).get(1) == (2, 2)


class DropOneArmedLinkAckNetwork(CppNetwork):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.arm_drop = False
        self.dropped_link_ack = False

    def _start_tx(self, sender, tx, at):
        if (
            self.arm_drop
            and not self.dropped_link_ack
            and (sender, tx.target) == (2, 1)
            and tx.payload
            and tx.payload[0] == 4
        ):
            self.drop_next_frames(2, 1, 1)
            self.dropped_link_ack = True
        return super()._start_tx(sender, tx, at)


def test_lost_link_ack_cannot_inherit_long_application_timeout_and_block_reply():
    with DropOneArmedLinkAckNetwork(seed=91_006, tick_ms=25) as net:
        net.add_node(1)
        net.add_node(2)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)
        net.run(60_000)
        assert net.routes(1).get(2) == (2, 1)
        assert net.routes(2).get(1) == (1, 1)

        net.arm_drop = True
        forward_id = net.send(
            1,
            2,
            b"long-logical-timeout",
            timeout_ms=240_000,
            e2e_ack=True,
        )
        assert forward_id != 0
        assert _run_until(
            net,
            lambda: _received_payloads(net, 2) == [b"long-logical-timeout"]
            and any(result == 1 for result, _ping in net.app_acks(1)),
            net.now + 30_000,
        )
        assert net.dropped_link_ack

        started = net.now
        reverse_id = net.send(
            2,
            1,
            b"reply-after-lost-link-ack",
            timeout_ms=120_000,
            e2e_ack=True,
        )
        assert reverse_id != 0
        assert _run_until(
            net,
            lambda: _received_payloads(net, 1) == [b"reply-after-lost-link-ack"]
            and any(result == 1 for result, _ping in net.app_acks(2)),
            started + 30_000,
        ), (net.nodes[1].events, net.nodes[2].events)
        assert net.now - started < 30_000


@pytest.mark.parametrize(
    "payload",
    (
        b"reentrant-echo",
        b"compressible-reentrant-echo;" * 80,
        _random_payload(0xE417, 1000),
    ),
    ids=("single", "compressed", "multipart"),
)
def test_two_reentrant_debug_echo_callbacks_with_different_suffixes_do_not_loop_during_startup(
    payload: bytes,
):
    held_keys = {
        (1, 2, "HELLO"),
        (1, 2, "CRYST"),
        (1, 2, "CRYST_REQ"),
        (2, 1, "CRYST"),
    }
    with HeldStartupControlNetwork(
        seed=91_007,
        tick_ms=25,
        hold=lambda sender, receiver, packet_kind: (
            sender,
            receiver,
            packet_kind,
        )
        in held_keys,
    ) as net:
        net.add_node(1)
        net.add_node(2)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)
        net.nodes[1].set_debug_echo(b" [A]")
        net.nodes[2].set_debug_echo(b" [B]")

        assert _run_until(
            net,
            lambda: 2 in net.routes(1) and 1 not in net.routes(2),
            20_000,
        )

        packet_id = net.send(
            1, 2, payload, timeout_ms=30_000, e2e_ack=True
        )
        assert packet_id != 0
        assert _run_until(
            net,
            lambda: _received_payloads(net, 2) == [payload]
            and _received_payloads(net, 1) == [payload + b" [B]"]
            and any(result == 1 for result, _ping in net.app_acks(1)),
            net.now + 60_000,
        ), (net.nodes[1].events, net.nodes[2].events, net.trace[-100:])

        # The reply is sent synchronously from node 2's application callback.
        # Direct-origin learning must therefore happen before callback entry.
        echo_ids = [
            int(values[0])
            for kind, values in net.nodes[2].events
            if kind == "ECHOID"
        ]
        assert echo_ids and echo_ids[-1] != 0
        assert not any(
            kind == "ECHOID" for kind, _values in net.nodes[1].events
        )
        assert _received_flags(net, 1)[0] & DTPK_FLAG_DEBUG_ECHO
        assert _run_until(
            net,
            lambda: any(
                kind == "ECHO_ACK" and int(values[0]) == 1
                for kind, values in net.nodes[2].events
            ),
            net.now + 60_000,
        )

        # Give both nodes several retry/HELLO periods. A suffix-only design
        # would bounce [A]/[B] forever; the protocol flag keeps each side at one
        # application delivery.
        net.run(net.now + 30_000)
        assert _received_payloads(net, 2) == [payload]
        assert _received_payloads(net, 1) == [payload + b" [B]"]

        net.release_all()
        net.hold = lambda _sender, _receiver, _kind: False
        net.run(net.now + 30_000)
        assert net.routes(1).get(2) == (2, 1)
        assert net.routes(2).get(1) == (1, 1)


def _data_frame(
    lcmm_id: int,
    packet_id: int,
    source_sequence: int,
    original_sender: int,
    final_target: int,
    payload: bytes,
    *,
    flags: int = 0,
    hop_limit: int = 255,
) -> bytes:
    return (
        struct.pack("<BH", 1, lcmm_id)
        + struct.pack(
            "<BHHHHBB",
            0x41,
            packet_id,
            source_sequence,
            original_sender,
            final_target,
            flags,
            hop_limit,
        )
        + payload
    )


def _inject_frame(
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
        # A real half-duplex receiver cannot decode while transmitting. Wait
        # for the next receive window instead of treating a deliberate
        # synthetic injection as an over-the-air retry failure.
        net.run(min(deadline, net.now + 25))
    raise AssertionError("receiver never returned to an RX window")


def _next_sequence(value: int) -> int:
    value = (int(value) + 1) & 0xFFFF
    return value or 1


def test_delayed_data_from_an_old_boot_is_not_delivered_after_newer_direct_data():
    held_keys = {
        (1, 2, "HELLO"),
        (1, 2, "CRYST"),
        (1, 2, "CRYST_REQ"),
    }
    with HeldStartupControlNetwork(
        seed=91_008,
        tick_ms=25,
        hold=lambda sender, receiver, packet_kind: (
            sender,
            receiver,
            packet_kind,
        )
        in held_keys,
    ) as net:
        net.add_node(1)
        net.add_node(2)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)

        old_sequence = net._node_origin_sequence[1]
        new_sequence = _next_sequence(old_sequence)
        _inject_frame(
            net,
            2,
            1,
            2,
            _data_frame(
                0xB101,
                0x9101,
                new_sequence,
                1,
                2,
                b"new-incarnation",
            ),
        )
        net.run(net.now + 2_000)
        assert _received_payloads(net, 2) == [b"new-incarnation"]
        assert net.routes(2).get(1) == (1, 1)

        # Delayed DATA and control from the previous boot must not downgrade the
        # provisional direct generation or reach the application.
        _inject_frame(
            net,
            2,
            1,
            2,
            _data_frame(
                0xB102,
                0x9102,
                old_sequence,
                1,
                2,
                b"stale-incarnation",
            ),
        )
        stale_cryst = _cryst_frame(
            0xB103,
            old_sequence,
            99,
            0,
            1,
            [(7, 1, 1)],
        )
        _inject_frame(net, 2, 1, 0, stale_cryst)
        net.run(net.now + 5_000)
        assert _received_payloads(net, 2) == [b"new-incarnation"]
        assert net.routes(2).get(1) == (1, 1)
        assert 7 not in net.routes(2)



def test_direct_application_origin_sequence_wraps_without_accepting_stale_boot():
    held_keys = {
        (1, 2, "HELLO"),
        (1, 2, "CRYST"),
        (1, 2, "CRYST_REQ"),
    }
    with HeldStartupControlNetwork(
        seed=91_017,
        tick_ms=25,
        hold=lambda sender, receiver, packet_kind: (
            sender, receiver, packet_kind
        ) in held_keys,
    ) as net:
        net.add_node(1)
        net.add_node(2)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)

        _inject_frame(
            net,
            2,
            1,
            2,
            _data_frame(
                0xB201, 0x9201, 0xFFFF, 1, 2, b"before-wrap"
            ),
        )
        _inject_frame(
            net,
            2,
            1,
            2,
            _data_frame(
                0xB202, 0x9202, 1, 1, 2, b"after-wrap"
            ),
        )
        _inject_frame(
            net,
            2,
            1,
            2,
            _data_frame(
                0xB203, 0x9203, 0xFFFE, 1, 2, b"stale-before-wrap"
            ),
        )
        _inject_frame(
            net,
            2,
            1,
            2,
            _data_frame(
                0xB204, 0x9204, 0x8001, 1, 2, b"ambiguous-half-space"
            ),
        )
        net.run(net.now + 5_000)
        assert _received_payloads(net, 2) == [b"before-wrap", b"after-wrap"]
        assert net.routes(2).get(1) == (1, 1)
        route = net.nodes[2].routes_with_sequence(net.now)[1]
        assert route[2] == 1


def test_newer_direct_data_discards_old_indirect_neighbor_contribution():
    held_keys = {
        (1, 2, "HELLO"),
        (1, 2, "CRYST"),
        (1, 2, "CRYST_REQ"),
    }
    with HeldStartupControlNetwork(
        seed=91_009,
        tick_ms=25,
        hold=lambda sender, receiver, packet_kind: (
            sender,
            receiver,
            packet_kind,
        )
        in held_keys,
    ) as net:
        net.add_node(1)
        net.add_node(2)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)

        old_sequence = net._node_origin_sequence[1]
        new_sequence = _next_sequence(old_sequence)
        initial = _cryst_frame(
            0xB201,
            old_sequence,
            1,
            0,
            1,
            [(3, 0x2222, 1)],
        )
        _inject_frame(net, 2, 1, 0, initial)
        net.run(net.now + 1_000)
        assert net.routes(2).get(1) == (1, 1)
        assert net.routes(2).get(3) == (1, 2)

        _inject_frame(
            net,
            2,
            1,
            2,
            _data_frame(
                0xB202,
                0x9201,
                new_sequence,
                1,
                2,
                b"reboot-proof",
            ),
        )
        net.run(net.now + 2_000)
        assert _received_payloads(net, 2) == [b"reboot-proof"]
        assert net.routes(2).get(1) == (1, 1)
        assert 3 not in net.routes(2)

        # A delayed complete snapshot from the old incarnation cannot restore
        # the invalidated indirect route.
        _inject_frame(net, 2, 1, 0, initial)
        net.run(net.now + 2_000)
        assert 3 not in net.routes(2)


def test_relayed_data_never_creates_false_direct_route_to_original_source():
    with CppNetwork(seed=91_010, tick_ms=25) as net:
        net.add_node(2)
        net.add_node(3)
        net.add_link(2, 3, latency_ms=0, jitter_ms=0)
        net.run(60_000)
        assert net.routes(2).get(3) == (3, 1)

        payload = b"relayed-origin-is-not-neighbor"
        _inject_frame(
            net,
            2,
            1,
            2,
            _data_frame(
                0xB301,
                0x9301,
                0x3344,
                9,
                3,
                payload,
            ),
        )
        assert _run_until(
            net,
            lambda: _received_payloads(net, 3) == [payload],
            net.now + 30_000,
        )
        assert 1 not in net.routes(2)
        assert 9 not in net.routes(2)
        assert 9 not in net.routes(3)


class DataSequenceTraceNetwork(HeldStartupControlNetwork):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.source_data_sequences: List[Tuple[float, int, int]] = []

    def _start_tx(self, sender, tx, at):
        if (
            sender == 1
            and len(tx.payload) >= 8
            and tx.payload[LCMM_HEADER_SIZE] in (0x41, 0x47, 0x49)
        ):
            self.source_data_sequences.append(
                (
                    self.now,
                    int.from_bytes(tx.payload[6:8], "little"),
                    tx.target,
                )
            )
        return super()._start_tx(sender, tx, at)


def _seq_request_frame(
    lcmm_id: int,
    request_id: int,
    requester: int,
    destination: int,
    requested_sequence: int,
) -> bytes:
    return (
        struct.pack("<BH", 1, lcmm_id)
        + struct.pack(
            "<BHHHHBB",
            0x46,
            request_id,
            requester,
            destination,
            requested_sequence,
            0,
            255,
        )
    )


def test_sequence_repair_at_ack_boundary_rebases_queued_data_before_transmit():
    # Hold the routed DTPK ACK so a second application packet is queued behind
    # the first transaction. A SEQ_REQ then asks the source to advance its own
    # generation while both packets still exist.
    with DataSequenceTraceNetwork(
        seed=91_011,
        tick_ms=25,
        hold=lambda sender, receiver, packet_kind: (
            sender == 2 and receiver == 1 and packet_kind == "ACK"
        ),
    ) as net:
        net.add_node(1)
        net.add_node(2)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)
        net.run(60_000)

        old_sequence = net._node_origin_sequence[1]
        new_sequence = _next_sequence(old_sequence)
        assert net.send(
            1,
            2,
            b"first-generation",
            timeout_ms=180_000,
            e2e_ack=True,
        ) != 0
        assert _run_until(
            net,
            lambda: _received_payloads(net, 2) == [b"first-generation"]
            and bool(net.held),
            net.now + 60_000,
        )

        # This packet is accepted but cannot transmit until the first E2E ACK
        # closes the application gate.
        assert net.send(
            1,
            2,
            b"queued-generation",
            timeout_ms=180_000,
            e2e_ack=False,
        ) != 0
        _inject_frame(
            net,
            1,
            2,
            1,
            _seq_request_frame(0xD101, 0xD102, 2, 1, new_sequence),
        )
        net.run(net.now + 5_000)
        assert [sequence for _time, sequence, _target in net.source_data_sequences] == [
            old_sequence
        ]

        net.release_all()
        net.hold = lambda _sender, _receiver, _kind: False
        assert _run_until(
            net,
            lambda: _received_payloads(net, 2)
            == [b"first-generation", b"queued-generation"],
            net.now + 120_000,
        )
        assert [sequence for _time, sequence, _target in net.source_data_sequences] == [
            old_sequence,
            new_sequence,
        ]


def test_multihop_debug_echo_uses_reverse_data_path_before_crystallization():
    # Node 1 knows node 3 through node 2. Neither relay nor destination has yet
    # received a normal route to node 1; node 3 must still reply synchronously
    # from its application callback using the reverse DATA path.
    held_keys = {
        (1, 2, "HELLO"),
        (1, 2, "CRYST"),
        (1, 2, "CRYST_REQ"),
        (2, 3, "CRYST"),
    }
    with HeldStartupControlNetwork(
        seed=91_012,
        tick_ms=25,
        hold=lambda sender, receiver, packet_kind: (
            sender,
            receiver,
            packet_kind,
        )
        in held_keys,
    ) as net:
        for node_id in (1, 2, 3):
            net.add_node(node_id)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)
        net.add_link(2, 3, latency_ms=0, jitter_ms=0)
        net.nodes[3].set_debug_echo(b" [echo]")

        assert _run_until(
            net,
            lambda: 3 in net.routes(1)
            and 1 not in net.routes(2)
            and 1 not in net.routes(3),
            60_000,
        )
        payload = b"early-multihop-echo"
        assert net.send(
            1, 3, payload, timeout_ms=120_000, e2e_ack=True
        ) != 0
        assert _run_until(
            net,
            lambda: _received_payloads(net, 3) == [payload]
            and _received_payloads(net, 1) == [payload + b" [echo]"]
            and any(result == 1 for result, _ping in net.app_acks(1)),
            net.now + 180_000,
        ), (net.nodes[1].events, net.nodes[3].events, net.trace[-120:])
        assert any(
            kind == "ECHOID" and int(values[0]) != 0
            for kind, values in net.nodes[3].events
        )
        echo_frames = [
            frame
            for frame in net.routed_frames
            if frame[6] == 3
            and frame[7] == 1
            and (frame[4] & DTPK_FLAG_DEBUG_ECHO) != 0
            and frame[3] == "DATA"
        ]
        assert [(frame[1], frame[2], frame[5]) for frame in echo_frames] == [
            (3, 2, 255),
            (2, 1, 254),
        ]

        net.release_all()
        net.hold = lambda _sender, _receiver, _kind: False
        net.run(net.now + 60_000)
        assert net.routes(1).get(3) == (2, 2)
        assert net.routes(3).get(1) == (2, 2)



def test_reverse_breadcrumb_expires_instead_of_becoming_permanent_route():
    held_keys = {
        (1, 2, "HELLO"),
        (1, 2, "CRYST"),
        (1, 2, "CRYST_REQ"),
        (2, 3, "CRYST"),
    }
    with HeldStartupControlNetwork(
        seed=91_015,
        tick_ms=25,
        hold=lambda sender, receiver, packet_kind: (
            sender, receiver, packet_kind
        ) in held_keys,
    ) as net:
        for node_id in (1, 2, 3):
            net.add_node(node_id)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)
        net.add_link(2, 3, latency_ms=0, jitter_ms=0)
        assert _run_until(
            net,
            lambda: 3 in net.routes(1)
            and 1 not in net.routes(2)
            and 1 not in net.routes(3),
            60_000,
        )

        payload = b"breadcrumb-lifetime"
        assert net.send(
            1, 3, payload, timeout_ms=120_000, e2e_ack=False
        ) != 0
        assert _run_until(
            net,
            lambda: _received_payloads(net, 3) == [payload],
            net.now + 90_000,
        )
        assert 1 not in net.routes(3)

        # Reverse fallback is intentionally ephemeral. Continued HELLO traffic
        # keeps the physical neighbours alive, while the packet-specific
        # breadcrumb itself ages out after 120 seconds.
        net.run(net.now + 122_000)
        assert 1 not in net.routes(3)
        assert net.send(
            3, 1, b"too-late-for-breadcrumb",
            timeout_ms=60_000,
            e2e_ack=True,
        ) == 0
        assert net.app_acks(3)[-1] == (0, 0)

        net.release_all()
        net.hold = lambda _sender, _receiver, _kind: False
        assert _run_until(
            net,
            lambda: net.routes(3).get(1) == (2, 2),
            net.now + 90_000,
        )
        recovery = b"after-normal-route"
        assert net.send(
            3, 1, recovery, timeout_ms=90_000, e2e_ack=True
        ) != 0
        assert _run_until(
            net,
            lambda: _received_payloads(net, 1)[-1:] == [recovery]
            and net.app_acks(3)[-1][0] == 1,
            net.now + 120_000,
        )


def test_known_component_delivers_when_another_node_is_permanently_unreachable():
    """There is no global crystallization barrier across disconnected components."""
    with CppNetwork(seed=96_001, tick_ms=25) as net:
        for node_id in (1, 2, 3):
            net.add_node(node_id)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)

        assert _run_until(
            net,
            lambda: net.routes(1).get(2) == (2, 1)
            and net.routes(2).get(1) == (1, 1),
            60_000,
        )
        assert 3 not in net.routes(1)
        assert 3 not in net.routes(2)

        payload = b"known-component-while-node3-unreachable"
        assert net.send(
            1, 2, payload, timeout_ms=60_000, e2e_ack=True
        ) != 0
        assert _run_until(
            net,
            lambda: _received_payloads(net, 2) == [payload]
            and any(result == 1 for result, _ping in net.app_acks(1)),
            net.now + 90_000,
        )

        # Continued attempts to discover the isolated node must not invalidate
        # or duplicate the usable route inside the connected component.
        net.run(net.now + 180_000)
        assert _received_payloads(net, 2) == [payload]
        assert net.routes(1) == {2: (2, 1)}
        assert net.routes(2) == {1: (1, 1)}
        assert net.routes(3) == {}


def test_simultaneous_bidirectional_data_before_full_snapshots_delivers_once():
    """Both peers may use direct digest routes before either full CRYST commits."""
    with HeldStartupControlNetwork(
        seed=96_002,
        tick_ms=25,
        hold=lambda _sender, _receiver, packet_kind: packet_kind == "CRYST",
    ) as net:
        net.add_node(1)
        net.add_node(2)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)

        assert _run_until(
            net,
            lambda: net.routes(1).get(2) == (2, 1)
            and net.routes(2).get(1) == (1, 1)
            and len(net.held) >= 2,
            60_000,
        ), (net.routes(1), net.routes(2), net.trace[-80:])

        payload_1 = b"early-cross-1-to-2"
        payload_2 = b"early-cross-2-to-1"
        assert net.send(
            1, 2, payload_1, timeout_ms=120_000, e2e_ack=True
        ) != 0
        assert net.send(
            2, 1, payload_2, timeout_ms=120_000, e2e_ack=True
        ) != 0

        assert _run_until(
            net,
            lambda: _received_payloads(net, 2) == [payload_1]
            and _received_payloads(net, 1) == [payload_2]
            and any(result == 1 for result, _ping in net.app_acks(1))
            and any(result == 1 for result, _ping in net.app_acks(2)),
            net.now + 180_000,
        ), (net.nodes[1].events, net.nodes[2].events, net.trace[-120:])

        assert len(net.held) >= 2
        net.release_all()
        net.hold = lambda _sender, _receiver, _kind: False
        net.run(net.now + 90_000)
        assert not net.held
        assert _received_payloads(net, 2) == [payload_1]
        assert _received_payloads(net, 1) == [payload_2]
        assert net.routes(1).get(2) == (2, 1)
        assert net.routes(2).get(1) == (1, 1)


def test_maximum_16k_message_delivers_before_reciprocal_snapshot_completion():
    """The largest configured multipart transfer must not require global readiness."""
    held_keys = {
        (1, 2, "HELLO"),
        (1, 2, "CRYST"),
        (1, 2, "CRYST_REQ"),
        (2, 1, "CRYST"),
    }
    with HeldStartupControlNetwork(
        seed=96_003,
        tick_ms=25,
        hold=lambda sender, receiver, packet_kind: (
            sender,
            receiver,
            packet_kind,
        )
        in held_keys,
    ) as net:
        net.add_node(1)
        net.add_node(2)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)

        assert _run_until(
            net,
            lambda: 2 in net.routes(1) and 1 not in net.routes(2),
            30_000,
        )
        payload = _random_payload(0x96003, 16 * 1024)
        assert net.send(
            1,
            2,
            payload,
            timeout_ms=2_000_000,
            e2e_ack=True,
        ) != 0
        assert _run_until(
            net,
            lambda: _received_payloads(net, 2) == [payload]
            and any(result == 1 for result, _ping in net.app_acks(1)),
            net.now + 2_200_000,
        ), (net.nodes[1].events[-80:], net.nodes[2].events[-80:])

        # DATA itself established the reverse route while all reciprocal startup
        # control was still retained. Releasing it afterward must complete the
        # snapshot without another application delivery.
        assert net.routes(2).get(1) == (1, 1)
        assert net.held
        net.release_all()
        net.hold = lambda _sender, _receiver, _kind: False
        assert _run_until(
            net,
            lambda: net.routes(2).get(1) == (1, 1) and not net.held,
            net.now + 180_000,
        )
        assert _received_payloads(net, 2) == [payload]
        assert sum(result == 1 for result, _ping in net.app_acks(1)) == 1
