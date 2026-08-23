import pathlib
import random
import struct
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

DTPK_DATA_SINGLE = 0x41
DTPK_DATA_FRAGMENT = 0x47
DTPK_FLAG_COMPRESSED = 0x02
DTPK_FLAG_NACK_FINAL_REJECT = 0x08
LCMM_HEADER_SIZE = 3
DTPK_FLAGS_OFFSET = LCMM_HEADER_SIZE + 9
FRAGMENT_INDEX_OFFSET = LCMM_HEADER_SIZE + 13


def _payload(size: int) -> bytes:
    # A deterministic but non-periodic binary fixture. The previous affine byte
    # sequence repeated every 256 bytes and was correctly compressed by v4,
    # which made it unsuitable for raw multipart regression tests.
    generator = random.Random(0xD7A40000 + size)
    return bytes(generator.getrandbits(8) for _ in range(size))


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
            flags = (
                tx.payload[DTPK_FLAGS_OFFSET]
                if len(tx.payload) > DTPK_FLAGS_OFFSET
                else 0
            )
            self.fragment_frames.append(
                (
                    sender,
                    tx.target,
                    packet_type,
                    fragment_index,
                    lcmm_id,
                    flags,
                    len(tx.payload),
                )
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
    for sender, target, packet_type, index, lcmm_id, _flags, _size in net.fragment_frames:
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

        # Distinct LCMM ids separate message-level selective retransmission from
        # ordinary per-hop retries, which retain the same LCMM id. Fragment 3
        # must be repaired. A final fragment can legitimately overtake at most a
        # small number of earlier relay frames, so the first bitmap may also
        # conservatively request one still-in-flight fragment; it must never
        # restart the whole message.
        for edge in ((1, 2), (2, 3)):
            counts = _distinct_fragment_sends(net, edge)
            assert counts[3] == 2
            assert set(counts) == set(range(8))
            assert all(value in (1, 2) for value in counts.values())
            assert sum(value - 1 for value in counts.values()) <= 2


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



def test_real_cpp_auto_compresses_only_when_radio_airtime_drops():
    compressible = (
        b'{"node":7,"status":"online","message":"hello mesh","battery":91}\n'
        * 80
    )
    incompressible = _payload(1000)

    with FragmentTraceNetwork(seed=70_006, tick_ms=50) as net:
        net.add_node(1)
        net.add_node(2)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)
        net.run(60_000)

        packet_id = net.send(
            1, 2, compressible, timeout_ms=180_000, e2e_ack=True
        )
        net.run(180_000)
        assert packet_id != 0
        assert _received_payloads(net, 2) == [compressible]
        compressed_frames = [
            frame for frame in net.fragment_frames if frame[0:2] == (1, 2)
        ]
        assert compressed_frames
        assert all(frame[5] & DTPK_FLAG_COMPRESSED for frame in compressed_frames)
        raw_fragment_count = (len(compressible) + 229) // 230
        assert len({frame[4] for frame in compressed_frames}) < raw_fragment_count
        stats_after_compressed = net.nodes[1].compression_stats()
        assert stats_after_compressed["attempts"] == 1
        assert stats_after_compressed["selected"] == 1
        assert stats_after_compressed["original_bytes"] == len(compressible)
        assert 0 < stats_after_compressed["encoded_bytes"] < len(compressible)
        assert stats_after_compressed["estimated_airtime_saved_ms"] > 0

        net.fragment_frames.clear()
        packet_id = net.send(
            1, 2, incompressible, timeout_ms=180_000, e2e_ack=True
        )
        net.run(300_000)
        assert packet_id != 0
        assert _received_payloads(net, 2) == [compressible, incompressible]
        raw_frames = [
            frame for frame in net.fragment_frames if frame[0:2] == (1, 2)
        ]
        assert raw_frames
        assert all((frame[5] & DTPK_FLAG_COMPRESSED) == 0 for frame in raw_frames)
        assert _distinct_fragment_sends(net, (1, 2)) == {
            index: 1 for index in range(5)
        }
        stats_after_random = net.nodes[1].compression_stats()
        assert stats_after_random["attempts"] == 2
        assert stats_after_random["selected"] == 1
        assert stats_after_random["candidate_too_large"] == 1
        assert stats_after_random["codec_failures"] == 0
        assert stats_after_random["verification_failures"] == 0


def test_real_cpp_compression_can_turn_multipart_into_one_frame():
    payload = b'A' * 300
    with FragmentTraceNetwork(seed=70_007, tick_ms=50) as net:
        net.add_node(1)
        net.add_node(2)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)
        net.run(60_000)

        packet_id = net.send(1, 2, payload, timeout_ms=60_000, e2e_ack=True)
        net.run(120_000)
        assert packet_id != 0
        assert _received_payloads(net, 2) == [payload]
        data_frames = [
            frame for frame in net.fragment_frames if frame[0:2] == (1, 2)
        ]
        assert len({frame[4] for frame in data_frames}) == 1
        assert data_frames[0][2] == DTPK_DATA_SINGLE
        assert data_frames[0][5] & DTPK_FLAG_COMPRESSED


