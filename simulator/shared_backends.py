from __future__ import annotations

import hashlib
import random
from collections import defaultdict
from typing import DefaultDict, Tuple

from cpp_sim_adapter import CppSimNetwork
from simulator import Simulator


class KeyedEnvironmentMixin:
    """Environment randomness keyed to physical events, not call order.

    A sequential PRNG makes differential tests fragile: if one protocol backend
    emits one extra frame, every subsequent loss/jitter draw shifts even when
    later physical events are otherwise identical.  This mixin derives a small
    deterministic random stream from the environment seed and a physical event
    identity instead.

    The final per-key ordinal only disambiguates repeated operations at the same
    timestamp on the same directed link. It does not couple independent links or
    namespaces together.
    """

    def _init_keyed_environment(self) -> None:
        self._environment_ordinals: DefaultDict[Tuple, int] = defaultdict(int)

    def _keyed_rng(self, namespace: str, *parts) -> random.Random:
        key = (namespace, *parts)
        ordinal = self._environment_ordinals[key]
        self._environment_ordinals[key] += 1
        encoded = "|".join(
            [str(self.seed), namespace, *(str(part) for part in parts), str(ordinal)]
        ).encode("utf-8")
        digest = hashlib.blake2b(encoded, digest_size=16).digest()
        return random.Random(int.from_bytes(digest, "big"))

    @staticmethod
    def _time_key(value: float) -> int:
        # Microsecond resolution is far below LoRa airtime while avoiding
        # backend-dependent float string representations.
        return int(round(float(value) * 1000.0))

    def jittered_latency(self, link) -> float:
        if link.jitter_ms <= 0:
            return link.latency_ms
        a, b = sorted((link.a, link.b))
        rng = self._keyed_rng(
            "latency",
            a,
            b,
            self._time_key(self.now),
        )
        return max(0.0, rng.gauss(link.latency_ms, link.jitter_ms))

    def sample_link_loss(self, sender: int, receiver: int, *, ack: bool = False) -> bool:
        link = self.get_link(sender, receiver)
        if link is None:
            return True
        if self._consume_drop(sender, receiver):
            return True
        probability = link.ack_loss if ack and link.ack_loss is not None else link.loss
        rng = self._keyed_rng(
            "ack-loss" if ack else "data-loss",
            sender,
            receiver,
            self._time_key(self.now),
        )
        return rng.random() < probability


class SharedPythonNetwork(KeyedEnvironmentMixin, Simulator):
    """Theoretical Python protocol on the common keyed environment."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._init_keyed_environment()


class SharedCppNetwork(KeyedEnvironmentMixin, CppSimNetwork):
    """Real C++ protocol subprocesses on the same keyed environment semantics."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._init_keyed_environment()


__all__ = ["SharedPythonNetwork", "SharedCppNetwork", "KeyedEnvironmentMixin"]
