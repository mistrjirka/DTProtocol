import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dtpsim import Profile, line_topology


def test_two_nodes_intended_converge():
    sim = line_topology(2, seed=1, profile=Profile.intended())
    sim.run(60_000)
    audit = sim.audit()
    assert audit["correct"], audit


def test_three_nodes_intended_converge():
    sim = line_topology(3, seed=4, profile=Profile.intended())
    sim.run(100_000)
    audit = sim.audit()
    assert audit["correct"], audit


def test_simultaneous_current_deadlocks_or_times_out():
    sim = line_topology(2, seed=3, profile=Profile.current())
    sim.run(60_000)
    assert sim.audit()["correct"]
    sim.nodes[1].send_data(2, e2e_ack=True)
    sim.nodes[2].send_data(1, e2e_ack=True)
    sim.run(75_000)
    assert sim.metrics.e2e_failure >= 1


def test_simultaneous_intended_succeeds():
    sim = line_topology(2, seed=3, profile=Profile.intended())
    sim.run(60_000)
    assert sim.audit()["correct"]
    sim.nodes[1].send_data(2, e2e_ack=True)
    sim.nodes[2].send_data(1, e2e_ack=True)
    sim.run(75_000)
    assert sim.metrics.e2e_success == 2


def test_robust_reconnects_after_link_heal():
    sim = line_topology(3, seed=7, profile=Profile.robust())
    sim.run(70_000)
    assert sim.audit()["correct"]
    sim.set_link(2, 3, False)
    sim.run(125_000)
    # Expiry should remove cross-partition routes.
    assert not sim.audit()["stale"]
    sim.set_link(2, 3, True)
    sim.run(210_000)
    assert sim.audit()["correct"], sim.audit()
