from __future__ import annotations

import math
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
    """One real DTProtocol firmware instance in one host process."""

    def __init__(
        self,
        node_id: int,
        *,
        seed: int = 1,
        k_limit: int = 20,
        origin_sequence: int = 1,
        duty_cycle_percent: float = 0.0,
        initial_duty_wait_ms: float = 0.0,
        mobile_hint: bool = False,
        binary: Optional[str] = None,
    ):
        self.node_id = node_id
        self.seed = seed
        self.k_limit = k_limit
        self.origin_sequence = int(origin_sequence) & 0xFFFF or 1
        self.duty_cycle_percent = float(duty_cycle_percent)
        self.mobile_hint = bool(mobile_hint)
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
        duty = max(0.0, min(100.0, self.duty_cycle_percent))
        initial_wait = max(0, int(math.ceil(initial_duty_wait_ms)))
        self.command(
            f"INIT {node_id} {k_limit} {seed} {self.origin_sequence} "
            f"{duty:.9f} {initial_wait} {1 if self.mobile_hint else 0}"
        )

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

    def send_and_collect(
        self,
        now_ms: float,
        target: int,
        payload: bytes = b"x",
        timeout_ms: int = 10000,
        e2e_ack: bool = True,
    ) -> Tuple[int, List[Tx]]:
        """Start an application send and return RF work emitted in that turn.

        The host runner services ``DTPK::loop()`` immediately after SEND, just
        like application code returning to the MCU main loop.  That can make
        the fake MAC emit a TX before the next periodic TICK, so the adapter
        must not discard the command's TX records.
        """
        before = len(self.events)
        data = payload.hex() if payload else "-"
        txs = self.command(
            f"SEND {int(now_ms)} {target} {timeout_ms} {1 if e2e_ack else 0} {data}"
        )
        for kind, values in reversed(self.events[before:]):
            if kind == "SENDID":
                return int(values[0]), txs
        for kind, values in reversed(self.events):
            if kind == "SENDID":
                return int(values[0]), txs
        return 0, txs

    def send(
        self,
        now_ms: float,
        target: int,
        payload: bytes = b"x",
        timeout_ms: int = 10000,
        e2e_ack: bool = True,
    ) -> int:
        packet_id, _txs = self.send_and_collect(
            now_ms, target, payload, timeout_ms, e2e_ack
        )
        return packet_id

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
        duty_cycle_percent: float = 0.0,
    ):
        super().__init__(
            seed,
            sf=sf,
            bandwidth_hz=bandwidth_hz,
            coding_rate_denominator=coding_rate_denominator,
            duty_cycle_percent=duty_cycle_percent,
        )
        self.binary = binary
        self.tick_ms = float(tick_ms)
        self.nodes: Dict[int, CppNodeProcess] = {}
        self._node_config: Dict[int, Tuple[int, int, bool]] = {}
        self._node_origin_sequence: Dict[int, int] = {}
        self._ticks_scheduled_until = 0.0

    @staticmethod
    def _initial_origin_sequence(seed: int, node_id: int) -> int:
        value = (int(seed) ^ (int(node_id) * 0x9E37)) & 0xFFFF
        return value or 1

    @staticmethod
    def _next_origin_sequence(value: int) -> int:
        value = (int(value) + 1) & 0xFFFF
        return value or 1

    def add_node(
        self,
        node_id: int,
        *,
        seed: Optional[int] = None,
        k_limit: int = 20,
        position: Tuple[float, float] = (0.0, 0.0),
        mobile_hint: bool = False,
    ) -> None:
        actual_seed = self.seed * 1009 + node_id if seed is None else seed
        self._node_config[node_id] = (actual_seed, k_limit, bool(mobile_hint))
        origin = self._initial_origin_sequence(actual_seed, node_id)
        self._node_origin_sequence[node_id] = origin
        self.register_node(node_id, up=True, position=position)
        self.nodes[node_id] = CppNodeProcess(
            node_id,
            seed=actual_seed,
            k_limit=k_limit,
            origin_sequence=origin,
            duty_cycle_percent=self.duty_cycle_percent,
            initial_duty_wait_ms=self.transmit_wait_ms(node_id),
            mobile_hint=mobile_hint,
            binary=self.binary,
        )

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
        interference_range: Optional[float] = None,
        cca_range: Optional[float] = None,
        burst_bad_loss: Optional[float] = None,
        burst_good_to_bad: float = 0.0,
        burst_bad_to_good: float = 1.0,
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
            interference_range=interference_range,
            cca_range=cca_range,
            burst_bad_loss=burst_bad_loss,
            burst_good_to_bad=burst_good_to_bad,
            burst_bad_to_good=burst_bad_to_good,
        )

    def _on_environment_node_down(self, node_id: int) -> None:
        node = self.nodes.get(node_id)
        if node:
            node.close()

    def _on_environment_node_up(self, node_id: int) -> None:
        config = self._node_config.get(node_id)
        if config is None:
            return
        base_seed, k_limit, mobile_hint = config
        reboot_seed = base_seed + self.node_epoch.get(node_id, 0)
        origin = self._next_origin_sequence(
            self._node_origin_sequence.get(node_id, 1)
        )
        self._node_origin_sequence[node_id] = origin
        self.nodes[node_id] = CppNodeProcess(
            node_id,
            seed=reboot_seed,
            k_limit=k_limit,
            origin_sequence=origin,
            duty_cycle_percent=self.duty_cycle_percent,
            # Regulatory off-time belongs to the RF history and must not be
            # erased merely because the emulated MCU rebooted.
            initial_duty_wait_ms=self.transmit_wait_ms(node_id),
            mobile_hint=mobile_hint,
            binary=self.binary,
        )

    def _handle_txs(self, sender: int, txs: List[Tx], at: float) -> None:
        for tx in txs:
            self._start_tx(sender, tx, at)

    def _start_tx(self, sender: int, tx: Tx, at: float) -> None:
        if not self.node_up.get(sender, False):
            return

        # The host fake MAC is configured with the same duty policy and should
        # therefore never emit a TX early. Keep this as a safety net against
        # adapter/numerical drift; it protects the physical trace without
        # consuming another firmware attempt.
        wait = self.transmit_wait_ms(sender, at)
        if wait > 1e-6:
            self.note_regulatory_deferral(sender)
            self.schedule_at(
                at + wait,
                self._start_tx,
                sender,
                tx,
                at + wait,
                priority=self.RADIO_PRIORITY,
            )
            return

        self.rf_metrics.tx_frames += 1
        sender_epoch = self.node_epoch.get(sender, 0)
        airtime = self.airtime_ms(MAC_OVERHEAD + len(tx.payload))
        rf_end = at + airtime
        self.account_transmission(sender, at, rf_end)

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
        txs = self.nodes[receiver].inject(self.now, sender, wire_target, payload)
        self.rf_metrics.rf_delivered += 1
        self._handle_txs(receiver, txs, self.now)

    def _phy_done(self, sender: int, sender_epoch: int, token: int) -> None:
        if (
            not self.node_up.get(sender, False)
            or self.node_epoch.get(sender, 0) != sender_epoch
        ):
            return
        txs = self.nodes[sender].phy_done(self.now, token)
        self._handle_txs(sender, txs, self.now)

    def _tick_all(self) -> None:
        for node_id in sorted(self.nodes):
            if not self.node_up.get(node_id, False):
                continue
            txs = self.nodes[node_id].tick(self.now)
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
        return self.nodes[node_id].routes(self.now)

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
        packet_id, txs = self.nodes[node_id].send_and_collect(
            self.now,
            target,
            payload,
            timeout_ms,
            e2e_ack,
        )
        self._handle_txs(node_id, txs, self.now)
        return packet_id

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
