import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from model import (
    DTPK_DEFAULT_HOP_LIMIT,
    DTPK_FRAGMENT_HEADER,
    DTPK_FRAGMENT_PAYLOAD_SIZE,
    DTPK_MAX_MESSAGE_SIZE,
    DTPK_SINGLE_PAYLOAD_SIZE,
    Packet,
    Profile,
)
from simulator import Simulator


class DropOneFragmentSimulator(Simulator):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.forced_drops = 0

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
            and target == 3
            and packet.kind == "DATA_FRAGMENT"
            and packet.fragment_index == 3
            and self.forced_drops < 5
        ):
            self.drop_next_frames(2, 3, 1)
            self.forced_drops += 1
        return super().transmit(
            sender_id,
            target,
            packet,
            reliable,
            on_complete,
            attempt,
        )


def _line(sim, nodes):
    for node in range(1, nodes + 1):
        sim.add_node(node)
    for node in range(1, nodes):
        sim.add_link(node, node + 1, latency_ms=0, jitter_ms=0)


def _fragment(total_size: int, index: int, *, packet_id: int = 7) -> Packet:
    count = (total_size + DTPK_FRAGMENT_PAYLOAD_SIZE - 1) // DTPK_FRAGMENT_PAYLOAD_SIZE
    offset = index * DTPK_FRAGMENT_PAYLOAD_SIZE
    payload_size = min(DTPK_FRAGMENT_PAYLOAD_SIZE, total_size - offset)
    return Packet(
        "DATA_FRAGMENT",
        packet_id,
        original_sender=1,
        final_target=2,
        payload_size=payload_size,
        e2e_ack_requested=True,
        sender_sequence=9,
        wire_dtpk_size=DTPK_FRAGMENT_HEADER + payload_size,
        total_size=total_size,
        fragment_index=index,
        fragment_count=count,
        hop_limit=DTPK_DEFAULT_HOP_LIMIT,
    )


def test_python_multipart_lossless_delivery_uses_five_full_fragments():
    sim = Simulator(seed=81_001, profile=Profile.crystallized_v2())
    _line(sim, 2)
    sim.run(100_000)

    packet_id = sim.nodes[1].send_data(
        2, payload_size=1000, e2e_ack=True, timeout_ms=180_000
    )
    sim.run(260_000)

    assert packet_id is not None
    assert sim.metrics.delivered_app == 1
    assert sim.metrics.e2e_success == 1
    assert sim.metrics.multipart_messages_started == 1
    assert sim.metrics.multipart_messages_completed == 1
    assert sim.metrics.fragments_tx == 5
    assert sim.metrics.fragment_retransmits == 0
    assert sim.metrics.oversize_drops == 0
    assert sim.nodes[1].multipart_send is None
    assert not sim.nodes[2].fragment_assemblies


def test_python_multipart_selectively_repairs_only_forced_missing_fragment():
    sim = DropOneFragmentSimulator(seed=81_002, profile=Profile.crystallized_v2())
    _line(sim, 3)
    sim.run(150_000)

    packet_id = sim.nodes[1].send_data(
        3, payload_size=1800, e2e_ack=True, timeout_ms=300_000
    )
    sim.run(500_000)

    assert packet_id is not None
    assert sim.forced_drops == 5
    assert sim.metrics.delivered_app == 1
    assert sim.metrics.e2e_success == 1
    assert sim.metrics.fragments_tx <= 10
    assert sim.metrics.fragment_retransmits <= 2
    assert sim.metrics.fragment_status_tx >= 1
    enqueued = [
        event
        for event in sim.trace
        if event.get("event") == "fragment_enqueue"
        and event.get("packet_id") == packet_id
    ]
    retransmitted = [
        event["index"] for event in enqueued if event["retransmit"]
    ]
    assert retransmitted.count(3) == 1
    assert set(retransmitted).issubset({3, 7})


def test_python_multipart_never_delivers_partial_assembly():
    sim = Simulator(seed=81_003, profile=Profile.crystallized_v2())
    receiver = sim.add_node(2, start=False)
    receiver.up = True
    total_size = 1000

    for index in (0, 1, 3, 4):
        receiver.receive_fragment_local(_fragment(total_size, index), 1)
    assert sim.metrics.delivered_app == 0
    assert len(receiver.fragment_assemblies) == 1
    statuses = [
        request.packet
        for request in receiver.txq
        if request.packet.kind == "FRAGMENT_STATUS"
    ]
    assert statuses and statuses[-1].missing_fragments == (2,)

    receiver.receive_fragment_local(_fragment(total_size, 2), 1)
    assert sim.metrics.delivered_app == 1
    assert sim.metrics.multipart_messages_completed == 1
    assert not receiver.fragment_assemblies

    # A late duplicate only causes the destination to re-ACK the completed
    # identity; it is not delivered to the application again.
    receiver.receive_fragment_local(_fragment(total_size, 2), 1)
    assert sim.metrics.delivered_app == 1


def test_python_multipart_incomplete_assembly_expires():
    sim = Simulator(seed=81_004, profile=Profile.crystallized_v2())
    receiver = sim.add_node(2, start=False)
    receiver.up = True
    receiver.receive_fragment_local(_fragment(1000, 0), 1)
    assert receiver.fragment_assemblies

    sim.run(120_001)
    receiver._expire_fragment_assemblies()
    assert not receiver.fragment_assemblies
    assert sim.metrics.fragment_assemblies_expired == 1
    assert sim.metrics.delivered_app == 0


def test_python_multipart_boundary_and_memory_cap():
    assert DTPK_SINGLE_PAYLOAD_SIZE == 233
    assert DTPK_FRAGMENT_PAYLOAD_SIZE == 230

    sim = Simulator(seed=81_005, profile=Profile.crystallized_v2())
    _line(sim, 2)
    sim.run(100_000)

    single = sim.nodes[1].send_data(2, payload_size=233, e2e_ack=True)
    sim.run(140_000)
    assert single is not None
    assert sim.metrics.multipart_messages_started == 0

    multipart = sim.nodes[1].send_data(2, payload_size=234, e2e_ack=True)
    sim.run(220_000)
    assert multipart is not None
    assert sim.metrics.multipart_messages_started == 1

    too_large = sim.nodes[1].send_data(
        2, payload_size=DTPK_MAX_MESSAGE_SIZE + 1, e2e_ack=True
    )
    assert too_large is None
    assert sim.metrics.oversize_drops == 1
