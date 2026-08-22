from __future__ import annotations

import heapq
import math
import os
import pathlib
import random
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

MAC_OVERHEAD = 8


def lora_airtime_ms(payload_bytes: int, sf: int = 9, bandwidth_hz: int = 125_000,
                    coding_rate_denominator: int = 7) -> float:
    de = 1 if sf >= 11 and bandwidth_hz == 125_000 else 0
    tsym = (2 ** sf) / bandwidth_hz
    cr = max(1, coding_rate_denominator - 4)
    numerator = 8 * max(0, payload_bytes) - 4 * sf + 28 + 16
    denominator = 4 * (sf - 2 * de)
    payload_symbols = 8 + max(math.ceil(numerator / denominator) * (cr + 4), 0)
    return (8 + 4.25 + payload_symbols) * tsym * 1000.0


@dataclass
class Tx:
    token: int
    target: int
    payload: bytes


class CppNodeProcess:
    def __init__(self, node_id: int, *, seed: int = 1, k_limit: int = 20,
                 binary: Optional[str] = None):
        self.node_id = node_id
        self.seed = seed
        self.k_limit = k_limit
        self.events: List[Tuple[str, Tuple]] = []
        self.binary = binary or self.default_binary()
        self.proc = subprocess.Popen(
            [self.binary], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1,
        )
        ready = self.proc.stdout.readline().strip()
        if ready != "READY":
            raise RuntimeError(f"host node failed to start: {ready!r}")
        self.command(f"INIT {node_id} {k_limit} {seed}")

    @staticmethod
    def default_binary() -> str:
        root = pathlib.Path(__file__).resolve().parents[1]
        candidates = [
            root / "simulator" / "cpp" / "build" / "dtprotocol_host_node",
            root / "build" / "dtprotocol_host_node",
        ]
        override = os.environ.get("DTP_CPP_NODE")
        if override:
            return override
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)
        return str(candidates[0])

    @classmethod
    def available(cls) -> bool:
        return pathlib.Path(cls.default_binary()).exists()

    def command(self, line: str) -> List[Tx]:
        if self.proc.poll() is not None:
            stderr = self.proc.stderr.read()
            raise RuntimeError(f"C++ node {self.node_id} exited {self.proc.returncode}: {stderr}")
        assert self.proc.stdin and self.proc.stdout
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()
        txs: List[Tx] = []
        while True:
            raw = self.proc.stdout.readline()
            if raw == "":
                stderr = self.proc.stderr.read() if self.proc.stderr else ""
                raise RuntimeError(f"C++ node {self.node_id} closed stdout: {stderr}")
            parts = raw.strip().split()
            if not parts:
                continue
            if parts[0] == "DONE":
                return txs
            if parts[0] == "TX":
                payload = b"" if parts[3] == "-" else bytes.fromhex(parts[3])
                txs.append(Tx(int(parts[1]), int(parts[2]), payload))
            elif parts[0] == "APP_RX":
                self.events.append(("APP_RX", tuple(parts[1:])))
            elif parts[0] == "APP_ACK":
                self.events.append(("APP_ACK", (int(parts[1]), int(parts[2]))))
            elif parts[0] == "ERR":
                raise RuntimeError("host runner error: " + " ".join(parts[1:]))
            else:
                self.events.append((parts[0], tuple(parts[1:])))

    def tick(self, now_ms: float) -> List[Tx]:
        return self.command(f"TICK {int(now_ms)}")

    def inject(self, now_ms: float, sender: int, target: int, payload: bytes) -> List[Tx]:
        data = payload.hex() if payload else "-"
        return self.command(f"INJECT {int(now_ms)} {sender} {target} {data}")

    def phy_done(self, now_ms: float, token: int) -> List[Tx]:
        return self.command(f"PHYDONE {int(now_ms)} {token}")

    def send(self, now_ms: float, target: int, payload: bytes = b"x",
             timeout_ms: int = 10000, e2e_ack: bool = True) -> int:
        before = len(self.events)
        data = payload.hex() if payload else "-"
        self.command(f"SEND {int(now_ms)} {target} {timeout_ms} {1 if e2e_ack else 0} {data}")
        for kind, values in reversed(self.events[before:]):
            if kind == "SENDID":
                return int(values[0])
        # SENDID is parsed as a generic event.
        for kind, values in reversed(self.events):
            if kind == "SENDID":
                return int(values[0])
        return 0

    def routes(self, now_ms: float) -> Dict[int, Tuple[int, int]]:
        before = len(self.events)
        self.command(f"ROUTES {int(now_ms)}")
        for kind, values in reversed(self.events[before:]):
            if kind != "ROUTES":
                continue
            result: Dict[int, Tuple[int, int]] = {}
            for value in values[1:]:  # first value is count
                dest, via, distance = value.split(":")
                result[int(dest)] = (int(via), int(distance))
            return result
        return {}

    def close(self) -> None:
        if self.proc.poll() is None:
            try:
                self.command("QUIT")
            except Exception:
                pass
            self.proc.terminate()
            try:
                self.proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.proc.kill()


