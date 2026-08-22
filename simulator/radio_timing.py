from __future__ import annotations

"""Timing helpers for the SX1262 + RadioLib 6.x path used by DTProtocol.

These values model host/radio overhead that is separate from LoRa time-on-air.
They are deliberately kept in one place so the Python protocol adapter, timing
regressions and documentation use the same assumptions.

The SPI byte counts below are an approximation of the public RadioLib 6.0
startTransmit()/startReceive()/readData() command sequence. They include
command/data bytes but not MCU/HAL software overhead, so they should be read as
a conservative wire-time model rather than cycle-accurate emulation.
"""

# RadioLib 6.0 BuildOpt.h default for ordinary Arduino platforms.
RADIOLIB6_DEFAULT_SPI_HZ = 2_000_000

# SX1261/2 datasheet typical STBY_RC transitions.
SX1262_STBY_RC_TO_RX_MS = 0.083
SX1262_STBY_RC_TO_TX_MS = 0.126
SX1262_WARM_SLEEP_TO_STBY_RC_MS = 0.340

# Production DTProtocol MAC samples RSSI three times with 10 ms between samples.
RSSI_CCA_SAMPLES = 3
RSSI_CCA_SAMPLE_SPACING_MS = 10.0

# Approximate SPI bytes consumed by one GetRssiInst transaction: command,
# status/dummy and one returned byte.
RSSI_SPI_BYTES_PER_SAMPLE = 3

# Approximate fixed RadioLib 6 startTransmit traffic outside the frame buffer:
# GetPacketType, SetPacketParams (+ IQ register handling), SetDioIrqParams,
# SetBufferBaseAddress, WriteBuffer offset, ClearIrqStatus, sensitivity
# read/write/GetPacketType and SetTx. The payload/frame bytes themselves are
# added separately below.
TX_SETUP_FIXED_SPI_BYTES = 50

# RX_DONE processing before MAC can hand a packet to LCMM: IRQ/length queries,
# ReadBuffer command/offset, buffer reset and IRQ clear. The received frame bytes
# are added to this fixed command traffic.
RX_READ_FIXED_SPI_BYTES = 14

# Approximate public startReceive path after TX completion: finishTransmit IRQ
# clear + standby, RX IRQ mapping, buffer base, IRQ clear, packet type/params and
# SetRx. This intentionally models SPI wire time only; BUSY/state transition is
# added separately.
RX_REARM_SPI_BYTES = 42


def spi_wire_time_ms(byte_count: int, spi_hz: int = RADIOLIB6_DEFAULT_SPI_HZ) -> float:
    if byte_count <= 0 or spi_hz <= 0:
        return 0.0
    return float(byte_count) * 8.0 / float(spi_hz) * 1000.0


def rssi_cca_duration_ms(
    samples: int = RSSI_CCA_SAMPLES,
    spacing_ms: float = RSSI_CCA_SAMPLE_SPACING_MS,
    spi_hz: int = RADIOLIB6_DEFAULT_SPI_HZ,
) -> float:
    """Wall time of the blocking RSSI CCA used by production MAC.

    With three samples there are two 10 ms gaps plus three short SPI reads.
    """
    n = max(1, int(samples))
    return max(0.0, float(spacing_ms)) * float(n - 1) + spi_wire_time_ms(
        n * RSSI_SPI_BYTES_PER_SAMPLE, spi_hz
    )


def tx_startup_ms(frame_bytes: int, spi_hz: int = RADIOLIB6_DEFAULT_SPI_HZ) -> float:
    """Approximate startTransmit host/SPI setup plus PA/radio transition."""
    spi_bytes = TX_SETUP_FIXED_SPI_BYTES + max(0, int(frame_bytes))
    return spi_wire_time_ms(spi_bytes, spi_hz) + SX1262_STBY_RC_TO_TX_MS


def rx_packet_read_ms(frame_bytes: int, spi_hz: int = RADIOLIB6_DEFAULT_SPI_HZ) -> float:
    """Approximate RX_DONE -> MAC/LCMM packet-available SPI latency."""
    spi_bytes = RX_READ_FIXED_SPI_BYTES + max(0, int(frame_bytes))
    return spi_wire_time_ms(spi_bytes, spi_hz)


def rx_rearm_ms(spi_hz: int = RADIOLIB6_DEFAULT_SPI_HZ) -> float:
    """Approximate TX_DONE -> continuous RX-ready interval."""
    return spi_wire_time_ms(RX_REARM_SPI_BYTES, spi_hz) + SX1262_STBY_RC_TO_RX_MS


def rssi_sample_offsets_ms(
    samples: int = RSSI_CCA_SAMPLES,
    spacing_ms: float = RSSI_CCA_SAMPLE_SPACING_MS,
    spi_hz: int = RADIOLIB6_DEFAULT_SPI_HZ,
) -> tuple[float, ...]:
    """Approximate instants at which each GetRssiInst result becomes available."""
    n = max(1, int(samples))
    read_ms = spi_wire_time_ms(RSSI_SPI_BYTES_PER_SAMPLE, spi_hz)
    return tuple(i * max(0.0, float(spacing_ms)) + (i + 1) * read_ms for i in range(n))
