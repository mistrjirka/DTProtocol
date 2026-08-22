from __future__ import annotations

"""Timing helpers for the SX1262 + RadioLib 6.0 path used by DTProtocol.

The constants in this module are derived from the actual RadioLib 6.0
``SX126x`` and ``Module`` command paths, not from MCU cycle estimates.  RadioLib
uses a 2 MHz SPI clock by default and enables ``RADIOLIB_SPI_PARANOID`` by
default.  Consequently, every ordinary verified stream command is followed by
a three-byte ``GetStatus`` transaction.

Only documented/observable radio state-transition time is added.  MCU/HAL
software execution time, interrupt dispatch latency and allocator/CRC CPU time
are intentionally not guessed; they are small compared with LoRa airtime and
are documented as simulator limitations instead of being hidden in constants.
"""

# RadioLib 6.0 BuildOpt.h default for ordinary Arduino platforms.
RADIOLIB6_DEFAULT_SPI_HZ = 2_000_000

# SX1261/2 datasheet typical STBY_RC transitions.  RadioLib waits on BUSY, so
# these are wall-clock intervals after the final SetRx/SetTx SPI transaction.
SX1262_STBY_RC_TO_RX_MS = 0.083
SX1262_STBY_RC_TO_TX_MS = 0.126
SX1262_WARM_SLEEP_TO_STBY_RC_MS = 0.340

# Production DTProtocol MAC samples RSSI three times with 10 ms between reads.
RSSI_CCA_SAMPLES = 3
RSSI_CCA_SAMPLE_SPACING_MS = 10.0

# ---------------------------------------------------------------------------
# Exact RadioLib 6.0 SPI byte counts for the paths used by DTProtocol.
# ---------------------------------------------------------------------------
# SPIcheckStream(): GetStatus command + status/NOP byte + one returned byte.
RADIOLIB6_VERIFY_STATUS_BYTES = 3

# getRSSI(false): GetRssiInst command + status/NOP + 3 returned bytes, followed
# by the default paranoid GetStatus verification transaction.
RSSI_SPI_BYTES_PER_SAMPLE = 1 + 1 + 3 + RADIOLIB6_VERIFY_STATUS_BYTES  # 8

# Common primitive byte counts, including RadioLib's default verification where
# the corresponding public helper requests it.
GET_PACKET_TYPE_SPI_BYTES = 1 + 1 + 1 + RADIOLIB6_VERIFY_STATUS_BYTES  # 6
GET_IRQ_STATUS_SPI_BYTES = 1 + 1 + 2 + RADIOLIB6_VERIFY_STATUS_BYTES   # 7
REGISTER_READ_ONE_SPI_BYTES = 3 + 1 + 1 + RADIOLIB6_VERIFY_STATUS_BYTES  # 8
REGISTER_WRITE_ONE_SPI_BYTES = 3 + 1  # raw register burst write: no SPIcheck
SET_PACKET_PARAMS_WIRE_BYTES = 1 + 6 + RADIOLIB6_VERIFY_STATUS_BYTES  # 10
FIX_INVERTED_IQ_SPI_BYTES = REGISTER_READ_ONE_SPI_BYTES + REGISTER_WRITE_ONE_SPI_BYTES  # 12
SET_PACKET_PARAMS_SPI_BYTES = FIX_INVERTED_IQ_SPI_BYTES + SET_PACKET_PARAMS_WIRE_BYTES  # 22
SET_DIO_IRQ_SPI_BYTES = 1 + 8 + RADIOLIB6_VERIFY_STATUS_BYTES  # 12
SET_BUFFER_BASE_SPI_BYTES = 1 + 2 + RADIOLIB6_VERIFY_STATUS_BYTES  # 6
CLEAR_IRQ_SPI_BYTES = 1 + 2 + RADIOLIB6_VERIFY_STATUS_BYTES  # 6
SET_STANDBY_SPI_BYTES = 1 + 1 + RADIOLIB6_VERIFY_STATUS_BYTES  # 5
SET_TX_SPI_BYTES = 1 + 3 + RADIOLIB6_VERIFY_STATUS_BYTES  # 7
# setRx(..., waitForGpio=true, verify=false)
SET_RX_SPI_BYTES = 1 + 3  # 4
WRITE_BUFFER_FIXED_SPI_BYTES = 2 + RADIOLIB6_VERIFY_STATUS_BYTES  # + payload
READ_BUFFER_FIXED_SPI_BYTES = 2 + 1 + RADIOLIB6_VERIFY_STATUS_BYTES  # + payload
GET_RX_BUFFER_STATUS_SPI_BYTES = 1 + 1 + 2 + RADIOLIB6_VERIFY_STATUS_BYTES  # 7
GET_PACKET_LENGTH_EXPLICIT_SPI_BYTES = GET_PACKET_TYPE_SPI_BYTES + GET_RX_BUFFER_STATUS_SPI_BYTES  # 13
FIX_SENSITIVITY_SPI_BYTES = (
    REGISTER_READ_ONE_SPI_BYTES
    + GET_PACKET_TYPE_SPI_BYTES
    + REGISTER_WRITE_ONE_SPI_BYTES
)  # 18

