import pathlib
import sys

import pytest

SIM_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SIM_ROOT))

from model import DTPK_HELLO_SIZE, Packet, Profile
from radio_timing import rx_rearm_after_read_ms
from timed_python_backend import TimedSharedPythonNetwork


def _network():
    net = TimedSharedPythonNetwork(
        seed=911,
        profile=Profile.crystallized_v2(),
        radio_contention=False,
    )
    net.add_node(1)
    net.add_node(2)
    net.add_link(1, 2, latency_ms=0.0, jitter_ms=0.0)
    net.run(0)
    return net


def test_noack_rx_callback_reserves_documented_startreceive_interval():
    net = _network()
    net.now = 1234.0
    packet = Packet(
        "HELLO",
        77,
        wire_dtpk_size=DTPK_HELLO_SIZE,
        sender_sequence=1,
        route_version=1,
    )

    net._deliver_after_read(2, 1, packet, False, None, net.node_epoch[2])

    assert net.nodes[2].radio_busy_until == pytest.approx(
        1234.0 + rx_rearm_after_read_ms(), abs=1e-12
    )


def test_link_ack_receive_rearms_before_owner_can_start_next_radio_operation():
    net = _network()
    net.now = 4321.0
    completed = []

    net._complete_ack_receive(
        1,
        net.node_epoch[1],
        lambda ok: completed.append((ok, net.nodes[1].radio_busy_until)),
    )

    assert completed and completed[0][0] is True
    assert completed[0][1] == pytest.approx(
        4321.0 + rx_rearm_after_read_ms(), abs=1e-12
    )
