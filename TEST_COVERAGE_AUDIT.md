# DTProtocol test-coverage audit

This audit distinguishes safety properties already exercised by the real C++
implementation from remaining allocator-failure, simulator, and hardware
boundaries.

## Strong current coverage

### Routing and startup

- all connected labelled five-node graphs in the exhaustive topology suite;
- lines, rings, trees, grids, ladders, wheels, barbells, sparse/dense random
  graphs, link cut/heal, partitions, reboot, and larger networks;
- transactional CRYST assembly, out-of-order/missing chunks, stale generations,
  directed/flood sequence repair, and sequence-number wraparound;
- application traffic before full crystallization, asymmetric discovery,
  provisional direct routes, reverse breadcrumbs, lost repair requests,
  reliable multi-chunk direct snapshot responses, and crossing relay traffic;
- a reproducible 900-run real-C++ startup matrix across direct single,
  multipart, and partial-line scenarios at 0%, 5%, and 10% seeded loss;
- a reproducible 100-seed real-C++ cut/heal matrix across 5–20-node line,
  ring, star, and random topologies at 0%, 2%, 5%, and 10% loss: 100/100
  initial convergence, 100/100 healed convergence, 100/100 post-heal delivery,
  and zero loops;
- `millis()` rollover in the real C++ backend.

### Application transport

- single-frame and multipart delivery, 233/234-byte boundary, 16 KiB maximum,
  selective repair, source/destination reboot, route failover, and multiple
  simultaneous senders competing for bounded assembly slots;
- first-hop and downstream failures, transient versus final NACK, lost complete
  E2E ACK, same-identity retry, replay suppression, and reentrant callbacks;
- automatic compression selection/fallback, malformed compressed input,
  compressed multipart repair, and application metadata preservation;
- no-E2E sends and exact once-only application delivery;
- an independent eight-message default budget for locally originated traffic,
  including E2E, multipart, retry, and no-E2E ownership transitions, while
  ACK/NACK, relayed DATA, CRYST, and repair remain admitted.

### Malformed input and timing

- deterministic malformed frame corpus and semantic validation of DATA,
  fragments, ACK/NACK, HELLO, CRYST, CRYST_REQ, and sequence repair;
- invalid frames neither install routes nor refresh liveness;
- normal plus ASan/UBSan native contracts and real-C++ network scenarios;
- LCMM retry timing, DATA-plus-ACK airtime, clock wrap, duty deferral, motion,
  mid-air link failure, burst fading, CCA/collision model, and reboot epochs.

### Device tooling

- strict node-ID/boolean/unknown-field validation, UTF-8 suffix-byte bounds,
  action/device mapping, and constrained Linux upload-port validation;
- one active build/flash job per flasher process, process-group cancellation,
  stale generated-header cleanup, bounded-log rollover, and physical-port lock;
- token-protected local API, localhost-only binding, no-store/nosniff/referrer/
  opener/CSP headers, and request-size limits;
- compile-time debug-echo composition and protocol-level no-re-echo marker;
- clean PlatformIO builds for T-Watch S3, Heltec Wireless Stick Lite V3, and the
  custom RP2040 board.

## Bugs found by the added gap tests

1. Early direct DATA delivered, but did not establish a reverse application
   route before callback.
2. Early relayed DATA lacked a temporary reverse path for ACK/status or an
   immediate application reply.
3. A source packet sent through a temporary reverse route initialized hop limit
   from route distance, corrupting later metrics.
4. A relay's own pending E2E transaction blocked unrelated crossing DATA.
5. A completely lost provisional CRYST request was never retried by later DATA
   from the same incarnation.
6. A long application timeout leaked into LCMM's per-attempt silence interval.
7. Queued DATA retained a stale next hop instead of refreshing at radio ownership.
8. Structurally invalid DATA/control could contribute liveness or provisional
   routing before full semantic validation.
9. A local destination generation could advance while old-generation DATA was
   still queued or active.
10. Sequence repair and CRYST traffic could be deferred until a multipart
    transaction timed out, making a legitimate longer route unusable.
11. Conversely, an always-eligible reliable CRYST request could starve a healthy
    startup multipart transfer; repair now becomes urgent only after failure.
12. Single-frame E2E traffic lacked a same-identity retry after route change or
    a lost final ACK.
