# Application messages during route crystallization

DTProtocol has no global “network ready” phase. Routing state converges per
destination: an application may use a destination as soon as that node has a
selected route, even while unrelated HELLO/CRYST exchanges and multi-chunk
snapshot assemblies are still incomplete.

## What happens to an early send

- When no route exists yet, `sendPacket()` returns `0` immediately and invokes a
  supplied callback with failure. It does not hide the payload in an unbounded
  pre-route queue.
- Once any selected route exists, ordinary, compressed, and multipart DATA can
  use it while the rest of the topology continues crystallizing.
- A newly received CRYST snapshot is transactional. Its route records replace
  committed state only after every chunk is present. Until then, the previous
  complete route remains usable.
- Valid direct DATA is also link evidence. Before the application callback runs,
  the receiver installs a provisional one-hop route to the immediate sender and
  requests the missing full snapshot. The peer answers immediately with direct,
reliable LCMM chunks rather than waiting for the next broadcast snapshot.
- A lost provisional CRYST request is retried when later valid DATA from the
  same boot incarnation arrives. Once a nonzero route version is committed,
  matching DATA/HELLO no longer creates request traffic.
- Every forwarded DATA packet leaves a bounded reverse breadcrumb keyed by
  `{original sender, boot incarnation, packet ID}`. ACK, NACK, fragment status,
  and immediate application replies can therefore follow the exact ingress path
  before proactive reverse routing has converged.
- Breadcrumbs expire after 120 seconds. They are a temporary response aid, not
  permanent routing state.

## Scheduling during convergence

An outstanding local end-to-end transaction gates only a second unrelated local
application transaction. It does **not** pause the routing protocol or turn a
relay into a stop-and-wait bottleneck:

- ACK/NACK and fragment status remain highest-priority responses;
- HELLO, CRYST broadcasts, sequence repair, and bounded repair traffic continue;
- relayed DATA continues even when the relay itself is waiting for an ACK;
- diagnostic echo replies may be queued reentrantly from the receive callback;
- CRYST requests are coalesced and normally deferred while the active route is
  healthy, preventing a five-attempt neighbor-sync exchange from starving the
  returning DATA ACK;
- once a hop fails or a relay returns a transient routing NACK, the transaction
  marks route repair urgent and CRYST requests become immediately eligible.

Per-hop LCMM silence timers are independent of the application timeout. A long
application deadline therefore cannot make one lost link ACK monopolize the
radio for minutes. A relay that exhausts all five next-hop attempts immediately
returns a transient NACK upstream. Each upstream relay rewrites the NACK's failed
branch ID, so the source can avoid the failed first-hop branch, use a feasible
alternate, and retry the same application identity. If no alternate exists, the
same route is retried only after bounded backoff.

SEQ_REQ uses a different reliability boundary from DATA and direct CRYST
responses. A directed repair wave is transmitted once at each hop; the requester
owns persistent logical retry with 5–60 second exponential backoff, and every
fourth wave floods. Giving every SEQ_REQ five LCMM attempts at every hop multiplied
control traffic on a half-duplex mesh and delayed the CRYST state needed to end
the repair. The one-shot-hop/persistent-origin split removes that feedback loop
without turning sequence repair into a finite best-effort burst.

Single-frame and multipart retries reuse `{source, boot incarnation, packet ID}`.
The destination therefore re-ACKs a duplicate without calling the application a
second time. A separate final-reject NACK bit is used only when the final
destination assembled and parsed the complete payload but could not accept it;
that failure is terminal rather than route-retryable.

## Important bounds

- One source-side multipart message may be active at a node. A second immediate
  multipart call is rejected visibly with return value `0`; this prevents two
  message-sized retransmission buffers from silently exhausting RAM.
- Destination multipart assemblies, replay identities, reverse breadcrumbs, and
  route snapshot chunks are bounded.
- Locally originated application transactions are capped at eight by default
  (`DTPK_MAX_LOCAL_PENDING_MESSAGES`). Rejection is synchronous (`sendPacket()`
  returns `0` and invokes a supplied callback with failure) before compression or
  payload allocation. This is deliberately a **local-application-only** budget:
  ACK/NACK, fragment status, route repair, relayed DATA, and crystallization do
  not consume it and therefore keep making progress under a faulty app flood.

## Real-C++ validation

`simulator/tests/test_startup_messages.py` exercises the production C++ code and
covers, among other cases:

