from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from model import DTPK_NACK_HEADER, DTPK_DEFAULT_HOP_LIMIT, Packet, Profile
from simulator import Simulator


class FirstHopFailureSimulator(Simulator):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cut = False

    def transmit(
        self,
        sender_id,
        target,
        packet,
        reliable,
        on_complete,
        attempt=1,
    ):
        if (
            not self.cut
            and sender_id == 1
            and target == 2
            and packet.kind == "DATA"
        ):
            self.cut = True
            self.set_link(1, 2, False)
        return super().transmit(
            sender_id,
            target,
            packet,
            reliable,
            on_complete,
            attempt,
        )


class DownstreamFailureSimulator(Simulator):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cut = False

    def transmit(
        self,
        sender_id,
        target,
        packet,
        reliable,
        on_complete,
        attempt=1,
    ):
        if (
            not self.cut
            and sender_id == 2
            and target == 4
            and packet.kind == "DATA"
        ):
            self.cut = True
            self.set_link(2, 4, False)
        return super().transmit(
            sender_id,
            target,
            packet,
            reliable,
            on_complete,
            attempt,
        )


class LostFirstAckSimulator(Simulator):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ack_attempts_dropped = 0

    def transmit(
        self,
        sender_id,
        target,
        packet,
        reliable,
        on_complete,
        attempt=1,
    ):
        if (
            sender_id == 2
            and target == 1
            and packet.kind == "ACK"
            and self.ack_attempts_dropped < 5
        ):
            self.drop_next_frames(2, 1, 1)
            self.ack_attempts_dropped += 1
        return super().transmit(
            sender_id,
            target,
            packet,
            reliable,
            on_complete,
            attempt,
        )


def _diamond(sim: Simulator) -> None:
    for node_id in (1, 2, 3, 4):
        sim.add_node(node_id)
    for left, right in ((1, 2), (2, 4), (1, 3), (3, 4)):
        sim.add_link(left, right, latency_ms=0, jitter_ms=0)


def test_python_single_retry_recovers_first_hop_route_change():
    sim = FirstHopFailureSimulator(
        seed=82_001, profile=Profile.crystallized_v2()
    )
    _diamond(sim)
    sim.run(120_000)
    assert sim.nodes[1].routes[4].next_hop == 2

    packet_id = sim.nodes[1].send_data(
        4, payload_size=40, e2e_ack=True, timeout_ms=400_000
    )
    sim.run(620_000)

    assert packet_id is not None
    assert sim.cut
    assert sim.nodes[1].routes[4].next_hop == 3
    assert sim.metrics.delivered_app == 1
    assert sim.metrics.e2e_success == 1
    assert sim.metrics.e2e_failure == 0
    assert sim.nodes[1].waiting_e2e is None
    assert not sim.nodes[1].single_sends


def test_python_single_retry_recovers_downstream_transient_nack():
    sim = DownstreamFailureSimulator(
        seed=82_002, profile=Profile.crystallized_v2()
    )
    _diamond(sim)
    sim.run(120_000)
    assert sim.nodes[1].routes[4].next_hop == 2

    packet_id = sim.nodes[1].send_data(
        4, payload_size=40, e2e_ack=True, timeout_ms=400_000
    )
    sim.run(620_000)

    assert packet_id is not None
    assert sim.cut
    assert sim.nodes[1].routes[4].next_hop == 3
    assert sim.metrics.delivered_app == 1
    assert sim.metrics.e2e_success == 1
    assert sim.metrics.e2e_failure == 0
    assert any(
        event.get("event") == "e2e_nack_transient"
        and event.get("packet_id") == packet_id
        for event in sim.trace
    )


def test_python_lost_complete_ack_retries_same_identity_once_to_application():
    sim = LostFirstAckSimulator(
        seed=82_003, profile=Profile.crystallized_v2()
    )
    sim.add_node(1)
    sim.add_node(2)
    sim.add_link(1, 2, latency_ms=0, jitter_ms=0)
    sim.run(100_000)

    packet_id = sim.nodes[1].send_data(
        2, payload_size=40, e2e_ack=True, timeout_ms=180_000
    )
    sim.run(340_000)

    assert packet_id is not None
    assert sim.ack_attempts_dropped == 5
    assert sim.metrics.delivered_app == 1
    assert sim.metrics.duplicate_app >= 1
    assert sim.metrics.e2e_success == 1
    assert sim.metrics.e2e_failure == 0


def test_python_final_destination_nack_is_terminal_not_retryable():
    sim = Simulator(seed=82_004, profile=Profile.crystallized_v2())
    sim.add_node(1)
    sim.add_node(2)
    sim.add_link(1, 2, latency_ms=0, jitter_ms=0)
    sim.run(100_000)

    packet_id = sim.nodes[1].send_data(
        2, payload_size=40, e2e_ack=True, timeout_ms=180_000
    )
    # Run only until the source has installed its E2E waiter, before the real
    # destination response is likely to complete.
    deadline = sim.now + 20_000
    while sim.now < deadline and sim.nodes[1].waiting_e2e is None:
        sim.run(min(deadline, sim.now + 1))
    assert packet_id is not None
    assert sim.nodes[1].waiting_e2e is not None

    source_sequence = sim.nodes[1].waiting_e2e[1]
    terminal = Packet(
        "NACK",
        packet_id,
        original_sender=2,
        final_target=1,
        sender_sequence=source_sequence,
        wire_dtpk_size=DTPK_NACK_HEADER,
        hop_limit=DTPK_DEFAULT_HOP_LIMIT,
        nack_final_reject=True,
    )
    sim.nodes[1].receive_ack_local(terminal, False)

    assert sim.nodes[1].waiting_e2e is None
    assert not sim.nodes[1].single_sends
    assert sim.metrics.e2e_failure == 1
    assert sim.metrics.e2e_success == 0
