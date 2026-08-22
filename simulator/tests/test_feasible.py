import pathlib
import sys

SIM_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SIM_ROOT))

from model import AdvertisedRoute, FeasibilityState, Profile, ROUTE_INFINITY, sequence_newer
from simulator import Simulator


def add_nodes(sim, count):
    for node_id in range(1, count + 1):
        sim.add_node(node_id)


def test_sequence_number_serial_arithmetic_wraps_safely():
    assert sequence_newer(11, 10)
    assert not sequence_newer(10, 11)
    assert sequence_newer(0, 0xFFFF)
    assert not sequence_newer(0xFFFF, 0)


def test_unfeasible_update_from_current_successor_is_not_selected():
    sim = Simulator(seed=1, profile=Profile.feasible())
    node = sim.add_node(1, start=False)

    # Node 1 previously advertised distance 2 to destination 4 at sequence 10.
    node.feasibility[4] = FeasibilityState(sequence=10, feasible_distance=2)
    node.routes_by_neighbor = {
        2: {4: AdvertisedRoute(4, 2, 3, 10)},  # neighbour advertises 2: not < FD 2
        3: {4: AdvertisedRoute(4, 3, 4, 10)},  # neighbour advertises 3: not < FD 2
    }

    node.rebuild_routes()
    assert 4 not in node.routes


def test_newer_source_generation_allows_longer_route():
    sim = Simulator(seed=2, profile=Profile.feasible())
    node = sim.add_node(1, start=False)
    node.feasibility[4] = FeasibilityState(sequence=10, feasible_distance=2)

    # Same longer route was previously unfeasible, but a source-generated newer
    # sequence proves it belongs to a new routing generation.
    node.routes_by_neighbor = {
        3: {4: AdvertisedRoute(4, 3, 4, 11)},
    }
    node.rebuild_routes()

    assert node.routes[4].next_hop == 3
    assert node.routes[4].distance == 4
    assert node.routes[4].sequence == 11


def test_feasible_profile_converges_on_lossless_line():
    sim = Simulator(seed=41, profile=Profile.feasible())
    add_nodes(sim, 4)
    sim.add_link(1, 2, jitter_ms=0)
    sim.add_link(2, 3, jitter_ms=0)
    sim.add_link(3, 4, jitter_ms=0)

    sim.run(240_000)
    assert sim.audit()["correct"], sim.audit()


def test_feasible_profile_clears_minimal_count_to_infinity_partition():
    """Triangle 1-2-3 can circulate a stale route to isolated node 4 in DV."""
    sim = Simulator(seed=51, profile=Profile.feasible())
    add_nodes(sim, 4)
    for a, b in ((1, 2), (2, 3), (3, 1), (1, 4), (3, 4)):
        sim.add_link(a, b, jitter_ms=0)

    sim.run(240_000)
    assert sim.audit()["correct"], sim.audit()

    sim.set_link(1, 4, False)
    sim.set_link(3, 4, False)

    # Observe the complete reconvergence period, not only the final snapshot.
    loops = []
    for t in range(245_000, 480_001, 5_000):
        sim.run(t)
        loops.extend(sim.audit()["loops"])

    final = sim.audit()
    assert not loops, loops
    assert not final["stale"], final
    assert not final["loops"], final
    assert 4 not in sim.nodes[1].routes
    assert 4 not in sim.nodes[2].routes
    assert 4 not in sim.nodes[3].routes


def test_feasible_profile_recovers_when_only_remaining_path_is_longer():
    # Initially: 1-2-4 is the shortest path. 1-3-5-4 is one hop longer.
    # Removing 2-4 requires accepting a worse metric, which is blocked within
    # the old sequence but becomes feasible after node 4 originates a new one.
    sim = Simulator(seed=61, profile=Profile.feasible())
    add_nodes(sim, 5)
    for a, b in ((1, 2), (2, 4), (1, 3), (3, 5), (5, 4)):
        sim.add_link(a, b, jitter_ms=0)

    sim.run(240_000)
    assert sim.audit()["correct"], sim.audit()
    assert sim.nodes[1].routes[4].distance == 2

    sim.set_link(2, 4, False)
    sim.run(480_000)

    final = sim.audit()
    assert final["correct"], final
    assert sim.nodes[1].routes[4].distance == 3
    assert sim.nodes[1].routes[4].next_hop == 3


def test_metric_selects_shorter_route_among_feasible_generations():
    sim = Simulator(seed=71, profile=Profile.crystallized_v2())
    for node_id in (1, 2, 3, 4):
        sim.add_node(node_id)
    sim.run(0)
    node = sim.nodes[1]

    node.routes_by_neighbor[2] = {
        4: AdvertisedRoute(4, 2, 2, 1),
    }
    node.rebuild_routes()
    node._update_feasibility_from_advertisement()
    assert node.routes[4].next_hop == 2
    assert node.routes[4].sequence == 1

    node.routes_by_neighbor[3] = {
        4: AdvertisedRoute(4, 3, 4, 2),
    }
    node.rebuild_routes()
    assert node.routes[4].next_hop == 2
    assert node.routes[4].distance == 2
    assert node.routes[4].sequence == 1

    del node.routes_by_neighbor[2]
    node.rebuild_routes()
    assert node.routes[4].next_hop == 3
    assert node.routes[4].distance == 4
    assert node.routes[4].sequence == 2
