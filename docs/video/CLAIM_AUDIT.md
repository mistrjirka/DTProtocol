# Video claim audit

This file maps the substantive claims in `dtprotocol_explainer.py` to current
code or repeatable tests. It is intentionally separate from the narration so a
future visual edit cannot quietly turn an illustration into an unsupported
protocol claim.

The protocol implementation shown by the video was validated at commit
`302ec8deca927e697e75f5868470d504ea63a3bd`. Commits after that point and before
this audit changed documentation/video assets only; the DTPK/LCMM implementation
is unchanged.

## 1. DATA before complete crystallization

Claim: a node may use an already selected route while unrelated CRYST state is
still incomplete, and control convergence continues in parallel.

Evidence:

- `STARTUP_MESSAGE_DELIVERY.md`
- `simulator/tests/test_startup_messages.py`
- `simulator/startup_message_matrix.py`

The release startup matrix contains 900 deterministic real-C++ runs: direct
single-frame, direct multipart, and partially converged four-node-line traffic at
0%, 5%, and 10% independent DATA/link-ACK loss. Result: 900/900 passed.

## 2. Reverse breadcrumb before proactive reverse convergence

Claim: ACK/NACK/status traffic may use the bounded ingress breadcrumb before
proactive reverse routing has converged. The animation draws that breadcrumb as
`C → B → A` and returns the E2E ACK one physical hop at a time; it does not imply
a C-to-A radio link.

Evidence:

- reverse-breadcrumb state in `include/DTPK.h` / `lib/DTPK/DTPKReceive.cpp`
- focused startup tests covering multi-hop early replies, breadcrumb expiry, and
  later proactive-route recovery

The implementation also has separate direct-neighbor learning rules, but the
current explainer deliberately leaves that detail out of this scene to keep the
visual argument focused.

## 3. Transactional CRYST snapshots

Claim: a multi-chunk neighbor snapshot is staged privately and replaces that
neighbor's committed contribution only after every chunk is present.

Evidence:

- CRYST assembly/commit path in `lib/DTPK/DTPKReceive.cpp`
- startup tests that hold, duplicate, reorder, and drop snapshot chunks while
  application DATA continues using the last committed table

## 4. Feasibility and destination generations

Claim: freshness is a feasibility condition, not a path metric. Among feasible
candidates, ordinary metric selection applies. If all known candidates are
blocked, destination-generation repair can make a longer surviving path feasible.

Evidence:

- `lib/DTPK/CrystDatabase.cpp`
- `simulator/tests/test_crystallized_v2.py`
- `simulator/tests/test_transfer_resilience.py`, including the longer replacement
  route during an active multipart transfer

## 5. Per-hop reliability versus end-to-end completion

Claim: LCMM confirms a selected next hop; DTPK optionally confirms acceptance by
the final destination. Same-identity retries are re-ACKed but not delivered to
the application twice.

Evidence:

- LCMM sender/ACK state in `lib/lcmm/lcmm.cpp`
- replay identity `{original sender, source incarnation, packet ID}` in DTPK
- lost-final-ACK and replay-eviction regressions in the real-C++ test suite

## 6. Compression example

The animation uses a measured test case, not an invented ratio.

Payload from `test_real_cpp_auto_compresses_only_when_radio_airtime_drops()`:

- original application bytes: **5,200**
- raw multipart fragments at 230 B/fragment: **23**
- heatshrink encoded bytes reported by production diagnostics: **590**
- encoded fragments observed by the C++ backend: **3**
- estimated reliable-link airtime saving reported by DTPK: **37,797 ms**

The source also performs a local decode-and-byte-compare before selecting a
compressed representation. Incompressible data falls back to raw bytes.

## 7. Multipart selective repair

Claim: partial application data is not exposed; the destination reports missing
fragment indices and the source resends only those pieces.

Evidence:

- `MULTIPART_PROTOCOL.md`
- multipart/fragment status implementation in `lib/DTPK/DTPKFragment.cpp`
- real-C++ tests for 1,000 B, 1,800 B, 16 KiB, missing-fragment repair, lost
  status/query/final ACK, compressed multipart, and malformed streams

## 8. SEQ_REQ repair after a cut

Claim: a SEQ_REQ repair wave is one-shot at each hop; the requester owns
persistent 5–60 s exponential retry and every fourth logical attempt floods.

Evidence:

- `retrySequenceRequests()` in `lib/DTPK/DTPK.cpp`
- `sendSeqRequest()` / forwarding in `lib/DTPK/DTPKControl.cpp` and
  `lib/DTPK/DTPKReceive.cpp`
- parity behavior in `simulator/node.py`

In the representative zero-loss 20-node seed 69 post-heal trace, SEQ_REQ
transmissions over the 600 s observation window fell from **2,630** to **290**,
about an 89% reduction. The fixed trace reached a correct topology by 400 s;
the pre-fix trace was still incorrect at 600 s.

The 100-seed real-C++ cut/heal matrix after the fix reported:

- initial convergence: **100/100**
- healed convergence: **100/100**
- post-heal application delivery: **100/100**
- routing loops: **0**

## 9. Scheduler progress

Claim: response traffic has first priority, repair gets a bounded burst, and
normal work still receives service. A local end-to-end waiter does not stop relay
traffic or routing control.

Evidence:

- queue classes and `MAX_REPAIR_BURST` selection in `lib/DTPK/DTPK.cpp`
- startup/resilience tests for crossing relay traffic, deferred/urgent CRYST
  repair, reentrant callbacks, and local-admission saturation while a new node
  still crystallizes

## 10. Reported validation counts

The validation scene reports the saved release results:

- normal host suite: **325 passed**
- ASan/UBSan host suite: **325 passed**
- startup matrix: **900/900**
- cut/heal matrix: **100/100** healed and **100/100** delivered, **0 loops**

These are bounded simulator results. The video explicitly leaves calibrated RF
capture/near-far behavior, hidden terminals, brownouts, exact MCU scheduling,
long hardware soaks, and duplicate physical node IDs outside that claim.

## Post-video focused rerun

After the storyboard, z-order, and claim text were finalized, the current source
was rebuilt and the claim-heavy subset was rerun against a fresh real-C++ host
binary:

- native C++ contracts: **11/11 passed**
- startup + feasibility + transfer resilience + multipart/compression + local
  admission scenarios: **82/82 passed**

No protocol implementation files changed during the video/README pass.
