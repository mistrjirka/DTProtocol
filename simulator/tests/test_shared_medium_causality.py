import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from model import Profile
from shared_backends import SharedCppNetwork, SharedPythonNetwork


@pytest.mark.parametrize("network_type", [SharedPythonNetwork, SharedCppNetwork])
def test_interference_before_link_failure_is_not_erased_retroactively(network_type):
    kwargs = {"profile": Profile.intended()} if network_type is SharedPythonNetwork else {}
    net = network_type(seed=501, radio_contention=True, **kwargs)
    for node in (1, 2, 3):
        net.register_node(node)
    net.add_link(1, 2, jitter_ms=0)
    net.add_link(3, 2, jitter_ms=0)

    # Desired 1->2 and hidden-terminal interferer 3->2 overlap from t=0.
    net._record_medium_tx(1, 0.0, 100.0)
    net._record_medium_tx(3, 0.0, 100.0)
    desired_epochs = net.capture_frame_epochs(1, 2)

    # The interferer disappears after 20 ms. The first 20 ms already happened;
    # decode-time state must not retroactively remove that physical collision.
    net.now = 20.0
    net.set_link(3, 2, False)
    net.now = 100.0
    valid, reason = net.frame_path_valid(1, 2, 0.0, 100.0, *desired_epochs)
    assert not valid
    assert reason == "collision"


@pytest.mark.parametrize("network_type", [SharedPythonNetwork, SharedCppNetwork])
def test_recovery_after_overlap_does_not_create_past_interference(network_type):
    kwargs = {"profile": Profile.intended()} if network_type is SharedPythonNetwork else {}
    net = network_type(seed=502, radio_contention=True, **kwargs)
    for node in (1, 2, 3):
        net.register_node(node)
    net.add_link(1, 2, jitter_ms=0)
    net.add_link(3, 2, jitter_ms=0, up=False)

    net._record_medium_tx(1, 0.0, 80.0)
    net._record_medium_tx(3, 0.0, 80.0)
    desired_epochs = net.capture_frame_epochs(1, 2)

    # 3 becomes audible only after both RF frames are over.
    net.now = 90.0
    net.set_link(3, 2, True)
    net.now = 100.0
    valid, reason = net.frame_path_valid(1, 2, 0.0, 80.0, *desired_epochs)
    assert valid, reason


@pytest.mark.parametrize("network_type", [SharedPythonNetwork, SharedCppNetwork])
def test_mobile_interferer_crossing_range_midframe_collides(network_type):
    kwargs = {"profile": Profile.intended()} if network_type is SharedPythonNetwork else {}
    net = network_type(seed=503, radio_contention=True, **kwargs)
    for node in (1, 2, 3):
        net.register_node(node)
    net.add_link(1, 2, jitter_ms=0, max_range=10)
    net.add_link(3, 2, jitter_ms=0, max_range=10)
    net.set_trajectory(2, [(0, 0, 0), (100, 0, 0)])
    net.set_trajectory(3, [(0, 20, 0), (50, 0, 0), (100, 20, 0)])

    net._record_medium_tx(1, 0.0, 100.0)
    net._record_medium_tx(3, 0.0, 100.0)
    desired_epochs = net.capture_frame_epochs(1, 2)
    net.now = 100.0
    valid, reason = net.frame_path_valid(1, 2, 0.0, 100.0, *desired_epochs)
    assert not valid
    assert reason == "collision"
