from __future__ import annotations

"""Timing helpers for the SX1262 + RadioLib 6.0 path used by DTProtocol.

SPI byte counts and transaction counts are derived from RadioLib 6.0's actual
``SX126x``/``Module`` paths.  RadioLib uses 2 MHz SPI by default and enables
``RADIOLIB_SPI_PARANOID`` by default, so verified stream calls are followed by a
GetStatus transaction.

Absolute wall time cannot be cycle-exact from documentation alone: RadioLib
waits for SX1262 BUSY around every stream transaction and Semtech publishes
explicit typical BUSY durations for mode transitions, but not deterministic
processing time for every configuration command.  The helpers below therefore
model:

* exact SPI wire bytes at the declared 2 MHz default;
* RadioLib's deterministic 1 us delay before each post-command BUSY poll;
* documented typical STBY_RC->TX/RX mode-transition BUSY intervals.

Any additional command-processing BUSY time, MCU/HAL execution, interrupt
latency, allocator/CRC CPU time and scheduling jitter remain bounded omissions.
"""

RADIOLIB6_DEFAULT_SPI_HZ = 2_000_000
RADIOLIB6_BUSY_POLL_FLOOR_MS = 0.001

# SX1261/2 datasheet Table 8-2 typical switching times.
SX1262_STBY_RC_TO_RX_MS = 0.083
SX1262_STBY_RC_TO_TX_MS = 0.126
SX1262_WARM_SLEEP_TO_STBY_RC_MS = 0.340

RSSI_CCA_SAMPLES = 3
RSSI_CCA_SAMPLE_SPACING_MS = 10.0

# ---------------------------------------------------------------------------
# RadioLib 6.0 SPI bytes and waited SPI-transaction counts.
# ---------------------------------------------------------------------------
RADIOLIB6_VERIFY_STATUS_BYTES = 3
RADIOLIB6_VERIFIED_STREAM_TRANSACTIONS = 2  # command + paranoid GetStatus

RSSI_SPI_BYTES_PER_SAMPLE = 1 + 1 + 3 + RADIOLIB6_VERIFY_STATUS_BYTES  # 8
RSSI_SPI_TRANSACTIONS_PER_SAMPLE = 2

