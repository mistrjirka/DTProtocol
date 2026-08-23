# DTPK asymptotic complexity notes

> **Historical pre-v3 analysis.** The warnings below about unprotected relays,
> missing hop limits, count-to-infinity and single-frame CRYST limits describe the
> predecessor protocol. V3 uses reliable forwarded hops, a finite hop limit,
> destination sequence feasibility and chunked transactional snapshots. The
> asymptotic lower-bound discussion remains useful, but implementation-specific
> claims below are not a current-state audit.


Notation:

- `n`: number of nodes/destinations
- `m`: number of undirected physical links
- `d_v`: physical degree of node `v`
- `Delta`: maximum degree
- `D`: physical network diameter
- `h`: hop count of a particular data route (`h <= D` for a shortest route)
- `L`: application payload bytes
- `r`: maximum LCMM attempts per reliable hop
- `p`: probability that one complete link attempt succeeds (DATA and link ACK)

## Data plane

Routing-cache lookup is average `O(1)` with `unordered_map` (worst-case `O(n)`). Each relay copies the packet, so CPU/memory-copy work per delivered message is `O(h L)`.

Ignoring losses and constants:

- DATA without end-to-end ACK and reliable hop ACKs: `h` DATA + `h` link-ACK radio frames = `2h = O(h)`.
- DATA plus a DTPK ACK travelling back, with reliable hop ACKs in both directions: `4h = O(h)` radio frames.
- Current C++ is inconsistent: the source DATA hop and first DTPK-ACK hop request LCMM ACK, but forwarded hops do not. It therefore still uses `O(h)` frames, but reliability falls exponentially with additional unprotected hops.

For independent attempts with success probability `p`, truncated to `r` attempts, expected attempts on one reliable hop are

`E[A] = sum_{k=1..r} P(A >= k) = (1 - (1-p)^r) / p`.

Thus expected radio work remains `O(h)` for fixed `p,r`, with a constant multiplier `E[A]`. The probability that all `h` reliable hops complete is roughly

`(1 - (1-p)^r)^h`,

and for a reliable return ACK path roughly

`(1 - (1-p)^r)^(2h)`.

Without per-hop retries on relayed hops, path delivery probability instead contains a factor approximately `q^(h-1)` (`q` = one-shot frame success), explaining why reliability degrades rapidly with hop count.

## Control plane: full-vector crystallization

A CRYST packet advertises up to `O(n)` selected routes, so its payload size is `O(n)`.

In an ideal synchronous Bellman-Ford-like convergence on a static graph, shortest-path information can advance at most one hop per round, requiring at most `D` rounds. If every node emits one full vector per round:

- broadcast transmissions: `O(n D)`;
- advertised route entries / bytes: `O(n^2 D)`;
- neighbor receive events: `O(m D)`;
- route entries parsed across receivers: `O(m n D)`.

Worst case `D = O(n)`, giving `O(n^2)` CRYST broadcasts and `O(n^3)` advertised bytes/entries. The current event-triggered implementation does **not** have this convergence guarantee because some route-table changes do not trigger another CRYST.

### Current C++ processing cost

Each received CRYST:

- builds an incoming vector: `O(n)`;
- sorts two vectors to compare them: `O(n log n)`;
- when changed, `buildCache()` scans every route contribution stored from every direct neighbor: `O(d_v n)`.

So one changed CRYST at node `v` costs roughly

`O(n log n + d_v n)`.

Across a full round this is approximately

`O(m n log n + n * sum_v d_v^2)`,

and multiplied by however many convergence rounds/triggered waves occur. Using `sum d_v^2 <= 2m Delta`, an upper expression is

`O(m n (log n + Delta) D)`.

The per-node contribution database stores up to `O(d_v n)` records; network-wide this is `O(m n)` records, with `O(n)` selected-route cache per node.

## Packet-size scaling limit

`NeighborRecord` is 5 bytes. With a 255-byte radio packet and MAC/LCMM/CRYST headers, a single full-vector CRYST fits only about 48 destinations. Therefore the current full-vector protocol does not asymptotically scale beyond that point at all without fragmentation, delta advertisements, or a different encoding.

## Failure convergence

With only split horizon and hop-count distance, classic 3+ node distance-vector loops/count-to-infinity remain possible. Since the current metric is `uint8_t` and has no explicit infinity/saturation rule, metric growth can eventually wrap from 255 to 0, turning a pathological stale route into an apparently optimal route. This needs a hard metric infinity and/or a loop-feasibility mechanism (sequence numbers, path information, or a diffusing computation).

## Important caveat: hop complexity is not bounded during a routing loop

When the selected forwarding graph is correct, a data packet uses `h <= D <= n-1` hops, so forwarding is `O(h)`.

The present packet format has **no TTL/hop-limit**. Therefore if reconvergence creates a forwarding cycle, there is no protocol-level finite bound on the number of forwarding hops. A packet can circulate until routing changes, a radio transmission fails, the node crashes, or (in the current buggy implementation) the forwarding-size bug eventually makes the packet exceed the MTU. After fixing the size bug, a TTL/hop limit becomes even more important.

So the rigorous statement is:

- stable loop-free state: `O(h)`, with `h <= D`;
- arbitrary transient current state: **unbounded** hop count without an explicit loop-freedom invariant or TTL.

## Failure-control complexity is also unbounded without loop avoidance

For initial discovery on a static topology, distributed Bellman-Ford-style propagation has finite `O(D)` round convergence under fair delivery and triggered updates.

After route failures, however, simple distance vector can enter count-to-infinity. Without a saturating finite infinity, sequence numbers, path vectors, or a feasibility/diffusing rule, there is no useful topology-only `O(f(n,m,D))` convergence bound. A finite infinity `M` turns this into a bound involving `M` (roughly `O(M)` bad-metric increments in the pathological case), at the cost of limiting supported network diameter.

The current `uint8_t` metric is not a valid infinity because arithmetic wraps; `255 + 1` becomes `0` rather than an unreachable value.