13. Every NACK was treated as terminal, so transient forwarding failure could not
    recover through crystallization.
14. A relay that exhausted all next-hop attempts did not immediately notify the
    source.
15. Equal-cost route selection could keep choosing the failed deterministic
    tie-break winner even when a feasible alternate existed.
16. A transient NACK did not identify which source-side branch failed.
17. ACK callbacks ran while waiter containers were still being modified, making
    immediate reentrant sends unsafe.
18. The global 64-entry replay ring allowed unrelated traffic to evict an active
    retry identity and cause a second application delivery.
19. Duplicate CRYST destinations and impossible route records were not rejected
    transactionally.
20. Duplicate fragment status could request a fragment already queued or in
    flight, adding avoidable repair traffic.
21. Repeated local small-message sends could grow `_packetRequests` and
    `_packetWaiting` without a bound. Local admission is now capped independently
    from control, response, relay, and repair traffic; the no-E2E packet already
    owned by LCMM is counted explicitly so the boundary is exact.
22. Directed SEQ_REQ used five LCMM attempts at every hop even though the
    requester already persisted the logical repair with exponential backoff and
    flood escape. Dense cut/heal cases amplified each wave, delayed useful CRYST
    snapshots, and missed convergence bounds. Repair is now one-shot per hop,
    persistent at the requester, with every fourth attempt flooding.

Each item now has a focused permanent regression in the real C++ backend; the
single-retry, branch-failure, NACK, route-refresh, and repair behavior is also
mirrored in the fast Python backend.

## Remaining software gaps, in priority order

### 1. Allocation-failure injection across every ownership edge

Normal allocation failures are checked, but the suite does not deterministically
fail each individual `malloc`/container growth site while traffic is in flight.
A fault-injecting allocator should verify cleanup, NACK behavior, and continued
routing after each failure point.

### 2. Fan-in above the configured replay-source table

The old global replay ring was replaced with one 64-ID serial window per source,
and high-rate unrelated traffic is now a regression. The default table has 256
source slots, matching the validated 255-node envelope. Deployments that permit
more concurrently active source IDs must raise `DTPK_REPLAY_SOURCE_SLOTS`; once
the table itself is exceeded, the least-recently-used source window can age out.

### 3. Very long stochastic soak and reset/power-loss traces

The suite has deterministic fault matrices and sanitizer runs, but not a
multi-day hardware/simulator soak with random brownouts, flash persistence
faults, and repeated boot-sequence storage interruption.

### 4. Duplicate logical node IDs

The flashing UI requires an explicit nonzero ID, but it cannot know whether a
second physical device already uses that ID. The current simulator indexes nodes
by logical ID, so two radios claiming the same ID require an alias-capable RF
adapter or hardware-in-loop test. Duplicate IDs remain a deployment error rather
than a resolved protocol condition.

## Remaining physical/model gaps

These require hardware-in-loop or a more calibrated RF model rather than more
routing assertions alone:

- received power, capture effect, preamble lock, near/far behavior;
- mixed spreading factors, bandwidths, channels, and adjacent-channel effects;
- exact asynchronous ESP32/RP2040 CCA/task/ISR interleavings;
- BLE interoperability across Android clients and negotiated MTUs;
- current draw, PMIC/battery behavior, display sleep, and RF operation during
  real power-state transitions;
- antenna/RF-switch/TCXO variation across physical boards.

## Interpretation

Passing the suite is strong evidence for finite-state safety, route convergence,
and transactional delivery in the modeled conditions. It is not a claim that
all RF environments, heap-pressure schedules, or hardware failures have been
exhaustively proven. The unresolved items above are explicit test targets rather
than silent assumptions.

## Final software validation for this revision

- **11/11** native C++ contracts pass in normal and ASan/UBSan builds.
- **325/325** Python tests pass against the real current C++ host binary in both
  normal and ASan/UBSan configurations.
- The focused startup/transfer/wire-hardening group passes **52/52** before the
  two local-admission regressions, which also pass independently.
- The seeded real-C++ startup matrix passes **900/900** runs at 0%, 5%, and 10%
  independent DATA/link-ACK loss.
- The seeded real-C++ cut/heal matrix passes **100/100** initial topologies,
  **100/100** healed topologies, and **100/100** post-heal application sends with
  zero loops; the pre-fix baseline was 89/100 healed and 84/100 delivered.
