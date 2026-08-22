# Simulator radio / hardware fidelity validation

This document is the acceptance matrix for the shared Python/C++ DTProtocol simulator.
It deliberately separates **protocol correctness** from **radio realism**. A result is
not described as hardware-realistic unless every radio feature materially involved in
that result is at least `Exact` or `Bounded approximation` below.

Reference configuration unless a scenario overrides it:

- SX1262
- LoRa SF9 / BW125 kHz / CR 4/7
- 8-symbol preamble
- RadioLib 6.0 command behavior (`library.json` currently declares `^6.0.0`)
- RadioLib ordinary-Arduino default SPI: 2 MHz, mode 0
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
| LoRa frame airtime | **Exact for modeled explicit-header LoRa profile** | Same payload/symbol equation as RadioLib, including SF5/6 special term and automatic LDRO at symbol time >=16 ms | RadioLib 6.0 SX126x implementation | cross-SF/BW/payload parameterized test |
| Maximum radio payload | **Exact** | 255 bytes | SX126x packet buffer / RadioLib SX126x max packet length | oversize-drop tests |
| Default propagation delay | **Intentional abstraction** | zero unless scenario explicitly supplies synthetic latency | RF propagation is ~3.3 us/km, negligible beside SF9 airtime for intended local meshes | `test_default_link_delay_is_not_fake_radio_propagation` |
| Continuous motion during a frame | **Exact for piecewise-linear trajectories** | analytic range check over the whole RF interval, not timestep sampling | simulator mathematical invariant | movement/failure tests |
| Failure/link-change ordering | **Exact simulator invariant** | environment events win at equal timestamp over PHY/protocol callbacks | explicit event-priority contract | shared environment tests |
| Radio half-duplex | **Intentional abstraction, conservative** | receiver transmitting / not RX-ready at frame start loses that frame | SX1262 single transceiver cannot TX and RX simultaneously | hidden-terminal / timed-backend tests |
| Co-SF collision without power model | **Intentional abstraction, pessimistic** | any audible overlapping transmission corrupts the frame | real LoRa has capture/co-channel rejection | add received-power/capture model before quantitative dense-capacity claims |
| Capture effect | **Missing in current shared model** | none | Semtech quotes LoRa co-channel rejection; real capture depends on power/timing | add optional received-power model; keep disabled when powers are unknown |
| Preamble lock / late interferer timing | **Missing** | all overlap is treated equivalently | real outcome depends on desired/interferer arrival order and preamble/header lock | add together with capture model |
| Cross-SF interference | **Missing** | one global SF/BW per environment | LoRa SFs are not perfectly orthogonal | future multi-PHY/channel model |
| Multi-channel operation | **Missing** | one abstract shared channel | production MAC has region/channel tables | add channel id before multi-channel experiments |
| Path loss / received power | **Missing by default** | links provide range + independent packet loss, not dBm | RF link budget is deployment-specific | optional dBm/link-budget layer; do not invent path-loss coefficients by default |
| RSSI CCA schedule | **Exact timing, abstract energy** (Python backend) | 3 `GetRssiInst` reads with 10 ms gaps; RX stays armed | production `MAC::transmissionAuthorized()` + RadioLib 6.0 `getRSSI(false)` | `test_carrier_sense_timing.py`, `test_radio_timing.py` |
| RSSI CCA SPI time | **Exact RadioLib-6 wire sequence** | each sample = 8 SPI bytes at default 2 MHz; three-sample CCA = 20.096 ms | `SX126x::getRSSI(false)` + default `RADIOLIB_SPI_PARANOID` `SPIcheckStream()` | exact timing regression |
| RSSI threshold | **Intentional abstraction** | any reachable current RF energy is considered above CCA threshold | production compares instantaneous RSSI to calibrated noise floor + squelch | needs dBm model to reproduce threshold quantitatively |
| RX_DONE during RSSI CCA | **Production-faithful abstraction** | reachable frame completion during CCA aborts send even if energy falls between samples | production checks DIO1 wake flag between RSSI reads | dedicated carrier-sense regression |
| Carrier backoff | **Exact range/distribution** (Python backend) | independent keyed random 25-250 ms | production `25 + module.random(226)` | CCA tests; deterministic by environment seed |
| CAD default role | **Exact policy** | disabled by default; optional supplement to RSSI | production v2 MAC; hardware history; Semtech AN1200.48 | CAD is never authoritative CCA |
| CAD parameter defaults | **Exact for RadioLib 6.0** | SF9 uses 4 CAD symbols, DetPeak 23, RadioLib default DetMin | RadioLib 6.0 `SX126x::setCad()` citing AN1200.48 | source-pinned timing tests |
| CAD duration | **Bounded approximation** | four symbol intervals + ~0.5-symbol correlation/post-processing; 18.432 ms at SF9/BW125, plus 0.252 ms exact RadioLib SPI overhead | RadioLib defaults + Semtech CAD guidance/measurements | `test_radio_timing.py` |
| CAD detection probability | **Missing** | optional CAD contract can be forced detected/free; shared RF model has no sensitivity curve | AN1200.48 shows miss/false-detect probability depends on SF/BW/symbol count/threshold/power | add stochastic CAD only if CAD is used in production experiments |
| SPI clock (RadioLib path) | **Exact configured default** | 2 MHz assumed | RadioLib 6.0 `BuildOpt.h`; SX1262 permits up to 16 MHz SCK | timing test asserts 2 MHz <= 16 MHz |
| SPI BUSY discipline | **Exact in RadioLib source / contract-checked** | each stream command waits BUSY low before transfer; commands configured to wait also wait afterward | RadioLib 6.0 `Module::SPItransferStream`; SX1262 datasheet BUSY requirement | contract shim + source audit |
| SPI transfer duration | **Exact wire time for modeled RadioLib-6 paths** | `8 * physical_bytes / 2 MHz`; byte counts include command/status/data and default paranoid verification | RadioLib 6.0 `SX126x.cpp`, `Module.cpp`, `BuildOpt.h` | byte-count constants pinned in `test_radio_timing.py` |
| `MAC::sendData` clear CCA -> RF start | **Exact command sequence + bounded chip transition** | 87 fixed RadioLib/MAC SPI bytes + frame bytes + documented typical 126 us STBY_RC->TX | production MAC + RadioLib 6.0 `startTransmit()` + SX1262 switching table | 20-byte=0.554 ms setup, 255-byte=1.494 ms setup after CCA |
| RX_DONE -> LCMM callback | **Exact command sequence / bounded MCU overhead** | 61 fixed SPI bytes + frame bytes | production `MAC::loop/handlePacket` + RadioLib 6.0 `readData()` | 11-byte ACK=0.288 ms; 255-byte frame=1.264 ms |
| TX_DONE -> continuous RX | **Exact command sequence + bounded chip transition** | 67 SPI bytes + documented typical 83 us STBY_RC->RX | `finishTransmit()` + production `startReceive()` | 0.351 ms at 2 MHz |
| RX callback return -> continuous RX | **Exact command sequence + bounded chip transition** | 56 SPI bytes + 83 us STBY_RC->RX | production MAC re-arms RX when callback did not start TX | 0.307 ms; `test_receive_rearm_timing.py` |
| warm sleep -> STBY_RC | **Bounded approximation** | 340 us in contract shim | SX1261/2 switching-time table | only relevant when sleep is used |
| Link ACK RF size | **Exact for current packed structs** | 8-byte MAC header + 3-byte LCMM ACK = 11 radio bytes | `MACHeader`, `LCMMPacketResponse` + packet id | timing/airtime test |
| Reliable DATA receive ordering | **Exact target ordering in Python timing overlay** | RX frame -> SPI read -> ACK CCA/setup -> ACK RF -> TX/RX re-arm -> DTPK callback | current LCMM `afterCallbackSent` | ACK/DTPK timeline regressions |
| ACK reception ordering | **Bounded hardware-faithful timing** | ACK RF end -> optional propagation -> ACK buffer SPI read -> LCMM completion -> RX re-arm | RadioLib receive + LCMM `handleACK` + production MAC loop | timing overlay + re-arm regression |
| Link retries | **Protocol-exact, radio timing bounded** | failed/lost ACK retries; CCA denial does not consume RF retry | current LCMM transient MAC handling | LCMM contract tests |
| CRC corruption | **Intentional abstraction** | packet loss/collision drops whole frame; no bit-error distribution | hardware CRC rejects corrupted packet | suitable for routing reliability, not BER/PER studies |
| Frequency error / oscillator drift | **Missing** | none | production cumulative auto-correction intentionally removed; real crystals/TCXO still have ppm error | add only for RF-margin/frequency-offset studies |
| Noise-floor calibration | **Partial abstraction** | production C++ contract exercises algorithm; shared RF model does not synthesize analog RSSI calibration | production MAC samples/sorts RSSI | dBm layer prerequisite |
| IRQ latching / event classification | **Strong contract-level model** | production MAC contract uses SX126x IRQ bits rather than mutable software state | RadioLib 6.x `getIrqStatus()` + SX126x IRQ API | TX_DONE/RX_DONE/CAD/error contract cases |
| MCU time (`millis`) across reboot | **Exact adapter invariant** | each C++ node gets local time from boot epoch; environment time remains global | Arduino semantics | C++ environment tests |
| C++ subprocess tick quantization | **Intentional abstraction** | default adapter tick can be 50 ms; radio callbacks are serviced as events | real firmware loop frequency is board/load dependent | use <=5-10 ms for latency/timer validation; 50 ms only for long scale studies |
| C++ clear-channel send/receive timing | **Bounded hardware-faithful overlay** | synthetic clear RSSI CCA + exact RadioLib SPI setup/read/re-arm timing around real C++ DTPK/LCMM | `timed_cpp_backend.py` | scheduler + compiled-host smoke tests |
| C++ busy-CCA semantics | **Missing** | synchronous fake `sendData()` cannot remain interruptible in RX for 20 ms and then return carrier-busy to LCMM | real production MAC blocks through CCA and may latch DIO1 before returning | needs coroutine/bidirectional CCA handshake; do not use C++ backend as dense MAC-capacity oracle |
| Strict regulatory duty policy | **Optional experiment** | separate per-node airtime spacing; survives simulated reboot | regulatory experiment, not default MAC behavior | `strict_duty_cycle=True` only |

