# Protocol v2 implementation order

`protocol-v2` is deliberately split into mechanical safety fixes and distributed-algorithm changes.  The simulator branch remains the reference/counterexample harness.

## Phase 1 — make the current data plane mechanically trustworthy

Before changing routing semantics:

1. fix forwarded packet size/ownership;
2. ACK/NACK/control packets bypass end-to-end data waiting;
3. relay hops use LCMM ACK/retry;
4. NACK reports failure, not success;
5. guard optional callbacks and null reverse routes;
6. add duplicate suppression / finite data hop limit;
7. saturate metrics instead of uint8 wrap;
8. fix timeout arithmetic and obvious packet lifetime leaks;
9. make host ASan/UBSan a hard gate once these are clean.

This produces a useful control implementation for comparing routing algorithms.

## Phase 2 — crystallization v2

Correctness must not depend on a `mobile` label.

- direct-neighbor HELLO/liveness independent from crystallization waves;
- event-triggered route-state propagation;
- per-destination sequence/generation numbers;
- feasibility condition for loop-free route acceptance;
- sequence-number request when feasibility blocks the only surviving longer route;
- route-state version/digest so a neighbor can request missed state;
- explicit infinity/saturation and packet hop limit;
- later: chunked/delta CRYST so control payload is not capped by one full vector.

## Phase 3 — radio/MAC cleanup

- region profile (433/CZ and conservative EU868);
- radio-family adapter instead of concrete `SX1262&`;
- asynchronous CAD/LBT/backoff and duty-cycle accounting;
- explicit TX/RX IRQ events and propagated RadioLib errors;
- remove cumulative stale frequency correction unless measurements justify it.

A mobile hint may shorten HELLO/expiry timers or reduce transit preference, but every safety/liveness invariant must remain valid if every hint is wrong.
