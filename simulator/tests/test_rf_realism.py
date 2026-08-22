import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from environment import EnvironmentKernel
from model import Profile
from scenario import Scenario
from shared_backends import SharedCppNetwork, SharedPythonNetwork


def _registered_network(*, seed=1, contention=True):
    net = SharedPythonNetwork(
        seed=seed,
        profile=Profile.intended(),
        radio_contention=contention,
    )
    return net


def test_decode_interference_and_cca_ranges_are_independent():
    net = _registered_network(seed=41)
    net.register_node(1, position=(0.0, 0.0))
    net.register_node(2, position=(10.0, 0.0))
    net.add_link(
        1,
        2,
        max_range=5.0,
        interference_range=12.0,
        cca_range=11.0,
    )

    assert not net.frame_start_valid(1, 2)
    assert not net.in_range_now(1, 2)
    assert net.in_interference_range_now(1, 2)
    assert net.in_cca_range_now(1, 2)

    net._record_medium_tx(1, 0.0, 100.0)
    assert net._medium_energy_at(2, 50.0)


def test_undecodable_hidden_interferer_can_still_destroy_frame():
    net = _registered_network(seed=42)
    net.register_node(1, position=(0.0, 0.0))
    net.register_node(2, position=(1.0, 0.0))
    net.register_node(3, position=(10.0, 0.0))
    net.add_link(1, 2, max_range=2.0)
    net.add_link(
        3,
        2,
        max_range=3.0,
        interference_range=12.0,
        cca_range=3.0,
    )

    assert not net.frame_start_valid(3, 2)
    assert net.in_interference_range_now(3, 2)
    assert not net.in_cca_range_now(3, 2)

    net._record_medium_tx(1, 0.0, 100.0)
    net._record_medium_tx(3, 0.0, 100.0)
    epochs = net.capture_frame_epochs(1, 2)
    valid, reason = net.frame_path_valid(1, 2, 0.0, 100.0, *epochs)
    assert not valid
    assert reason == "collision"


def test_gilbert_elliott_fading_creates_deterministic_bursts():
    env = EnvironmentKernel(seed=43)
    env.register_node(1)
    env.register_node(2)
    env.add_link(
        1,
        2,
        loss=0.0,
        burst_bad_loss=1.0,
        burst_good_to_bad=1.0,
        burst_bad_to_good=1.0,
    )

    # The current state applies to the current frame, then transitions. Starting
    # in good state therefore yields good, bad, good, bad ... exactly.
    assert [env.sample_link_loss(1, 2) for _ in range(6)] == [
        False,
        True,
        False,
        True,
        False,
        True,
    ]
    assert env.rf_metrics.burst_bad_samples == 3
    assert env.rf_metrics.burst_state_transitions == 6


def test_burst_fading_draws_match_python_and_cpp_physical_backends():
    py = SharedPythonNetwork(seed=44, profile=Profile.intended())
    cpp = SharedCppNetwork(seed=44)
    for net in (py, cpp):
        net.add_link(
            7,
            8,
            loss=0.05,
            ack_loss=0.1,
            burst_bad_loss=0.9,
            burst_good_to_bad=0.3,
            burst_bad_to_good=0.2,
        )
        net.now = 1_000.0

    assert [py.sample_link_loss(7, 8) for _ in range(20)] == [
        cpp.sample_link_loss(7, 8) for _ in range(20)
    ]
    assert [py.sample_link_loss(8, 7, ack=True) for _ in range(20)] == [
        cpp.sample_link_loss(8, 7, ack=True) for _ in range(20)
    ]


def test_scenario_round_trip_preserves_rf_realism_parameters():
    scenario = Scenario.line(
        2,
        seed=45,
        max_range=10.0,
        interference_range=20.0,
        cca_range=15.0,
        loss=0.02,
        burst_bad_loss=0.8,
        burst_good_to_bad=0.1,
        burst_bad_to_good=0.25,
        radio_contention=True,
    )
    restored = Scenario.from_json(scenario.to_json())
    assert restored == scenario
    link = restored.links[0]
    assert link.max_range == 10.0
    assert link.interference_range == 20.0
    assert link.cca_range == 15.0
    assert link.burst_bad_loss == 0.8
