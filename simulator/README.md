# DTProtocol simulator

Discrete-event simulator for the current DTPK crystallization/routing algorithm and proposed replacements.

The goal is to separate three questions:

1. **Is the current C++ implementation correct?**
2. **If implementation bugs are removed, is the crystallization algorithm itself correct?**
3. **What additions are required for robust convergence under loss, delay, partitions, movement and reconnects?**

## One environment, two protocol adapters

The simulator now has a single protocol-independent physical core in `environment.py`.

It owns:

- event time and deterministic same-time priority;
- topology and link state;
- independent node/link failure epochs;
- piecewise-linear continuous node motion;
- whole-airtime range validation;
- LoRa airtime;
- RF loss RNG;
- latency/jitter;
- shared-channel occupancy, collision/hidden-terminal and RSSI-CCA/backoff modelling.

Two protocol implementations attach to that same environment:

- **Python theoretical adapter** — `Simulator` + `Node`, used for rapidly trying protocol rules and finding counterexamples;
- **real C++ adapter** — `CppNetwork` + one `dtprotocol_host_node` subprocess per simulated device, executing the real `DTPK.cpp`, `CrystDatabase.cpp` and `lcmm.cpp`.

The environment RNG is separate from protocol RNG. Environmental failures are scheduled with higher priority than RF/protocol callbacks, so a failure can occur during any packet/ACK airtime and does not depend on the current firmware state.

`scenario.py` defines backend-independent, JSON-serializable scenarios. The same object can be replayed as:

```python
scenario = Scenario.line(3, seed=42)

python_net = scenario.build("python", profile=Profile.intended())
cpp_net = scenario.build("cpp")
```

This is the preferred way to do differential testing. Topology, trajectories, failures and application demand are identical; only the node protocol implementation changes.

## Profiles

- `current`: reproduces important behavior of the current C++ implementation, including the global end-to-end ACK gate, no triggered CRYST on every route change, unreliable relayed hops, forwarding-size growth, uint8 metric wrap, etc.
- `intended`: fixes implementation defects while keeping event-triggered crystallization semantics.
- `robust`: `intended` plus periodic CRYST refresh and per-neighbor expiry. This is a test candidate, **not yet a claim that the protocol is proven correct**.
- `feasible`: adds destination generations + the loop-free feasibility condition.
- `cryst-v2`: event-triggered/chunked CRYST, HELLO state identity, sequence requests, explicit infinity and feasibility; this is the current v2 design candidate.

## Examples

```bash
python dtpsim.py static-line --profile current --nodes 4 --seed 17 --duration 120000
python dtpsim.py reconnect --profile robust --nodes 5 --loss 0.05 --duration 240000
python dtpsim.py simultaneous --profile current --duration 120000
python dtpsim.py monte-carlo --profile current --mc-scenario static-line --nodes 4 --runs 1000
```

Add `--trace` to a single scenario for a replayable event trace.

## Real C++ backend

Build it with:

```bash
cmake -S simulator/cpp -B simulator/cpp/build
cmake --build simulator/cpp/build
python -m pytest simulator/tests/test_cpp_backend.py simulator/tests/test_cpp_environment.py -q -rxX
```

Each emulated device is a separate process because the production stack uses process-global singletons. Python acts as the RF environment. This gives every node independent globals, heap, clock/reboot lifetime and firmware state while keeping failures/mobility external.

Sanitizers are also exercised in CI. On the reference implementation that job is currently informational because known memory-lifetime bugs are expected; it should be a hard gate for `protocol-v2`.

## What is modeled

- versioned v4 CRYST snapshots with five-byte route records and transactional chunk assembly;
- sequence/feasibility route selection without split-horizon wire state;
- single-frame and selective-repair multipart DATA, end-to-end ACK/NACK, and per-hop LCMM retries;
- independent or Gilbert-Elliott burst packet/ACK loss, latency/jitter, partitions/healing and reboot;
- continuous moving-node geometry over the complete RF airtime;
- separate decode, interference and CCA visibility ranges;
- documented LoRa airtime and the 255-byte packet ceiling;
- RSSI CCA timing, 25-250 ms randomized backoff, collisions, hidden terminals and half-duplex;
- important historical implementation defects as Python profile switches.