## CAD conclusion

CAD must **not** be treated as a reliable universal carrier detector for DTProtocol.

Semtech AN1200.48 makes the trade-off explicit: CAD sensitivity and false detections
vary with SF, bandwidth, number of CAD symbols, `cadDetPeak`, `cadDetMin`, and received
power. CAD correlates LoRa modulation rather than measuring arbitrary channel energy,
and each CAD operation takes the radio out of normal continuous-RX handling.

Therefore the v2 MAC policy is:

1. remain in continuous RX;
2. use instantaneous RSSI samples as the default CCA;
3. if an RX interrupt arrives during CCA, defer TX and service it;
4. randomized 25-250 ms backoff when busy;
5. CAD may be enabled as an **additional** weak-LoRa check, never as the only test.

This preserves the reason the original hardware implementation moved back to RSSI,
while still allowing SX1262 CAD where measured deployment data shows it helps.

## Mobile-hint conclusion

A semantic `mobile` routing label is not needed for correctness and should probably
not become part of the wire protocol. With independent 10 s HELLO phases on both
ends of a newly formed link, discovery time is `min(U(0,10), U(0,10))`: mean about
3.33 s and 96% probability of discovery within 8 s. Giving one endpoint a 4 s HELLO
clock reduces mean discovery to about 1.73 s and guarantees a beacon opportunity
within 4 s, which is useful mainly for **very short contact windows** (drive-by nodes,
small high-speed RF cells, intermittent contacts).

