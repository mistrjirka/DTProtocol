"""Compatibility layer for the old MovingCppNetwork import.

Motion, failure epochs, RF timing and range checks live in the shared
``environment.EnvironmentKernel``.  The concrete C++ adapter additionally maps
global simulation time to per-node Arduino ``millis()`` epochs after reboot.
"""

from cpp_sim_adapter import CppSimNetwork
from environment import RfMetrics as CppRfMetrics, Waypoint


class MovingCppNetwork(CppSimNetwork):
    pass


__all__ = ["MovingCppNetwork", "Waypoint", "CppRfMetrics"]