@dataclass
class CppLink:
    a: int
    b: int
    loss: float = 0.0
    latency_ms: float = 5.0
    up: bool = True
    epoch: int = 0

    def other(self, node: int) -> int:
        return self.b if node == self.a else self.a


class CppNetwork:
    """RF/environment scheduler around one real DTProtocol process per node."""

    def __init__(self, seed: int = 1, *, binary: Optional[str] = None,
                 tick_ms: float = 50.0):
        self.env_rng = random.Random(seed)
        self.seed = seed
        self.binary = binary
        self.tick_ms = tick_ms
        self.now = 0.0
        self.nodes: Dict[int, CppNodeProcess] = {}
        self.node_epoch: Dict[int, int] = {}
        self.node_up: Dict[int, bool] = {}
        self.links: Dict[frozenset[int], CppLink] = {}
        self.drop_next: Dict[Tuple[int, int], int] = {}
        self._events = []
        self._seq = 0
        self._ticks_scheduled_until = 0.0

    def _schedule(self, when: float, priority: int, fn, *args) -> None:
        self._seq += 1
        heapq.heappush(self._events, (float(when), priority, self._seq, fn, args))

    def add_node(self, node_id: int, *, seed: Optional[int] = None, k_limit: int = 20) -> None:
        self.nodes[node_id] = CppNodeProcess(
            node_id, seed=self.seed * 1009 + node_id if seed is None else seed,
            k_limit=k_limit, binary=self.binary,
        )
        self.node_epoch[node_id] = 0
        self.node_up[node_id] = True

    def add_link(self, a: int, b: int, *, loss: float = 0.0,
                 latency_ms: float = 5.0, up: bool = True) -> None:
        self.links[frozenset((a, b))] = CppLink(a, b, loss, latency_ms, up)

    def _link(self, a: int, b: int) -> Optional[CppLink]:
        return self.links.get(frozenset((a, b)))

    def set_link_at(self, when: float, a: int, b: int, up: bool) -> None:
        def change():
            link = self._link(a, b)
            if link:
                link.up = up
                link.epoch += 1
        self._schedule(when, 0, change)

    def drop_next_frames(self, sender: int, receiver: int, count: int = 1) -> None:
        self.drop_next[(sender, receiver)] = self.drop_next.get((sender, receiver), 0) + count

    def fail_node_at(self, when: float, node_id: int) -> None:
        def fail():
            self.node_up[node_id] = False
            self.node_epoch[node_id] += 1
            self.nodes[node_id].close()
        self._schedule(when, 0, fail)

    def recover_node_at(self, when: float, node_id: int, *, k_limit: int = 20) -> None:
        def recover():
            self.nodes[node_id] = CppNodeProcess(
                node_id, seed=self.seed * 1009 + node_id + self.node_epoch[node_id],
                k_limit=k_limit, binary=self.binary,
            )
            self.node_epoch[node_id] += 1
            self.node_up[node_id] = True
        self._schedule(when, 0, recover)

    def _consume_drop(self, a: int, b: int) -> bool:
        key = (a, b)
        if self.drop_next.get(key, 0) <= 0:
            return False
        self.drop_next[key] -= 1
        return True

    def _handle_txs(self, sender: int, txs: List[Tx], at: float) -> None:
        for tx in txs:
            self._start_tx(sender, tx, at)

    def _start_tx(self, sender: int, tx: Tx, at: float) -> None:
        if not self.node_up.get(sender, False):
            return
        sender_epoch = self.node_epoch[sender]
        airtime = lora_airtime_ms(MAC_OVERHEAD + len(tx.payload))

        # PHY completion is independent of whether any receiver heard the frame.
        self._schedule(at + airtime, 10, self._phy_done, sender, sender_epoch, tx.token)

        receivers: List[int]
        if tx.target == 0:
            receivers = [link.other(sender) for link in self.links.values()
                         if sender in (link.a, link.b)]
        else:
            receivers = [tx.target]

        for receiver in receivers:
            link = self._link(sender, receiver)
            if not link or not link.up or not self.node_up.get(receiver, False):
                continue
            link_epoch = link.epoch
            receiver_epoch = self.node_epoch[receiver]
            lost = self._consume_drop(sender, receiver) or self.env_rng.random() < link.loss
            if lost:
                continue
            wire_target = 0 if tx.target == 0 else receiver
            self._schedule(at + airtime + link.latency_ms, 10, self._deliver,
                           sender, sender_epoch, receiver, receiver_epoch,
                           wire_target, link_epoch, tx.payload)

    def _phy_done(self, sender: int, sender_epoch: int, token: int) -> None:
        if not self.node_up.get(sender, False) or self.node_epoch[sender] != sender_epoch:
            return
        txs = self.nodes[sender].phy_done(self.now, token)
        self._handle_txs(sender, txs, self.now)

    def _deliver(self, sender: int, sender_epoch: int, receiver: int,
                 receiver_epoch: int, wire_target: int, link_epoch: int,
                 payload: bytes) -> None:
        link = self._link(sender, receiver)
        if (not link or not link.up or link.epoch != link_epoch or
                not self.node_up.get(sender, False) or self.node_epoch[sender] != sender_epoch or
                not self.node_up.get(receiver, False) or self.node_epoch[receiver] != receiver_epoch):
            return
        txs = self.nodes[receiver].inject(self.now, sender, wire_target, payload)
        self._handle_txs(receiver, txs, self.now)

    def _tick_all(self) -> None:
        for node_id in sorted(self.nodes):
            if not self.node_up.get(node_id, False):
                continue
            txs = self.nodes[node_id].tick(self.now)
            self._handle_txs(node_id, txs, self.now)

    def run(self, until_ms: float) -> None:
        t = self._ticks_scheduled_until
        if t < self.now:
            t = self.now
        while t + self.tick_ms <= until_ms:
            t += self.tick_ms
            self._schedule(t, 20, self._tick_all)
        self._ticks_scheduled_until = max(self._ticks_scheduled_until, t)

        while self._events and self._events[0][0] <= until_ms:
            when, _, _, fn, args = heapq.heappop(self._events)
            self.now = when
            fn(*args)
        self.now = float(until_ms)

    def routes(self, node_id: int) -> Dict[int, Tuple[int, int]]:
        if not self.node_up.get(node_id, False):
            return {}
        return self.nodes[node_id].routes(self.now)

    def send(self, node_id: int, target: int, payload: bytes = b"hello",
             timeout_ms: int = 10000, e2e_ack: bool = True) -> int:
        if not self.node_up.get(node_id, False):
            return 0
        return self.nodes[node_id].send(self.now, target, payload, timeout_ms, e2e_ack)

    def app_acks(self, node_id: int) -> List[Tuple[int, int]]:
        return [values for kind, values in self.nodes[node_id].events if kind == "APP_ACK"]

    def close(self) -> None:
        for node in self.nodes.values():
            node.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
