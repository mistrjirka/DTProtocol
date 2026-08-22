# Simulator radio / hardware fidelity validation

This is the acceptance matrix for the shared Python/C++ DTProtocol simulator.
It deliberately separates **protocol correctness**, **documented digital timing**
and **analog RF realism**. A benchmark is not called hardware-realistic unless the
features that materially determine its result are covered below.

Reference configuration unless a scenario overrides it:

- SX1262
- LoRa SF9 / BW125 kHz / CR 4/7
- explicit header, payload CRC, 8-symbol preamble
- RadioLib 6.0 behavior (`library.json` declares `^6.0.0`)
- RadioLib ordinary-Arduino default SPI: 2 MHz, mode 0
- private LoRa sync word
- production MAC RSSI-first carrier sensing; CAD disabled by default
- no automatic 1%/10% duty throttle (strict duty mode is an explicit experiment)

## Fidelity labels

- **Exact** — same discrete rule/equation or exact SPI byte sequence as the reference.
- **Bounded approximation** — based on documented chip operations/typical values,
  but CPU/HAL/analog variability remains.
- **Intentional abstraction** — deliberately higher-level representation suitable
  only for the stated experiment class.
- **Missing** — not currently modeled and capable of biasing that experiment.

## Digital / timing validation

| Area | Fidelity | Current result | Evidence / caveat |
|---|---|---|---|
| LoRa symbol time | **Exact** | `2^SF / BW` | SX126x LoRa definition |
| LoRa frame airtime | **Exact for modeled packet mode** | RadioLib-equivalent equation, SF5/6 special term, auto-LDRO at symbol >=16 ms | parameterized SF5-12 / BW125-500 tests |
| Max radio frame | **Exact** | 255 bytes | SX126x buffer / RadioLib maximum |
| SPI mode / speed | **Exact configured default** | mode 0, 2 MHz | RadioLib 6.0 default; SX1262 permits up to 16 MHz |
| SPI BUSY discipline | **Exact in real RadioLib / contract checked** | RadioLib waits BUSY before stream operations and, when requested, after them | SX1262 requires BUSY low before a new host command |
| SPI wire duration | **Exact for audited RadioLib-6 paths** | `8 * bytes / 2 MHz` | byte/transaction counts pinned by tests |
| RSSI CCA | **Exact digital timing, abstract energy** (Python) | 3 `GetRssiInst` reads, 10 ms spacing; each read is 6 SPI bytes incl. paranoid verification | total documented/software floor = **20.078 ms** |
| Carrier backoff | **Exact range/distribution** (Python) | `25 + random(226)` => 25-250 ms | matches production MAC |
| Clear CCA -> TX RF start | **Exact command sequence + bounded chip timing** | 87 fixed SPI bytes + radio frame bytes + typical 126 us STBY_RC->TX | 20-byte frame **0.577 ms** setup; 255-byte **1.517 ms** |
| RX_DONE -> MAC/LCMM callback | **Exact command sequence + bounded CPU overhead** | 61 fixed SPI bytes + received frame | 11-byte ACK **0.307 ms**; 255-byte frame **1.283 ms** |
| TX_DONE -> continuous RX ready | **Exact command sequence + bounded chip timing** | MAC IRQ-status read + `finishTransmit()` + `startReceive()` = 74 SPI bytes; typical 83 us STBY_RC->RX | **0.398 ms** |
| RX callback return -> continuous RX | **Exact command sequence + bounded chip timing** | `startReceive()` = 56 SPI bytes + typical 83 us STBY_RC->RX | **0.320 ms** |
| warm sleep -> STBY_RC | **Bounded approximation** | 340 us typical | SX1261/2 switching table |
| firmware polling | **Intentional abstraction** | normal `Scenario.build("cpp")` defaults to **10 ms**; scale studies may explicitly use 50-100 ms | actual Pico main loop has no intentional sleep and spins continuously |
| MCU reboot clock | **Exact adapter invariant** | C++ `millis()` restarts from local boot epoch while RF world time remains global | Arduino semantics |

### What the chip documentation actually guarantees

The SX1261/2 datasheet states that SPI may run up to 16 MHz (minimum SCK period
62.5 ns). BUSY low means the internal state machine is ready for another command;
BUSY can also assert while internal IRQ work is being handled, so the host must wait
for BUSY low before a read **or** write. RadioLib 6.0 does this in its Module SPI
path. Therefore DTProtocol must not add guessed fixed delays between ordinary
RadioLib commands; the driver/BUSY pin is the authority.

Published typical active transition times include:

- warm SLEEP -> STBY_RC: 340 us
- STBY_RC -> RX: 83 us
- STBY_RC -> TX: 126 us
- RX -> FS: 15 us
- RX -> TX: 92 us

The datasheet does not give a deterministic processing duration for every register
write/configuration command. The simulator therefore models exact SPI wire time,
RadioLib's deterministic 1 us BUSY-poll floor and documented mode-transition typical
values; it does **not** invent a fake fixed BUSY duration for every command.

