# DTProtocol v3 Bluetooth gateway

The ESP32 BLE gateway exposes DTProtocol application messages and route updates
to a phone without exposing MAC or LCMM framing.

## GATT service

| Purpose | UUID | Properties |
|---|---|---|
| Service | `4fafc201-1fb5-459e-8fcc-c5c9c331914b` | primary service |
| Messages | `beb5483e-36e1-4688-b7f5-ea07361b26a8` | read, write, write-without-response, notify |
| Route count | `beb5483e-36e1-4688-b7f5-ea07361b26a9` | read, notify; little-endian `uint16_t` |

Both notifying characteristics include a Client Characteristic Configuration
Descriptor (`0x2902`). The server requests ATT MTU 247 but sizes every outgoing
frame from the peer MTU actually recorded for the connection. Until negotiation,
it uses the Bluetooth default MTU 23 and therefore a 20-byte notification value.

All multi-byte integers below are little-endian. Every message starts with:

```text
u8  type
u16 message_id
u16 total_frame_length   # includes this five-byte header
```

The receiver must reject a frame whose encoded length differs from the actual
GATT value length.

## Phone to LoRa: type `0x01`

```text
header
u16 recipient_node_id    # zero is invalid/reserved for DTPK broadcast
u8  payload[]
```

The phone should use a normal GATT write for a short value and a prepared/long
write for a value larger than the negotiated ATT write payload. The ESP32 BLE
stack commits the complete long value before invoking the characteristic write
callback. DTProtocol sends a short payload in one LoRa packet and automatically
uses selective-repair multipart transfer for a larger payload.

The maximum accepted payload is `DTPK::maximumMessageSize()` (16 KiB by
default, configurable at compile time). BLE callbacks only copy validated-size
commands into a bounded queue. `Bluetooth::loop()` is the sole owner that calls
DTProtocol, so the Bluetooth host task cannot mutate the radio/protocol queues
concurrently.

## Delivery result: type `0x02`

```text
header
u16 original_phone_message_id
u8  success              # 0 or 1
u16 round_trip_ms
```

This is a DTProtocol end-to-end result, not merely a one-hop radio ACK.

## LoRa to phone, short: type `0x03`

```text
header
u16 sender_node_id
u8  payload[]
```

This form is used when the complete payload fits in one notification at the
currently negotiated ATT MTU.

## LoRa to phone, fragmented: type `0x05`

```text
header                     # same message_id on every fragment
u16 sender_node_id
u16 complete_payload_length
u16 payload_offset
u8  payload_chunk[]
```

Chunks are generated just before transmission using `peer_mtu - 3`, not queued
as a burst sized for a hoped-for MTU. One notification is submitted every 30 ms
to avoid unbounded Bluedroid backlog. The client reassembles by
`(sender_node_id, message_id)` and places each chunk at `payload_offset`.

## Paged route list: type `0x04`

```text
header
u16 complete_route_count
u16 first_route_index
u8  included_count
repeat included_count times:
    u16 destination_node_id
    u16 hop_distance
```

Every page fits the actual peer MTU. At the default MTU 23, two routes fit per
page; at MTU 247, 58 fit. The separate route-count characteristic contains the
same complete count. A page beginning at index zero replaces the client's old
snapshot.

## Queue and connection behavior

- Callback-to-loop writes: two complete commands, plus eight negative-result IDs.
- Pending LoRa-to-phone messages: four logical messages. Payloads are fragmented
  lazily, so a 16 KiB message does not create thousands of tiny heap objects at
  MTU 23.
- Control notifications: 32; delivery ACKs have priority over route pages.
- Notifications remain queued until the client enables the CCCD.
- All pending traffic is discarded on disconnect, and advertising restarts after
  250 ms.
- `BluetoothDiagnostics` exposes dropped-write, inbound-message and control-frame
  counters for device diagnostics.

## Laptop smoke-test client

```bash
python -m pip install bleak
python tools/dtpk_ble_client.py --name DTPK-LoraWatch --listen
python tools/dtpk_ble_client.py \
  --name DTPK-StickLiteV3 --recipient 3 --text 'hello over LoRa'
```

The client validates frame lengths, reassembles application fragments and paged
route snapshots, and waits for the DTProtocol end-to-end result.
