# Radio / 868 MHz audit

> **Historical design research.** This file explains the radio decisions that led
> to the current regional profiles and MAC implementation. Sections describing
> “current” anti-patterns refer to the earlier firmware unless explicitly marked
> otherwise. The active configuration/API is documented in the repository
> `README.md` and verified by `simulator/cpp/mac_contract_tests.cpp`.


## Recommended regional abstraction

The MAC should not contain one hard-coded channel array. Introduce a `RadioRegionProfile` containing at least:

- legal channel centres / usable band;
- bandwidth and modulation defaults;
- maximum permitted **e.r.p.** (not merely chip output power);
- per-sub-band duty-cycle budget;
- whether standards-compliant spectrum-access/LBT is enabled;
- antenna/cable gain budget;
- retry/backoff parameters.

A conservative Czech/EU 868 profile can start with three 125 kHz channels centred at **868.1, 868.3 and 868.5 MHz**, maximum 25 mW e.r.p., and a **1% duty-cycle token bucket**. These centres are also conventional LoRaWAN EU868 channels, although DTProtocol remains a private LoRa network because it uses its own framing/sync word.

Do not assume `14 dBm` transmitter setting automatically equals legal 25 mW e.r.p. Antenna gain and cable loss matter. A conservative default such as 13 dBm is preferable unless the product RF path is characterized.

The Czech VO-R/10/05.2025-5 permits alternative spectrum-access/interference-mitigation techniques instead of the stated duty cycle when they provide at least equivalent effect to the harmonized standards. Therefore an ad-hoc RSSI check must **not** be treated as permission to ignore the 1% budget. Keep duty-cycle enforcement as the safe default; standards-informed LBT can be an optional later mode.

Sources:
- Czech Telecommunications Office, VO-R/10/05.2025-5: https://ctu.gov.cz/sites/default/files/obsah/ctu-new/WEB_EN/VO-R10-05.2025-5_EN_final.pdf
- RadioLib SX1262 API: https://jgromes.github.io/RadioLib/class_s_x1262.html

## Radio compatibility

### SX1262

Good fit for both 433 and 868 MHz. RadioLib supports 150–960 MHz. The **module RF matching network and antenna still have to support the selected band**; a 433 MHz module/antenna should not be assumed to work well at 868 MHz simply because the silicon tunes there.

### SX1276 / common 868/915 RFM9x variants

Good candidate. RadioLib allows SX1276 operation in 862–1020 MHz and also the 433 MHz range. The current code cannot use it because `MAC` stores a concrete `SX1262&`. Introduce a radio adapter/interface rather than adding `#ifdef SX1276` throughout MAC.

### SX1278

Not an 868 MHz replacement. Its normal RadioLib frequency ranges stop around 525 MHz.

### SX1268

Not an 868 MHz replacement under its normal supported range (410–810 MHz).

### LLCC68

RadioLib derives it from SX1262 and it is broadly suitable for 868 MHz, with narrower supported LoRa parameter choices (notably 125/250/500 kHz BW and SF constraints).

### LR11xx

Can be supported later through the same adapter abstraction. Do not make DTPK depend on radio-family details.

## Recommended radio API boundary

DTPK and LCMM should know nothing about RadioLib. MAC should depend on an interface roughly like:

```cpp
struct RadioDriver {
    virtual RadioResult configure(const RadioConfig&) = 0;
    virtual RadioResult startReceive() = 0;
    virtual RadioResult startTransmit(std::span<const uint8_t>) = 0;
    virtual RadioResult readReceived(std::span<uint8_t>, size_t&) = 0;
    virtual RadioResult setFrequency(float mhz) = 0;
    virtual ChannelState channelActivity() = 0;
    virtual RadioStats lastPacketStats() = 0;
};
```

Use small adapters for SX1262, SX1276, etc. Capabilities such as frequency-error measurement should be optional.

## Current LoRa / radio anti-patterns

### 1. Output power is not region-aware

The watch branch initializes MAC with **22 dBm**. That is far above the normal Czech non-specific 433 MHz limit (10 mW e.r.p.) and above the simple 868.0–868.6 MHz profile (25 mW e.r.p.). Software should reject a profile whose RF budget exceeds the regional limit.

### 2. Current 433 MHz channel table touches/exceeds band edges

With 125 kHz LoRa bandwidth, a centre exactly at 433.05 MHz extends below the 433.05 MHz band edge, and the last configured centre is 434.8 MHz while the general-authorization band ends at 434.79 MHz. Channel definitions must account for **occupied bandwidth**, not just centre frequency.

### 3. RSSI-only LBT is a weak LoRa carrier-sense mechanism

LoRa can decode signals below the instantaneous noise floor, so an RSSI threshold can miss a LoRa transmission. SX126x has CAD specifically for LoRa activity; RadioLib's `scanChannel`/`startChannelScan` uses CAD defaults based on Semtech AN1200.48.

Use CAD plus an energy check where appropriate, then randomized backoff. Hidden terminals still exist, so ACK failure should also cause randomized backoff rather than synchronized immediate retry.

### 4. Squelch is applied twice

Noise calibration stores `average + squelch`, while `transmissionAuthorized()` compares RSSI against `noiseFloor[channel] + squelch` again. This makes carrier sense substantially too permissive.

### 5. Carrier sensing blocks the entire protocol loop

`waitForTransmissionAuthorization()` loops with `delay()`. During a long busy channel, routing timers, RX processing and higher-level scheduling stop. Make CAD/LBT an asynchronous MAC state.

### 6. Cumulative automatic frequency correction is risky

Before sending, MAC reads `getFrequencyError()` from the most recently received packet and subtracts it from `calibratedFrequency`. RadioLib explicitly warns that SX126x frequency-error reading is based on undocumented behavior. Repeatedly applying one peer's measured offset can walk the local TX frequency.

If correction is retained, filter measurements, associate them with a peer/channel, bound total correction, and never use an old measurement repeatedly. Prefer normal crystal/TCXO tolerance unless testing demonstrates a real need.

### 7. Radio configuration errors are mostly logged and ignored

`check()` prints RadioLib errors but initialization continues. Invalid BW/frequency/power can therefore leave the protocol running on a partially configured radio. Initialization should return an error and DTPK should not start.

### 8. Default-bandwidth typo

`MAC::initialize()` declares `default_bandwidth = DEFAULT_SPREADING_FACTOR` instead of `DEFAULT_BANDWIDTH`. Calls that omit bandwidth can request approximately 9 kHz instead of 125 kHz.

### 9. Static PHY settings for every link

SF9/BW125/CR4/7 is a reasonable baseline but not universally optimal. Later, link-quality adaptation can improve airtime dramatically. Do this only after routing correctness; route generation must not depend on nodes agreeing that a peer is 'mobile'.

### 10. Full-table CRYST packets are expensive on LoRa

At SF9/BW125/CR4/7, a near-255-byte frame is roughly 1.7 s on air. In a 1%-duty-cycle sub-band, control-plane design must aggressively avoid full-table periodic broadcasts. Triggered deltas plus infrequent repair summaries are much better.

## Mobile-node hint

A `mobile` label is acceptable only as an optimization hint. For example it may:

- shorten that node's HELLO interval;
- shorten expiry of routes learned directly from it;
- increase triggered-update priority;
- avoid using a highly mobile node as transit when an equally good stable route exists.

Correctness must remain identical if every label is wrong. Never use the label to change loop-prevention, sequence-number or liveness invariants.