The main remaining RF realism gaps are calibrated received power/path loss, capture and preamble lock, mixed channels/SFs, and an asynchronous C++-subprocess CCA handshake. See `SIMULATOR_VALIDATION.md`; use the timed Python backend—not C++ contention—as the quantitative MAC-policy reference.

For the protocol-level dependency graphs, state ownership, pruning analysis and the staged simplification plan, see [`PROTOCOL_ARCHITECTURE.md`](PROTOCOL_ARCHITECTURE.md).

Application payloads larger than one LoRa frame use the selective-repeat multipart layer documented in [`../MULTIPART_PROTOCOL.md`](../MULTIPART_PROTOCOL.md).

## Correctness properties checked by `audit()`

For a stable physical graph:

- every physically reachable destination eventually has a route;
- disconnected destinations do not retain stale routes;
- selected route distance equals graph shortest-path distance;
- following next-hop pointers reaches the destination rather than a loop;
- path stretch can be measured when non-shortest routing is intentionally allowed later.

Simulation is useful for finding counterexamples but is not a proof. Small-state exhaustive/model-checking work or a TLA+/PlusCal model is still desirable for actual safety/liveness arguments.

## Protocol-level validation strategy

### Safety

- A selected next-hop graph for any destination contains no cycle.
- A route never points over a link that the node considers dead.
- Packet forwarding has a finite hop bound.
- Metric arithmetic cannot wrap and make an invalid route preferable.

### Liveness

Assuming the physical topology stops changing and packet loss is bounded:

- every reachable node is eventually learned;
- every unreachable node is eventually removed;
- routing state eventually stabilizes (apart from bounded repair refreshes);
- after partition healing, routes eventually become usable again.

### Optimality

After convergence, selected hop count should equal graph shortest-path distance unless a richer link metric is deliberately introduced.

### Crucial distinction

The current crystallization session uses "which neighbors transmitted during this wave" as a liveness test. These are not equivalent facts. A healthy neighbor may have no reason to transmit during another node's quiet-period session and can therefore be removed. `robust` disables session-participation deletion and instead uses explicit per-neighbor last-heard expiry.

`experiments.py` contains Monte-Carlo convergence, hop-reliability, reconnect, and routing-loop counterexample searches.

## Current v2 liveness result

A direct-neighbor liveness probe is **positive evidence only**. A successful LCMM ACK refreshes liveness, but a finite number of unanswered probes must not withdraw topology: loss/half-duplex can make a healthy neighbor miss them. Only the 120 s hard no-valid-packet timeout currently removes a direct neighbor.

This distinction was found by the real-C++ scale simulator. With the old "two failed probes = dead" rule a stable 64-node line was correct at 600 s but later lost 626 routes at 1200 s before relearning them. Keeping the 120 s hard timeout while removing failed-probe eviction stayed at 0 missing / 0 wrong routes through 1500 s; a 96-node line reached 0 / 0 by 1200 s and stayed there through 2400 s.

## Mobile hint

`mobile_hint` is not a correctness input and is not transmitted. It only shortens that node's HELLO period from 10 s to 4 s. A 500-seed two-node contact experiment found the hint useful for short encounters (5 s: 92.8% versus 55.4% mutual discovery), while the difference disappeared by roughly 10-12 s. In a stable two-node 60 s run it increased modeled bytes on air from 350 to 530. Keep it optional/local; do not use it in feasibility, route metrics, expiry or advertised state.

## Compression model boundary

The abstract Python state machine models payload lengths and multipart behavior,
but not payload bytes or entropy. Automatic v4 compression is therefore tested
in the byte-aware real-C++ backend; the Python model does not invent a
compression ratio.
