import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[0]
sys.path.insert(0, str(ROOT))

from cpp_backend import CppNodeProcess
from cpp_environment import MovingCppNetwork


requires_cpp = pytest.mark.skipif(
    not CppNodeProcess.available(),
    reason="host C++ node not built",
)


def test_continuous_motion_detects_leave_and_return_inside_one_frame():
    net = MovingCppNetwork(seed=1)
    net.set_trajectory(1, [(0, 0, 0), (100, 0, 0)])
    net.set_trajectory(2, [(0, 0, 0), (50, 20, 0), (100, 0, 0)])
    net.link_max_range[frozenset((1, 2))] = 10.0

    assert net.stays_in_range(1, 2, 0, 100) is False
    assert net.position_at(2, 0) == (0.0, 0.0)
    assert net.position_at(2, 100) == (0.0, 0.0)


@requires_cpp
def test_real_cpp_link_failure_mid_air_invalidates_then_lcmm_retries():
    with MovingCppNetwork(seed=101, tick_ms=50) as net:
        net.add_node(1, position=(0, 0))
        net.add_node(2, position=(0, 0))
        net.add_link(1, 2, max_range=50)
        net.run(60_000)
        assert 2 in net.routes(1) and 1 in net.routes(2)

        start = net.now
        net.send(1, 2, b"mid-air-link-failure", timeout_ms=10_000, e2e_ack=True)
        # First DATA starts at the next 50 ms firmware tick and remains on air
        # for hundreds of ms.  These environment events therefore happen in
        # the middle of the actual frame, not before transmit starts.
        net.set_link_at(start + 100, 1, 2, False)
        net.set_link_at(start + 500, 1, 2, True)
        net.run(start + 18_000)

        assert net.rf_metrics.rf_epoch_drops >= 1
        assert any(result == 1 for result, _ in net.app_acks(1)), net.nodes[1].events


@requires_cpp
def test_real_cpp_motion_out_and_back_mid_air_invalidates_first_attempt():
    with MovingCppNetwork(seed=102, tick_ms=50) as net:
        net.add_node(1, position=(0, 0))
        net.add_node(2, position=(0, 0))
        net.add_link(1, 2, max_range=10)
        net.run(60_000)
        assert 2 in net.routes(1) and 1 in net.routes(2)

        start = net.now
        net.set_trajectory(
            2,
            [
                (0, 0, 0),
                (start, 0, 0),
                (start + 100, 30, 0),
                (start + 200, 0, 0),
            ],
        )
        net.send(1, 2, b"motion-inside-one-airframe", timeout_ms=10_000, e2e_ack=True)
        net.run(start + 18_000)

        assert net.rf_metrics.rf_range_drops >= 1
        # Once the node is stationary/in-range again, normal LCMM retry should
        # recover without the environment having to know LCMM state.
        assert any(result == 1 for result, _ in net.app_acks(1)), net.nodes[1].events


@requires_cpp
@pytest.mark.xfail(
    reason=(
        "current DTPK sendAckPacket dereferences a missing reverse route after "
        "destination reboot instead of safely ACKing through the previous hop"
    )
)
def test_real_cpp_rebooted_destination_can_ack_without_reverse_route():
    with MovingCppNetwork(seed=103, tick_ms=50) as net:
        net.add_node(1, position=(0, 0))
        net.add_node(2, position=(0, 0))
        net.add_link(1, 2, max_range=50)
        net.run(60_000)
        assert 2 in net.routes(1) and 1 in net.routes(2)

        start = net.now
        net.send(1, 2, b"reboot-mid-air", timeout_ms=10_000, e2e_ack=True)
        net.fail_node_at(start + 100, 2)
        net.recover_node_at(start + 900, 2)
        net.run(start + 18_000)

        assert net.rf_metrics.rf_epoch_drops >= 1
        assert any(result == 1 for result, _ in net.app_acks(1)), net.nodes[1].events
