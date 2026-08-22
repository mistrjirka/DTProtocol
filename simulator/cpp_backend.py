from __future__ import annotations

import os
import pathlib
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from environment import EnvironmentKernel, MAC_OVERHEAD


def lora_airtime_ms(
    payload_bytes: int,
    sf: int = 9,
    bandwidth_hz: int = 125_000,
    coding_rate_denominator: int = 7,
) -> float:
    """Compatibility wrapper around the shared RF airtime implementation."""
    env = EnvironmentKernel(
        0,
        sf=sf,
        bandwidth_hz=bandwidth_hz,
        coding_rate_denominator=coding_rate_denominator,
    )
    return env.airtime_ms(payload_bytes)


@dataclass
class Tx:
    token: int
    target: int
    payload: bytes


class CppNodeProcess:
    """One real DTProtocol firmware instance in one host process.

    Production DTPK/MAC/LCMM are singleton-heavy, so separate processes are the
    closest host analogue to physically separate ESP32/Pico devices and give us
    independent globals, heap state, RNG and reboot lifetime.
    """

    def __init__(
        self,
        node_id: int,
        *,
        seed: int = 1,
        k_limit: int = 20,
        binary: Optional[str] = None,
    ):
        self.node_id = node_id
        self.seed = seed
        self.k_limit = k_limit
        self.events: List[Tuple[str, Tuple]] = []
        self.binary = binary or self.default_binary()
        self.proc = subprocess.Popen(
            [self.binary],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        assert self.proc.stdout
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
            stderr = self.proc.stderr.read() if self.proc.stderr else ""
            raise RuntimeError(
                f"C++ node {self.node_id} exited {self.proc.returncode}: {stderr}"
            )
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

    def inject(
        self,
        now_ms: float,
        sender: int,
        target: int,
        payload: bytes,
    ) -> List[Tx]:
        data = payload.hex() if payload else "-"
        return self.command(f"INJECT {int(now_ms)} {sender} {target} {data}")

    def phy_done(self, now_ms: float, token: int) -> List[Tx]:
        return self.command(f"PHYDONE {int(now_ms)} {token}")

    def send(
        self,
        now_ms: float,
        target: int,
        payload: bytes = b"x",
        timeout_ms: int = 10000,
        e2e_ack: bool = True,
    ) -> int:
        before = len(self.events)
        data = payload.hex() if payload else "-"
        self.command(
            f"SEND {int(now_ms)} {target} {timeout_ms} {1 if e2e_ack else 0} {data}"
        )
        for kind, values in reversed(self.events[before:]):
            if kind == "SENDID":
                return int(values[0])
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
            for value in values[1:]:
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


class CppNetwork(EnvironmentKernel):
    """Real-C++ protocol adapter on the shared RF/environment simulator."""

    def __init__(
        self,
        seed: int = 1,
        *,
        binary: Optional[str] = None,
        tick_ms: float = 50.0,
        sf: int = 9,
        bandwidth_hz: int = 125_000,
        coding_rate_denominator: int = 7,
    ):
        super().__init__(
            seed,
            sf=sf,
            bandwidth_hz=bandwidth_hz,
            coding_rate_denominator=coding_rate_denominator,
        )
        self.binary = binary
        self.tick_ms = float(tick_ms)
        self.nodes: Dict[int, CppNodeProcess] = {}
        self._node_config: Dict[int, Tuple[int, int]] = {}
        # Absolute environment time at which each emulated MCU last booted.
        # The environment has one monotonic world clock, while Arduino millis()
        # restarts at zero after every real reset/reboot.
        self._boot_world_ms: Dict[int, float] = {}
        self._ticks_scheduled_until = 0.0

    def _local_time(self, node_id: int) -> float:
        """Translate absolute simulation time to this MCU's boot-relative millis."""
        return max(0.0, self.now - self._boot_world_ms.get(node_id, self.now))

    def add_node(
        self,
        node_id: int,
        *,
        seed: Optional[int] = None,
        k_limit: int = 20,
        position: Tuple[float, float] = (0.0, 0.0),
    ) -> None:
        actual_seed = self.seed * 1009 + node_id if seed is None else seed
        self._node_config[node_id] = (actual_seed, k_limit)
        self.nodes[node_id] = CppNodeProcess(
            node_id,
            seed=actual_seed,
            k_limit=k_limit,
            binary=self.binary,
        )
        self._boot_world_ms[node_id] = self.now
        self.register_node(node_id, up=True, position=position)

    def add_link(
        self,
        a: int,
        b: int,
        *,
        loss: float = 0.0,
        ack_loss: Optional[float] = None,
        latency_ms: float = 5.0,
        jitter_ms: float = 0.0,
        up: bool = True,
        max_range: Optional[float] = None,
    ) -> None:
        super().add_link(
            a,
            b,
            loss=loss,
            ack_loss=ack_loss,
            latency_ms=latency_ms,
            jitter_ms=jitter_ms,
            up=up,
            max_range=max_range,
        )

    def _on_environment_node_down(self, node_id: int) -> None:
        node = self.nodes.get(node_id)
        if node:
            node.close()

    def _on_environment_node_up(self, node_id: int) -> None:
        config = self._node_config.get(node_id)
        if config is None:
            return
        base_seed, k_limit = config
        # Every reboot gets a deterministic but different firmware seed while
        # environment randomness remains unchanged.
        reboot_seed = base_seed + self.node_epoch.get(node_id, 0)
        self.nodes[node_id] = CppNodeProcess(
            node_id,
            seed=reboot_seed,
            k_limit=k_limit,
            binary=self.binary,
        )
        self._boot_world_ms[node_id] = self.now

    def _handle_txs(self, sender: int, txs: List[Tx], at: float) -> None:
        for tx in txs:
            self._start_tx(sender, tx, at)

    def _start_tx(self, sender: int, tx: Tx, at: float) -> None:
        if not self.node_up.get(sender, False):
            return

        self.rf_metrics.tx_frames += 1
        sender_epoch = self.node_epoch.get(sender, 0)
        airtime = self.airtime_ms(MAC_OVERHEAD + len(tx.payload))
        rf_end = at + airtime

        # TX-complete IRQ belongs to the transmitter even if no receiver hears it.
        self.schedule_at(
            rf_end,
            self._phy_done,
            sender,
            sender_epoch,
            tx.token,
            priority=self.RADIO_PRIORITY,
        )

        receivers = self.linked_nodes(sender) if tx.target == 0 else [tx.target]
        for receiver in receivers:
            self.rf_metrics.rf_receivers_considered += 1
            if not self.frame_start_valid(sender, receiver):
                continue

            link = self.get_link(sender, receiver)
            assert link is not None
            sender_ep, receiver_ep, link_ep = self.capture_frame_epochs(sender, receiver)
            if self.sample_link_loss(sender, receiver):
                self.rf_metrics.rf_loss_drops += 1
                continue

            wire_target = 0 if tx.target == 0 else receiver
            self.schedule_at(
                rf_end,
                self._rf_complete,
                sender,
                sender_ep,
                receiver,
                receiver_ep,
                wire_target,
                link_ep,
                at,
                rf_end,
                self.jittered_latency(link),
                tx.payload,
                priority=self.RADIO_PRIORITY,
            )

    def _rf_complete(
        self,
        sender: int,
        sender_epoch: int,
        receiver: int,
        receiver_epoch: int,
        wire_target: int,
        link_epoch: int,
        rf_start: float,
        rf_end: float,
        latency_ms: float,
        payload: bytes,
    ) -> None:
        valid, reason = self.frame_path_valid(
            sender,
            receiver,
            rf_start,
            rf_end,
            sender_epoch,
            receiver_epoch,
            link_epoch,
        )
        if not valid:
            if reason == "range":
                self.rf_metrics.rf_range_drops += 1
            else:
                self.rf_metrics.rf_epoch_drops += 1
            return

        self.schedule(
            latency_ms,
            self._firmware_deliver,
            sender,
            receiver,
            receiver_epoch,
            wire_target,
            payload,
            priority=self.RADIO_PRIORITY,
        )

    def _firmware_deliver(
        self,
        sender: int,
        receiver: int,
        receiver_epoch: int,
        wire_target: int,
        payload: bytes,
    ) -> None:
        if (
            not self.node_up.get(receiver, False)
            or self.node_epoch.get(receiver, 0) != receiver_epoch
        ):
            self.rf_metrics.firmware_epoch_drops += 1
            return
        txs = self.nodes[receiver].inject(
            self._local_time(receiver), sender, wire_target, payload
        )
        self.rf_metrics.rf_delivered += 1
        self._handle_txs(receiver, txs, self.now)

    def _phy_done(self, sender: int, sender_epoch: int, token: int) -> None:
        if (
            not self.node_up.get(sender, False)
            or self.node_epoch.get(sender, 0) != sender_epoch
        ):
            return
        txs = self.nodes[sender].phy_done(self._local_time(sender), token)
        self._handle_txs(sender, txs, self.now)

    def _tick_all(self) -> None:
        for node_id in sorted(self.nodes):
            if not self.node_up.get(node_id, False):
                continue
            txs = self.nodes[node_id].tick(self._local_time(node_id))
            self._handle_txs(node_id, txs, self.now)

    def run(self, until_ms: float) -> None:
        until = float(until_ms)
        t = max(self._ticks_scheduled_until, self.now)
        while t + self.tick_ms <= until:
            t += self.tick_ms
            self.schedule_at(
                t,
                self._tick_all,
                priority=self.PROTOCOL_PRIORITY,
            )
        self._ticks_scheduled_until = max(self._ticks_scheduled_until, t)
        self.run_events(until)

    def routes(self, node_id: int) -> Dict[int, Tuple[int, int]]:
        if not self.node_up.get(node_id, False):
            return {}
        return self.nodes[node_id].routes(self._local_time(node_id))

    def send(
        self,
        node_id: int,
        target: int,
        payload: bytes = b"hello",
        timeout_ms: int = 10000,
        e2e_ack: bool = True,
    ) -> int:
        if not self.node_up.get(node_id, False):
            return 0
        return self.nodes[node_id].send(
            self._local_time(node_id),
            target,
            payload,
            timeout_ms,
            e2e_ack,
        )

    def app_acks(self, node_id: int) -> List[Tuple[int, int]]:
        node = self.nodes.get(node_id)
        if node is None:
            return []
        return [values for kind, values in node.events if kind == "APP_ACK"]

    def close(self) -> None:
        for node in self.nodes.values():
            node.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()