# DTProtocol v4

DTProtocol is a small proactive multi-hop protocol for SX126x LoRa networks. It
keeps destination routes proactively, sends application traffic along the
selected path, retries every radio hop through LCMM, and optionally confirms
final delivery with a separate end-to-end ACK.

Version 4 is a **wire-incompatible** update. Packet types carry a v4 prefix so
v3 nodes drop compressed-capable frames instead of exposing encoded bytes to an
application. Upgrade one routing domain together.

## How it works

Each node periodically broadcasts a small HELLO containing its boot incarnation
and current route-state version. A neighbor that is missing that version sends a
reliable CRYST request. The response is a complete, chunked route snapshot that
is applied transactionally only after every chunk arrives.

A route candidate is usable only when it satisfies the destination-sequence
feasibility rule. Sequence numbers prove freshness and loop safety; among
feasible candidates the lowest metric wins. When every known candidate is
blocked, a directed generation request asks the destination to originate newer
state. Periodic flood fallback prevents stale local knowledge from trapping the
repair request.

Application DATA uses:

- per-hop LCMM ACK and up to five retries;
- a finite 255-hop bound;
- replay identity `{sender, boot incarnation, packet id}`;
- optional end-to-end ACK/NACK;
- selective-repair multipart transfer for payloads larger than one LoRa frame;
- transparent heatshrink compression only when the configured PHY predicts less
  reliable-link airtime after all headers and fragment boundaries.

## ESP32 quick start

Initialize the RadioLib `SX1262` object first, then MAC and DTPK:

```cpp
if (!MAC::initialize(
        radio,
        nodeId,
        MACRegion::EU868,
        0,       // channel
        9,       // spreading factor
        125.0f,  // bandwidth kHz
        15,      // squelch margin
        13,      // conducted power dBm
        7))      // coding-rate denominator
    handleFatalRadioError();

if (!DTPK::initialize(
        20,
        persistentBootSequence,
        isMobileDevice))
    handleFatalProtocolError();
```

`persistentBootSequence` must be nonzero and advance after every real reboot.
The Picopod ESP32 targets store it in `Preferences`. This prevents a rebooted
sender from colliding with old replay-cache entries or delayed ACKs.

Call exactly one loop owner:

```cpp
// Without BLE
DTPK::getInstance()->loop();

// With the supplied ESP32 BLE gateway; this already calls DTPK::loop()
Bluetooth::getInstance()->loop();
```

A mobile node advertises every second while isolated, then relaxes to four
seconds after contact. Static nodes use ten seconds. The mobility hint affects
only local discovery cadence, never route validity or metric.

## Automatic compression

Application code still sends and receives ordinary bytes. For payloads of at
least 32 bytes, DTProtocol may encode them with heatshrink, locally verify the
round trip, and compare complete reliable-link airtime against the original.
Byte savings that do not remove any LoRa symbols are rejected. See
[COMPRESSION.md](COMPRESSION.md) for the wire envelope, memory bounds,
configuration and diagnostics.

## Bluetooth gateway

The optional ESP32 gateway exposes outbound messages, inbound messages,
end-to-end results and route updates over GATT. It supports prepared long writes
and fragments large inbound notifications without silent truncation.

See [BLUETOOTH_PROTOCOL.md](BLUETOOTH_PROTOCOL.md) and the included laptop client:

```bash
python -m pip install bleak
python tools/dtpk_ble_client.py --name DTPK-LoraWatch --listen
```

## Host validation

```bash
python -m venv .venv
.venv/bin/pip install pytest -r simulator/requirements-optimization.txt

cmake -S simulator/cpp -B simulator/cpp/build -DCMAKE_BUILD_TYPE=RelWithDebInfo
cmake --build simulator/cpp/build --parallel
ctest --test-dir simulator/cpp/build --output-on-failure

DTP_CPP_NODE="$PWD/simulator/cpp/build/dtprotocol_host_node" \
PYTHONPATH=simulator .venv/bin/python -m pytest simulator/tests -q
```

The same scenarios can run against the fast Python state machine or the actual
production C++ DTPK/LCMM code. The shared environment owns airtime, continuous
motion, failures, duty limits, CCA, collisions and loss.

Important simulator documents:

- [architecture and pruning review](simulator/PROTOCOL_ARCHITECTURE.md)
- [accuracy and validation boundary](simulator/SIMULATOR_VALIDATION.md)
- [bounded ILP optimization oracle](simulator/OPTIMIZATION_ORACLE.md)
- [bounded protocol-synthesis direction](simulator/PROTOCOL_SYNTHESIS.md)

## RF-model boundary

The simulator now supports separate decode/interference/CCA ranges and optional
Gilbert-Elliott burst fading. It still does not claim calibrated received power,
capture/preamble lock, mixed SF/channel behavior, or exact asynchronous C++ CCA
contention. See the validation document before using it for capacity claims.
