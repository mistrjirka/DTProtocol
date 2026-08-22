from __future__ import annotations

from typing import Dict, Tuple

from cpp_backend import CppNetwork


class CppSimNetwork(CppNetwork):
    """C++ protocol adapter with MCU-local clocks.

    The environment uses global simulation time, but Arduino ``millis()`` starts
    again from zero after a reboot. Each subprocess therefore receives local
    time = global time - that node's boot epoch.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.boot_time_ms: Dict[int, float] = {}

    def _local_time(self, node_id: int) -> float:
        return max(0.0, self.now - self.boot_time_ms.get(node_id, self.now))

    def add_node(self, node_id: int, **kwargs) -> None:
        super().add_node(node_id, **kwargs)
        self.boot_time_ms[node_id] = self.now

    def _on_environment_node_up(self, node_id: int) -> None:
        super()._on_environment_node_up(node_id)
        self.boot_time_ms[node_id] = self.now

    def _tick_all(self) -> None:
        for node_id in sorted(self.nodes):
            if not self.node_up.get(node_id, False):
                continue
            txs = self.nodes[node_id].tick(self._local_time(node_id))
            self._handle_txs(node_id, txs, self.now)

    def _firmware_deliver(self, sender: int, receiver: int, receiver_epoch: int,
                          wire_target: int, payload: bytes) -> None:
        if (not self.node_up.get(receiver, False) or
                self.node_epoch.get(receiver, 0) != receiver_epoch):
            self.rf_metrics.firmware_epoch_drops += 1
            return
        txs = self.nodes[receiver].inject(
            self._local_time(receiver), sender, wire_target, payload
        )
        self.rf_metrics.rf_delivered += 1
        self._handle_txs(receiver, txs, self.now)

    def _phy_done(self, sender: int, sender_epoch: int, token: int) -> None:
        if (not self.node_up.get(sender, False) or
                self.node_epoch.get(sender, 0) != sender_epoch):
            return
        txs = self.nodes[sender].phy_done(self._local_time(sender), token)
        self._handle_txs(sender, txs, self.now)

    def routes(self, node_id: int) -> Dict[int, Tuple[int, int]]:
        if not self.node_up.get(node_id, False):
            return {}
        return self.nodes[node_id].routes(self._local_time(node_id))

    def send(self, node_id: int, target: int, payload: bytes = b"hello",
             timeout_ms: int = 10000, e2e_ack: bool = True) -> int:
        if not self.node_up.get(node_id, False):
            return 0
        packet_id, txs = self.nodes[node_id].send_and_collect(
            self._local_time(node_id), target, payload, timeout_ms, e2e_ack
        )
        self._handle_txs(node_id, txs, self.now)
        return packet_id
