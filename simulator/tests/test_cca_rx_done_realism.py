import pathlib
import sys

SIM_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SIM_ROOT))

from model import Profile
from radio_timing import rssi_cca_duration_ms
from timed_python_backend import TimedSharedPythonNetwork


def _network():
    net = TimedSharedPythonNetwork(
        seed=1201,
        profile=Profile.intended(),
        radio_contention=True,
    )
    for node_id in (1, 2, 3):
        node = net.add_node(node_id, start=False)
        node.up = True
    net.add_link(1, 2, latency_ms=0.0, jitter_ms=0.0)
    net.add_link(2, 3, latency_ms=0.0, jitter_ms=0.0)
    return net


def test_decodable_frame_completion_between_rssi_samples_aborts_cca():
    net = _network()
    # Synthetic short RF interval deliberately fits between the first and second
    # RSSI sample. Hardware can still assert RX_DONE at frame completion even
    # though none of the instantaneous samples saw energy.
    net._record_medium_tx(1, 1.0, 3.0)

    assert net._rssi_cca_busy(2, 0.0) is True


def test_collided_frame_completion_does_not_invent_rx_done_during_cca():
    net = _network()
    # Both short intervals fit between RSSI samples and overlap at node 2. Under
    # the simulator's current no-capture collision model neither frame can
    # decode, so neither completion is allowed to masquerade as RX_DONE.
    net._record_medium_tx(1, 1.0, 3.0)
    net._record_medium_tx(3, 2.0, 4.0)

    assert net._rssi_cca_busy(2, 0.0) is False


def test_frame_whose_preamble_started_while_receiver_busy_cannot_raise_rx_done():
    net = _network()
    net.nodes[2].radio_busy_until = 2.0
    net._record_medium_tx(1, 1.0, 3.0)

    assert net._rx_completion_during_cca(
        2, 0.0, rssi_cca_duration_ms()
    ) is False
