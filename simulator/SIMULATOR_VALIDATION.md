# Simulator radio / hardware fidelity validation

This document is the acceptance matrix for the shared Python/C++ DTProtocol simulator.
It deliberately separates **protocol correctness** from **radio realism**. A result is
not described as hardware-realistic unless every radio feature materially involved in
that result is at least `Exact` or `Bounded approximation` below.

Reference configuration unless a scenario overrides it:

- SX1262
- LoRa SF9 / BW125 kHz / CR 4/7
- 8-symbol preamble
- RadioLib 6.x API (`library.json` currently declares `^6.0.0`)
- private LoRa sync word
- production MAC RSSI-first carrier sensing; CAD disabled by default
- no automatic 1%/10% duty throttle (strict duty mode is an explicit experiment)

## Fidelity levels

- **Exact** — simulator implements the same discrete rule/equation as the reference implementation/documentation.
- **Bounded approximation** — timing/behavior is based on documented chip/library operations, but MCU/HAL overhead or analog behavior is simplified.
- **Intentional abstraction** — behavior is deliberately represented at a higher level and is suitable only for the stated class of experiments.
- **Missing** — hardware behavior is currently absent and can materially bias some experiments.

## Validation matrix

| Area | Fidelity | Current model | Reference / evidence | Regression / next step |
|---|---|---|---|---|
| LoRa symbol time | **Exact** | `2^SF / BW` | SX126x LoRa modem equation; RadioLib `getTimeOnAir()` implementation | `test_radio_timing.py` |
| LoRa frame airtime | **Exact for modeled explicit-header LoRa profile** | Same payload/symbol equation as RadioLib, including SF5/6 special term and automatic LDRO at symbol time >=16 ms | RadioLib 6.x SX126x implementation | Cross-SF/BW/payload parameterized test |
| Maximum radio payload | **Exact** | 255 bytes | SX126x packet buffer / RadioLib SX126x max packet length | oversize-drop tests |
| Default propagation delay | **Intentional abstraction** | zero unless scenario explicitly supplies synthetic latency | RF propagation is ~3.3 us/km, negligible beside SF9 airtime for intended local meshes | `test_default_link_delay_is_not_fake_radio_propagation` |
| Continuous motion during a frame | **Exact for piecewise-linear trajectories** | analytic range check over the whole RF interval, not timestep sampling | simulator mathematical invariant | movement/failure tests |
| Failure/link-change ordering | **Exact simulator invariant** | environment events win at equal timestamp over PHY/protocol callbacks | explicit event-priority contract | shared environment tests |
| Radio half-duplex | **Intentional abstraction, conservative** | receiver transmitting during an overlapping frame loses that frame | SX1262 single transceiver cannot TX and RX simultaneously | hidden-terminal / half-duplex tests |
| Co-SF collision without power model | **Intentional abstraction, pessimistic** | any audible overlapping transmission corrupts the frame | real LoRa has capture/co-channel rejection | add received-power/capture model before using dense-network capacity numbers |
| Capture effect | **Missing in current shared model** | none | Semtech quotes 19 dB LoRa co-channel rejection; experimental LoRa literature observes capture in favorable power/timing cases | add optional received-power model; keep disabled when link powers are unknown |
| Preamble lock / late interferer timing | **Missing** | all overlap is treated equivalently | real outcome depends on desired/interferer arrival order and preamble/header lock | add only together with capture model |
| Cross-SF interference | **Missing** | one global SF/BW per environment | LoRa SFs are not perfectly orthogonal | future multi-PHY/channel model |
| Multi-channel operation | **Missing** | one abstract shared channel | production MAC has region/channel tables | add channel id to each transmission before multi-channel experiments |
| Path loss / received power | **Missing by default** | links provide range + independent packet loss, not dBm | RF link budget is deployment-specific | optional dBm/link-budget layer; do not invent path-loss coefficients by default |
| RSSI CCA schedule | **Exact timing, abstract energy** (Python backend) | 3 instantaneous RSSI reads with 10 ms between reads; RX stays armed | production `MAC::transmissionAuthorized()` | `test_carrier_sense_timing.py` |
| RSSI threshold | **Intentional abstraction** | any reachable current RF energy is considered above CCA threshold | production compares instantaneous RSSI to calibrated noise floor + squelch | needs dBm model to reproduce threshold quantitatively |
| RX_DONE during RSSI CCA | **Production-faithful abstraction** | a reachable frame completing during CCA also aborts the send even if energy fell between sample instants | production checks DIO1 wake flag between RSSI samples | dedicated short-between-samples regression |
| Carrier backoff | **Exact range/distribution** | independent keyed random 25-250 ms | production `25 + module.random(226)` | CCA tests; deterministic by environment seed |
| CAD default role | **Exact policy** | disabled by default; optional supplement to RSSI | production v2 MAC; user hardware history; Semtech AN1200.48 shows CAD is probabilistic/parameter dependent | do not make CAD authoritative CCA |
| CAD duration | **Bounded approximation** | default 4-symbol scan plus about 0.5-symbol processing | RadioLib 6 defaults / Semtech AN1200.48 timing measurements | SF9/BW125 pinned near 18.432 ms |
| CAD detection probability | **Missing** | optional CAD contract can be forced detected/free; shared RF model has no sensitivity curve | AN1200.48 shows false detection and missed detection depend on SF/BW/symbol count/threshold/power | add stochastic CAD only if CAD is used in production experiments |
| SPI clock (RadioLib path) | **Exact configured default** | 2 MHz assumed | RadioLib 6 `BuildOpt.h` ordinary Arduino default; SX1262 allows up to 16 MHz SCK | timing test asserts 2 MHz <= 16 MHz |
| SPI transfer duration | **Exact wire-time equation / approximate byte count** | `8 * bytes / 2 MHz` | SPI framing + RadioLib command sequence | command byte-count constants are documented in `radio_timing.py` |
| SX1262 BUSY handling | **Exact in production library; bounded in contract shim; not separately modeled in high-level RF kernel** | RadioLib waits around SPI commands; host shim accounts key state transitions | SX1261/2 datasheet requires BUSY low before commands | contract tests must reject API/timing sequences that skip key transitions |
| STBY_RC -> TX | **Bounded approximation** | 126 us typical plus RadioLib SPI/setup | SX1261/2 documented typical transition | `tx_startup_ms()` + C++ MAC contract |
| STBY_RC -> RX | **Bounded approximation** | 83 us typical plus RadioLib re-arm SPI | SX1261/2 documented typical transition | `rx_rearm_ms()` + C++ MAC contract |
| warm sleep -> STBY_RC | **Bounded approximation** | 340 us in contract shim | SX1261/2 documented typical transition | only relevant when sleep is used |
| TX startup / buffer write | **Bounded approximation** | frame-size-dependent RadioLib SPI traffic + 126 us transition before RF airtime | RadioLib 6 `startTransmit()` writes packet params/buffer and waits for BUSY | `tx_startup_ms()` tests |
| RX packet extraction | **Bounded approximation** | frame-size-dependent SPI read after RF RX_DONE before MAC/LCMM callback | RadioLib 6 `readData()` / buffer reset / IRQ clear path | `rx_packet_read_ms()` |
| TX_DONE -> continuous RX | **Bounded approximation** | finish/re-arm SPI + 83 us RX transition | production MAC calls `finishTransmit()`, then `startReceive()`, then owner callback | separate no-ACK and link-ACK timing tests |
| Link ACK RF size | **Exact for current packed structs** | 8-byte MAC header + 3-byte LCMM ACK = 11 radio bytes | `MACHeader`, `LCMMPacketResponse` + packet id | timing/airtime test |
| Reliable DATA receive ordering | **Exact target ordering once timing overlay is applied** | RX frame -> SPI read -> ACK CCA/setup -> ACK RF -> RX re-arm -> DTPK callback | current LCMM `afterCallbackSent` | ACK/DTPK timeline regression |
| ACK reception ordering | **Bounded approximation** | ACK RF end -> propagation -> ACK buffer SPI read -> sender completion | RadioLib receive + LCMM `handleACK` | same timeline regression |
| Link retries | **Protocol-exact, radio timing bounded** | failed/lost ACK retries up to configured count; CCA denial does not consume RF retry | current LCMM transient MAC handling | LCMM contract tests |
| CRC corruption | **Intentional abstraction** | packet loss/collision drops whole frame; no bit-error distribution | hardware CRC rejects corrupted packet | sufficient for routing reliability, not BER/PER studies |
| Frequency error / oscillator drift | **Missing** | none | production cumulative auto-correction was intentionally removed; real crystals/TCXO still have ppm error | add only for RF-margin/frequency-offset studies |
| Noise-floor calibration | **Partial abstraction** | production C++ contract exercises algorithm; shared Python RF model does not synthesize analog RSSI calibration | production MAC samples/sorts RSSI | dBm layer prerequisite |
| IRQ latching / event classification | **Strong contract-level model** | production MAC contract uses SX126x IRQ bits rather than mutable software state | RadioLib/SX126x IRQ API | MAC contract tests inject TX_DONE/RX_DONE/CAD/error cases |
| MCU time (`millis`) across reboot | **Exact adapter invariant** | each C++ node gets local time from boot epoch; environment time remains global | Arduino semantics | C++ environment tests |
| C++ subprocess tick quantization | **Intentional abstraction** | default adapter tick can be 50 ms | real firmware loop frequency is board/load dependent | use <=5 ms for latency/timer validation; 50 ms is acceptable only for long scale studies |
| C++ subprocess production CCA | **Missing** | fake MAC currently starts TX synchronously and cannot yield mid-`sendData()` to query environment | real production MAC blocks through RSSI CCA before returning | requires bidirectional/coroutine CCA handshake; until then use Python backend for contention capacity studies |
| Strict regulatory duty policy | **Optional experiment** | separate per-node airtime spacing; survives simulated reboot | regulatory experiment, not default MAC behavior | `strict_duty_cycle=True` only |

