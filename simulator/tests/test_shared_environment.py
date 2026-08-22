import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cpp_backend import CppNodeProcess
from model import Profile
from scenario import LinkStateEvent, NodeStateEvent, Scenario, route_snapshot
from shared_backends import SharedCppNetwork, SharedPythonNetwork


def test_scenario_round_trip_preserves_environment_definition():
    scenario = Scenario.line(
        3,
        seed=77,
        max_range=15.0,
        radio_contention=True,
        radio_profile="eu869-high-duty",
    )
    scenario.trajectories[2] = [(0, 1, 0), (1000, 20, 0), (2000, 1, 0)]
    scenario.link_events.append(LinkStateEvent(500, 1, 2, False))
    scenario.link_events.append(LinkStateEvent(750, 1, 2, True))
    scenario.node_events.append(NodeStateEvent(1200, 3, False))
    scenario.node_events.append(NodeStateEvent(1800, 3, True))
    restored = Scenario.from_json(scenario.to_json())
    assert restored.to_json() == scenario.to_json()
    assert restored.radio_profile == "eu869-high-duty"


def test_named_radio_profiles_do_not_impose_duty_policy_unless_strict():
    for radio_profile in ("eu868", "eu869-high-duty", "eu433"):
        practical = Scenario.line(1, seed=1, radio_profile=radio_profile).build(
            "python", profile=Profile.intended()
        )
        assert practical.duty_cycle_percent == 0.0

    eu868 = Scenario.line(
        1, seed=1, radio_profile="eu868", strict_duty_cycle=True
    ).build("python", profile=Profile.intended())
    eu869 = Scenario.line(
        1, seed=1, radio_profile="eu869-high-duty", strict_duty_cycle=True
    ).build("python", profile=Profile.intended())
    eu433 = Scenario.line(
        1, seed=1, radio_profile="eu433", strict_duty_cycle=True
    ).build("python", profile=Profile.intended())
    assert eu868.duty_cycle_percent == 1.0
    assert eu869.duty_cycle_percent == 10.0
    assert eu433.duty_cycle_percent == 10.0


def test_eu868_total_duty_period_is_ten_times_high_duty_for_same_frame():
    # Use the shared policy directly so protocol startup traffic cannot affect
    # the measurement. The total period is airtime / duty_fraction; off-time
    # alone is 99A vs 9A, so compare wait+airtime rather than only wait.
    one = SharedPythonNetwork(
        seed=1,
        profile=Profile.intended(),
        duty_cycle_percent=1.0,
    )
    ten = SharedPythonNetwork(
        seed=1,
        profile=Profile.intended(),
        duty_cycle_percent=10.0,
    )
    for net in (one, ten):
        net.register_node(1)
        net.account_transmission(1, 0.0, 100.0)
        net.now = 100.0

    period_1pct = one.transmit_wait_ms(1) + 100.0
    period_10pct = ten.transmit_wait_ms(1) + 100.0
    assert period_1pct == pytest.approx(period_10pct * 10.0)
    assert period_1pct == pytest.approx(10_000.0)
    assert period_10pct == pytest.approx(1_000.0)


def test_regulatory_tx_history_survives_environment_reboot():
    net = SharedPythonNetwork(
        seed=2,
        profile=Profile.intended(),
        duty_cycle_percent=10.0,
    )
    net.register_node(1)
    net.account_transmission(1, 0.0, 100.0)
    net.now = 250.0
    before = net.transmit_wait_ms(1)
    net.set_node_up(1, False, reason="test")
    net.set_node_up(1, True, reason="test")
    assert net.transmit_wait_ms(1) == pytest.approx(before)


def test_python_backend_uses_shared_continuous_motion_kernel():
    scenario = Scenario.line(2, seed=4, max_range=10.0)
    scenario.trajectories[1] = [(0, 0, 0), (100, 0, 0)]
    scenario.trajectories[2] = [(0, 0, 0), (50, 20, 0), (100, 0, 0)]
    net = scenario.build("python", profile=Profile.intended())
    assert net.position_at(2, 0) == (0.0, 0.0)
    assert net.position_at(2, 100) == (0.0, 0.0)
    assert net.stays_in_range(1, 2, 0, 100) is False


def test_environment_failure_epoch_is_independent_of_protocol_events():
    scenario = Scenario.line(2, seed=9)
    scenario.link_events.extend([
        LinkStateEvent(100, 1, 2, False),
        LinkStateEvent(200, 1, 2, True),
    ])
    scenario.node_events.extend([
        NodeStateEvent(125, 2, False),
        NodeStateEvent(175, 2, True),
    ])
    net = scenario.build("python", profile=Profile.intended())
    net.run(250)
    assert net.get_link(1, 2).epoch == 2
    assert net.node_epoch[2] == 2


def test_keyed_environment_loss_is_not_shifted_by_unrelated_draws():
    a = SharedPythonNetwork(seed=123, profile=Profile.intended())
    b = SharedPythonNetwork(seed=123, profile=Profile.intended())
    for net in (a, b):
        net.add_link(1, 2, loss=0.37, jitter_ms=7.0)
        net.add_link(3, 4, loss=0.91, jitter_ms=19.0)
        net.now = 1234.5
    expected_loss = a.sample_link_loss(1, 2)
    expected_jitter = a.jittered_latency(a.get_link(1, 2))
    for _ in range(50):
        b.sample_link_loss(3, 4)
        b.jittered_latency(b.get_link(3, 4))
    assert b.sample_link_loss(1, 2) == expected_loss
    assert b.jittered_latency(b.get_link(1, 2)) == expected_jitter


