import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cpp_backend import CppNetwork, CppNodeProcess

pytestmark = pytest.mark.skipif(
    not CppNodeProcess.available(),
    reason="host C++ node not built; run cmake -S simulator/cpp -B simulator/cpp/build && cmake --build simulator/cpp/build",
)


def test_real_cpp_two_nodes_discover_each_other():
    with CppNetwork(seed=11, tick_ms=50) as net:
        net.add_node(1)
        net.add_node(2)
        net.add_link(1, 2)
        net.run(60_000)
        assert net.routes(1).get(2) == (2, 1)
        assert net.routes(2).get(1) == (1, 1)


def test_real_lcmm_retries_after_one_lost_data_frame():
    with CppNetwork(seed=17, tick_ms=50) as net:
        net.add_node(1)
        net.add_node(2)
        net.add_link(1, 2)
        net.run(60_000)
        assert 2 in net.routes(1) and 1 in net.routes(2)

        # Failure is imposed by the environment, outside either firmware state
        # machine. The first actual 1->2 PHY frame after SEND is discarded.
        net.drop_next_frames(1, 2, 1)
        net.send(1, 2, b"retry-me", timeout_ms=10_000, e2e_ack=True)
        net.run(78_000)
        assert any(result == 1 for result, _ping in net.app_acks(1)), net.nodes[1].events


@pytest.mark.xfail(reason="current DTPK globally gates ACK transmission while waiting for its own end-to-end ACK")
def test_real_cpp_simultaneous_e2e_send_should_not_deadlock():
    with CppNetwork(seed=23, tick_ms=50) as net:
        net.add_node(1)
        net.add_node(2)
        net.add_link(1, 2)
        net.run(60_000)
        net.send(1, 2, b"a", timeout_ms=10_000, e2e_ack=True)
        net.send(2, 1, b"b", timeout_ms=10_000, e2e_ack=True)
        net.run(78_000)
        successes = sum(result == 1 for node in (1, 2) for result, _ in net.app_acks(node))
        assert successes == 2