## Send / receive event ordering

The timed Python backend models the production ordering rather than collapsing RF
completion into an application callback:

1. RSSI CCA completes;
2. RadioLib/SX1262 TX setup completes;
3. RF frame occupies the channel for LoRa time-on-air;
4. receiver performs RadioLib RX-buffer/IRQ SPI operations;
5. a reliable DATA receiver performs its own CCA + ACK setup;
6. ACK occupies the RF channel;
7. original DATA sender reads/processes the ACK;
8. ACK transmitter separately completes TX_DONE and re-arms RX before delivering
   the deferred DTPK DATA callback.

This ordering is pinned by `test_carrier_sense_timing.py`,
`test_receive_rearm_timing.py` and `test_radio_timing.py`.

The timed C++ backend uses the real C++ DTPK/LCMM subprocess and overlays the same
clear-channel CCA/setup/airtime/read/re-arm intervals. It is suitable for protocol
correctness plus hardware-informed **clear-channel** wall-clock timing.

## CAD audit

CAD is **not** the default carrier-sense mechanism.

The SX1261/2 datasheet describes CAD as a LoRa correlator: it searches for LoRa
symbols for 1/2/4/8/16 selected symbols and then spends roughly another half-symbol
in RX for post-processing. RadioLib 6.0 selects AN1200.48-derived parameters; for
SF9/BW125 it uses 4 symbols with DetPeak 23 and DetMin 10.

The generic datasheet timing model therefore gives:

- SF9/BW125 symbol = 4.096 ms
- 4 + 0.5 symbols = **18.432 ms** CAD correlation interval
- audited RadioLib synchronous scan SPI floor = **0.270 ms**

Semtech AN1200.48 measured the actual SF9/BW125 4-symbol CAD consumption interval at
about **19.145 ms (4.67 symbols)**. That lab result is deliberately recorded as a
measured reference rather than replacing the generic equation for every SF/BW.

More importantly, AN1200.48 shows that detection and false-positive behavior depend
on SF, BW, symbol count, DetPeak/DetMin and received power. For SF9/BW125 it recommends
4 symbols / DetPeak 23 / DetMin 10. Near sensitivity, CAD is probabilistic rather
than a perfect busy/free oracle.

Production-v2 policy is therefore:

1. stay in continuous RX;
2. take three instantaneous RSSI samples as default CCA;
3. if an RX interrupt arrives while sampling, defer TX and service RX;
4. use randomized 25-250 ms backoff on busy;
5. optionally use CAD as an *additional* weak-LoRa check, never as the only carrier
   detector.

This matches the historical hardware observation that CAD alone was unreliable while
avoiding the old bug where CAD repeatedly forced RX -> standby/CAD -> RX and could
lose a packet that began during carrier sensing.

## RF / medium validation

| Area | Fidelity | Current model | Interpretation |
|---|---|---|---|
| propagation delay | **Intentional abstraction** | 0 by default; configurable synthetic latency | physical ~3.3 us/km is negligible beside LoRa airtime in local meshes |
| continuous motion during frame | **Exact for piecewise-linear trajectories** | analytic whole-frame range check | not timestep sampled |
| link/node failure ordering | **Exact simulator invariant** | environment event wins at equal timestamp over PHY/protocol | deterministic replay |
| half-duplex | **Conservative abstraction** | a radio not RX-ready cannot receive | correct direction, omits analog acquisition details |
| hidden terminals | **Modeled topologically** | decode, interference and CCA reach are independently configurable | represents audible-undecodable and hidden-interferer cases |
| temporal fading | **Optional bounded model** | independent loss or directional/ACK-specific Gilbert-Elliott states | supports reproducible loss bursts without claiming a deployment fit |
| RSSI threshold in dBm | **Missing** | CCA reach is an explicit binary threshold region | requires received-power/noise-floor calibration for dBm claims |
| capture effect | **Missing** | any in-range interfering overlap corrupts | pessimistic; dense capacity is underestimated |
| preamble lock / late interferer | **Missing** | all interfering overlap is treated equivalently | needed with a received-power capture model |
| cross-SF interference | **Missing** | one global SF/BW | no quantitative mixed-SF claims |
| multiple RF channels | **Missing** | one shared abstract channel | add before channel-allocation studies |
| path loss / SNR / sensitivity | **Missing by default** | explicit decode/interference/CCA ranges + selectable loss process | deployment-specific; do not invent coefficients |
| CRC/BER | **Intentional abstraction** | bad frame is dropped as a whole | routing reliability only, not BER studies |
| oscillator/frequency error | **Missing** | none | cumulative auto-correction was intentionally removed from production |

Because capture and dBm thresholds are missing, a dense-contention run remains a
**conservative qualitative stress test**, not a calibrated prediction of packets/s or
maximum network density. The new three-range model is nevertheless stricter than a
single connectivity radius: a transmitter may be undecodable, still interfere, and
remain below another node's CCA reach.

