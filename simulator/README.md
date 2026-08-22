# DTProtocol simulator

Discrete-event simulator for the current DTPK crystallization/routing algorithm.

The goal is to separate three questions:

1. **Is the current C++ implementation correct?**
2. **If implementation bugs are removed, is the crystallization algorithm itself correct?**
3. **What additions are required for robust convergence under loss, delay, partitions and reconnects?**

## Profiles

- `current`: reproduces important behavior of the current C++ implementation, including the global end-to-end ACK gate, no triggered CRYST on every route change, unreliable relayed hops, forwarding-size growth, uint8 metric wrap, etc.
- `intended`: fixes implementation defects while keeping event-triggered crystallization semantics.
- `robust`: `intended` plus periodic CRYST refresh and per-neighbor expiry. This is a test candidate, **not yet a claim that the protocol is proven correct**.

## Examples

```bash
python dtpsim.py static-line --profile current --nodes 4 --seed 17 --duration 120000
python dtpsim.py reconnect --profile robust --nodes 5 --loss 0.05 --duration 240000
python dtpsim.py simultaneous --profile current --duration 120000
python dtpsim.py monte-carlo --profile current --mc-scenario static-line --nodes 4 --runs 1000
```

Add `--trace` to a single scenario for a replayable event trace.

## What is modeled

- CRYST full-vector advertisements and the current split-horizon rule.
- Crystallization quiet-period sessions and session garbage collection.
- Route selection by minimum hop count.
- Data forwarding, end-to-end DTPK ACK/NACK, and per-hop LCMM-style retries.
- Packet loss, ACK loss, latency/jitter, link partitions/healing, node restart hooks.
- LoRa airtime approximation and the 255-byte packet ceiling.
- Important current implementation defects as profile switches.

## Correctness properties checked by `audit()`

For a stable physical graph:

- every physically reachable destination eventually has a route;
- disconnected destinations do not retain stale routes;
- selected route distance equals graph shortest-path distance;
- following next-hop pointers reaches the destination rather than a loop;
- path stretch can be measured when non-shortest routing is intentionally allowed later.

These checks are useful for randomized counterexample search. A later step should add an explicit-state model checker (small `n`, all packet reorderings/failures) or a TLA+/PlusCal model for actual safety/liveness arguments.

## Planned C++-in-the-loop backend

The simulator deliberately separates the event/radio network from node protocol behavior. A second backend can compile the real DTProtocol C++ against a shim implementing:

- Arduino `millis()/delay()/random()` from simulated time;
- a fake RadioLib SX1262;
- fake interrupts / TX-complete callbacks;
- radio delivery through the same Python topology/event engine.

That permits the same scenario and random seed to be run against the Python model and the actual C++ implementation.

## Protocol-level validation strategy

Simulation is not a proof. The simulator is intended to discover counterexamples and quantify behavior. The conceptual protocol should be judged against explicit properties:

### Safety

- A selected next-hop graph for any destination contains no cycle.
- A route never points over a link that the node considers dead.
- Packet forwarding has a finite hop bound.
- Metric arithmetic cannot wrap and make an invalid route preferable.

### Liveness

Assuming the physical topology stops changing and packet loss is bounded:

- every reachable node is eventually learned;
- every unreachable node is eventually removed;
- the routing state eventually stops changing (or reaches a bounded periodic refresh state);
- after partition healing, routes eventually become usable again.

### Optimality

After convergence, selected hop count should equal graph shortest-path distance (unless a richer radio-quality metric is intentionally introduced).

### Crucial distinction

The current crystallization session uses "which neighbors transmitted during this wave" as a liveness test. These are not equivalent facts. A healthy neighbor may have no reason to transmit during another node's quiet-period session and can therefore be removed. `robust` disables session-participation deletion and instead uses explicit per-neighbor last-heard expiry.

`experiments.py` contains Monte-Carlo convergence, hop-reliability, reconnect, and routing-loop counterexample searches.
