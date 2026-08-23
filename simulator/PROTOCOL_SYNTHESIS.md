# Bounded protocol optimization and synthesis

“Find the optimal routing protocol” is not a single mathematical problem until
we fix the environment, allowed state, packet grammar, failure assumptions and
objective. An unrestricted search over arbitrary programs is not an ILP.

The useful decomposition is:

1. **MILP/ILP oracles** for finite scheduling, airtime, channel and redundancy
   choices. These provide lower bounds and can prove that a requested guarantee
   is infeasible under stated physical constraints.
2. **SAT/SMT/CEGIS** for a bounded local transition grammar: a fixed number of
   state bits, packet fields and guarded actions, checked against exhaustive
   small executions. Counterexamples are added until the candidate passes or
   the grammar is exhausted.
3. **Model checking** for safety and liveness of the selected transition system.
4. **The radio simulator and hardware traces** for assumptions omitted by the
   formal abstraction: timing, collision, queue pressure and correlated loss.

## Current MILP oracles

`optimization_oracle.py` covers bounded beacon scheduling and minimum-byte state
dissemination. `reliability_oracle.py` adds a probabilistic cyclic schedule:

```text
minimize     total airtime/cost
subject to   every arbitrary contact phase contains enough complete
             opportunities that their independent failure product <= epsilon
             at most N attempts may use one slot/radio
```

Taking `-log()` converts the product constraint to a linear inequality. This
makes the assumptions explicit. For a five-second contact with one second
reserved for the reciprocal handshake:

- one independent opportunity with 10% failure needs four opportunities for a
  `1e-4` failure target;
- the four available one-second starts are therefore all required;
- a `1e-6` target needs six opportunities and is **infeasible** with a single
  radio and one-second slots;
- half-second slots provide eight starts and make six opportunities possible,
  at substantially higher airtime and collision pressure.

This explains why literal 100% cannot be promised under unbounded stochastic
loss. Lossless and bounded-erasure guarantees can be absolute; probabilistic
claims must state the loss model and confidence.

## Proposed CEGIS grammar

A small synthesis experiment should initially permit only:

- per-neighbour state: `heard`, incarnation, version, age bucket;
- per-destination state: generation, feasible distance, selected metric;
- packet kinds: digest, snapshot request, snapshot, generation request;
- actions: ignore, refresh liveness, request state, replace contribution,
  choose candidate, originate generation, forward repair;
- bounded timers and one of `{broadcast, reliable-unicast}`.

Safety properties:

- no selected next-hop cycle;
- no forwarding beyond the hop bound;
- partial snapshots never mutate routing state;
- a stale incarnation cannot replace a newer one.

Liveness assumptions and properties:

- topology eventually stops changing;
- every repeatedly attempted valid link eventually delivers;
- every reachable destination is eventually selected;
- every unreachable destination is eventually removed.

The synthesized result should be compared with the current v4 state machine, not
blindly deployed. The main value is finding counterexamples and proving which
mechanisms are indispensable.
