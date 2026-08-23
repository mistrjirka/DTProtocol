# Optional narration

The rendered video is designed to work silently. If narration is recorded, add it
in the edit rather than synchronizing speech inside Manim. The script follows the
visual order and leaves the animation responsible for most of the explanation.

## 1. Can a half-crystallized mesh send?

Start with the uncomfortable case. A already trusts a route to C through B, while
information involving D is still moving through the mesh. Does A have to wait for
everything else to settle before it can send?

It does not. DTProtocol has no global ready bit. The useful question is whether A
already has a selected, feasible route to this destination.

## 2. Crystallization, from physical reach to trusted state

At the beginning, the radios are nearby but the routing layer has learned no
neighbors. A range circle is a physical statement only: B is inside A's radio
range, but that is not yet a learned route.

A sends HELLO. B notices the state mismatch and asks for the relevant CRYST state.
After that local exchange the direct-neighbor relationship is known. The same
thing happens independently from B to C and from C to D.

Indirect knowledge is different. A CRYST snapshot may need several LoRa frames,
so it is assembled away from the active route table. After the first chunk, A
still uses the old complete contribution. Only when every chunk is present does
the new contribution commit atomically. That is when C becomes usable at A via B.
D may still be propagating farther through the network.

So crystallization grows useful knowledge locally. It is not a global phase that
every node must finish before anyone can communicate.

## 3. DATA before global convergence

Now use that committed A-to-C route. DATA moves A to B, then B to C. While that
logical message is in flight, ordinary control work can continue; the video lets
a CRYST chunk move from C to B between message steps.

For the end-to-end response, forwarding leaves a bounded reverse breadcrumb. The
ACK therefore returns C to B to A, one hop at a time, even before proactive reverse
routing has fully settled. Afterward the pending CRYST transaction simply
continues.

The important point is that complete old state remains usable while incomplete
replacement state is isolated.

## 4. Loop-safe feasibility

Shortest path alone is not enough when stale information is circulating. Each
destination also has a generation and a feasible distance.

For the same generation, a neighbor has to advertise a strictly better metric
than the feasible distance already recorded. If it cannot, that candidate is
blocked. A newer destination generation is fresh; among feasible candidates,
ordinary metric selection decides which route wins.

Freshness is therefore a safety condition, not a second path metric.

## 5. Per-hop reliability and end-to-end completion

LCMM answers a local question: did the next hop receive this frame? DTPK answers
a different one: did the final destination accept this logical message?

A reliable DATA frame gets bounded link retries on every hop. At the destination,
replay identity includes source, boot incarnation, and packet ID.

If the final end-to-end ACK is lost, the source retries the same identity. The
destination recognizes the replay, sends the ACK again, and does not call the
application a second time. Exactly-once application delivery comes from replay
handling, not from pretending radio frames arrive once.

## 6. Compression and multipart repair

Compression is selected only when the complete LoRa airtime estimate improves
after headers, fragment boundaries, and reliable-link ACKs are included.

In the measured regression example, a repetitive 5,200-byte payload would need
23 raw fragments. Heatshrink encodes it to 590 bytes, which needs three fragments
and saves about 37.8 seconds of modeled reliable-link airtime on the test PHY. If
the saving is not worthwhile, DTProtocol sends the original bytes.

Those three fragments then travel over the ordinary hop-by-hop transport. If one
fragment remains missing after its link retries are exhausted, the destination
keeps the partial assembly private and reports the missing index. Only that
fragment is sent again. Once the complete logical message exists, the application
receives it once and one whole-message ACK returns.

## 7. Repair after a cut

The preferred route is A to B to C to E to D. Break C-to-E. A directed repair
request can still advance A to B to C, where it reaches the break.

Persistence belongs to the requester rather than every relay: retry delays grow,
and periodically one wave floods to escape stale directed knowledge. The flood
expands over actual surviving links. Fresh destination state then returns through
the surviving A-to-X-to-E-to-D branch, and that longer path becomes usable.

The earlier design also gave every directed SEQ_REQ hop five LCMM attempts. In a
dense half-duplex mesh those nested retries amplified repair traffic. In the
representative seed 69 trace, SEQ_REQ transmissions fell from 2,630 to 290 after
the one-shot intermediate repair change.

## 8. Scheduling and progress

Reliability is useless if one class of reliable work can starve everything else.
The transmit scheduler separates immediate responses, repair work, and normal
work. Responses go first, repair gets a bounded burst, and normal traffic still
gets a turn.

Waiting for one local end-to-end result does not freeze HELLO, CRYST, relayed DATA,
ACKs, or NACKs.

## 9. Architecture recap

Only now name the machinery the viewer has already seen. The data plane owns
message identity, replay, hop limits, and end-to-end completion. Neighbor
synchronization owns HELLO and transactional CRYST state. CrystDatabase owns
candidate routes and feasibility. Sequence repair restores liveness when usable
knowledge becomes stale. The scheduler feeds work into LCMM and the MAC.

One radio frame, one confirmed hop, and one completed application message are
three different success conditions.

## 10. What has actually been tested

The saved release results are 325 host scenarios in the normal build and again
under address and undefined-behavior sanitizers, a 900-of-900 startup matrix, and
a 100-of-100 cut-and-heal matrix with successful post-heal delivery and zero
routing loops in that bounded test set.

Those results are evidence, not a proof about arbitrary radio conditions. Capture,
hidden terminals, exact MCU scheduling, brownouts, long hardware soaks, and field
mistakes such as duplicate physical node IDs still require hardware validation.

## 11. Summary

Trusted state becomes useful locally, not all at once. Physical radio reach is
only the beginning; direct knowledge is learned locally, indirect snapshots commit
transactionally, and DATA keeps using already committed routes while the rest of
the mesh continues to crystallize.
