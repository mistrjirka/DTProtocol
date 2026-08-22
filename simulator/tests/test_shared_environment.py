import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cpp_backend import CppNodeProcess
from model import Profile
from scenario import LinkStateEvent, NodeStateEvent, Scenario, route_snapshot


def test_scenario_round_trip_preserves_environment_definition():
    scenario = Scenario.line(3, seed=77, max_range=15.0)
    scenario.trajectories[2] = [
        (0, 1, 0),
        (1000, 20, 0),
        (2000, 1, 0),
    ]
    scenario.link_events.append(LinkStateEvent(500, 1, 2, False))
    scenario.link_events.append(LinkStateEvent(750, 1, 2, True))
    scenario.node_events.append(NodeStateEvent(1200, 3, False))
    scenario.node_events.append(NodeStateEvent(1800, 3, True))

    restored = Scenario.from_json(scenario.to_json())
    assert restored.to_json() == scenario.to_json()


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

    # A down->up transition that happens entirely between protocol events still
    # changes the epoch and therefore invalidates any frame spanning it.
    assert net.get_link(1, 2).epoch == 2
    assert net.node_epoch[2] == 2


@pytest.mark.skipif(
    not CppNodeProcess.available(),
    reason="host C++ node not built",
)
def test_same_static_scenario_runs_python_and_cpp_adapters():
    scenario = Scenario.line(2, seed=31, latency_ms=5.0, jitter_ms=0.0)

    with scenario.build("python", profile=Profile.intended()) as py_net:
        py_net.run(60_000)
        py_routes = route_snapshot(py_net)

    with scenario.build("cpp", tick_ms=50) as cpp_net:
        cpp_net.run(60_000)
        cpp_routes = route_snapshot(cpp_net)

    # We compare the externally observable routing result, not internal data
    # structures. The adapters are allowed to implement the protocol differently.
    assert py_routes[1].get(2) == (2, 1)
    assert py_routes[2].get(1) == (1, 1)
    assert cpp_routes[1].get(2) == (2, 1)
    assert cpp_routes[2].get(1) == (1, 1)


@pytest.mark.skipif(
    not CppNodeProcess.available(),
    reason="host C++ node not built",
)
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