GET_PACKET_TYPE_SPI_BYTES = 1 + 1 + 1 + RADIOLIB6_VERIFY_STATUS_BYTES  # 6
GET_PACKET_TYPE_SPI_TRANSACTIONS = 2
GET_IRQ_STATUS_SPI_BYTES = 1 + 1 + 2 + RADIOLIB6_VERIFY_STATUS_BYTES  # 7
GET_IRQ_STATUS_SPI_TRANSACTIONS = 2
REGISTER_READ_ONE_SPI_BYTES = 3 + 1 + 1 + RADIOLIB6_VERIFY_STATUS_BYTES  # 8
REGISTER_READ_ONE_SPI_TRANSACTIONS = 2
REGISTER_WRITE_ONE_SPI_BYTES = 3 + 1  # raw burst write, no SPIcheck
REGISTER_WRITE_ONE_SPI_TRANSACTIONS = 1
SET_PACKET_PARAMS_WIRE_BYTES = 1 + 6 + RADIOLIB6_VERIFY_STATUS_BYTES  # 10
SET_PACKET_PARAMS_WIRE_TRANSACTIONS = 2
FIX_INVERTED_IQ_SPI_BYTES = REGISTER_READ_ONE_SPI_BYTES + REGISTER_WRITE_ONE_SPI_BYTES  # 12
FIX_INVERTED_IQ_SPI_TRANSACTIONS = 3
SET_PACKET_PARAMS_SPI_BYTES = FIX_INVERTED_IQ_SPI_BYTES + SET_PACKET_PARAMS_WIRE_BYTES  # 22
SET_PACKET_PARAMS_SPI_TRANSACTIONS = 5
SET_DIO_IRQ_SPI_BYTES = 1 + 8 + RADIOLIB6_VERIFY_STATUS_BYTES  # 12
SET_DIO_IRQ_SPI_TRANSACTIONS = 2
SET_BUFFER_BASE_SPI_BYTES = 1 + 2 + RADIOLIB6_VERIFY_STATUS_BYTES  # 6
SET_BUFFER_BASE_SPI_TRANSACTIONS = 2
CLEAR_IRQ_SPI_BYTES = 1 + 2 + RADIOLIB6_VERIFY_STATUS_BYTES  # 6
CLEAR_IRQ_SPI_TRANSACTIONS = 2
SET_STANDBY_SPI_BYTES = 1 + 1 + RADIOLIB6_VERIFY_STATUS_BYTES  # 5
SET_STANDBY_SPI_TRANSACTIONS = 2
SET_TX_SPI_BYTES = 1 + 3 + RADIOLIB6_VERIFY_STATUS_BYTES  # 7
SET_TX_SPI_TRANSACTIONS = 2
# setRx(..., waitForGpio=true, verify=false)
SET_RX_SPI_BYTES = 1 + 3  # 4
SET_RX_SPI_TRANSACTIONS = 1
WRITE_BUFFER_FIXED_SPI_BYTES = 2 + RADIOLIB6_VERIFY_STATUS_BYTES  # + payload
WRITE_BUFFER_SPI_TRANSACTIONS = 2
READ_BUFFER_FIXED_SPI_BYTES = 2 + 1 + RADIOLIB6_VERIFY_STATUS_BYTES  # + payload
READ_BUFFER_SPI_TRANSACTIONS = 2
GET_RX_BUFFER_STATUS_SPI_BYTES = 1 + 1 + 2 + RADIOLIB6_VERIFY_STATUS_BYTES  # 7
GET_RX_BUFFER_STATUS_SPI_TRANSACTIONS = 2
GET_PACKET_LENGTH_EXPLICIT_SPI_BYTES = GET_PACKET_TYPE_SPI_BYTES + GET_RX_BUFFER_STATUS_SPI_BYTES  # 13
GET_PACKET_LENGTH_EXPLICIT_SPI_TRANSACTIONS = 4
FIX_SENSITIVITY_SPI_BYTES = (
    REGISTER_READ_ONE_SPI_BYTES
    + GET_PACKET_TYPE_SPI_BYTES
    + REGISTER_WRITE_ONE_SPI_BYTES
)  # 18
FIX_SENSITIVITY_SPI_TRANSACTIONS = 5

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
)  # 87, plus frame
TX_SETUP_SPI_TRANSACTIONS = (
    SET_STANDBY_SPI_TRANSACTIONS
    + GET_PACKET_TYPE_SPI_TRANSACTIONS
    + SET_PACKET_PARAMS_SPI_TRANSACTIONS
    + SET_DIO_IRQ_SPI_TRANSACTIONS
    + SET_BUFFER_BASE_SPI_TRANSACTIONS
    + WRITE_BUFFER_SPI_TRANSACTIONS
    + CLEAR_IRQ_SPI_TRANSACTIONS
    + FIX_SENSITIVITY_SPI_TRANSACTIONS
    + SET_TX_SPI_TRANSACTIONS
)  # 24

RX_READ_FIXED_SPI_BYTES = (
    GET_IRQ_STATUS_SPI_BYTES
    + GET_PACKET_LENGTH_EXPLICIT_SPI_BYTES
    + RADIOLIB6_VERIFY_STATUS_BYTES  # readData() entry SPIcheckStream
    + GET_IRQ_STATUS_SPI_BYTES
    + GET_PACKET_LENGTH_EXPLICIT_SPI_BYTES
    + READ_BUFFER_FIXED_SPI_BYTES
    + SET_BUFFER_BASE_SPI_BYTES
    + CLEAR_IRQ_SPI_BYTES
)  # 61, plus frame
RX_READ_SPI_TRANSACTIONS = (
    GET_IRQ_STATUS_SPI_TRANSACTIONS
    + GET_PACKET_LENGTH_EXPLICIT_SPI_TRANSACTIONS
    + 1
    + GET_IRQ_STATUS_SPI_TRANSACTIONS
    + GET_PACKET_LENGTH_EXPLICIT_SPI_TRANSACTIONS
    + READ_BUFFER_SPI_TRANSACTIONS
    + SET_BUFFER_BASE_SPI_TRANSACTIONS
    + CLEAR_IRQ_SPI_TRANSACTIONS
)  # 19