def test_real_cpp_tiny_payload_skips_compression_attempt():
    payload = b'A' * 20
    with FragmentTraceNetwork(seed=70_008, tick_ms=50) as net:
        net.add_node(1)
        net.add_node(2)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)
        net.run(60_000)

        net.send(1, 2, payload, timeout_ms=60_000, e2e_ack=True)
        net.run(100_000)
        assert _received_payloads(net, 2) == [payload]
        data_frames = [
            frame for frame in net.fragment_frames if frame[0:2] == (1, 2)
        ]
        assert data_frames
        assert all((frame[5] & DTPK_FLAG_COMPRESSED) == 0 for frame in data_frames)
        assert net.nodes[1].compression_stats()["attempts"] == 0


def test_real_cpp_compressed_multipart_selectively_repairs_one_fragment():
    generator = random.Random(0xC04D)
    payload = b"".join(
        bytes(generator.getrandbits(8) for _ in range(160))
        + b"common-pattern-" * 5
        for _ in range(12)
    )

    with FragmentTraceNetwork(
        seed=70_009,
        tick_ms=50,
        forced_edge=(2, 3),
        forced_index=3,
    ) as net:
        for node in (1, 2, 3):
            net.add_node(node)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)
        net.add_link(2, 3, latency_ms=0, jitter_ms=0)
        net.run(120_000)

        packet_id = net.send(
            1, 3, payload, timeout_ms=300_000, e2e_ack=True
        )
        net.run(380_000)

        assert packet_id != 0
        assert net.forced_drops == 5
        assert _received_payloads(net, 3) == [payload]
        assert sum(result == 1 for result, _ping in net.app_acks(1)) == 1
        compressed = [
            frame for frame in net.fragment_frames
            if frame[2] == DTPK_DATA_FRAGMENT
        ]
        assert compressed
        assert all(frame[5] & DTPK_FLAG_COMPRESSED for frame in compressed)

        for edge in ((1, 2), (2, 3)):
            counts = _distinct_fragment_sends(net, edge)
            assert counts[3] == 2
            assert set(counts) == set(range(10))
            assert all(value in (1, 2) for value in counts.values())
            assert sum(value - 1 for value in counts.values()) <= 2



def test_real_cpp_rejects_malformed_compression_without_app_delivery():
    with FragmentTraceNetwork(seed=70_010, tick_ms=50) as net:
        net.add_node(1)
        net.add_node(2)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)
        net.run(60_000)

        # LCMM reliable-data header followed by a v4 DATA_SINGLE packet whose
        # compression codec is unknown. It is link-valid but application-invalid.
        malformed = (
            struct.pack("<BH", 1, 0x7110)
            + struct.pack(
                "<BHHHHBB", 0x41, 0x3344,
                net._node_origin_sequence[1], 1, 2,
                0x01 | DTPK_FLAG_COMPRESSED, 255
            )
            + struct.pack("<HB", 100, 0x7F)
            + b"not-a-supported-codec"
        )
        txs = net.nodes[2].inject(net.now, 1, 2, malformed)
        net._handle_txs(2, txs, net.now)
        net.run(90_000)

        assert _received_payloads(net, 2) == []
        stats = net.nodes[2].compression_stats()
        assert stats["decode_failures"] == 1
        assert stats["selected"] == 0



def test_real_cpp_rejects_byte_savings_without_airtime_savings():
    # Heatshrink encodes this 34-byte payload into a 33-byte v4 envelope. At
    # SF9/BW125 both packet sizes occupy the same LoRa symbol count, so sending
    # compressed bytes would add decode work without reducing radio time.
    payload = bytes.fromhex(
        "8b52efbaa55d4b58e24fe1d640dc7ede118b584b5da5baef528b584b5da5baef528b"
    )
    with FragmentTraceNetwork(seed=70_011, tick_ms=50) as net:
        net.add_node(1)
        net.add_node(2)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)
        net.run(60_000)

        net.send(1, 2, payload, timeout_ms=60_000, e2e_ack=True)
        net.run(100_000)
        assert _received_payloads(net, 2) == [payload]
        data_frames = [
            frame for frame in net.fragment_frames if frame[0:2] == (1, 2)
        ]
        assert data_frames
        assert all((frame[5] & DTPK_FLAG_COMPRESSED) == 0 for frame in data_frames)
        stats = net.nodes[1].compression_stats()
        assert stats["attempts"] == 1
        assert stats["selected"] == 0
        assert stats["no_airtime_benefit"] == 1
        assert stats["candidate_too_large"] == 0
        assert stats["codec_failures"] == 0


