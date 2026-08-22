# DTProtocol simulator

Discrete-event simulator for the current DTPK crystallization/routing algorithm and proposed replacements.

The goal is to separate three questions:

1. **Is the current C++ implementation correct?**
2. **If implementation bugs are removed, is the crystallization algorithm itself correct?**
3. **What additions are required for robust convergence under loss, delay, partitions, movement and reconnects?**

## One environment, two protocol adapters

The simulator has a single protocol-independent physical core in `environment.py`.

It owns:

- event time and deterministic same-time priority;
- topology and link state;
- independent node/link failure epochs;
- piecewise-linear continuous node motion;
- whole-airtime range validation;
- LoRa airtime;
- RF loss RNG;
- latency/jitter;
- later, shared collision/LBT/channel occupancy modelling.

Two protocol implementations attach to that same environment:

- **Python theoretical adapter** — `Simulator` + `Node`, used for rapidly trying protocol rules and finding counterexamples;
- **real C++ adapter** — `CppNetwork` + one `dtprotocol_host_node` subprocess per simulated device, executing the real `DTPK.cpp`, `CrystDatabase.cpp` and `lcmm.cpp`.

The environment RNG is separate from protocol RNG. Environmental failures are scheduled with higher priority than RF/protocol callbacks, so a failure can occur during any packet/ACK airtime and does not depend on the current firmware state.

The C++ adapter also separates **world time** from **MCU time**: the environment clock remains monotonic, while the `millis()` presented to an emulated node starts from zero again after each reboot, as it would on real hardware.

`scenario.py` defines backend-independent, JSON-serializable scenarios. The same object can be replayed as:

```python
scenario = Scenario.line(3, seed=42)

python_net = scenario.build("python", profile=Profile.intended())
cpp_net = scenario.build("cpp")
```

This is the preferred way to do differential testing. Topology, trajectories, failures and application demand are identical; only the node protocol implementation changes.

A saved scenario can also be replayed from the command line:

```bash
PYTHONPATH=simulator python simulator/run_scenario.py scenario.json \
  --backend python --profile current --duration-ms 120000 --pretty

PYTHONPATH=simulator python simulator/run_scenario.py scenario.json \
  --backend cpp --duration-ms 120000 --pretty

PYTHONPATH=simulator python simulator/run_scenario.py scenario.json \
  --backend both --profile current --duration-ms 120000 --pretty
```

`--backend both` executes the same scenario twice and reports externally observable route-table differences between the Python model and the real C++ implementation. This is the main mechanism for finding places where the abstract model does not yet match firmware behavior.

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

## Real C++ backend

Build it with:

```bash
cmake -S simulator/cpp -B simulator/cpp/build
cmake --build simulator/cpp/build
PYTHONPATH=simulator python -m pytest simulator/tests -q -rxX
```

Each emulated device is a separate process because the production stack uses process-global singletons. Python acts as the RF environment. This gives every node independent globals, heap, clock/reboot lifetime and firmware state while keeping failures/mobility external.

Sanitizers are also exercised in CI. On the reference implementation that job is currently informational because known memory-lifetime bugs are expected; it should be a hard gate for `protocol-v2`.

## What is modeled

- CRYST full-vector advertisements and the current split-horizon rule;
- crystallization quiet-period sessions and session garbage collection;
- route selection by minimum hop count;
- data forwarding, end-to-end DTPK ACK/NACK, and per-hop LCMM-style retries;
- packet/ACK loss, latency/jitter, partitions/healing, node failures/reboots;
- continuous moving-node geometry over the complete RF airtime;
- LoRa airtime approximation and the 255-byte packet ceiling;
- important current implementation defects as Python profile switches.

Shared-channel collision/hidden-terminal/CAD/LBT modelling is the next major physical-layer addition. It belongs in `EnvironmentKernel`, not in either adapter, so both theoretical and C++ runs see the same RF world.

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
