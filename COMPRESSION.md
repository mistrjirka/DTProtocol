# DTProtocol v4 automatic message compression

DTProtocol v4 transparently compresses application payloads when doing so lowers
the actual LoRa airtime. The public `sendPacket()` API and the receive callback
still use the original uncompressed bytes.

## Decision rule

Compression is attempted for payloads of at least 32 bytes by default. A
candidate is selected only after all of these checks pass:

1. The encoded stream, including its three-byte compression envelope, is no
   larger than the original payload.
2. Decoding the candidate locally reproduces every original byte exactly.
3. At the MAC's configured spreading factor, bandwidth and coding rate, the
   complete reliable-link radio cost is strictly lower. The calculation includes
   DATA or fragment headers, fragment boundaries and one LCMM link ACK per frame.
4. The estimated saving is at least
   `DTPK_COMPRESSION_MIN_AIRTIME_SAVINGS_MS` (one millisecond by default).

This is deliberately stricter than comparing byte counts. LoRa airtime is
quantized into symbol groups, so a payload can become one byte smaller without
becoming any faster. In that case DTProtocol sends the original bytes.

The route length does not change the decision: every forwarded hop carries the
same representation, so a positive one-hop saving scales with every hop.

## Wire representation

Compression is a v4 wire feature. The high packet-type nibble is `0x4`, so v3
and v4 nodes ignore one another rather than allowing a v3 application to receive
compressed bytes accidentally. Upgrade a routing domain together.

A compressed logical payload begins with:

```text
original application size: u16 little-endian
codec:                     u8  (1 = heatshrink W8/L4)
encoded bytes:             remaining payload
```

The three-byte envelope is included in the benefit calculation. Compression can
turn a multipart message into an ordinary single-frame message. If the encoded
form still needs fragments, selective repair operates on those encoded bytes;
the destination decompresses only after the complete logical stream exists.

## Codec and safety

The codec is the statically allocated heatshrink v0.4.1 implementation with an
8-bit (256-byte) history window and 4-bit lookahead. DTProtocol uses approximately
1,554 bytes of static encoder workspace and 302 bytes of static decoder
workspace on the host ABI.

The source bounds the candidate buffer to the original message size and performs
a complete decode-and-compare before transmitting it. The destination validates
the codec, original size, exact decoded length and stream completion. A malformed
or unsupported stream is not delivered to the application and produces an
end-to-end NACK when one was requested.

Compression is not encryption and provides no authenticity or secrecy.

## Memory behavior

The source temporarily needs one candidate buffer no larger than the original
message. When selected for multipart transmission, DTProtocol asks the allocator to
shrink that same allocation to the encoded size and then retains it for
selective retransmission; it is not copied again. If a heap cannot shrink in
place, correctness is unchanged and only the reserved capacity remains larger.

A destination assembling compressed multipart data temporarily owns both the
encoded assembly and the decoded application buffer. With the default 16 KiB
logical-message limit and two assembly slots, the conservative application-data
peak is approximately 48 KiB, plus codec and container overhead.

## Configuration

Applications can override these before including DTProtocol headers:

```cpp
#define DTPK_ENABLE_COMPRESSION 1
#define DTPK_COMPRESSION_MIN_INPUT_SIZE 32u
#define DTPK_COMPRESSION_MIN_AIRTIME_SAVINGS_MS 1u
#include <DTPK.h>
```

Setting `DTPK_ENABLE_COMPRESSION` to zero disables creation of compressed
messages but keeps decoding enabled, so the node remains compatible with other
v4 senders.

Runtime counters are available through:

```cpp
const auto &stats = DTPK::getInstance()->getCompressionDiagnostics();
```

They distinguish attempted and selected candidates, candidates that could not
fit below the original size, candidates with no airtime benefit, codec or
self-verification failures, receive-side decode failures, allocation failures,
and cumulative estimated byte/airtime savings.

## Validation boundary

The byte-aware real-C++ simulator verifies automatic selection, exact binary
round trips, single-frame conversion, compressed multipart selective repair,
no-benefit fallback, incompressible fallback and malformed-stream rejection. The
abstract Python state machine knows only payload lengths, not their bytes or
entropy, so it intentionally does not predict compression ratios.
