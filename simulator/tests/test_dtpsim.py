import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dtpsim import Profile, line_topology
from simulator import Simulator


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


def test_no_e2e_ack_delivery_does_not_generate_ack_or_waiter():
    sim = line_topology(2, seed=31, profile=Profile.intended())
    sim.run(60_000)
    assert sim.audit()["correct"]
    trace_start = len(sim.trace)
    success_before = sim.metrics.e2e_success
    failure_before = sim.metrics.e2e_failure

    sim.nodes[1].send_data(2, e2e_ack=False)
    sim.run(75_000)

    assert sim.metrics.delivered_app == 1
    assert sim.metrics.e2e_success == success_before
    assert sim.metrics.e2e_failure == failure_before
    assert sim.nodes[1].waiting_e2e is None
    assert not any(
        event.get("kind") in ("ACK", "NACK")
        for event in sim.trace[trace_start:]
    )


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


def test_audit_rejects_environment_up_but_unstarted_protocol_node():
    sim = Simulator(seed=91, profile=Profile.crystallized_v2())
    sim.add_node(1, start=False)
    audit = sim.audit()
    assert audit["inactive"] == [(1, "not_started")]
    assert not audit["correct"]


def test_audit_rejects_crashed_protocol_node_until_environment_recovery():
    sim = line_topology(2, seed=92, profile=Profile.crystallized_v2())
    sim.run(60_000)
    sim.nodes[2].crash("test")
    audit = sim.audit()
    assert (2, "crashed") in audit["inactive"]
    assert not audit["correct"]
