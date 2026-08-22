import pathlib
import sys

SIM_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SIM_ROOT))

from model import AdvertisedRoute, Profile
from timed_python_backend import TimedSharedPythonNetwork, TimedV2Node


def _two_node_network():
    net = TimedSharedPythonNetwork(seed=1901, profile=Profile.crystallized_v2())
    first = net.add_node(1, start=False)
    second = net.add_node(2, start=False)
    first.up = True
    second.up = True
    net.add_link(1, 2, latency_ms=0, jitter_ms=0)

    first.routes_by_neighbor[2] = {
        2: AdvertisedRoute(2, 1, 1, 1),
    }
    second.routes_by_neighbor[1] = {
        1: AdvertisedRoute(1, 2, 1, 1),
    }
    first.rebuild_routes()
    second.rebuild_routes()
    return net, first, second


def test_packet_id_zero_is_reserved_across_startup_and_wrap():
    _net, first, _second = _two_node_network()
    first.packet_counter = 0
    assert first.next_packet_id() == 1
    first.packet_counter = 0xFFFE
    assert first.next_packet_id() == 0xFFFF
    assert first.next_packet_id() == 1


def test_no_e2e_data_does_not_generate_routed_ack():
    net, _first, second = _two_node_network()
    before_data = net.metrics.radio_data_frames
    before_link_ack = net.metrics.radio_link_ack_frames

    packet_id = net.send(1, 2, b"one-way", timeout_ms=10_000, e2e_ack=False)
    assert packet_id != 0
    net.run(5_000)

    assert net.metrics.delivered_app == 1
    assert net.metrics.e2e_success == 0
    assert net.metrics.e2e_failure == 0
    # One DATA frame and its LCMM ACK. There is no routed DTPK ACK frame.
    assert net.metrics.radio_data_frames - before_data == 1
    assert net.metrics.radio_link_ack_frames - before_link_ack == 1
    assert not second.txq


def test_any_successful_reliable_unicast_refreshes_sender_liveness():
    net, first, _second = _two_node_network()
    assert isinstance(first, TimedV2Node)
    first.last_heard[2] = -50_000.0
    first.last_liveness_probe[2] = -20_000.0

    net.send(1, 2, b"one-way", timeout_ms=10_000, e2e_ack=False)
    net.run(5_000)

    assert first.last_heard[2] > 0.0
    assert 2 not in first.last_liveness_probe
