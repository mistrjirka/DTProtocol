from __future__ import annotations

import pathlib
import random
import struct
import sys
from typing import List

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cpp_backend import CppNetwork, CppNodeProcess

pytestmark = pytest.mark.skipif(
    not CppNodeProcess.available(),
    reason="host C++ node not built",
)

DTPK_DATA_FRAGMENT = 0x47
DTPK_FLAG_DEBUG_ECHO = 0x04


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
    *,
    timeout_ms: float = 30_000,
) -> None:
    deadline = net.now + timeout_ms
    while net.now <= deadline:
        before = len(net.nodes[receiver].events)
        txs = net.nodes[receiver].inject(
            net.now, sender, wire_target, frame
        )
        results = [
            values
            for kind, values in net.nodes[receiver].events[before:]
            if kind == "INJECTED"
        ]
        assert results
        if results[-1][0] == "1":
            net._handle_txs(receiver, txs, net.now)
            return
        net.run(min(deadline, net.now + 25.0))
    raise AssertionError("receiver never returned to RX")



def _hello_frame(lcmm_id: int, origin_sequence: int, route_version: int) -> bytes:
    return struct.pack(
        "<BHBHI", 0, lcmm_id, 0x44, origin_sequence, route_version
    )


def _cryst_header_frame(
    lcmm_id: int,
    origin_sequence: int,
    route_version: int,
    chunk_index: int,
    chunk_count: int,
) -> bytes:
    return struct.pack(
        "<BHBHIHH",
        0,
        lcmm_id,
        0x40,
        origin_sequence,
        route_version,
        chunk_index,
        chunk_count,
    )


def _fragment_frame(
    lcmm_id: int,
    packet_id: int,
    source_sequence: int,
    original_sender: int,
    final_target: int,
    flags: int,
    total_size: int,
    fragment_index: int,
    payload: bytes,
) -> bytes:
    return (
        struct.pack("<BH", 1, lcmm_id)
        + struct.pack(
            "<BHHHHBBHB",
            DTPK_DATA_FRAGMENT,
            packet_id,
            source_sequence,
            original_sender,
            final_target,
            flags,
            255,
            total_size,
            fragment_index,
        )
        + payload
    )


def test_fragment_assembly_rejects_inconsistent_flags_but_accepts_correct_repair():
    payload = bytes((index * 61 + 7) & 0xFF for index in range(300))
    first, second = payload[:230], payload[230:]
    with CppNetwork(seed=94_001, tick_ms=25) as net:
        net.add_node(1)
        net.add_node(2)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)
        net.run(60_000)
        sequence = net._node_origin_sequence[1]

        _inject_when_receiving(
            net,
            2,
            1,
            2,
            _fragment_frame(
                0xC101,
                0x9401,
                sequence,
                1,
                2,
                0,
                len(payload),
                0,
                first,
            ),
        )
        _inject_when_receiving(
            net,
            2,
            1,
            2,
            _fragment_frame(
                0xC102,
                0x9401,
                sequence,
                1,
                2,
                DTPK_FLAG_DEBUG_ECHO,
                len(payload),
                1,
                second,
            ),
        )
        net.run(net.now + 5_000)
        assert _received_payloads(net, 2) == []

        # A later fragment with metadata matching the transaction completes the
        # original assembly. The mismatched fragment did not poison its flags.
        _inject_when_receiving(
            net,
            2,
            1,
            2,
            _fragment_frame(
                0xC103,
                0x9401,
                sequence,
                1,
                2,
                0,
                len(payload),
                1,
                second,
            ),
        )
        assert _run_until(
            net,
            lambda: _received_payloads(net, 2) == [payload],
            net.now + 20_000,
        )


