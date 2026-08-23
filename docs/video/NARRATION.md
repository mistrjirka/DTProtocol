# Optional narration

The rendered video works without narration. This script is written for a calm,
technical delivery at roughly 145–155 words per minute. Leave the short pauses in
place; the animation carries part of the explanation.

## 1. The idea

DTProtocol is a proactive LoRa mesh protocol. Application messages are not
flooded through the network. Nodes first exchange route knowledge. A message then
follows one selected path.

## 2. Architecture

The implementation is easier to understand as cooperating state machines. The
data plane owns message identity, replay protection, hop limits, and end-to-end
results. Neighbour synchronization owns HELLO, CRYST requests, snapshot chunks,
and liveness. The routing database selects feasible routes. Sequence repair
restores liveness when feasibility blocks every known candidate. A scheduler
feeds all of this into LCMM and the MAC.

These layers do not mean the same thing by success. The MAC completes one radio
frame. LCMM confirms one next hop. DTPK completes a logical application message.

## 3. Crystallization

A HELLO carries only a boot incarnation and route-state version. When that digest
is missing or newer, the neighbour asks directly for a CRYST snapshot.

A large snapshot can span several frames. Those chunks are assembled away from
the active routing table. Until every chunk is present, the previous complete
state remains usable. The new contribution is committed atomically.

A route advertised by a neighbour is stored as “via that neighbour, metric plus
one.”

## 4. Feasibility

Distance alone is not enough to prevent count-to-infinity loops. Each destination
also has a generation and a feasible distance. A same-generation candidate must
report a strictly better neighbour metric than the state this node previously
advertised.

When every candidate is blocked, a sequence request reaches the destination. The
destination advances its generation, advertises fresh state, and a valid longer
route can become feasible.

## 5. Messages during startup

There is no global ready phase. If A already has a selected route to C, it may
send to C while unrelated state involving D is still incomplete.

Direct DATA is also evidence of a one-hop link. The immediate sender gets a
provisional reverse route. A bounded breadcrumb records the ingress path back to
the original source, so acknowledgements and immediate replies can return before
proactive reverse routing has fully converged.

## 6. Reliability

Every reliable hop uses LCMM acknowledgement and bounded retry. The destination
then returns one DTPK end-to-end result.

Retries retain the identity made from source, boot incarnation, and packet ID. If
the final acknowledgement is lost, the source resends the same identity. The
destination recognizes the replay, acknowledges it again, and does not call the
application twice.

## 7. Compression and multipart messages

Compression is chosen by predicted LoRa airtime, not merely by byte count. A
smaller payload that occupies the same LoRa symbol groups is rejected. A useful
candidate is decoded locally and compared byte for byte before transmission.

Messages that still exceed one frame are divided into 230-byte fragments. The
destination exposes nothing until the complete message exists. If one fragment
is missing, it sends a bitmap and the source retransmits only that fragment.

## 8. Scheduling

Responses have first priority. Repair traffic gets a bounded burst. Normal work
must still run. Waiting for one local end-to-end result does not pause HELLO,
CRYST, relayed DATA, or acknowledgements.

## 9. Repair after a cut

A sequence-repair wave is sent once at each hop. The requester, not every relay,
owns persistent retry. Delays grow from five seconds up to sixty seconds, and
every fourth attempt floods to escape stale directed candidates.

The old design also gave every directed hop five LCMM attempts. In a dense
half-duplex mesh, that multiplied control traffic and delayed the CRYST state
needed to finish repair. In the representative twenty-node trace, sequence
requests fell from two thousand six hundred and thirty to two hundred and ninety.

## 10. Validation

The current revision passes three hundred and twenty-five real-C++ tests in both
normal and sanitizer builds. The startup matrix passes nine hundred of nine
hundred cases. The cut-and-heal matrix reaches one hundred of one hundred initial
and healed topologies, one hundred successful post-heal messages, and no routing
loops.

Those are bounded results. Capture effects, hidden terminals, brownouts, long
hardware soaks, and duplicate physical node IDs still require physical testing.

## 11. Summary

The central rule is simple: keep complete route state usable while new state is
incomplete. Feasibility provides safety. Destination-generation repair provides
liveness. Layered acknowledgements provide reliable application delivery.