def test_real_cpp_sf8_also_rejects_zero_airtime_savings():
    payload = bytes.fromhex(
        "591ed345718eb800591c4115bcfb1c5900b88e7145d31e5900b88e7145d31e59"
    )
    with FragmentTraceNetwork(seed=70_012, tick_ms=50, sf=8) as net:
        net.add_node(1)
        net.add_node(2)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)
        net.run(60_000)

        net.send(1, 2, payload, timeout_ms=60_000, e2e_ack=True)
        net.run(100_000)
        assert _received_payloads(net, 2) == [payload]
        frames = [frame for frame in net.fragment_frames if frame[0:2] == (1, 2)]
        assert frames
        assert all((frame[5] & DTPK_FLAG_COMPRESSED) == 0 for frame in frames)
        stats = net.nodes[1].compression_stats()
        assert stats["attempts"] == 1
        assert stats["selected"] == 0
        assert stats["no_airtime_benefit"] == 1


def test_real_cpp_compressed_16k_logical_maximum_round_trips():
    payload = bytes(16 * 1024)
    with FragmentTraceNetwork(seed=70_013, tick_ms=50) as net:
        net.add_node(1)
        net.add_node(2)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)
        net.run(60_000)

        packet_id = net.send(1, 2, payload, timeout_ms=1_000, e2e_ack=True)
        net.run(240_000)
        assert packet_id != 0
        assert _received_payloads(net, 2) == [payload]
        assert sum(result == 1 for result, _ping in net.app_acks(1)) == 1
        frames = [frame for frame in net.fragment_frames if frame[0:2] == (1, 2)]
        assert frames
        assert all(frame[5] & DTPK_FLAG_COMPRESSED for frame in frames)
        assert len({frame[4] for frame in frames}) < 72
        stats = net.nodes[1].compression_stats()
        assert stats["selected"] == 1
        assert stats["original_bytes"] == len(payload)
        assert stats["encoded_bytes"] < len(payload)


def test_real_cpp_compressed_no_e2e_ack_still_delivers_without_callback():
    payload = b"mesh-status=" + b"online;" * 80
    with FragmentTraceNetwork(seed=70_014, tick_ms=50) as net:
        net.add_node(1)
        net.add_node(2)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)
        net.run(60_000)

        packet_id = net.send(1, 2, payload, timeout_ms=90_000, e2e_ack=False)
        net.run(150_000)
        assert packet_id != 0
        assert _received_payloads(net, 2) == [payload]
        assert net.app_acks(1) == []
        frames = [frame for frame in net.fragment_frames if frame[0:2] == (1, 2)]
        assert frames
        assert all(frame[5] & DTPK_FLAG_COMPRESSED for frame in frames)


def test_real_cpp_corrupted_compressed_stream_is_visible_to_sender():
    class CorruptCompressedNetwork(FragmentTraceNetwork):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.corrupted = False
            self.source_lcmm_ids = []
            self.final_nack_flags = []

        def _start_tx(self, sender, tx, at):
            packet_type = tx.payload[LCMM_HEADER_SIZE] if len(tx.payload) > 3 else None
            flags = (
                tx.payload[DTPK_FLAGS_OFFSET]
                if len(tx.payload) > DTPK_FLAGS_OFFSET
                else 0
            )
            if sender == 1 and packet_type == DTPK_DATA_SINGLE:
                self.source_lcmm_ids.append(
                    int.from_bytes(tx.payload[1:3], "little")
                )
            if sender == 2 and tx.target == 1 and packet_type == 0x43:
                self.final_nack_flags.append(flags)
            if (
                not self.corrupted
                and (sender, tx.target) == (1, 2)
                and packet_type == DTPK_DATA_SINGLE
                and flags & DTPK_FLAG_COMPRESSED
            ):
                encoded_start = LCMM_HEADER_SIZE + 11 + 3
                assert len(tx.payload) > encoded_start + 2
                damaged = bytearray(tx.payload)
                damaged[encoded_start + 1] ^= 0x5A
                tx.payload = bytes(damaged)
                self.corrupted = True
            return super()._start_tx(sender, tx, at)

    payload = b"repeated-mesh-payload;" * 20
    with CorruptCompressedNetwork(seed=70_015, tick_ms=50) as net:
        net.add_node(1)
        net.add_node(2)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)
        net.run(60_000)

        packet_id = net.send(1, 2, payload, timeout_ms=60_000, e2e_ack=True)
        net.run(120_000)
        assert packet_id != 0
        assert net.corrupted
        assert _received_payloads(net, 2) == []
        assert [result for result, _ping in net.app_acks(1)] == [0]
        assert len(set(net.source_lcmm_ids)) == 1
        assert net.final_nack_flags
        assert all(
            flags & DTPK_FLAG_NACK_FINAL_REJECT
            for flags in net.final_nack_flags
        )
        assert net.nodes[2].compression_stats()["decode_failures"] == 1