def test_deterministic_malformed_frame_corpus_does_not_crash_or_poison_recovery():
    generator = random.Random(0x94F022)
    with CppNetwork(seed=94_002, tick_ms=25) as net:
        net.add_node(1)
        net.add_node(2)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)
        net.run(60_000)

        # Exercise every length around the LCMM/DTPK boundaries, plus randomized
        # type/flag/count combinations. MAC CRC is valid because hostInject()
        # constructs the MAC envelope exactly as real RadioLib receive code does.
        lengths = list(range(1, 40)) + [63, 127, 200, 230, 233, 234, 244]
        for case in range(320):
            size = lengths[case % len(lengths)] if case < len(lengths) else generator.randint(1, 244)
            frame = bytearray(generator.getrandbits(8) for _ in range(size))
            frame[0] = (0, 1, 4, 0xFF)[case % 4]
            if size >= 3:
                frame[1:3] = (case & 0xFFFF).to_bytes(2, "little")
            if size >= 4 and case % 3 == 0:
                frame[3] = 0x40 | (case % 16)
            _inject_when_receiving(net, 1, 2, 1, bytes(frame))
            net.run(net.now + 2.0)

        # Reset all protocol state after fuzzing, then prove both processes,
        # radio state machines, and ordinary delivery remain healthy.
        net.set_node_up(1, False, reason="post-fuzz-reset")
        net.set_node_up(2, False, reason="post-fuzz-reset")
        net.run(net.now + 500)
        net.set_node_up(1, True, reason="post-fuzz-reset")
        net.set_node_up(2, True, reason="post-fuzz-reset")
        net.run(net.now + 60_000)
        assert net.routes(2).get(1) == (1, 1)

        payload = b"valid-after-malformed-corpus"
        assert net.send(
            2, 1, payload, timeout_ms=60_000, e2e_ack=True
        ) != 0
        assert _run_until(
            net,
            lambda: _received_payloads(net, 1) == [payload]
            and any(result == 1 for result, _ping in net.app_acks(2)),
            net.now + 90_000,
        )


def _invalid_fragment_frame(lcmm_id: int, source_sequence: int) -> bytes:
    # totalSize=300 permits only fragment indices 0 and 1. Index 9 is impossible
    # but all fixed headers are otherwise well formed.
    return _fragment_frame(
        lcmm_id,
        0xE102,
        source_sequence,
        1,
        2,
        0,
        300,
        9,
        b"",
    )


def test_structurally_invalid_data_neither_installs_route_nor_refreshes_liveness():
    with CppNetwork(seed=94_003, tick_ms=25) as net:
        net.add_node(1)
        net.add_node(2)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)
        net.run(60_000)
        assert net.routes(2).get(1) == (1, 1)
        sequence = net._node_origin_sequence[1]

        # Remove the physical link, then keep injecting link-valid but DTPK-
        # invalid fragments. They may receive a lower-layer link ACK, but must
        # not be accepted as routing/liveness evidence.
        net.set_link(1, 2, False)
        for index in range(7):
            _inject_when_receiving(
                net,
                2,
                1,
                2,
                _invalid_fragment_frame(0xE200 + index, sequence),
            )
            net.run(net.now + 20_000)

        assert 1 not in net.routes(2)
        assert _received_payloads(net, 2) == []

        # A later valid link and packet still recover normally.
        net.set_link(1, 2, True)
        assert _run_until(
            net,
            lambda: net.routes(2).get(1) == (1, 1),
            net.now + 60_000,
        )
        payload = b"valid-after-structural-rejection"
        assert net.send(
            1, 2, payload, timeout_ms=60_000, e2e_ack=True
        ) != 0
        assert _run_until(
            net,
            lambda: _received_payloads(net, 2) == [payload]
            and any(result == 1 for result, _ping in net.app_acks(1)),
            net.now + 90_000,
        )



def test_semantically_invalid_startup_control_does_not_keep_dead_neighbor_alive():
    with CppNetwork(seed=94_004, tick_ms=25) as net:
        net.add_node(1)
        net.add_node(2)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)
        net.run(60_000)
        assert net.routes(2).get(1) == (1, 1)

        net.set_link(1, 2, False)
        invalid_frames = (
            lambda index: _hello_frame(0xF100 + index, 0, 7),
            lambda index: _hello_frame(0xF200 + index, 7, 0),
            lambda index: _cryst_header_frame(
                0xF300 + index, 0, 9, 0, 1
            ),
            lambda index: _cryst_header_frame(
                0xF400 + index, 9, 0, 0, 1
            ),
            lambda index: _cryst_header_frame(
                0xF500 + index, 9, 9, 1, 1
            ),
        )
        for index in range(7):
            for make_frame in invalid_frames:
                _inject_when_receiving(
                    net, 2, 1, 0, make_frame(index)
                )
            net.run(net.now + 20_000)

        assert 1 not in net.routes(2)
        assert _received_payloads(net, 2) == []

        net.set_link(1, 2, True)
        assert _run_until(
            net,
            lambda: net.routes(2).get(1) == (1, 1),
            net.now + 90_000,
        )


def _data_frame(
    lcmm_id: int,
    packet_id: int,
    source_sequence: int,
    original_sender: int,
    final_target: int,
    payload: bytes,
) -> bytes:
    return (
        struct.pack("<BH", 0, lcmm_id)
        + struct.pack(
            "<BHHHHBB",
            0x41,
            packet_id,
            source_sequence,
            original_sender,
            final_target,
            0,
            255,
        )
        + payload
    )