START_RECEIVE_SPI_BYTES = (
    SET_DIO_IRQ_SPI_BYTES
    + SET_BUFFER_BASE_SPI_BYTES
    + CLEAR_IRQ_SPI_BYTES
    + GET_PACKET_TYPE_SPI_BYTES
    + SET_PACKET_PARAMS_SPI_BYTES
    + SET_RX_SPI_BYTES
)  # 56
START_RECEIVE_SPI_TRANSACTIONS = (
    SET_DIO_IRQ_SPI_TRANSACTIONS
    + SET_BUFFER_BASE_SPI_TRANSACTIONS
    + CLEAR_IRQ_SPI_TRANSACTIONS
    + GET_PACKET_TYPE_SPI_TRANSACTIONS
    + SET_PACKET_PARAMS_SPI_TRANSACTIONS
    + SET_RX_SPI_TRANSACTIONS
)  # 14
FINISH_TRANSMIT_SPI_BYTES = CLEAR_IRQ_SPI_BYTES + SET_STANDBY_SPI_BYTES  # 11
FINISH_TRANSMIT_SPI_TRANSACTIONS = CLEAR_IRQ_SPI_TRANSACTIONS + SET_STANDBY_SPI_TRANSACTIONS  # 4
RX_REARM_AFTER_TX_SPI_BYTES = FINISH_TRANSMIT_SPI_BYTES + START_RECEIVE_SPI_BYTES  # 67
RX_REARM_AFTER_TX_SPI_TRANSACTIONS = FINISH_TRANSMIT_SPI_TRANSACTIONS + START_RECEIVE_SPI_TRANSACTIONS  # 18
RX_REARM_AFTER_READ_SPI_BYTES = START_RECEIVE_SPI_BYTES  # 56
RX_REARM_AFTER_READ_SPI_TRANSACTIONS = START_RECEIVE_SPI_TRANSACTIONS  # 14

CAD_SCAN_FIXED_SPI_BYTES = (
    GET_PACKET_TYPE_SPI_BYTES
    + SET_STANDBY_SPI_BYTES
    + SET_DIO_IRQ_SPI_BYTES
    + CLEAR_IRQ_SPI_BYTES
    + (1 + 7 + RADIOLIB6_VERIFY_STATUS_BYTES)  # SetCadParams
    + (1 + RADIOLIB6_VERIFY_STATUS_BYTES)  # SetCad
    + GET_PACKET_TYPE_SPI_BYTES
    + GET_IRQ_STATUS_SPI_BYTES
    + CLEAR_IRQ_SPI_BYTES
)  # 63
CAD_SCAN_SPI_TRANSACTIONS = 18


def spi_wire_time_ms(byte_count: int, spi_hz: int = RADIOLIB6_DEFAULT_SPI_HZ) -> float:
    if byte_count <= 0 or spi_hz <= 0:
        return 0.0
    return float(byte_count) * 8.0 / float(spi_hz) * 1000.0


def busy_poll_floor_ms(transaction_count: int) -> float:
    """Deterministic RadioLib delay before BUSY polling for waited transactions."""
    return max(0, int(transaction_count)) * RADIOLIB6_BUSY_POLL_FLOOR_MS


def _path_floor_ms(
    byte_count: int,
    transaction_count: int,
    *,
    mode_transition_ms: float = 0.0,
    mode_transition_transactions: int = 0,
    spi_hz: int = RADIOLIB6_DEFAULT_SPI_HZ,
) -> float:
    """Documented minimum/typical wall time for a RadioLib path.

    For a mode-changing transaction the 1 us RadioLib delay occurs *inside* the
    datasheet's NSS-rise -> BUSY-fall transition, so it is not added twice.
    Other waited transactions contribute the deterministic 1 us polling floor;
    any additional BUSY-high command processing is intentionally not invented.
    """
    ordinary = max(0, int(transaction_count) - int(mode_transition_transactions))
    return (
        spi_wire_time_ms(byte_count, spi_hz)
        + busy_poll_floor_ms(ordinary)
        + max(0.0, float(mode_transition_ms))
    )


