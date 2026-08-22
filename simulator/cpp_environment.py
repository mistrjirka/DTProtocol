"""Compatibility layer for the old MovingCppNetwork import.

Motion, failure epochs, RF timing and range checks live in the shared
EnvironmentKernel. CppSimNetwork additionally gives every rebooted firmware
process its own Arduino millis() epoch.
"""

from cpp_sim_adapter import CppSimNetwork
from environment import RfMetrics as CppRfMetrics, Waypoint


class MovingCppNetwork(CppSimNetwork):
    pass


__all__ = ["MovingCppNetwork", "Waypoint", "CppRfMetrics"]
