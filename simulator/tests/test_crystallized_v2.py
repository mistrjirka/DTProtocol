import pathlib
import sys

SIM_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SIM_ROOT))

from model import Profile
from simulator import Simulator


def add_nodes(sim, count):
    for node_id in range(1, count + 1):
        sim.add_node(node_id)


def test_cryst_v2_is_event_triggered_not_periodic_full_vector():
    profile = Profile.crystallized_v2()
    assert profile.periodic_cryst_ms is None
    assert profile.hello_period_ms is not None
    assert profile.state_digest_requests
    assert profile.seqno_requests


def test_cryst_v2_discovers_one_hop_after_link_heals_without_external_protocol_trigger():
    sim = Simulator(seed=101, profile=Profile.crystallized_v2())
    add_nodes(sim, 2)
    sim.add_link(1, 2, up=False, jitter_ms=0)

    sim.run(30_000)
    assert 2 not in sim.nodes[1].routes
    assert 1 not in sim.nodes[2].routes

    # Only the physical environment changes. Neither protocol node is told that
    # a link came back; periodic HELLO must discover it.
    sim.set_link(1, 2, True)
    sim.run(50_000)

    assert sim.nodes[1].routes[2].next_hop == 2
    assert sim.nodes[1].routes[2].distance == 1
    assert sim.nodes[2].routes[1].next_hop == 1
    assert sim.nodes[2].routes[1].distance == 1


def test_cryst_v2_hello_digest_repairs_missing_neighbor_state():
    sim = Simulator(seed=102, profile=Profile.crystallized_v2())
    add_nodes(sim, 3)
    sim.add_link(1, 2, jitter_ms=0)
    sim.add_link(2, 3, jitter_ms=0)
    sim.run(80_000)
    assert sim.audit()["correct"], sim.audit()

    node1 = sim.nodes[1]
    node2 = sim.nodes[2]

    # Model exactly what a missed route-state update means: node 1 still knows
    # node 2 is a direct neighbour but has an older state version and lacks 2's
    # indirect contribution. No protocol callback is invoked to repair it.
    direct = node1.routes_by_neighbor[2][2]
    node1.routes_by_neighbor[2] = {2: direct}
    node1.neighbor_cryst_state[2] = (
        node2.origin_sequence,
        (node2.route_version - 1) & 0xFFFF,
    )
    node1.rebuild_routes()
    assert 3 not in node1.routes

    req_before = sim.metrics.cryst_req_tx
    sim.run(100_000)

    assert sim.metrics.cryst_req_tx > req_before
    assert node1.routes[3].distance == 2
    assert sim.audit()["correct"], sim.audit()


def test_cryst_v2_sequence_request_recovers_longer_only_path_without_periodic_origin_bump():
    # Initially 1-2-4 is shortest (2 hops); 1-3-5-4 is 3 hops. Removing 2-4
    # forces node 1 to accept a worse metric. Feasibility blocks that within the
    # old generation, so SEQ_REQ must make destination 4 originate a newer one.
    sim = Simulator(seed=103, profile=Profile.crystallized_v2())
    add_nodes(sim, 5)
    for a, b in ((1, 2), (2, 4), (1, 3), (3, 5), (5, 4)):
        sim.add_link(a, b, jitter_ms=0)

    sim.run(100_000)
    assert sim.audit()["correct"], sim.audit()
    assert sim.nodes[1].routes[4].distance == 2
    old_seq = sim.nodes[4].origin_sequence

    sim.set_link(2, 4, False)
    # v2 now matches the C++ 120 s hard-inactivity policy. The theoretical
    # backend has no active liveness probe, so allow expiry + SEQ_REQ repair.
    sim.run(280_000)

    assert sim.metrics.seq_req_satisfied >= 1
    assert sim.nodes[4].origin_sequence != old_seq
    assert sim.nodes[1].routes[4].distance == 3
    assert sim.nodes[1].routes[4].next_hop == 3
    assert sim.audit()["correct"], sim.audit()


def test_cryst_v2_clears_triangle_count_to_infinity_partition_without_loops():
    sim = Simulator(seed=104, profile=Profile.crystallized_v2())
    add_nodes(sim, 4)
    for a, b in ((1, 2), (2, 3), (3, 1), (1, 4), (3, 4)):
        sim.add_link(a, b, jitter_ms=0)

    sim.run(100_000)
    assert sim.audit()["correct"], sim.audit()

    sim.set_link(1, 4, False)
    sim.set_link(3, 4, False)

    loops = []
    for t in range(105_000, 280_001, 5_000):
        sim.run(t)
        loops.extend(sim.audit()["loops"])

    final = sim.audit()
    assert not loops, loops
    assert not final["stale"], final
    assert not final["loops"], final
    for node in (1, 2, 3):
        assert 4 not in sim.nodes[node].routes


def test_cryst_v2_stable_network_stops_sending_full_cryst_but_keeps_hellos():
    sim = Simulator(seed=105, profile=Profile.crystallized_v2())
    add_nodes(sim, 4)
    for a, b in ((1, 2), (2, 3), (3, 4)):
        sim.add_link(a, b, jitter_ms=0)

    sim.run(100_000)
    assert sim.audit()["correct"], sim.audit()
    cryst_before = sim.metrics.cryst_tx
    hello_before = sim.metrics.hello_tx

    sim.run(140_000)

    assert sim.metrics.hello_tx > hello_before
    assert sim.metrics.cryst_tx == cryst_before