def rssi_cca_duration_ms(
    samples: int = RSSI_CCA_SAMPLES,
    spacing_ms: float = RSSI_CCA_SAMPLE_SPACING_MS,
    spi_hz: int = RADIOLIB6_DEFAULT_SPI_HZ,
) -> float:
    n = max(1, int(samples))
    return (
        max(0.0, float(spacing_ms)) * float(n - 1)
        + spi_wire_time_ms(n * RSSI_SPI_BYTES_PER_SAMPLE, spi_hz)
        + busy_poll_floor_ms(n * RSSI_SPI_TRANSACTIONS_PER_SAMPLE)
    )


def tx_startup_ms(frame_bytes: int, spi_hz: int = RADIOLIB6_DEFAULT_SPI_HZ) -> float:
    """CCA-clear -> first RF symbol, as a documented lower/typical bound."""
    return _path_floor_ms(
        TX_SETUP_FIXED_SPI_BYTES + max(0, int(frame_bytes)),
        TX_SETUP_SPI_TRANSACTIONS,
        mode_transition_ms=SX1262_STBY_RC_TO_TX_MS,
        mode_transition_transactions=1,  # main SetTx transfer
        spi_hz=spi_hz,
    )


def rx_packet_read_ms(frame_bytes: int, spi_hz: int = RADIOLIB6_DEFAULT_SPI_HZ) -> float:
    """RX_DONE DIO1 -> MAC/LCMM callback, excluding MCU execution overhead."""
    return _path_floor_ms(
        RX_READ_FIXED_SPI_BYTES + max(0, int(frame_bytes)),
        RX_READ_SPI_TRANSACTIONS,
        spi_hz=spi_hz,
    )


def rx_rearm_ms(spi_hz: int = RADIOLIB6_DEFAULT_SPI_HZ) -> float:
    """TX_DONE -> continuous RX-ready, documented lower/typical bound."""
    return _path_floor_ms(
        RX_REARM_AFTER_TX_SPI_BYTES,
        RX_REARM_AFTER_TX_SPI_TRANSACTIONS,
        mode_transition_ms=SX1262_STBY_RC_TO_RX_MS,
        mode_transition_transactions=1,  # main SetRx transfer
        spi_hz=spi_hz,
    )


def rx_rearm_after_read_ms(spi_hz: int = RADIOLIB6_DEFAULT_SPI_HZ) -> float:
    """RX callback return -> continuous RX-ready when no immediate TX starts."""
    return _path_floor_ms(
        RX_REARM_AFTER_READ_SPI_BYTES,
        RX_REARM_AFTER_READ_SPI_TRANSACTIONS,
        mode_transition_ms=SX1262_STBY_RC_TO_RX_MS,
        mode_transition_transactions=1,
        spi_hz=spi_hz,
    )


def cad_scan_spi_overhead_ms(spi_hz: int = RADIOLIB6_DEFAULT_SPI_HZ) -> float:
    """Documented minimum SPI/BUSY-poll overhead around synchronous CAD."""
    return _path_floor_ms(
        CAD_SCAN_FIXED_SPI_BYTES,
        CAD_SCAN_SPI_TRANSACTIONS,
        spi_hz=spi_hz,
    )


def rssi_sample_offsets_ms(
    samples: int = RSSI_CCA_SAMPLES,
    spacing_ms: float = RSSI_CCA_SAMPLE_SPACING_MS,
    spi_hz: int = RADIOLIB6_DEFAULT_SPI_HZ,
) -> tuple[float, ...]:
    n = max(1, int(samples))
    one_read = (
        spi_wire_time_ms(RSSI_SPI_BYTES_PER_SAMPLE, spi_hz)
        + busy_poll_floor_ms(RSSI_SPI_TRANSACTIONS_PER_SAMPLE)
    )
    return tuple(
        i * max(0.0, float(spacing_ms)) + (i + 1) * one_read
        for i in range(n)
    )
