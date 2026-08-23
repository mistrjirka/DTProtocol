# Embedded C++ audit

> **Historical pre-v3 audit.** This document records defects found in the old
> implementation and is retained for provenance. Many listed issues—including
> forwarding size/ownership, NACK semantics, hop limits, wrap-safe timers, radio
> error propagation and ISR classification—are fixed in DTProtocol v3 and covered
> by current host, simulator and sanitizer tests. Do not treat the numbered list
> below as the current defect state; use the repository `README.md`,
> `PROTOCOL_ARCHITECTURE.md`, and the test suite for current behavior.


This is a defect/maintainability audit of the current reference stack, separate from crystallization's algorithmic correctness.

## Critical / likely reliability bugs

1. **Use-after-free in LCMM no-ACK send.** `sendPacketSingle()` frees its packet in the no-ACK branch and then returns `packet->id`.
2. **LCMM marks no-ACK send idle before PHY TX completes.** It clears `sending` immediately after `MAC::sendData()`. A second packet can enter MAC while the radio is still `SENDING`; current MAC silently ignores such a call and still returns success.
3. **MAC busy send reports success.** `MAC::sendData()` returns 0 when already transmitting instead of a busy/error result.
4. **Null callback call.** `DTPK::sendPacket()` calls `callback(0, 0)` when no route exists even though callback defaults to `nullptr`.
5. **Null route dereference in ACK debug path.** `sendAckPacket()` keeps `whereToSend = from`, but later logs `routing->originalRouter` even when route lookup returned null.
6. **Forwarding-size corruption.** DTPK allocates `size - sizeof(LCMMDataHeader)` for a relayed packet but queues it using the original `size`, causing an out-of-bounds read and packet-size growth.
7. **NACK is treated as successful end-to-end ACK.** `NACK_NOTFOUND` sets `gotAck=true` and `success=true`; timeout processing ignores the semantic distinction.

## Memory ownership / fragmentation

8. Received DTPK packets accepted locally are not clearly freed after parsing. The ownership contract between MAC -> LCMM -> DTPK -> application is implicit.
9. LCMM allocates an ACK response in `handleDataACK()` without an obvious matching free after MAC copies it.
10. DTPK timeout cleanup erases queued requests via `remove_if` without first freeing each request's `packet` allocation.
11. Frequent `malloc/free`, `std::vector` erase/insert, `unordered_map`, `unordered_multimap`, and queued heap allocations create fragmentation and non-deterministic latency on small MCUs.
12. Erasing from vectors while iterating by index can skip the item shifted into the erased position.

Preferred direction: fixed-capacity queues/pools for packet buffers and routes, RAII ownership wrappers on host/large MCUs, and explicit ownership at every callback boundary.

## Wire-format portability

13. Flexible array members (`unsigned char data[]`) are a GCC extension in C++, not standard C++.
14. Raw packet bytes are cast directly to packed C++ structs. This assumes compiler layout, endianness and tolerance for unaligned multi-byte accesses.
15. Wire fields should be fixed-width integers serialized explicitly. Keep enums out of the wire ABI unless encoded/decoded as an explicit integer.
16. LCMM/MAC size arguments are `uint8_t`; DTPK frequently starts with `size_t`. Oversize values can narrow/wrap before a lower layer rejects them.
17. Route distance is `uint8_t`; current count-to-infinity can wrap 255 -> 0. V2 should use saturating metric/infinity even if the on-wire field remains one byte.

## Time / state-machine issues

18. DTPK stores `millis()` in 64-bit variables and subtracts them as normal integers. Arduino `millis()` wraps at 32 bits; after wrap, this can become a huge underflow rather than normal modular elapsed-time arithmetic.
19. MAC carrier sensing is a blocking loop with `delay()`, stopping all other cooperative protocol work.
20. DTPK state is spread across `_waitingForAck`, `_currentlySendingId`, packet vectors and timer flags. LCMM similarly has several global booleans. Explicit state enums make illegal combinations harder to represent.
21. The whole stack is singleton/global. This makes multi-radio systems, unit tests, dependency injection and deterministic reset much harder. The host simulator therefore needs one process per node.

## ISR / radio-state issues

22. `operationDone` is modified from the radio IRQ callback but is not declared `volatile`/atomic. More importantly, one shared flag plus a separately read global state can lose distinctions between RX-done and TX-done events.
23. Use the radio IRQ status/event type as the source of truth rather than inferring which IRQ happened from mutable MAC state.
24. `getNoiseFloorOfChannel()` uses `channel > NUM_OF_CHANNELS`; `channel == NUM_OF_CHANNELS` is already out of bounds and should be rejected.
25. `LORANoiseFloorCalibrate()` has a `setMode(prev_state)` after `return`, so that restoration is unreachable when the function is called directly.
26. MAC stores bandwidth in an `int` despite accepting a `float`, unnecessarily ruling out valid RadioLib bandwidths such as 62.5 kHz.

## Error handling / API design

27. Radio initialization continues after configuration errors; errors need to propagate.
28. Methods frequently encode different failures as `0`, `false`, or a packet ID of zero. Use a small `enum class Error`/`Result<T>`-style API so busy, no-route, timeout, invalid-size and RF errors stay distinct.
29. Logging expressions are mixed into protocol logic and at least one malformed string expression performs pointer arithmetic when a boolean is added to a string literal. Compile production code with warnings enabled in CI.
30. Names such as `getNeighbours()` return all reachable routing destinations, not just direct neighbors, which obscures invariants and contributed to confusion about crystallization semantics.

## Suggested CI gates for v2

- host C++ tests with `-Wall -Wextra -Wconversion` where practical;
- ASan + UBSan host tests;
- Python/C++ differential scenario tests using identical environment seeds;
- randomized restart/loss/mobility tests;
- a packet codec round-trip test independent of host endianness;
- a compile job for at least ESP32 + RP2040 targets if both remain supported.
