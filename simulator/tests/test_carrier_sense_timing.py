import pathlib
import sys

import pytest

SIM_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SIM_ROOT))

from model import Packet, Profile
from radio_timing import rssi_cca_duration_ms, tx_startup_ms
from shared_backends import SharedPythonNetwork


def _node(net, node_id):
    node = net.add_node(node_id, start=False)
    node.up = True
    return node


def _data(packet_id: int, sender: int, target: int, payload: int = 16) -> Packet:
    return Packet(
        "DATA",
        packet_id,
        original_sender=sender,
        final_target=target,
        payload_size=payload,
        wire_dtpk_size=9 + payload,
    )


def test_idle_tx_rf_start_includes_rssi_cca_and_radiolib_startup():
    net = SharedPythonNetwork(
        seed=901,
        profile=Profile.intended(),
        radio_contention=True,
    )
    _node(net, 1); _node(net, 2)
    net.add_link(1, 2, latency_ms=0, jitter_ms=0)
    packet = _data(1, 1, 2)
    frame_bytes = net._frame_bytes(packet)
    expected_start = rssi_cca_duration_ms() + tx_startup_ms(frame_bytes)

    net.transmit(1, 2, packet, False, lambda ok: None)
    net.run(expected_start - 0.001)
    assert not [tx for tx in net._medium_tx if tx["sender"] == 1]

    net.run(expected_start + 0.001)
    tx = next(tx for tx in net._medium_tx if tx["sender"] == 1)
    assert tx["start"] == pytest.approx(expected_start, abs=1e-9)
    assert net.medium_metrics.cca_scans == 1
    assert net.medium_metrics.cca_clear == 1


def test_audible_existing_tx_causes_randomized_25_to_250ms_backoff():
    net = SharedPythonNetwork(
        seed=902,
        profile=Profile.intended(),
        radio_contention=True,
    )
    for node_id in (1, 2, 3):
        _node(net, node_id)
    net.add_link(1, 2, latency_ms=0, jitter_ms=0)
    net.add_link(1, 3, latency_ms=0, jitter_ms=0)
    net.add_link(2, 3, latency_ms=0, jitter_ms=0)

    # Existing third-party RF energy spans all three RSSI samples at node 1.
    net._record_medium_tx(3, 0.0, 200.0)
    packet = _data(2, 1, 2)
    net.transmit(1, 2, packet, False, lambda ok: None)
    net.run(rssi_cca_duration_ms() + 0.001)

    assert net.medium_metrics.cca_busy == 1
    assert 25.0 <= net.medium_metrics.cca_backoff_ms <= 250.0
    assert not [tx for tx in net._medium_tx if tx["sender"] == 1]

    net.run(1_000)
    assert [tx for tx in net._medium_tx if tx["sender"] == 1]


def test_simultaneous_idle_cca_can_still_collide_at_common_receiver():
    net = SharedPythonNetwork(
        seed=903,
        profile=Profile.intended(),
        radio_contention=True,
    )
    for node_id in (1, 2, 3):
        _node(net, node_id)
    # Hidden terminals: both reach 2, but 1 and 3 cannot sense one another.
    net.add_link(1, 2, latency_ms=0, jitter_ms=0)
    net.add_link(2, 3, latency_ms=0, jitter_ms=0)

    delivered = []
    net.nodes[2].receive = lambda packet, previous_hop: delivered.append(packet.packet_id)
    net.transmit(1, 2, _data(10, 1, 2, 32), False, lambda ok: None)
    net.transmit(3, 2, _data(11, 3, 2, 32), False, lambda ok: None)
    net.run(2_000)

    assert delivered == []
    assert net.medium_metrics.collision_drops >= 2


def test_audible_tx_started_during_cca_is_seen_by_later_rssi_sample():
    net = SharedPythonNetwork(
        seed=904,
        profile=Profile.intended(),
        radio_contention=True,
    )
    for node_id in (1, 2, 3):
        _node(net, node_id)
    net.add_link(1, 2, latency_ms=0, jitter_ms=0)
    net.add_link(1, 3, latency_ms=0, jitter_ms=0)
    net.add_link(2, 3, latency_ms=0, jitter_ms=0)

    net.transmit(1, 2, _data(20, 1, 2, 64), False, lambda ok: None)
    # Node 3 begins sensing 5 ms later. Its early samples are clear, but the
    # final sample occurs after node 1 has entered RF TX and must defer.
    net.schedule(
        5.0,
        net.transmit,
        3,
        2,
        _data(21, 3, 2, 16),
        False,
        lambda ok: None,
    )
    net.run(2_000)

    assert net.medium_metrics.cca_busy >= 1
    assert net.medium_metrics.collision_drops == 0


def test_reliable_link_ack_pays_its_own_cca_setup_and_airtime():
    net = SharedPythonNetwork(
        seed=905,
        profile=Profile.intended(),
        radio_contention=True,
    )
    _node(net, 1); _node(net, 2)
    net.add_link(1, 2, latency_ms=0, jitter_ms=0)

    packet = _data(30, 1, 2, 16)
    frame_bytes = net._frame_bytes(packet)
    ack_bytes = 8 + 3
    expected_data_start = rssi_cca_duration_ms() + tx_startup_ms(frame_bytes)
    expected_data_end = expected_data_start + net.airtime_ms(frame_bytes)
    expected_ack_start = (
        expected_data_end + rssi_cca_duration_ms() + tx_startup_ms(ack_bytes)
    )
    expected_ack_end = expected_ack_start + net.airtime_ms(ack_bytes)

    completions = []
    net.transmit(1, 2, packet, True, lambda ok: completions.append((net.now, ok)))
    net.run(expected_ack_end - 0.001)
    assert completions == []

    net.run(expected_ack_end + 0.001)
    assert completions == [(pytest.approx(expected_ack_end, abs=1e-6), True)]
    assert net.metrics.radio_link_ack_frames == 1
    assert net.medium_metrics.cca_scans >= 2
