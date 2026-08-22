from dataclasses import replace
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from model import AdvertisedRoute, Profile
from simulator import Simulator


def v2_profile():
    return replace(
        Profile.crystallized_v2(),
        hello_period_ms=1000,
        neighbor_expiry_ms=3000,
        k_limit_ms=500,
        cryst_jitter_min_ms=20,
        seqno_request_cooldown_ms=1000,
    )


def test_cryst_v2_recovers_longer_alternate_without_loop_on_shared_kernel():
    sim = Simulator(seed=8, profile=v2_profile())
    for i in range(1, 6):
        sim.add_node(i)
    for a, b in ((1, 2), (2, 4), (1, 3), (3, 5), (5, 4)):
        sim.add_link(a, b, jitter_ms=0)

    sim.run(20_000)
    assert sim.audit()["correct"], sim.audit()
    assert sim.nodes[1].routes[4].distance == 2

    # The environment removes the currently shortest edge without consulting
    # protocol state. A valid replacement exists but is longer.
    sim.set_link(2, 4, False)
    saw_loop = False
    for t in range(20_100, 50_001, 100):
        sim.run(t)
        saw_loop |= bool(sim.audit()["loops"])

    assert not saw_loop
    assert sim.audit()["correct"], sim.audit()
    assert sim.nodes[1].routes[4].distance == 3
    assert sim.metrics.seq_req_satisfied > 0


def test_neighbor_expiry_discards_full_state_version_proof():
    sim = Simulator(seed=1, profile=v2_profile())
    a = sim.add_node(1, start=False)
    b = sim.add_node(2, start=False)
    a.up = b.up = True
    sim.add_link(1, 2, jitter_ms=0)

    a.routes_by_neighbor[2] = {
        2: AdvertisedRoute(2, 1, 1, 7, 0),
        9: AdvertisedRoute(9, 2, 2, 4, 1),
    }
    a.neighbor_cryst_version[2] = 11
    a.last_heard[2] = 0.0
    a.rebuild_routes()

    sim.now = 4000.0
    a._expiry_tick(a.generation)
    assert 2 not in a.routes_by_neighbor
    assert 2 not in a.neighbor_cryst_version


def test_cryst_v2_hello_jitter_is_not_required_for_correctness():
    base = v2_profile()
    assert base.hello_jitter_fraction > 0
    # Mobility labels/jitter may improve contention but are not routing
    # correctness invariants. Turning the jitter off must leave the protocol
    # logically valid in a lossless static network.
    sim = Simulator(seed=51, profile=replace(base, hello_jitter_fraction=0.0))
    sim.add_node(1)
    sim.add_node(2)
    sim.add_link(1, 2, jitter_ms=0)
    sim.run(15_000)
    assert sim.audit()["correct"], sim.audit()
