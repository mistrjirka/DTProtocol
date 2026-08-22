import pathlib
import sys

import pytest

SIM_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SIM_ROOT))

from cpp_backend import CppNodeProcess
from model import Profile
from unified_scenarios import (
    leave_and_return_midair_scenario,
    line_network,
    midair_link_flap_scenario,
)
from unified_simulator import UnifiedSimulator


def test_unified_python_lossless_two_node_converges():
    with line_network(
        backend="python",
        nodes=2,
        seed=11,
        profile=Profile.intended(),
    ) as sim:
        sim.run(60_000)
        assert sim.audit()["correct"], sim.audit()
        assert sim.routes(1).get(2) == (2, 1)
        assert sim.routes(2).get(1) == (1, 1)


def test_unified_environment_motion_is_continuous_not_sampled():
    with UnifiedSimulator(backend="python", seed=1, profile=Profile.intended()) as sim:
        sim.add_node(1, position=(0, 0))
        sim.add_node(2, position=(0, 0))
        sim.add_link(1, 2, max_range=10, jitter_ms=0)
        sim.set_trajectory(1, [(0, 0, 0), (100, 0, 0)])
        sim.set_trajectory(2, [(0, 0, 0), (50, 20, 0), (100, 0, 0)])
        assert not sim.stays_in_range(1, 2, 0, 100)
        assert sim.position_at(2, 0) == (0.0, 0.0)
        assert sim.position_at(2, 100) == (0.0, 0.0)


def test_environment_failure_priority_is_independent_of_protocol_order():
    with UnifiedSimulator(backend="python", seed=2, profile=Profile.intended()) as sim:
        sim.add_node(1)
        sim.add_node(2)
        sim.add_link(1, 2)
        observed = []
        # Schedule protocol first deliberately. Environment priority must still
        # make the physical transition happen first at an identical timestamp.
        sim.schedule_at(100, lambda: observed.append(sim.get_link(1, 2).up))
        sim.set_link_at(100, 1, 2, False)
        sim.run(100)
        assert observed == [False]


@pytest.mark.skipif(not CppNodeProcess.available(), reason="host C++ node not built")
def test_same_lossless_discovery_scenario_python_current_matches_real_cpp():
    py = line_network(
        backend="python",
        nodes=2,
        seed=11,
        profile=Profile.current(),
    )
    cpp = line_network(
        backend="cpp",
        nodes=2,
        seed=11,
        profile=Profile.current(),
    )
    try:
        py.run(60_000)
        cpp.run(60_000)
        assert py.routes(1) == cpp.routes(1)
        assert py.routes(2) == cpp.routes(2)
    finally:
        py.close()
        cpp.close()


@pytest.mark.skipif(not CppNodeProcess.available(), reason="host C++ node not built")
@pytest.mark.parametrize("backend", ["python", "cpp"])
def test_same_midair_link_failure_scenario_runs_on_both_backends(backend):
    profile = Profile.intended() if backend == "python" else Profile.current()
    sim = midair_link_flap_scenario(backend=backend, seed=101, profile=profile)
    try:
        assert 2 in sim.routes(1) and 1 in sim.routes(2)
    finally:
        sim.close()


@pytest.mark.skipif(not CppNodeProcess.available(), reason="host C++ node not built")
@pytest.mark.parametrize("backend", ["python", "cpp"])
def test_same_continuous_mobility_scenario_runs_on_both_backends(backend):
    profile = Profile.intended() if backend == "python" else Profile.current()
    sim = leave_and_return_midair_scenario(backend=backend, seed=102, profile=profile)
    try:
        # The first frame is invalid because the receiver crossed the range
        # boundary while it was on air, regardless of protocol backend.
        assert 2 in sim.routes(1) and 1 in sim.routes(2)
    finally:
        sim.close()
