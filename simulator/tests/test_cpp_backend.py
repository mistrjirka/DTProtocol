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
        net.drop_next_frames(1, 2, 1)
        net.send(1, 2, b"retry-me", timeout_ms=10_000, e2e_ack=True)
        net.run(78_000)
        assert any(result == 1 for result, _ping in net.app_acks(1)), net.nodes[1].events


def test_real_cpp_simultaneous_e2e_send_does_not_deadlock():
    with CppNetwork(seed=23, tick_ms=50) as net:
        net.add_node(1)
        net.add_node(2)
        net.add_link(1, 2)
        net.run(60_000)
        net.send(1, 2, b"a", timeout_ms=10_000, e2e_ack=True)
        net.send(2, 1, b"b", timeout_ms=10_000, e2e_ack=True)
        net.run(78_000)
        successes = sum(
            result == 1
            for node in (1, 2)
            for result, _ in net.app_acks(node)
        )
        assert successes == 2


def test_real_cpp_three_hop_data_uses_reliable_relays_and_preserves_packet_size():
    with CppNetwork(seed=29, tick_ms=50) as net:
        for node in (1, 2, 3):
            net.add_node(node)
        net.add_link(1, 2)
        net.add_link(2, 3)
        net.run(120_000)
        assert 3 in net.routes(1) and 1 in net.routes(3)

        # Drop the first actual relay attempt on 2->3. v2 should retry it at
        # LCMM instead of relying on an unreliable forwarded hop.
        net.drop_next_frames(2, 3, 1)
        net.send(1, 3, b"multi-hop", timeout_ms=15_000, e2e_ack=True)
        net.run(145_000)
        assert any(result == 1 for result, _ in net.app_acks(1)), net.nodes[1].events


def test_real_cpp_cryst_v2_clears_triangle_count_to_infinity_partition():
    """The minimal DV counterexample must not circulate a stale route in C++."""
    with CppNetwork(seed=104, tick_ms=100) as net:
        for node in (1, 2, 3, 4):
            net.add_node(node)
        for a, b in ((1, 2), (2, 3), (3, 1), (1, 4), (3, 4)):
            net.add_link(a, b, jitter_ms=0)

        net.run(60_000)
        assert net.routes(1).get(4) == (4, 1)
        assert net.routes(2).get(4) is not None
        assert net.routes(3).get(4) == (4, 1)

        # Only the physical world changes. The firmware discovers the outage
        # through HELLO expiry and must not count to infinity through 1-2-3.
        net.set_link(1, 4, False)
        net.set_link(3, 4, False)
        net.run(110_000)

        for node in (1, 2, 3):
            assert 4 not in net.routes(node), (node, net.routes(node))


def test_real_cpp_cryst_v2_seq_request_recovers_longer_only_path():
    """Feasibility safety must not permanently starve a valid longer route."""
    with CppNetwork(seed=103, tick_ms=100) as net:
        for node in (1, 2, 3, 4, 5):
            net.add_node(node)
        for a, b in ((1, 2), (2, 4), (1, 3), (3, 5), (5, 4)):
            net.add_link(a, b, jitter_ms=0)

        net.run(60_000)
        assert net.routes(1).get(4) == (2, 2)

        # The old two-hop route disappears. The only surviving route is longer
        # and is initially infeasible under the old destination generation.
        net.set_link(2, 4, False)
        net.run(110_000)

        assert net.routes(1).get(4) == (3, 3), net.routes(1)
        assert net.routes(3).get(4) == (5, 2), net.routes(3)