1. visible rejection before any route exists, followed by normal convergence;
2. asymmetric startup with single-frame, compressible, and 1,000-byte multipart
   DATA before reciprocal control arrives;
3. reverse DATA with no requested E2E ACK;
4. a reverse application send before the first forward ACK is allowed through;
5. a completely lost five-attempt provisional CRYST request, repaired by a
   second same-incarnation DATA packet;
6. use of a known two-hop route while a fourth node is still unknown;
7. mixed small/compressed/multipart/no-E2E backlog while the remaining topology
   continues converging;
8. continued use of an old committed route while a newer multi-chunk snapshot is
   only half assembled;
9. first-hop source learning, lost link ACK timing, and reentrant debug echo;
10. delayed DATA from an old boot, 16-bit incarnation wraparound, and removal of
    stale indirect state after newer direct DATA;
11. deferred generation repair at an active-ACK boundary;
12. multi-hop debug echo over a reverse breadcrumb with verified hop values
    `255 -> 254`;
13. breadcrumb expiry and later recovery through the normal proactive route;
14. first-hop and downstream route loss during single-frame and multipart DATA;
15. longer replacement routes requiring active sequence repair;
16. discovery of a previously hidden alternate neighbor during an active
    transaction, including urgent CRYST request and failed-router avoidance;
17. complete loss of the first whole-message ACK followed by a same-ID retry;
18. high-rate unrelated traffic exceeding the former global replay-ring size;
19. packet-ID and boot-incarnation wraparound plus out-of-order delivery;
20. ACK callbacks that immediately enqueue a second application message;
21. acknowledged and fire-and-forget local application floods at the admission
    boundary, while a newly attached node still crystallizes through the same
    saturated source.

Additional resilience tests prove that a relay's own pending E2E ACK does not
block crossing traffic, route changes refresh queued next hops, same-identity
retries deliver once, malformed startup control cannot refresh liveness, and
convergence/delivery survive 32-bit `millis()` rollover.

The manual trace and gap audit exposed real faults rather than merely adding
coverage: missing early reverse routes, incorrect hop-limit initialization,
relay E2E gating, lost snapshot requests, repair traffic deferred until timeout,
no single-message retry, terminal treatment of routing NACKs, stale next-hop use,
unsafe callback reentrancy, exact-once failure after global replay-ring
eviction, unbounded local application admission, and multiplicative SEQ_REQ
link retries that caused dense cut/heal repair storms. Each now has a focused
permanent regression.

## Reproducible startup matrix

The real-C++ matrix can be rerun after building the host backend:

```bash
cmake -S simulator/cpp -B simulator/cpp/build
cmake --build simulator/cpp/build --parallel
DTP_CPP_NODE="$PWD/simulator/cpp/build/dtprotocol_host_node" \
  PYTHONPATH=simulator python simulator/startup_message_matrix.py \
  --runs 100 --losses 0,0.05,0.10
```

The release run covered direct single-frame, direct multipart, and partially
converged four-node-line sends: **900/900 deterministic seeded runs passed**, at
0%, 5%, and 10% independent DATA/link-ACK loss. This is an empirical bounded
stress result, not a claim that finite retries can guarantee delivery against an
unbounded sequence of real RF losses.

## Dense cut/heal recovery matrix

The real-C++ cut/heal matrix can be rerun with:

```bash
DTP_CPP_NODE="$PWD/simulator/cpp/build/dtprotocol_host_node" \
  PYTHONPATH=simulator python simulator/real_cpp_random_matrix.py \
  --runs 100 --output /tmp/dtprotocol-random.json
```

It deterministically covers 5, 8, 12, and 20-node line, ring, star, and random
topologies at 0%, 2%, 5%, and 10% independent DATA/link-ACK loss. Before the
SEQ_REQ reliability fix, initial convergence was 100/100 but only 89/100 healed
topologies and 84/100 post-heal application sends completed within the matrix
bounds. With one-shot SEQ_REQ hops and every-fourth-attempt flood escape, the
same seeds reach **100/100 initial convergence, 100/100 healed convergence,
100/100 application delivery, and zero routing loops**. In the representative
zero-loss 20-node seed 69 trace, SEQ_REQ transmissions during the 600-second
post-heal window fell from 2,630 to 290 (about 89%), and the topology converged
by 400 seconds instead of remaining incorrect after 600 seconds.