## CAD conclusion

CAD must **not** be treated as a reliable universal carrier detector for DTProtocol.

Semtech AN1200.48 makes the trade-off explicit: CAD sensitivity and false detections
vary with SF, bandwidth, number of CAD symbols, `cadDetPeak`, `cadDetMin`, and received
power. The application note's own SF9 tables show that detection degrades near the
sensitivity edge, and field studies report missed whole transmissions at long range.
CAD also correlates LoRa modulation rather than measuring arbitrary channel energy.

Therefore the v2 MAC policy is:

1. remain in continuous RX;
2. use instantaneous RSSI samples as the default CCA;
3. if an RX interrupt arrives during CCA, defer TX and service it;
4. randomized 25-250 ms backoff when busy;
5. CAD may be enabled as an **additional** weak-LoRa check, never as the only test.

This preserves the reason the original hardware implementation moved back to RSSI,
while still allowing SX1262 CAD to be used where measurements show it helps.

## Large-network interpretation rules

- **Lines / sparse graphs without `radio_contention`:** suitable for routing-state,
  chunking, sequence/feasibility, convergence-diameter and memory studies.
- **Python backend with `radio_contention`:** suitable for CCA/backoff/hidden-terminal
  experiments, but current no-capture collision behavior is pessimistic.
- **C++ subprocess with `radio_contention`:** protocol is real C++, but carrier-sense
  timing is not yet exact. Do not use it as the quantitative MAC-capacity oracle.
