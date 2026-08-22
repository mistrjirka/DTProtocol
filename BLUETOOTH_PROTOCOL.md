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
Descriptor (`0x2902`). The server requests MTU 247, giving a 244-byte ATT
notification payload when the client accepts that MTU.

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
default, configurable at compile time). Only one source-side multipart message
is active at once; a busy or invalid request returns a negative ACK.

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

This form is used when the complete payload fits in one ATT notification.

## LoRa to phone, fragmented: type `0x05`

```text
header                     # same message_id on every fragment
u16 sender_node_id
u16 complete_payload_length
u16 payload_offset
u8  payload_chunk[]
```

Fragments are queued and emitted one per firmware loop rather than submitted as
a burst to the BLE stack. The client reassembles by `(sender_node_id,
message_id)`, places each chunk at `payload_offset`, and completes when all bytes
from zero through `complete_payload_length - 1` are present.

## Route list: type `0x04`

```text
header
u8  included_count
repeat included_count times:
    u16 destination_node_id
    u16 hop_distance
```

The separate route-count characteristic contains the full current count. One
message notification contains at most 59 entries at MTU 247. The current gateway
sends the first 59 routes; a future paged route-list message should be added if a
phone must enumerate larger tables rather than only display the total count.

## Connection behavior

- The device advertises the service at boot and restarts advertising 250 ms
after a disconnect.
- Route updates are sent on connection, when requested by the application, and
periodically every five seconds while connected.
- Pending message notifications are discarded on disconnect so a later client
does not receive stale traffic.
- `Bluetooth::loop()` is the single owner that advances both BLE notifications
and `DTPK::loop()` on the ESP32 firmware targets.

## Laptop smoke-test client

Install Bleak and run the included client:

```bash
python -m pip install bleak
python tools/dtpk_ble_client.py --name DTPK-LoraWatch --listen
python tools/dtpk_ble_client.py \
  --name DTPK-StickLiteV3 --recipient 3 --text 'hello over LoRa'
```

The client subscribes to both characteristics, validates encoded frame lengths,
prints route updates and end-to-end delivery results, and reassembles type
`0x05` inbound fragments.
