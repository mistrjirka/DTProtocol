import math
import pathlib
import sys

import pytest

SIM_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SIM_ROOT))

from environment import EnvironmentKernel
from radio_timing import (
    AN1200_48_SF9_BW125_CAD4_MEASURED_MS,
    CAD_SCAN_FIXED_SPI_BYTES,
    CAD_SCAN_SPI_TRANSACTIONS,
    RADIOLIB6_BUSY_POLL_FLOOR_MS,
    RADIOLIB6_DEFAULT_SPI_HZ,
    RSSI_SPI_BYTES_PER_SAMPLE,
    RX_READ_FIXED_SPI_BYTES,
    RX_READ_SPI_TRANSACTIONS,
    RX_REARM_AFTER_READ_SPI_BYTES,
    RX_REARM_AFTER_READ_SPI_TRANSACTIONS,
    RX_REARM_AFTER_TX_SPI_BYTES,
    RX_REARM_AFTER_TX_SPI_TRANSACTIONS,
    SX1262_STBY_RC_TO_RX_MS,
    SX1262_STBY_RC_TO_TX_MS,
    TX_SETUP_FIXED_SPI_BYTES,
    TX_SETUP_SPI_TRANSACTIONS,
    cad_scan_spi_overhead_ms,
    rssi_cca_duration_ms,
    rssi_sample_offsets_ms,
    rx_packet_read_ms,
    rx_rearm_after_read_ms,
    rx_rearm_ms,
    spi_wire_time_ms,
    tx_startup_ms,
)
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
    assert env.cad_duration_ms() == pytest.approx(18.432, abs=1e-9)

    assert env.airtime_ms(4) == pytest.approx(140.288, abs=1e-6)
    assert env.airtime_ms(11) == pytest.approx(168.960, abs=1e-6)
    assert env.airtime_ms(20) == pytest.approx(226.304, abs=1e-6)
    assert env.airtime_ms(31) == pytest.approx(312.320, abs=1e-6)
    assert env.airtime_ms(255) == pytest.approx(1717.248, abs=1e-6)


def test_semtech_measured_sf9_cad_reference_is_kept_distinct_from_generic_model():
    env = EnvironmentKernel(sf=9, bandwidth_hz=125_000)
    # AN1200.48's measured four-symbol SF9/BW125 CAD interval is ~4.67
    # symbols. Keep this as a measured reference; the generic environment uses
    # the datasheet N+~0.5-symbol model so it remains valid for arbitrary PHYs.
    assert AN1200_48_SF9_BW125_CAD4_MEASURED_MS == pytest.approx(19.145)
    assert AN1200_48_SF9_BW125_CAD4_MEASURED_MS > env.cad_duration_ms()
    assert AN1200_48_SF9_BW125_CAD4_MEASURED_MS / env.symbol_time_ms() == pytest.approx(
        4.674072265625, abs=1e-12
    )


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


def test_radiolib6_default_spi_is_well_below_sx1262_limit():
    assert RADIOLIB6_DEFAULT_SPI_HZ == 2_000_000
    assert RADIOLIB6_DEFAULT_SPI_HZ <= 16_000_000
    assert RADIOLIB6_BUSY_POLL_FLOOR_MS == pytest.approx(0.001)
    assert spi_wire_time_ms(255, RADIOLIB6_DEFAULT_SPI_HZ) == pytest.approx(
        1.020, abs=1e-12
    )


def test_exact_radiolib6_transaction_counts_are_pinned():
    # Derived from tag 6.0.0 SX126x.cpp + Module.cpp with default paranoid
    # verification. Byte counts are exact; transaction counts pin RadioLib's
    # deterministic delay-before-BUSY-poll floor as well.
    assert RSSI_SPI_BYTES_PER_SAMPLE == 6
    assert TX_SETUP_FIXED_SPI_BYTES == 87
    assert TX_SETUP_SPI_TRANSACTIONS == 24
    assert RX_READ_FIXED_SPI_BYTES == 61
    assert RX_READ_SPI_TRANSACTIONS == 19
    # MAC::loop reads IRQ status before RadioLib finishTransmit()+startReceive().
    assert RX_REARM_AFTER_TX_SPI_BYTES == 74
    assert RX_REARM_AFTER_TX_SPI_TRANSACTIONS == 20
    assert RX_REARM_AFTER_READ_SPI_BYTES == 56
    assert RX_REARM_AFTER_READ_SPI_TRANSACTIONS == 14
    assert CAD_SCAN_FIXED_SPI_BYTES == 63
    assert CAD_SCAN_SPI_TRANSACTIONS == 18


def test_production_rssi_cca_timing_includes_radiolib_busy_poll_floor():
    offsets = rssi_sample_offsets_ms()
    assert len(offsets) == 3
    assert offsets == pytest.approx((0.026, 10.052, 20.078), abs=1e-12)
    assert rssi_cca_duration_ms() == pytest.approx(20.078, abs=1e-12)


def test_tx_startup_is_documented_lower_typical_bound():
    # Exact wire bytes + deterministic RadioLib BUSY-poll floor + Semtech's
    # documented typical STBY_RC->TX transition. Configuration-command BUSY
    # processing and MCU execution remain explicitly unmodeled.
    assert tx_startup_ms(20) == pytest.approx(0.577, abs=1e-12)
    assert tx_startup_ms(255) == pytest.approx(1.517, abs=1e-12)
    assert tx_startup_ms(255) - tx_startup_ms(20) == pytest.approx(
        spi_wire_time_ms(255 - 20), abs=1e-12
    )
    assert tx_startup_ms(20) > SX1262_STBY_RC_TO_TX_MS


def test_rx_done_to_lcmm_callback_includes_busy_poll_floor():
    assert rx_packet_read_ms(11) == pytest.approx(0.307, abs=1e-12)
    assert rx_packet_read_ms(255) == pytest.approx(1.283, abs=1e-12)
    assert rx_packet_read_ms(255) - rx_packet_read_ms(11) == pytest.approx(
        spi_wire_time_ms(255 - 11), abs=1e-12
    )


def test_tx_rearm_and_continuous_rx_refresh_are_distinct_paths():
    assert rx_rearm_ms() == pytest.approx(0.398, abs=1e-12)
    # After an RX callback the SX1262 never left Rx Continuous mode. The
    # production startReceive() call is a redundant refresh; no documented
    # STBY_RC->RX transition applies, so only SPI + BUSY-poll floor is asserted.
    assert rx_rearm_after_read_ms() == pytest.approx(0.238, abs=1e-12)
    assert rx_rearm_ms() > rx_rearm_after_read_ms() > 0.0
    assert rx_rearm_after_read_ms() > spi_wire_time_ms(RX_REARM_AFTER_READ_SPI_BYTES)


def test_optional_cad_includes_spi_and_busy_poll_floor_around_correlation():
    assert cad_scan_spi_overhead_ms() == pytest.approx(0.270, abs=1e-12)
    env = EnvironmentKernel(sf=9, bandwidth_hz=125_000)
    assert env.cad_duration_ms() + cad_scan_spi_overhead_ms() == pytest.approx(
        18.702, abs=1e-9
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