- **Dense capacity / PDR claims:** wait for received-power + capture/preamble-lock model.

## Hardware integration warning: Picopod

The current `Picopod` tree is not the same SX1262/RadioLib path. Its
`lib/lora/LoRa-RP2040.cpp` uses an SX127x-style register interface and calls:

```cpp
spi_init(SPI_PORT, 12500);
```

The Pico SDK `spi_init()` baudrate argument is **Hz**, so that requests 12.5 kHz,
not 12.5 MHz. If this driver is still used on active hardware, it needs a separate
audit before timing conclusions from the RadioLib/SX1262 MAC are transferred to it.
At 12.5 kHz, clocking 255 bytes alone is roughly 163 ms.

## Primary references

- Semtech SX1261/SX1262 current product page and current datasheet listing:
  https://www.semtech.com/products/wireless-rf/lora-connect/sx1262
- Semtech AN1200.48, *LoRa Channel Activity Detection (CAD) with SX126x*.
- RadioLib 6.0.0 `SX126x.cpp`, `SX126x.h`, `BuildOpt.h`.
- Raspberry Pi Pico SDK `hardware_spi`: `spi_init(..., baudrate)` takes Hz.

Whenever these sources or the project's declared RadioLib major version change, rerun
this matrix before accepting new simulator benchmarks as hardware-realistic.
