# Optional narration

The rendered video is designed to work silently. If narration is recorded, add it
in the edit rather than synchronizing speech inside Manim. This script follows the
visual order and is intentionally conversational rather than documentation read
aloud.

## 1. Can a half-crystallized mesh send?

Start with the uncomfortable case. Node A already has a route to C through B, but
state involving D is still incomplete. Does A have to wait for the whole network
to settle before it can send anything useful?

It does not. There is no global ready bit in DTProtocol. The question is only
whether this destination already has a selected, feasible route.

## 2. Using a route before full convergence

A sends DATA to B, and B forwards it to C using the route that already exists.
The unfinished C-to-D snapshot keeps progressing independently.

The first valid DATA from a true one-hop sender is useful evidence too. C can
provisionally learn B as a direct neighbor. For the end-to-end response, each
forwarding hop leaves a short-lived reverse breadcrumb, so the ACK returns along
C to B to A even before proactive reverse routing has converged.

The important point is not that startup traffic gets special treatment. It is
that complete old state remains usable while incomplete new state is isolated.

## 3. Transactional crystallization

A HELLO is only a digest: boot incarnation plus route-state version. If a neighbor
is missing that version, it asks for a CRYST snapshot.

A snapshot may take several frames. Those chunks are assembled away from the
active routing table. Losing the second chunk does not leave the router with half
of a new worldview. The previous complete contribution stays active until the
whole replacement is present, then the update is committed at once.

An advertised route is rewritten locally as “via this neighbor, metric plus one.”

## 4. Loop-safe feasibility

Shortest path alone is not enough when stale information is circulating. Each
destination also has a generation and a feasible distance.

For the same generation, a neighbor has to advertise a strictly better metric
than the feasible distance already recorded. If it cannot, that candidate is
blocked even if it looks tempting locally.

When all known candidates are blocked, a sequence request eventually reaches the
destination. The destination advances its generation. That fresh generation can
make a longer surviving path feasible again.

Freshness decides whether a route is safe to consider. Distance decides which
safe route wins.

## 5. Per-hop reliability and end-to-end completion

LCMM answers a local question: did the next hop receive this frame? DTPK answers
a different one: did the final destination accept this logical message?

A reliable DATA frame gets bounded link retries on every hop. At the destination,
replay identity includes source, boot incarnation, and packet ID.

If the final end-to-end ACK is lost, the source retries the same identity. The
destination recognizes the replay, sends the ACK again, and does not call the
application a second time. Exactly-once application delivery comes from replay
handling, not from pretending the radio never duplicates anything.

## 6. Compression and multipart repair

Compression is not selected because the byte string merely got shorter. DTPK
estimates the complete LoRa cost after headers, fragment boundaries, and reliable
link ACKs.

In the measured regression example on screen, a repetitive 5,200-byte payload
would require 23 raw fragments. Heatshrink encodes it to 590 bytes, which needs
three fragments and saves about 37.8 seconds of modeled reliable-link airtime on
the configured test PHY. The source decodes the candidate locally and compares
it byte for byte before sending it.

If a message still needs several fragments, the destination exposes nothing to
the application until the whole message exists. Missing pieces are represented
by a compact bitmap, and only those pieces are sent again.

## 7. Repair after a cut

Now break the preferred route. A sequence-repair wave is forwarded once at each
hop. The requester, not every relay, owns persistence: retry delays grow from five
seconds toward a sixty-second cap, and every fourth attempt floods to escape a
stale directed path.

The previous design also gave every directed SEQ_REQ hop five LCMM attempts. In a
dense half-duplex mesh, those nested retries amplified repair traffic and delayed
the CRYST information required to finish repair.

In the representative twenty-node seed 69 trace, SEQ_REQ transmissions over the
post-heal window fell from 2,630 to 290, roughly an eighty-nine percent reduction.
The topology then converged instead of remaining incorrect at the old deadline.

## 8. Scheduling and progress

Reliability is useless if one class of reliable work can starve everything else.
The transmit scheduler therefore separates immediate responses, repair work, and
normal work.

Responses go first. Repair gets a bounded burst. Normal traffic must still get a
turn. Waiting for one local end-to-end result does not freeze HELLO, CRYST,
relayed DATA, ACKs, or NACKs.

## 9. Architecture recap

With the concrete mechanisms in view, the stack is easier to name. The data plane
owns message identity, replay, hop limits, and end-to-end completion. Neighbor
synchronization owns HELLO and transactional CRYST state. CrystDatabase owns
candidate routes and feasibility. Sequence repair restores liveness when every
candidate is blocked. The scheduler feeds those jobs into LCMM and the MAC.

The layers deliberately have different success conditions: one radio frame, one
confirmed hop, and one completed logical application message are not the same
thing.

## 10. What has actually been tested

The current protocol revision passes 325 host scenarios in the normal build and
again under address and undefined-behavior sanitizers. The startup matrix passes
900 of 900 cases. The cut-and-heal matrix reaches 100 of 100 healed topologies,
100 successful post-heal application deliveries, and zero routing loops in that
bounded test set.

Those results are evidence, not a proof about arbitrary radio conditions. Capture,
hidden terminals, exact MCU scheduling, brownouts, long hardware soaks, and
field mistakes such as duplicate physical node IDs still need real hardware.

## 11. Summary

The design can be reduced to one rule: do not destroy complete route knowledge
while replacement knowledge is incomplete.

Transactional snapshots preserve usable state. Feasibility prevents stale route
loops. Destination generations restore liveness. Layered acknowledgements and
replay identity make application delivery reliable on top of unreliable hops.
