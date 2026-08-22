"""Compatibility layer for the old MovingCppNetwork import.

Motion, failure epochs, RF timing and range checks now live in the shared
`environment.EnvironmentKernel` used by both Python and C++ protocol backends.
"""

from cpp_backend import CppNetwork
from environment import RfMetrics as CppRfMetrics, Waypoint


class MovingCppNetwork(CppNetwork):
    pass


__all__ = ["MovingCppNetwork", "Waypoint", "CppRfMetrics"]