The safer API is therefore a **local HELLO-period / fast-discovery override**, not a
routing identity. A wrong setting then only trades airtime for discovery latency and
can never change feasibility, route validity, sequence handling or loop prevention.


### Stable-network liveness stress result

Large real-C++ line tests exposed a false-failure mode in the earlier v2 liveness policy. Two unanswered reliable CRYST probes were being treated as authoritative proof that a direct neighbor was gone. On a stable 64-node line this caused large indirect route withdrawals/relearn waves even though every one-hop route was still physically valid.

Isolation tests separated the two mechanisms:

- 120 s hard inactivity + **failed-probe eviction disabled**: 64 nodes stayed 0 missing / 0 wrong through 1500 s; 96 nodes reached 0 / 0 by 1200 s and stayed there through 2400 s.
- hard expiry effectively disabled + **two-failed-probe eviction enabled**: the 64-node collapse reproduced (376 missing routes at 1140 s, 626 at 1200 s).
- a 60 s hard timeout, even without failed-probe eviction, was still too aggressive at this scale.

Therefore probe success may refresh liveness, but probe failure is only suspicion. Current C++ v2 changes topology only after 120 s without any valid packet from that neighbor. Future faster failure response should be a local soft-suspect/data-path policy, not a network-wide route withdrawal inferred from a small number of missed RF exchanges.

## Large-network interpretation rules

- **Lines / sparse graphs without `radio_contention`:** suitable for routing-state,
  chunking, sequence/feasibility, convergence-diameter and memory studies.
- **Python backend with `radio_contention`:** suitable for current RSSI CCA/backoff
  and hidden-terminal experiments, but no-capture collision behavior is pessimistic.
- **Timed C++ backend without contention:** suitable for real C++ protocol behavior
  with hardware-informed clear-channel send/receive wall-clock timing.
- **Timed C++ backend with contention:** collision history is modeled but busy-CCA
  firmware return semantics are not; do not use it as a quantitative capacity oracle.
- **Dense capacity / PDR claims:** wait for received-power + capture/preamble-lock model.

## Hardware integration warning: Picopod

The current `Picopod` tree is not the same SX1262/RadioLib path. Its
`lib/lora/LoRa-RP2040.cpp` uses an SX127x-style register interface and currently calls:

```cpp
spi_init(SPI_PORT, 12500);
```

The Raspberry Pi Pico SDK documents the `spi_init()` baudrate parameter in **Hz**.
Therefore this requests approximately 12.5 kHz, not 12.5 MHz. If this driver is still
used on active hardware, treat that as a likely configuration bug and audit it
separately before transferring any SX1262/RadioLib timing conclusion to Picopod.
At 12.5 kHz, clocking 255 bytes alone takes about 163 ms.

## Primary references

- Semtech SX1261/SX1262 current product page and datasheet listing:
  https://www.semtech.com/products/wireless-rf/lora-connect/sx1262
- Semtech SX1261/SX1262 datasheet switching/SPI/BUSY requirements.
- Semtech AN1200.48, *LoRa Channel Activity Detection (CAD) with SX126x*.
- RadioLib tag 6.0.0: `SX126x.cpp`, `SX126x.h`, `Module.cpp`, `Module.h`, `BuildOpt.h`.
- Raspberry Pi Pico SDK `hardware_spi`: `spi_init(..., baudrate)` takes Hz.

Whenever these sources or the project's declared RadioLib major version change, rerun
this matrix before accepting new simulator benchmarks as hardware-realistic.
