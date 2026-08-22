import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cpp_backend import CppNetwork, CppNodeProcess


@pytest.mark.skipif(not CppNodeProcess.available(), reason="host C++ node not built")
def test_cpp_millis_restarts_at_zero_after_environment_reboot():
    with CppNetwork(seed=211, tick_ms=50) as net:
        net.add_node(1)
        net.run(60_000)
        assert 59_900 <= net._local_time(1) <= 60_000

        # Failure/recovery belong to the environment. They do not inspect the
        # firmware's current state, and recovery creates a fresh MCU process.
        net.fail_node_at(60_100, 1)
        net.recover_node_at(61_000, 1)
        net.run(61_250)

        assert net.node_up[1]
        assert 200 <= net._local_time(1) <= 300


def test_world_clock_and_mcu_clock_are_explicitly_separate():
    net = CppNetwork(seed=212)
    net.now = 123_456.0
    net._boot_world_ms[7] = 120_000.0
    assert net._local_time(7) == 3_456.0