def _cryst_records_frame(
    lcmm_id: int,
    origin_sequence: int,
    route_version: int,
    chunk_index: int,
    chunk_count: int,
    records,
) -> bytes:
    return (
        _cryst_header_frame(
            lcmm_id,
            origin_sequence,
            route_version,
            chunk_index,
            chunk_count,
        )
        + b"".join(
            struct.pack("<HHB", destination, sequence, distance)
            for destination, sequence, distance in records
        )
    )


def test_packet_id_replay_window_handles_wrap_and_out_of_order_delivery():
    with CppNetwork(seed=94_005, tick_ms=25) as net:
        net.add_node(1)
        net.add_node(2)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)
        net.run(60_000)
        sequence = net._node_origin_sequence[1]

        cases = (
            (0xFFFE, b"id-65534"),
            (0xFFFF, b"id-65535"),
            (1, b"id-1-after-wrap"),
            (0xFFFF, b"duplicate-65535"),
            (3, b"id-3"),
            (2, b"id-2-out-of-order"),
            (2, b"duplicate-2"),
            (0xF000, b"too-old-outside-window"),
        )
        for index, (packet_id, payload) in enumerate(cases):
            _inject_when_receiving(
                net,
                2,
                1,
                2,
                _data_frame(
                    0xA100 + index,
                    packet_id,
                    sequence,
                    1,
                    2,
                    payload,
                ),
            )

        assert _received_payloads(net, 2) == [
            b"id-65534",
            b"id-65535",
            b"id-1-after-wrap",
            b"id-3",
            b"id-2-out-of-order",
        ]

        # A newer source incarnation starts a fresh packet-ID space. A delayed
        # packet from the old incarnation remains stale after that transition.
        newer = ((sequence + 1) & 0xFFFF) or 1
        _inject_when_receiving(
            net,
            2,
            1,
            2,
            _data_frame(0xA200, 1, newer, 1, 2, b"new-boot-id-1"),
        )
        _inject_when_receiving(
            net,
            2,
            1,
            2,
            _data_frame(0xA201, 4, sequence, 1, 2, b"late-old-boot"),
        )
        assert _received_payloads(net, 2)[-1] == b"new-boot-id-1"
        assert b"late-old-boot" not in _received_payloads(net, 2)


def test_duplicate_destinations_across_cryst_chunks_are_transactionally_rejected():
    with CppNetwork(seed=94_006, tick_ms=25) as net:
        net.add_node(1)
        net.add_node(2)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)
        net.run(60_000)
        origin = net._node_origin_sequence[1]
        version = 0x1000

        _inject_when_receiving(
            net,
            2,
            1,
            0,
            _cryst_records_frame(
                0xB100, origin, version, 0, 2, [(5, 0x2345, 1)]
            ),
        )
        _inject_when_receiving(
            net,
            2,
            1,
            0,
            _cryst_records_frame(
                0xB101, origin, version, 1, 2, [(5, 0x2346, 2)]
            ),
        )
        assert 5 not in net.routes(2)

        # A later well-formed snapshot is still accepted; rejection did not
        # poison the neighbor's assembly or committed state.
        _inject_when_receiving(
            net,
            2,
            1,
            0,
            _cryst_records_frame(
                0xB102, origin, version + 1, 0, 1, [(5, 0x2346, 1)]
            ),
        )
        assert net.routes(2).get(5) == (1, 2)


def test_invalid_cryst_records_do_not_refresh_dead_neighbor_liveness():
    with CppNetwork(seed=94_007, tick_ms=25) as net:
        net.add_node(1)
        net.add_node(2)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)
        net.run(60_000)
        assert net.routes(2).get(1) == (1, 1)
        origin = net._node_origin_sequence[1]

        net.set_link(1, 2, False)
        invalid_records = (
            [(0, 0x3001, 1)],       # broadcast destination
            [(5, 0, 1)],            # zero destination generation
            [(5, 0x3002, 0)],       # impossible self metric
            [(1, 0x3003, 1)],       # neighbor advertising itself
            [(5, 0x3004, 254)],     # unusable infinity-adjacent metric
        )
        for index in range(7):
            _inject_when_receiving(
                net,
                2,
                1,
                0,
                _cryst_records_frame(
                    0xC200 + index,
                    origin,
                    0x2000 + index,
                    0,
                    1,
                    invalid_records[index % len(invalid_records)],
                ),
            )
            net.run(net.now + 20_000)

        assert 1 not in net.routes(2)
        assert 0 not in net.routes(2)
        assert 5 not in net.routes(2)
