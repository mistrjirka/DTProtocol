import pathlib
import sys

import pytest

SIM_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SIM_ROOT))

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


@requires_cpp
def test_real_cpp_reboot_resets_mcu_millis_epoch():
    with MovingCppNetwork(seed=88, tick_ms=50) as net:
        net.add_node(1)
        net.run(30_000)
        assert net.rf_metrics.tx_frames >= 1
        net.fail_node_at(40_000, 1)
        net.recover_node_at(50_000, 1)
        # Capture the baseline after the failure event: the old node is allowed
        # to transmit normally between 30 s and 40 s.
        net.run(40_000)
        before = net.rf_metrics.tx_frames
        # Initial HELLO is randomized no earlier than 100 ms and CRYST no
        # earlier than 200 ms after boot.  At +50 ms, any new TX would therefore
        # prove that the pre-reboot/global millis epoch leaked into firmware.
        net.run(50_050)
        assert net.rf_metrics.tx_frames == before
        net.run(71_000)
        assert net.rf_metrics.tx_frames > before


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
        net.set_trajectory(2, [
            (0, 0, 0),
            (start + 90, 0, 0),
            (start + 130, 30, 0),
            (start + 250, 30, 0),
            (start + 300, 0, 0),
        ])
        net.send(1, 2, b"motion-inside-one-airframe", timeout_ms=10_000, e2e_ack=True)
        net.run(start + 18_000)
        assert net.rf_metrics.rf_range_drops >= 1
        assert any(result == 1 for result, _ in net.app_acks(1)), net.nodes[1].events


@requires_cpp
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


@requires_cpp
def test_real_cpp_reboot_incarnation_prevents_false_duplicate_delivery():
    """A rebooted sender may reuse a packet id; its new incarnation must differ."""
    with MovingCppNetwork(seed=177, tick_ms=50) as net:
        net.add_node(1)
        net.add_node(2)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)
        net.run(60_000)

        # Populate node 2's replay cache with ids that the rebooted process will
        # deterministically reuse after startup control traffic.
        for index in range(6):
            net.send(
                1,
                2,
                f"before-{index}".encode(),
                timeout_ms=15_000,
                e2e_ack=True,
            )
            net.run(net.now + 5_000)
        before = [event for event in net.nodes[2].events if event[0] == "APP_RX"]
        assert len(before) == 6

        net.fail_node_at(net.now + 10_000, 1)
        net.recover_node_at(net.now + 20_000, 1)
        net.run(net.now + 100_000)
        assert net.routes(1).get(2) == (2, 1)

        net.send(1, 2, b"after-reboot", timeout_ms=15_000, e2e_ack=True)
        net.run(net.now + 25_000)

        received = [event for event in net.nodes[2].events if event[0] == "APP_RX"]
        assert len(received) == 7, received
        assert received[-1][1][3] == b"after-reboot".hex()
        assert any(result == 1 for result, _ in net.app_acks(1))
