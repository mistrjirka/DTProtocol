import pathlib
import sys

SIM_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SIM_ROOT))

from model import MAX_PACKET_SIZE, Profile
from scenario import Scenario


def _network(radio_profile: str):
    scenario = Scenario.line(1, seed=701, radio_profile=radio_profile)
    return scenario.build("python", profile=Profile.crystallized_v2())


def test_radio_profile_json_round_trip_preserves_policy():
    scenario = Scenario.line(2, seed=702, radio_profile="eu869-high-duty")
    restored = Scenario.from_json(scenario.to_json())
    assert restored.radio_profile == "eu869-high-duty"
    assert restored.seed == scenario.seed
    assert restored.nodes == scenario.nodes
    assert restored.links == scenario.links


def test_duty_aware_neighbor_expiry_only_expands_eu868_at_sf9():
    unconstrained = _network("unconstrained")
    eu433 = _network("eu433")
    high = _network("eu869-high-duty")
    eu868 = _network("eu868")

    assert unconstrained.profile.neighbor_expiry_ms == 30_000
    assert eu433.profile.neighbor_expiry_ms == 30_000
    assert high.profile.neighbor_expiry_ms == 30_000

    # Full-size SF9/BW125 frame ~=1.717 s. At 1% the following legal silence is
    # ~=170 s; adding max HELLO jitter and scheduler margin yields ~183 s.
    assert 180_000 < eu868.profile.neighbor_expiry_ms < 190_000


def test_eu868_and_high_duty_have_expected_full_frame_off_time_ratio():
    eu868 = _network("eu868")
    high = _network("eu869-high-duty")

    airtime = eu868.airtime_ms(MAX_PACKET_SIZE)
    assert 1_700 < airtime < 1_730

    # Account the same physical frame in both policy profiles, then look at the
    # remaining legal silence after RF TX has completed.
    eu868.account_transmission(1, 0.0, airtime)
    high.account_transmission(1, 0.0, airtime)
    eu868.now = airtime
    high.now = airtime

    wait_1pct = eu868.transmit_wait_ms(1)
    wait_10pct = high.transmit_wait_ms(1)

    assert 165_000 < wait_1pct < 175_000
    assert 15_000 < wait_10pct < 16_000
    assert wait_1pct > 10 * wait_10pct


def test_eu868_two_nodes_do_not_self_expire_under_legal_silence():
    scenario = Scenario.line(
        2,
        seed=703,
        radio_profile="eu868",
        latency_ms=5.0,
        jitter_ms=0.0,
    )
    net = scenario.build("python", profile=Profile.crystallized_v2())

    # This spans multiple 30 s legacy-expiry windows. With the adaptive bound,
    # healthy nodes must remain mutually reachable despite 1% regulatory gaps.
    net.run(120_000)
    assert net.routes(1).get(2) == (2, 1)
    assert net.routes(2).get(1) == (1, 1)
    assert net.profile.neighbor_expiry_ms > 120_000