# MAC::sendData performs SetStandby before RadioLib::startTransmit.  The
# startTransmit LoRa path then does GetPacketType, SetPacketParams (including
# the IQ erratum register read/write), DIO IRQ mapping, buffer base, WriteBuffer,
# IRQ clear, sensitivity erratum read/get-type/write, and SetTx.
TX_SETUP_FIXED_SPI_BYTES = (
    SET_STANDBY_SPI_BYTES
    + GET_PACKET_TYPE_SPI_BYTES
    + SET_PACKET_PARAMS_SPI_BYTES
    + SET_DIO_IRQ_SPI_BYTES
    + SET_BUFFER_BASE_SPI_BYTES
    + WRITE_BUFFER_FIXED_SPI_BYTES
    + CLEAR_IRQ_SPI_BYTES
    + FIX_SENSITIVITY_SPI_BYTES
    + SET_TX_SPI_BYTES
)  # 87, plus frame bytes

# DIO1/RX_DONE -> LCMM callback in production MAC:
#   MAC::loop getIrqStatus
#   MAC::handlePacket getPacketLength
#   RadioLib::readData:
#       SPIcheckStream, getIrqStatus, getPacketLength, ReadBuffer,
#       setBufferBaseAddress, clearIrqStatus.
RX_READ_FIXED_SPI_BYTES = (
    GET_IRQ_STATUS_SPI_BYTES
    + GET_PACKET_LENGTH_EXPLICIT_SPI_BYTES
    + RADIOLIB6_VERIFY_STATUS_BYTES
    + GET_IRQ_STATUS_SPI_BYTES
    + GET_PACKET_LENGTH_EXPLICIT_SPI_BYTES
    + READ_BUFFER_FIXED_SPI_BYTES
    + SET_BUFFER_BASE_SPI_BYTES
    + CLEAR_IRQ_SPI_BYTES
)  # 61, plus frame bytes

# TX_DONE -> continuous RX-ready.  finishTransmit() clears IRQ and explicitly
# enters standby; startReceive() restores RX IRQ mapping/buffer/packet params and
# issues SetRx.  The final STBY_RC->RX BUSY interval is added separately.
START_RECEIVE_SPI_BYTES = (
    SET_DIO_IRQ_SPI_BYTES
    + SET_BUFFER_BASE_SPI_BYTES
    + CLEAR_IRQ_SPI_BYTES
    + GET_PACKET_TYPE_SPI_BYTES
    + SET_PACKET_PARAMS_SPI_BYTES
    + SET_RX_SPI_BYTES
)  # 56
FINISH_TRANSMIT_SPI_BYTES = CLEAR_IRQ_SPI_BYTES + SET_STANDBY_SPI_BYTES  # 11
RX_REARM_AFTER_TX_SPI_BYTES = FINISH_TRANSMIT_SPI_BYTES + START_RECEIVE_SPI_BYTES  # 67
RX_REARM_AFTER_READ_SPI_BYTES = START_RECEIVE_SPI_BYTES  # 56

