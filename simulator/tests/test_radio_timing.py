import math
import pathlib
import sys

import pytest

SIM_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SIM_ROOT))

from environment import EnvironmentKernel
from scenario import LinkSpec, Scenario


def _reference_airtime_ms(payload, sf, bandwidth_hz, cr_den=7):
    """Independent transcription of the SX126x/RadioLib LoRa ToA equation."""
    symbol_ms = (2**sf) / bandwidth_hz * 1000.0
    ldro = symbol_ms >= 16.0
    preamble_extra = 6.25 if sf <= 6 else 4.25
    sf_coefficient2 = 0 if sf <= 6 else 8
    divisor = 4 * (sf - (2 if ldro else 0))
    bit_count = max(8 * payload + 16 - 4 * sf + sf_coefficient2 + 20, 0)
    pre_coded_symbols = math.ceil(bit_count / divisor)
    symbols = 8.0 + preamble_extra + 8.0 + pre_coded_symbols * cr_den
    return symbols * symbol_ms


def test_sf9_symbol_cad_and_known_airtime_values():
    env = EnvironmentKernel(sf=9, bandwidth_hz=125_000, coding_rate_denominator=7)

    assert env.symbol_time_ms() == pytest.approx(4.096, abs=1e-9)
    # RadioLib 6 defaults SF9 CAD to four symbols; SX126x then needs roughly
    # half a symbol of post-processing before CAD_DONE.
    assert env.cad_duration_ms() == pytest.approx(18.432, abs=1e-9)

    # Values are also pinned by the C++ embedded-math contract.
    assert env.airtime_ms(4) == pytest.approx(140.288, abs=1e-6)
    assert env.airtime_ms(11) == pytest.approx(168.960, abs=1e-6)
    assert env.airtime_ms(20) == pytest.approx(226.304, abs=1e-6)
    assert env.airtime_ms(31) == pytest.approx(312.320, abs=1e-6)
    assert env.airtime_ms(255) == pytest.approx(1717.248, abs=1e-6)


@pytest.mark.parametrize("sf", range(5, 13))
@pytest.mark.parametrize("bandwidth_hz", [125_000, 250_000, 500_000])
@pytest.mark.parametrize("payload", [0, 4, 20, 64, 255])
def test_airtime_matches_radiolib_equation_across_supported_profiles(
    sf, bandwidth_hz, payload
):
    env = EnvironmentKernel(
        sf=sf,
        bandwidth_hz=bandwidth_hz,
        coding_rate_denominator=7,
    )
    assert env.airtime_ms(payload) == pytest.approx(
        _reference_airtime_ms(payload, sf, bandwidth_hz), abs=1e-9
    )


def test_default_link_delay_is_not_fake_radio_propagation():
    assert LinkSpec(1, 2).latency_ms == 0.0
    scenario = Scenario.line(2)
    assert scenario.links[0].latency_ms == 0.0

    net = scenario.build("python")
    link = net.get_link(1, 2)
    assert link is not None
    assert link.latency_ms == 0.0
    assert link.jitter_ms == 0.0


def test_explicit_synthetic_link_latency_is_still_available():
    scenario = Scenario.line(2, latency_ms=7.5, jitter_ms=0.0)
    net = scenario.build("python")
    link = net.get_link(1, 2)
    assert link is not None
    assert link.latency_ms == 7.5
    assert net.jittered_latency(link) == 7.5
