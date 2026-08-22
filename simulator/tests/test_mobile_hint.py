import pathlib
import sys

SIM_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SIM_ROOT))

from model import Profile
from scenario import LinkSpec, NodeSpec, Scenario


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
    assert net.nodes[2]._effective_hello_period_ms() == 1_000

    # Any recently heard direct frame returns the mobile node to its normal
    # connected cadence without changing route semantics.
    net.nodes[2].last_heard[1] = net.now
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


def test_cpp_scenario_wires_mobile_hint_to_real_dtpk():
    static = Scenario(seed=804, nodes=[NodeSpec(1, mobile_hint=False)])
    mobile = Scenario(seed=804, nodes=[NodeSpec(1, mobile_hint=True)])

    net_static = static.build("cpp", tick_ms=50)
    net_mobile = mobile.build("cpp", tick_ms=50)
    try:
        net_static.run(60_000)
        net_mobile.run(60_000)
        # Isolated mobile nodes use the 1 s discovery cadence; static nodes use
        # 10 s. After contact the mobile cadence relaxes to 4 s.
        assert net_mobile.rf_metrics.tx_frames > net_static.rf_metrics.tx_frames
    finally:
        net_static.close()
        net_mobile.close()


def test_mobile_contact_has_deterministic_lossless_mutual_discovery_within_5s():
    # Sample arbitrary protocol phases, not only startup. Enriched CRYST_REQ
    # makes one received mobile HELLO sufficient for both direct routes.
    for seed in range(1, 1001):
        scenario = Scenario.line(
            2, seed=20_000 + seed, mobile_nodes=(1,),
            latency_ms=0.0, jitter_ms=0.0
        )
        net = scenario.build("python", profile=Profile.crystallized_v2())
        net.set_link(1, 2, False)
        phase = 15_000 + (seed * 7919) % 20_000
        net.run(phase)
        net.set_link(1, 2, True)
        net.run(phase + 5_000)
        assert net.routes(1).get(2) == (2, 1), (seed, net.routes(1))
        assert net.routes(2).get(1) == (1, 1), (seed, net.routes(2))


def test_enriched_cryst_request_does_not_ping_pong():
    scenario = Scenario.line(
        2, seed=21_000, mobile_nodes=(1,), latency_ms=0.0, jitter_ms=0.0
    )
    net = scenario.build("python", profile=Profile.crystallized_v2())
    net.set_link(1, 2, False)
    net.run(20_000)
    before = net.metrics.cryst_req_tx
    net.set_link(1, 2, True)
    net.run(40_000)

    assert net.routes(1).get(2) == (2, 1)
    assert net.routes(2).get(1) == (1, 1)
    # One request in each direction may be needed while snapshots converge, but
    # requests must not become a self-sustaining priority exchange.
    assert net.metrics.cryst_req_tx - before <= 4


def test_mobile_fast_discovery_cadence_relaxes_after_contact():
    scenario = Scenario.line(
        2, seed=21_001, mobile_nodes=(1,), latency_ms=0.0, jitter_ms=0.0
    )
    net = scenario.build("python", profile=Profile.crystallized_v2())
    assert net.nodes[1]._effective_hello_period_ms() == 1_000
    net.run(5_000)
    assert net.routes(1).get(2) == (2, 1)
    assert net.nodes[1]._effective_hello_period_ms() == 4_000


def test_connected_mobile_still_discovers_new_peer_within_five_seconds():
    # Once connected, the mobile node relaxes to a 4 s maximum HELLO gap. The
    # additional CRYST_REQ jitter is capped at 0.5 s, so a new lossless peer
    # still fits inside a 5 s encounter for every sampled phase.
    for seed in range(1, 501):
        scenario = Scenario(
            seed=22_000 + seed,
            nodes=[
                NodeSpec(1, mobile_hint=True),
                NodeSpec(2),
                NodeSpec(3),
            ],
            links=[
                LinkSpec(1, 2, latency_ms=0, jitter_ms=0, up=True),
                LinkSpec(1, 3, latency_ms=0, jitter_ms=0, up=False),
            ],
        )
        net = scenario.build("python", profile=Profile.crystallized_v2())
        phase = 20_000 + (seed * 7919) % 20_000
        net.run(phase)
        assert net.routes(1).get(2) == (2, 1)
        assert net.nodes[1]._effective_hello_period_ms() == 4_000
        net.set_link(1, 3, True)
        net.run(phase + 5_000)
        assert net.routes(1).get(3) == (3, 1), (seed, net.routes(1))
        assert net.routes(3).get(1) == (1, 1), (seed, net.routes(3))