# Optional synchronous scanChannel() at SF9+ uses startChannelScan followed by
# getChannelScanResult.  CAD RF correlation duration itself is modeled by
# EnvironmentKernel.cad_duration_ms().  No undocumented RX->STBY/CAD transition
# delay is invented here.
CAD_SCAN_FIXED_SPI_BYTES = (
    GET_PACKET_TYPE_SPI_BYTES
    + SET_STANDBY_SPI_BYTES
    + SET_DIO_IRQ_SPI_BYTES
    + CLEAR_IRQ_SPI_BYTES
    + (1 + 7 + RADIOLIB6_VERIFY_STATUS_BYTES)  # SetCadParams
    + (1 + RADIOLIB6_VERIFY_STATUS_BYTES)      # SetCad
    + GET_PACKET_TYPE_SPI_BYTES
    + GET_IRQ_STATUS_SPI_BYTES
    + CLEAR_IRQ_SPI_BYTES
)  # 63


def spi_wire_time_ms(byte_count: int, spi_hz: int = RADIOLIB6_DEFAULT_SPI_HZ) -> float:
    if byte_count <= 0 or spi_hz <= 0:
        return 0.0
    return float(byte_count) * 8.0 / float(spi_hz) * 1000.0


def rssi_cca_duration_ms(
    samples: int = RSSI_CCA_SAMPLES,
    spacing_ms: float = RSSI_CCA_SAMPLE_SPACING_MS,
    spi_hz: int = RADIOLIB6_DEFAULT_SPI_HZ,
) -> float:
    """Wall time of production's blocking three-sample RSSI CCA."""
    n = max(1, int(samples))
    return max(0.0, float(spacing_ms)) * float(n - 1) + spi_wire_time_ms(
        n * RSSI_SPI_BYTES_PER_SAMPLE, spi_hz
    )


def tx_startup_ms(frame_bytes: int, spi_hz: int = RADIOLIB6_DEFAULT_SPI_HZ) -> float:
    """CCA-clear -> first RF symbol, including SPI setup and SetTx BUSY time."""
    spi_bytes = TX_SETUP_FIXED_SPI_BYTES + max(0, int(frame_bytes))
    return spi_wire_time_ms(spi_bytes, spi_hz) + SX1262_STBY_RC_TO_TX_MS


def rx_packet_read_ms(frame_bytes: int, spi_hz: int = RADIOLIB6_DEFAULT_SPI_HZ) -> float:
    """RX_DONE DIO1 -> MAC/LCMM packet callback for explicit-header LoRa."""
    spi_bytes = RX_READ_FIXED_SPI_BYTES + max(0, int(frame_bytes))
    return spi_wire_time_ms(spi_bytes, spi_hz)


def rx_rearm_ms(spi_hz: int = RADIOLIB6_DEFAULT_SPI_HZ) -> float:
    """TX_DONE -> continuous RX-ready (finishTransmit + startReceive)."""
    return spi_wire_time_ms(RX_REARM_AFTER_TX_SPI_BYTES, spi_hz) + SX1262_STBY_RC_TO_RX_MS


def rx_rearm_after_read_ms(spi_hz: int = RADIOLIB6_DEFAULT_SPI_HZ) -> float:
    """RX callback return -> continuous RX-ready when no immediate TX starts."""
    return spi_wire_time_ms(RX_REARM_AFTER_READ_SPI_BYTES, spi_hz) + SX1262_STBY_RC_TO_RX_MS


def cad_scan_spi_overhead_ms(spi_hz: int = RADIOLIB6_DEFAULT_SPI_HZ) -> float:
    """SPI-only overhead around synchronous RadioLib 6 scanChannel()."""
    return spi_wire_time_ms(CAD_SCAN_FIXED_SPI_BYTES, spi_hz)


def rssi_sample_offsets_ms(
    samples: int = RSSI_CCA_SAMPLES,
    spacing_ms: float = RSSI_CCA_SAMPLE_SPACING_MS,
    spi_hz: int = RADIOLIB6_DEFAULT_SPI_HZ,
) -> tuple[float, ...]:
    """Instants at which each GetRssiInst result is available."""
    n = max(1, int(samples))
    read_ms = spi_wire_time_ms(RSSI_SPI_BYTES_PER_SAMPLE, spi_hz)
    return tuple(i * max(0.0, float(spacing_ms)) + (i + 1) * read_ms for i in range(n))
