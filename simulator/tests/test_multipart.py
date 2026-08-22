import pathlib
import sys
from collections import defaultdict

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cpp_backend import CppNetwork, CppNodeProcess


pytestmark = pytest.mark.skipif(
    not CppNodeProcess.available(),
    reason="host C++ node not built",
)

DTPK_DATA_SINGLE = 0x31
DTPK_DATA_FRAGMENT = 0x37
LCMM_HEADER_SIZE = 3
FRAGMENT_INDEX_OFFSET = LCMM_HEADER_SIZE + 13


def _payload(size: int) -> bytes:
    return bytes((index * 37 + 11) & 0xFF for index in range(size))


class FragmentTraceNetwork(CppNetwork):
    def __init__(self, *args, forced_edge=None, forced_index=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fragment_frames = []
        self.forced_edge = forced_edge
        self.forced_index = forced_index
        self.forced_drops = 0

    def _start_tx(self, sender, tx, at):
        packet_type = tx.payload[LCMM_HEADER_SIZE] if len(tx.payload) > 3 else None
        fragment_index = (
            tx.payload[FRAGMENT_INDEX_OFFSET]
            if packet_type == DTPK_DATA_FRAGMENT
            and len(tx.payload) > FRAGMENT_INDEX_OFFSET
            else None
        )
        lcmm_id = (
            int.from_bytes(tx.payload[1:3], "little")
            if len(tx.payload) >= 3
            else None
        )
        if packet_type in (DTPK_DATA_SINGLE, DTPK_DATA_FRAGMENT):
            self.fragment_frames.append(
                (sender, tx.target, packet_type, fragment_index, lcmm_id, len(tx.payload))
            )
        if (
            self.forced_edge == (sender, tx.target)
            and packet_type == DTPK_DATA_FRAGMENT
            and fragment_index == self.forced_index
            and self.forced_drops < 5
        ):
            # Force every LCMM attempt for one fragment to disappear. The
            # message layer must recover it from the destination's missing bitmap.
            self.drop_next_frames(sender, tx.target, 1)
            self.forced_drops += 1
        return super()._start_tx(sender, tx, at)


def _received_payloads(net, node_id):
    return [
        bytes.fromhex(values[3])
        for kind, values in net.nodes[node_id].events
        if kind == "APP_RX"
    ]


def _distinct_fragment_sends(net, edge):
    result = defaultdict(set)
    for sender, target, packet_type, index, lcmm_id, _size in net.fragment_frames:
        if (sender, target) == edge and packet_type == DTPK_DATA_FRAGMENT:
            result[index].add(lcmm_id)
    return {index: len(ids) for index, ids in sorted(result.items())}


def test_real_cpp_multipart_delivers_binary_payload_once():
    payload = _payload(1000)
    with FragmentTraceNetwork(seed=70_001, tick_ms=50) as net:
        net.add_node(1)
        net.add_node(2)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)
        net.run(60_000)

        packet_id = net.send(1, 2, payload, timeout_ms=180_000, e2e_ack=True)
        net.run(180_000)

        assert packet_id != 0
        assert _received_payloads(net, 2) == [payload]
        assert sum(result == 1 for result, _ping in net.app_acks(1)) == 1
        counts = _distinct_fragment_sends(net, (1, 2))
        assert counts == {0: 1, 1: 1, 2: 1, 3: 1, 4: 1}
        assert max(size for *_prefix, size in net.fragment_frames) <= 247


def test_real_cpp_multipart_selectively_repairs_one_missing_relay_fragment():
    payload = _payload(1800)
    with FragmentTraceNetwork(
        seed=70_002,
        tick_ms=50,
        forced_edge=(2, 3),
        forced_index=3,
    ) as net:
        for node in (1, 2, 3):
            net.add_node(node)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)
        net.add_link(2, 3, latency_ms=0, jitter_ms=0)
        net.run(120_000)

        packet_id = net.send(1, 3, payload, timeout_ms=300_000, e2e_ack=True)
        net.run(350_000)

        assert packet_id != 0
        assert net.forced_drops == 5
        assert _received_payloads(net, 3) == [payload]
        assert sum(result == 1 for result, _ping in net.app_acks(1)) == 1

        expected = {index: 1 for index in range(8)}
        expected[3] = 2
        # Distinct LCMM ids separate message-level selective retransmission from
        # ordinary per-hop retries, which retain the same LCMM id.
        assert _distinct_fragment_sends(net, (1, 2)) == expected
        assert _distinct_fragment_sends(net, (2, 3)) == expected


def test_real_cpp_multipart_boundary_keeps_small_packet_wire_format():
    with FragmentTraceNetwork(seed=70_003, tick_ms=50) as net:
        net.add_node(1)
        net.add_node(2)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)
        net.run(60_000)

        net.fragment_frames.clear()
        net.send(1, 2, _payload(233), timeout_ms=60_000, e2e_ack=True)
        net.run(100_000)
        assert any(frame[2] == DTPK_DATA_SINGLE for frame in net.fragment_frames)
        assert not any(frame[2] == DTPK_DATA_FRAGMENT for frame in net.fragment_frames)

        net.fragment_frames.clear()
        net.send(1, 2, _payload(234), timeout_ms=90_000, e2e_ack=True)
        net.run(160_000)
        assert not any(frame[2] == DTPK_DATA_SINGLE for frame in net.fragment_frames)
        assert _distinct_fragment_sends(net, (1, 2)) == {0: 1, 1: 1}


def test_real_cpp_multipart_rejects_above_configured_memory_cap():
    with CppNetwork(seed=70_004, tick_ms=50) as net:
        net.add_node(1)
        net.add_node(2)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)
        net.run(60_000)
        assert net.send(
            1,
            2,
            _payload(16 * 1024 + 1),
            timeout_ms=120_000,
            e2e_ack=True,
        ) == 0


def test_real_cpp_multipart_delivers_configured_16k_maximum():
    payload = _payload(16 * 1024)
    with FragmentTraceNetwork(seed=70_005, tick_ms=50) as net:
        net.add_node(1)
        net.add_node(2)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)
        net.run(60_000)

        # Deliberately request an unrealistically short timeout. DTPK raises the
        # logical-message deadline to cover the stream plus one repair round.
        packet_id = net.send(1, 2, payload, timeout_ms=1_000, e2e_ack=True)
        net.run(250_000)

        assert packet_id != 0
        assert _received_payloads(net, 2) == [payload]
        assert sum(result == 1 for result, _ping in net.app_acks(1)) == 1
        assert _distinct_fragment_sends(net, (1, 2)) == {
            index: 1 for index in range(72)
        }
        assert max(size for *_prefix, size in net.fragment_frames) <= 247