def test_python_and_cpp_adapters_use_identical_keyed_physical_draws_without_firmware():
    py = SharedPythonNetwork(seed=321, profile=Profile.intended())
    cpp = SharedCppNetwork(seed=321)
    for net in (py, cpp):
        net.add_link(7, 8, loss=0.43, ack_loss=0.21, latency_ms=12, jitter_ms=3)
        net.now = 987.25
    py_link = py.get_link(7, 8)
    cpp_link = cpp.get_link(7, 8)
    assert py.sample_link_loss(7, 8) == cpp.sample_link_loss(7, 8)
    assert py.sample_link_loss(8, 7, ack=True) == cpp.sample_link_loss(8, 7, ack=True)
    assert py.jittered_latency(py_link) == cpp.jittered_latency(cpp_link)


@pytest.mark.parametrize("network_type", [SharedPythonNetwork, SharedCppNetwork])
def test_shared_medium_models_hidden_terminal_collision(network_type):
    kwargs = {"profile": Profile.intended()} if network_type is SharedPythonNetwork else {}
    net = network_type(seed=44, radio_contention=True, **kwargs)
    for node in (1, 2, 3):
        net.register_node(node)
    net.add_link(1, 2, jitter_ms=0)
    net.add_link(2, 3, jitter_ms=0)
    net._record_medium_tx(1, 0.0, 100.0)
    net._record_medium_tx(3, 0.0, 100.0)
    epochs = net.capture_frame_epochs(1, 2)
    valid, reason = net.frame_path_valid(1, 2, 0.0, 100.0, *epochs)
    assert not valid
    assert reason == "collision"


@pytest.mark.parametrize("network_type", [SharedPythonNetwork, SharedCppNetwork])
def test_shared_medium_models_half_duplex_receiver(network_type):
    kwargs = {"profile": Profile.intended()} if network_type is SharedPythonNetwork else {}
    net = network_type(seed=45, radio_contention=True, **kwargs)
    for node in (1, 2, 3):
        net.register_node(node)
    net.add_link(1, 2, jitter_ms=0)
    net.add_link(2, 3, jitter_ms=0)
    net._record_medium_tx(1, 0.0, 100.0)
    net._record_medium_tx(2, 50.0, 80.0)
    epochs = net.capture_frame_epochs(1, 2)
    valid, reason = net.frame_path_valid(1, 2, 0.0, 100.0, *epochs)
    assert not valid
    assert reason == "half-duplex"


@pytest.mark.skipif(not CppNodeProcess.available(), reason="host C++ node not built")
def test_same_static_scenario_runs_python_and_cpp_adapters():
    scenario = Scenario.line(2, seed=31, latency_ms=5.0, jitter_ms=0.0)
    with scenario.build("python", profile=Profile.intended()) as py_net:
        py_net.run(60_000)
        py_routes = route_snapshot(py_net)
    with scenario.build("cpp", tick_ms=50) as cpp_net:
        cpp_net.run(60_000)
        cpp_routes = route_snapshot(cpp_net)
    assert py_routes[1].get(2) == (2, 1)
    assert py_routes[2].get(1) == (1, 1)
    assert cpp_routes[1].get(2) == (2, 1)
    assert cpp_routes[2].get(1) == (1, 1)


@pytest.mark.skipif(not CppNodeProcess.available(), reason="host C++ node not built")
def test_same_failure_trace_has_identical_physical_epochs_in_both_backends():
    scenario = Scenario.line(2, seed=32)
    scenario.link_events.extend([
        LinkStateEvent(1000, 1, 2, False),
        LinkStateEvent(1100, 1, 2, True),
    ])
    scenario.node_events.extend([
        NodeStateEvent(1025, 2, False),
        NodeStateEvent(1075, 2, True),
    ])
    with scenario.build("python", profile=Profile.intended()) as py_net:
        py_net.run(1200)
        py_link_epoch = py_net.get_link(1, 2).epoch
        py_node_epoch = py_net.node_epoch[2]
    with scenario.build("cpp", tick_ms=50) as cpp_net:
        cpp_net.run(1200)
        cpp_link_epoch = cpp_net.get_link(1, 2).epoch
        cpp_node_epoch = cpp_net.node_epoch[2]
    assert (py_link_epoch, py_node_epoch) == (cpp_link_epoch, cpp_node_epoch) == (2, 2)


@pytest.mark.skipif(not CppNodeProcess.available(), reason="host C++ node not built")
def test_cpp_route_incarnation_advances_on_reboot():
    net = SharedCppNetwork(seed=88)
    try:
        net.add_node(1)
        first = net._node_origin_sequence[1]
        net.set_node_up(1, False, reason="test-reboot")
        net.set_node_up(1, True, reason="test-reboot")
        second = net._node_origin_sequence[1]
        assert second != first
        assert second == ((first + 1) & 0xFFFF or 1)
    finally:
        net.close()
