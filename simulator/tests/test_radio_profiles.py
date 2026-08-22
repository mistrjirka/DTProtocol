import pathlib
import sys

SIM_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SIM_ROOT))

from model import MAX_PACKET_SIZE, Profile
from scenario import Scenario


def _network(radio_profile: str, *, strict: bool = False):
    scenario = Scenario.line(
        1,
        seed=701,
        radio_profile=radio_profile,
        strict_duty_cycle=strict,
    )
    return scenario.build("python", profile=Profile.crystallized_v2())


def test_radio_profile_json_round_trip_preserves_separate_duty_policy():
    scenario = Scenario.line(
        2,
        seed=702,
        radio_profile="eu869-high-duty",
        strict_duty_cycle=True,
    )
    restored = Scenario.from_json(scenario.to_json())
    assert restored.radio_profile == "eu869-high-duty"
    assert restored.strict_duty_cycle is True
    assert restored.seed == scenario.seed
    assert restored.nodes == scenario.nodes
    assert restored.links == scenario.links


def test_region_selection_does_not_throttle_or_change_liveness_by_default():
    for radio_profile in ("unconstrained", "eu433", "eu868", "eu869-high-duty"):
        net = _network(radio_profile)
        assert net.duty_cycle_percent == 0.0
        assert net.profile.neighbor_expiry_ms == 120_000


def test_strict_eu868_expands_liveness_but_is_explicit_opt_in():
    practical = _network("eu868", strict=False)
    strict = _network("eu868", strict=True)

    assert practical.duty_cycle_percent == 0.0
    assert practical.profile.neighbor_expiry_ms == 120_000

    assert strict.duty_cycle_percent == 1.0
    # Full-size SF9/BW125 frame ~=1.717 s. At 1% the following legal silence is
    # ~=170 s; the strict simulator expands liveness accordingly.
    assert 180_000 < strict.profile.neighbor_expiry_ms < 190_000


def test_strict_duty_profiles_keep_expected_full_frame_spacing():
    eu868 = _network("eu868", strict=True)
    high = _network("eu869-high-duty", strict=True)

    airtime = eu868.airtime_ms(MAX_PACKET_SIZE)
    assert 1_700 < airtime < 1_730

    eu868.account_transmission(1, 0.0, airtime)
    high.account_transmission(1, 0.0, airtime)
    eu868.now = airtime
    high.now = airtime

    wait_1pct = eu868.transmit_wait_ms(1)
    wait_10pct = high.transmit_wait_ms(1)

    assert 165_000 < wait_1pct < 175_000
    assert 15_000 < wait_10pct < 16_000
    assert wait_1pct > 10 * wait_10pct


def test_practical_eu868_keeps_normal_crystallization_timing():
    scenario = Scenario.line(
        2,
        seed=703,
        radio_profile="eu868",
        strict_duty_cycle=False,
        latency_ms=5.0,
        jitter_ms=0.0,
    )
    net = scenario.build("python", profile=Profile.crystallized_v2())
    net.run(60_000)

    assert net.routes(1).get(2) == (2, 1)
    assert net.routes(2).get(1) == (1, 1)
    assert net.rf_metrics.regulatory_deferrals == 0
    assert net.profile.neighbor_expiry_ms == 120_000
