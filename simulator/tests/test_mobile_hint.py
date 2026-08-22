import pathlib
import sys

SIM_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SIM_ROOT))

from model import Profile
from scenario import NodeSpec, Scenario


def test_mobile_hint_changes_only_local_hello_period():
    scenario = Scenario(
        seed=801,
        nodes=[
            NodeSpec(1, mobile_hint=False),
            NodeSpec(2, mobile_hint=True),
        ],
    )
    net = scenario.build("python", profile=Profile.crystallized_v2())

    assert net.nodes[1].mobile_hint is False
    assert net.nodes[2].mobile_hint is True
    assert net.nodes[1]._effective_hello_period_ms() == 10_000
    assert net.nodes[2]._effective_hello_period_ms() == 4_000


def test_mobile_node_emits_more_hellos_without_wire_or_route_rule_changes():
    scenario = Scenario(
        seed=802,
        nodes=[
            NodeSpec(1, mobile_hint=False),
            NodeSpec(2, mobile_hint=True),
        ],
    )
    net = scenario.build("python", profile=Profile.crystallized_v2())
    net.run(60_000)

    hello_tx = {1: 0, 2: 0}
    for event in net.trace:
        if event.get("event") == "tx_broadcast" and event.get("kind") == "HELLO":
            hello_tx[event["node"]] += 1

    assert hello_tx[1] > 0
    assert hello_tx[2] > hello_tx[1]
    assert hello_tx[2] >= 2 * hello_tx[1] - 1


def test_wrong_mobile_labels_do_not_change_stable_route_result():
    static = Scenario.line(5, seed=803)
    hinted = Scenario.line(5, seed=803, mobile_nodes=(2, 3, 4))

    net_static = static.build("python", profile=Profile.crystallized_v2())
    net_hinted = hinted.build("python", profile=Profile.crystallized_v2())

    net_static.run(100_000)
    net_hinted.run(100_000)

    assert net_static.audit()["correct"], net_static.audit()
    assert net_hinted.audit()["correct"], net_hinted.audit()

    static_routes = {
        node: net_static.routes(node)
        for node in sorted(net_static.nodes)
    }
    hinted_routes = {
        node: net_hinted.routes(node)
        for node in sorted(net_hinted.nodes)
    }
    assert hinted_routes == static_routes
