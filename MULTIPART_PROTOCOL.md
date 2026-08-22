# DTProtocol v3 multipart application messages

DTProtocol automatically fragments an application payload when it no longer fits
in one 255-byte LoRa frame. The public `sendPacket()` API is unchanged.

## Capacity and wire overhead

At the current MAC/LCMM/DTPK sizes:

| Item | Bytes |
|---|---:|
| Maximum LoRa frame | 255 |
| MAC header | 8 |
| LCMM header | 3 |
| Ordinary DATA header | 11 |
| Multipart DATA header | 14 |
| Ordinary one-frame application payload | 233 |
| Multipart payload per full frame | 230 |

Only three bytes are added to each multipart DATA frame:

```text
total message size: u16
fragment index:     u8
```

The fragment count is derived from `ceil(total_size / 230)`, so it is not
repeated in every frame. A full fragment therefore carries 230 application
bytes and 25 bytes of total MAC + LCMM + DTPK overhead: **90.2% data-frame
efficiency**.

The wire format can describe 255 fragments, or 58,650 application bytes. The
default memory-safe API cap is 16 KiB and can be changed at compile time:

```cpp
#define DTPK_MAX_MESSAGE_SIZE (16u * 1024u)
#define DTPK_MAX_FRAGMENT_ASSEMBLIES 2u
#include <DTPK.h>
```

With the 16 KiB default, a message uses 72 fragments. Its entire missing-fragment
bitmap is nine bytes; the resulting status packet is only **34 radio bytes**
including MAC and LCMM.

## Reliability model

```text
source                     relays                       destination
  |-- fragment 0 -----------> ... ------------------------>|
  |-- fragment 1 -----------> ... ------------------------>|
  |                         ...                            |
  |-- final fragment -------> ... ------------------------>|
  |<--------- missing bitmap, only when incomplete --------|
  |-- only missing pieces --> ... ------------------------>|
  |<---------------- one whole-message ACK ----------------|
```

1. Every fragment uses the existing reliable LCMM transaction on every hop.
2. The destination stores fragments transactionally and does not call the
   application until all pieces are present.
3. If the final fragment arrives while earlier fragments are missing, the
   destination returns a compact missing bitmap.
4. The source retransmits only those indices, not the complete message.
5. If the final fragment, bitmap, or final ACK is lost, the source sends a small
   query. A completed destination re-ACKs; an incomplete destination returns its
   current bitmap; a rebooted destination reports all pieces missing.
6. Query rounds carry an ID, preventing LCMM duplicate delivery from causing
   duplicate message-level retransmissions.

The source query timer starts when its local LCMM fragment transaction finishes,
not when that fragment enters the queue. The delay scales with route distance and
includes both the remaining downstream fragment path and the returning ACK/status
path, so a repaired fragment receives a complete relay-attempt window before the
next query. The logical-message timeout is also raised automatically to cover the
initial stream plus one full selective-repair round.

## Memory bounds

Only one multipart source message is active per node. The source retains one
copy of its application payload so missing fragments can be recreated without
storing 72 separately allocated packets.

The destination has a fixed number of assembly slots. Each active slot owns one
contiguous application buffer plus a 32-byte receive bitmap. With defaults, the
worst-case application buffers are approximately:

```text
source:       16 KiB
receiver:  2 × 16 KiB
```

A full table evicts its oldest incomplete assembly. Incomplete assemblies also
expire after 120 seconds. Applications for smaller MCUs should lower either
compile-time limit.

## API

Existing code works without modification:

```cpp
DTPK::getInstance()->sendPacket(
    destination,
    bytes,
    byte_count,
    timeout_ms,
    true,
    [](uint8_t success, uint16_t elapsed_ms) {
        // Called once for the complete logical message.
    });
```

Useful compile-time/runtime-independent capacities:

```cpp
DTPK::maximumSinglePayloadSize(); // 233
DTPK::fragmentPayloadSize();      // 230
DTPK::maximumMessageSize();       // 16 KiB by default
```

Multipart messages always use an internal completion ACK/status exchange because
selective repair requires it. Passing `isAck=false` still suppresses the public
success callback, preserving the old observable API behavior.

The receive callback is invoked exactly once and receives a synthetic ordinary
`DTPKPacketGeneric` followed by the complete contiguous application payload.
The callback must consume or copy it before returning, as with the previous
single-frame callback.

## Measured regression scenarios

The real C++ host protocol tests include:

- exact 1,000-byte binary delivery with one application callback and one ACK;
- exact 1,800-byte delivery across two hops;
- all five LCMM attempts for one relay fragment forcibly erased;
- exactly one new message-level transmission for that missing index and none for
  the other seven fragments;
- the 233/234-byte single/multipart boundary;
- rejection above the configured 16 KiB memory cap;
- normal and ASan/UBSan execution.

The Python reference simulator mirrors the same frame sizes, source sequencing,
transactional reassembly, expiry, queries, status bitmaps, and selective repair.
