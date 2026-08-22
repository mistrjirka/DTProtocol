# Real C++ host backend

This backend executes the production `DTPK.cpp`, `CrystDatabase.cpp`, `lcmm.cpp`, and `mathextension.cpp` on a normal host compiler.

Only Arduino/MAC/RadioLib are replaced by a host shim. Each emulated device is a separate process because the production stack uses process-global singletons. Python owns the RF environment and therefore can independently change links, power state, loss, delays, and eventually mobility without asking the firmware state machine for permission.

## Build

```bash
cmake -S simulator/cpp -B simulator/cpp/build
cmake --build simulator/cpp/build
python -m pytest simulator/tests/test_cpp_backend.py -q -rxX
```

Sanitizers:

```bash
cmake -S simulator/cpp -B simulator/cpp/build-asan -DDTP_HOST_SANITIZERS=ON
cmake --build simulator/cpp/build-asan
DTP_CPP_NODE="$PWD/simulator/cpp/build-asan/dtprotocol_host_node" \
  python -m pytest simulator/tests/test_cpp_backend.py -q -rxX
```

The sanitizer job is currently non-gating because it is expected to expose memory-lifetime defects in the reference implementation. It should become mandatory for `protocol-v2`.

## Boundary

Currently real: DTPK, CrystDatabase, LCMM, MathExtension.

Currently simulated: MCU clock/RNG, MAC state/IRQ boundary, RadioLib/SX1262, propagation/collisions/environment.

The next step is to share mobility/collision models with the Python backend and add a RadioLib-level shim if we need to validate the MAC implementation itself rather than its API contract.
