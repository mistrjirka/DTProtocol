from __future__ import annotations

from typing import Optional

from model import Profile
from unified_simulator import UnifiedSimulator


def line_network(
    *,
    backend: str,
    nodes: int,
    seed: int = 1,
    profile: Optional[Profile] = None,
    loss: float = 0.0,
    spacing: float = 1.0,
    max_range: float = 1.1,
    tick_ms: float = 50.0,
    cpp_binary: Optional[str] = None,
) -> UnifiedSimulator:
    sim = UnifiedSimulator(
        backend=backend,
        seed=seed,
        profile=profile,
        tick_ms=tick_ms,
        cpp_binary=cpp_binary,
    )
    for node_id in range(1, nodes + 1):
        sim.add_node(node_id, position=((node_id - 1) * spacing, 0.0))
    for node_id in range(1, nodes):
        sim.add_link(
            node_id,
            node_id + 1,
            loss=loss,
            max_range=max_range,
            latency_ms=5.0,
            jitter_ms=0.0,
        )
    return sim


def settle_line(
    *,
    backend: str,
    nodes: int,
    seed: int = 1,
    profile: Optional[Profile] = None,
    duration_ms: float = 80_000,
    cpp_binary: Optional[str] = None,
) -> UnifiedSimulator:
    sim = line_network(
        backend=backend,
        nodes=nodes,
        seed=seed,
        profile=profile,
        cpp_binary=cpp_binary,
    )
    sim.run(duration_ms)
    return sim


def midair_link_flap_scenario(
    *,
    backend: str,
    seed: int = 101,
    profile: Optional[Profile] = None,
    cpp_binary: Optional[str] = None,
) -> UnifiedSimulator:
    """Same external link-failure trace for either backend."""
    sim = line_network(
        backend=backend,
        nodes=2,
        seed=seed,
        profile=profile,
        spacing=0.0,
        max_range=50.0,
        cpp_binary=cpp_binary,
    )
    sim.run(60_000)
    start = sim.now
    sim.send(1, 2, b"mid-air-link-failure", timeout_ms=10_000, e2e_ack=True)
    sim.set_link_at(start + 100, 1, 2, False)
    sim.set_link_at(start + 500, 1, 2, True)
    sim.run(start + 18_000)
    return sim


def leave_and_return_midair_scenario(
    *,
    backend: str,
    seed: int = 102,
    profile: Optional[Profile] = None,
    cpp_binary: Optional[str] = None,
) -> UnifiedSimulator:
    """Node 2 exits RF range and returns while the first DATA frame is on air."""
    sim = line_network(
        backend=backend,
        nodes=2,
        seed=seed,
        profile=profile,
        spacing=0.0,
        max_range=10.0,
        cpp_binary=cpp_binary,
    )
    sim.run(60_000)
    start = sim.now
    sim.set_trajectory(
        2,
        [
            (0, 0, 0),
            (start, 0, 0),
            (start + 100, 30, 0),
            (start + 200, 0, 0),
        ],
    )
    sim.send(1, 2, b"motion-inside-one-airframe", timeout_ms=10_000, e2e_ack=True)
    sim.run(start + 18_000)
    return sim
