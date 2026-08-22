# Bounded optimization oracle

`optimization_oracle.py` uses mixed-integer linear programming to answer two
questions that are useful while designing DTProtocol:

1. What is the sparsest repeating discovery-beacon schedule that guarantees a
   required number of opportunities in every contact window?
2. What is the minimum-byte synchronous broadcast schedule that disseminates
   every node's state over a fixed small topology?

The oracle is intentionally separate from the firmware and normal simulator. It
is an exact solver for a **bounded model**, not a claim that an unbounded,
asynchronous distributed routing algorithm can be expressed as one practical
ILP.

## Installation

```bash
python -m pip install -r simulator/requirements-optimization.txt
```

PuLP includes a CBC binary on the supported host platforms used for development.

## Mobile discovery schedule

```bash
PYTHONPATH=simulator python simulator/optimization_oracle.py mobile \
  --horizon-slots 60 \
  --contact-slots 5 \
  --handshake-slots 1 \
  --loss-budget 0
```

The schedule repeats every `horizon-slots`. A contact may start at any phase.
The final `handshake-slots` of the contact are reserved for the reliable
request/response exchange, so a discovery beacon must occur in the earlier
window. `loss-budget=k` requires at least `k+1` opportunities in every such
window.

This gives a literal guarantee only under a bounded-erasure assumption. Under
independent random loss there is no finite schedule with mathematical 100%
success; additional opportunities instead reduce the residual probability.
For example, if a complete opportunity independently fails with probability
`p`, `m` independent opportunities leave probability `p**m` that all fail.

The model is useful for deciding whether a 1-second mobile discovery cadence is
actually necessary and how many repetitions are required to tolerate one or two
arbitrary failures during a short encounter.

## State-dissemination lower bound

```bash
PYTHONPATH=simulator python simulator/optimization_oracle.py dissemination \
  --topology line \
  --nodes 5 \
  --rounds 8 \
  --channel-model global
```

Each node initially knows only its own state. A broadcast carries the sender's
identity in a fixed header plus one route record for every other state currently
known by the sender. The objective minimizes modeled transmitted bytes.

Channel models:

- `none`: simultaneous transmissions are allowed, but nodes remain half-duplex;
- `local`: at most one neighbour of any receiver transmits in a round;
- `global`: at most one transmitter exists anywhere in a round.

The result is an optimistic lower bound because the solver has global topology
knowledge, synchronous rounds, no packet loss, no request handshake, and no
extra chunk headers. Comparing real DTProtocol traces to this bound can still
show whether overhead is structural or merely an implementation/policy choice.

## Why not directly synthesize “the optimal protocol” with ILP?

An actual protocol must work for arbitrary node counts, asynchronous message
ordering, unknown topology, reboot, wraparound, loss, collision and bounded
local memory. Direct synthesis becomes finite only after bounding at least:

- number of nodes and topology family;
- local state bits;
- packet alphabet and packet size;
- execution horizon;
- loss/failure scenarios;
- fairness assumptions.

At that point one can encode local transition-table choices and correctness over
all bounded executions. SMT/SAT is generally a better fit than MILP because
protocol synthesis is dominated by Boolean transition logic and temporal
properties. Relevant directions include automated synthesis of distributed
self-stabilizing protocols and cutoff-based verification of parameterized
protocols.

The practical staged approach for DTProtocol is therefore:

1. use MILP to obtain schedule/airtime lower bounds for concrete scenarios;
2. use exhaustive simulation and model checking to discover counterexamples;
3. use bounded SMT synthesis for tiny local state machines or individual
   mechanisms;
4. infer a simple scalable rule from the bounded optima;
5. validate that rule on much larger random, adversarial and real-C++ scenarios.

This avoids asking one enormous solver instance to discover topology discovery,
loop prevention, reliable transport and failure detection simultaneously.