Loss may be independent or use an explicit two-state Gilbert-Elliott process per
direction and separately for DATA/ACK sampling. The parameters are scenario inputs;
the simulator does not pretend that one default transition matrix represents a real
field deployment.

A realistic next RF layer should remain optional and explicit: per-direction received
power (or a user-selected path-loss model), receiver noise floor/sensitivity, CCA
threshold, and a configurable capture/preamble-lock rule. The deterministic range
abstraction remains useful when deployment RF parameters are unknown.

## C++ busy-CCA accuracy boundary

There is one important asymmetry between backends.

The Python timed backend can remain in RX during the full ~20.078 ms CCA window and
observe energy/RX completion at each sample time. The current C++ host fake MAC is a
synchronous call: after it returns `sendData()==OK`, the subprocess already believes
it is transmitting. The timed overlay can delay **clear-channel** RF start correctly,
but cannot make that already-running C++ call remain interruptible in RX and later
return `MAC_SEND_CHANNEL_BUSY_TIMEOUT` when another transmitter appears during CCA.

Therefore:

- **C++ without contention:** appropriate for real protocol behavior and bounded
  hardware timing.
- **C++ with contention:** useful for collision-history experiments but **not** a
  quantitative MAC-capacity oracle.
- **Python with contention:** current reference for production-like RSSI CCA/backoff,
  still pessimistic because capture/dBm are missing.

Making busy CCA exact in the C++ subprocess requires a coroutine/bidirectional host
handshake that pauses one firmware call while the global RF event queue advances. Do
not hide this limitation by delaying a C++ TX after the fake MAC has already changed
its state to SENDING; that would look precise while being semantically wrong.

## Routing/liveness findings from larger networks

Large real-C++ line tests exposed a false-failure mode in the earlier 30/60 s policy.
Several unanswered control packets in a half-duplex busy network are **not proof that
a neighbor disappeared**. Deleting a direct neighbor then deletes all its indirect
contribution; a subsequent HELLO restores only the direct route until a full CRYST is
reacquired, causing a withdrawal/relearn wave.

Current v3 behavior treats probe failure as suspicion only. Valid traffic/probe success
refreshes liveness; authoritative topology removal uses a much longer hard inactivity
bound (currently 120 s). In earlier controlled scale runs this stabilized 64 nodes and
allowed a 96-node line to reach exact routing by 1200 s and remain correct through
2400 s. These results should be rerun with the current head using:

```bash
PYTHONPATH=simulator python simulator/scale_validation.py --backend python
PYTHONPATH=simulator python simulator/scale_validation.py --backend cpp --tick-ms 50
```

The line validator checks every expected route, so it separates slow diameter-driven
convergence from wrong routing or chunk-reassembly failure. Use `--tick-ms 10` when
latency/timer fidelity matters; the 50 ms example above is explicitly a large-scale
runtime tradeoff.

## Mobile-hint conclusion

A semantic `mobile` identity is not needed for correctness and should not enter the
wire protocol. With independent 10 s HELLO phases on both sides of a new link,
`min(U(0,10), U(0,10))` has a mean discovery time of ~3.33 s and 96% probability of a
beacon opportunity within 8 s. Giving one endpoint a 4 s HELLO clock lowers the mean
to ~1.73 s and guarantees an opportunity within ~4 s before contention/jitter.

That matters mainly for short contact windows (drive-by nodes, small fast-moving RF
cells, intermittent contacts). The safer API is a **local HELLO-period / fast-discovery
override**, not a routing label. A wrong setting then changes airtime only; it cannot
alter feasibility, route validity or loop prevention.

## Hardware integration warning: current Picopod tree

`Picopod/lib/lora/LoRa-RP2040.cpp` is an older SX127x-style register driver, not this
SX1262/RadioLib path. It currently calls:

```cpp
spi_init(SPI_PORT, 12500);
```

The Raspberry Pi Pico SDK defines the second argument in **Hz**, so this requests
approximately **12.5 kHz**, not 12.5 MHz. If this exact driver is still deployed,
treat it as a likely hardware configuration bug and audit/fix it separately. At
12.5 kHz, merely clocking 255 bytes takes ~163 ms.

## Primary references

- Semtech SX1261/SX1262 datasheet, DS.SX1261-2.W.APP, SPI/BUSY/CAD and switching-time sections.
- Semtech AN1200.48, *SX126x CAD Performance Evaluation*.
- RadioLib tag 6.0.0: `SX126x.cpp`, `SX126x.h`, `Module.cpp`, `Module.h`, `BuildOpt.h`.
- Raspberry Pi Pico SDK `hardware_spi`: `spi_init(..., baudrate)` specifies baudrate in Hz.

Re-run this matrix whenever the declared RadioLib major version, PHY settings or radio
hardware changes.
