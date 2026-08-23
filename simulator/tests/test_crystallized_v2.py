import pathlib
import sys

SIM_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SIM_ROOT))

from model import (
    AdvertisedRoute,
    DTPK_CRYST_V2_HEADER,
    FeasibilityState,
    MAC_OVERHEAD,
    LCMM_OVERHEAD,
    MAX_PACKET_SIZE,
    Packet,
    Profile,
    TxRequest,
    Route,
)
from simulator import Simulator


def add_nodes(sim, count):
    for node_id in range(1, count + 1):
        sim.add_node(node_id)


def test_cryst_v2_is_event_triggered_not_periodic_full_vector():
    profile = Profile.crystallized_v2()
    assert profile.periodic_cryst_ms is None
    assert profile.hello_period_ms is not None
    assert profile.state_digest_requests
    assert profile.seqno_requests


def test_cryst_v2_discovers_one_hop_after_link_heals_without_external_protocol_trigger():
    sim = Simulator(seed=101, profile=Profile.crystallized_v2())
    add_nodes(sim, 2)
    sim.add_link(1, 2, up=False, jitter_ms=0)

    sim.run(30_000)
    assert 2 not in sim.nodes[1].routes
    assert 1 not in sim.nodes[2].routes

    # Only the physical environment changes. Neither protocol node is told that
    # a link came back; periodic HELLO must discover it.
    sim.set_link(1, 2, True)
    sim.run(50_000)

    assert sim.nodes[1].routes[2].next_hop == 2
    assert sim.nodes[1].routes[2].distance == 1
    assert sim.nodes[2].routes[1].next_hop == 1
    assert sim.nodes[2].routes[1].distance == 1


def test_cryst_v2_hello_digest_repairs_missing_neighbor_state():
    sim = Simulator(seed=102, profile=Profile.crystallized_v2())
    add_nodes(sim, 3)
    sim.add_link(1, 2, jitter_ms=0)
    sim.add_link(2, 3, jitter_ms=0)
    sim.run(80_000)
    assert sim.audit()["correct"], sim.audit()

    node1 = sim.nodes[1]
    node2 = sim.nodes[2]

    # Model exactly what a missed route-state update means: node 1 still knows
    # node 2 is a direct neighbour but has an older state version and lacks 2's
    # indirect contribution. No protocol callback is invoked to repair it.
    direct = node1.routes_by_neighbor[2][2]
    node1.routes_by_neighbor[2] = {2: direct}
    node1.neighbor_cryst_state[2] = (
        node2.origin_sequence,
        (node2.route_version - 1) & 0xFFFF,
    )
    node1.rebuild_routes()
    assert 3 not in node1.routes

    req_before = sim.metrics.cryst_req_tx
    sim.run(100_000)

    assert sim.metrics.cryst_req_tx > req_before
    assert node1.routes[3].distance == 2
    assert sim.audit()["correct"], sim.audit()


def test_cryst_v2_sequence_request_recovers_longer_only_path_without_periodic_origin_bump():
    # Initially 1-2-4 is shortest (2 hops); 1-3-5-4 is 3 hops. Removing 2-4
    # forces node 1 to accept a worse metric. Feasibility blocks that within the
    # old generation, so SEQ_REQ must make destination 4 originate a newer one.
    sim = Simulator(seed=103, profile=Profile.crystallized_v2())
    add_nodes(sim, 5)
    for a, b in ((1, 2), (2, 4), (1, 3), (3, 5), (5, 4)):
        sim.add_link(a, b, jitter_ms=0)

    sim.run(100_000)
    assert sim.audit()["correct"], sim.audit()
    assert sim.nodes[1].routes[4].distance == 2
    old_seq = sim.nodes[4].origin_sequence

    sim.set_link(2, 4, False)
    # v2 now matches the C++ 120 s hard-inactivity policy. The theoretical
    # backend has no active liveness probe, so allow expiry + SEQ_REQ repair.
    sim.run(280_000)

    assert sim.metrics.seq_req_satisfied >= 1
    assert sim.nodes[4].origin_sequence != old_seq
    assert sim.nodes[1].routes[4].distance == 3
    assert sim.nodes[1].routes[4].next_hop == 3
    assert sim.audit()["correct"], sim.audit()


def test_cryst_v2_clears_triangle_count_to_infinity_partition_without_loops():
    sim = Simulator(seed=104, profile=Profile.crystallized_v2())
    add_nodes(sim, 4)
    for a, b in ((1, 2), (2, 3), (3, 1), (1, 4), (3, 4)):
        sim.add_link(a, b, jitter_ms=0)

    sim.run(100_000)
    assert sim.audit()["correct"], sim.audit()

    sim.set_link(1, 4, False)
    sim.set_link(3, 4, False)

    loops = []
    for t in range(105_000, 280_001, 5_000):
        sim.run(t)
        loops.extend(sim.audit()["loops"])

    final = sim.audit()
    assert not loops, loops
    assert not final["stale"], final
    assert not final["loops"], final
    for node in (1, 2, 3):
        assert 4 not in sim.nodes[node].routes


def test_cryst_v2_stable_network_stops_sending_full_cryst_but_keeps_hellos():
    sim = Simulator(seed=105, profile=Profile.crystallized_v2())
    add_nodes(sim, 4)
    for a, b in ((1, 2), (2, 3), (3, 4)):
        sim.add_link(a, b, jitter_ms=0)

    sim.run(100_000)
    assert sim.audit()["correct"], sim.audit()
    cryst_before = sim.metrics.cryst_tx
    hello_before = sim.metrics.hello_tx

    sim.run(140_000)

    assert sim.metrics.hello_tx > hello_before
    assert sim.metrics.cryst_tx == cryst_before



def test_data_replay_identity_includes_sender_incarnation_after_reboot():
    sim = Simulator(seed=177, profile=Profile.crystallized_v2())
    add_nodes(sim, 2)
    sim.add_link(1, 2, latency_ms=0, jitter_ms=0)
    sim.run(100_000)

    sender = sim.nodes[1]
    receiver = sim.nodes[2]
    sender.packet_counter = 19
    first_id = sender.send_data(2, payload_size=8, e2e_ack=True, timeout_ms=20_000)
    first_sequence = sender.origin_sequence
    sim.run(130_000)
    assert first_id == 20
    assert sim.metrics.delivered_app == 1

    sim.reboot_node(1, downtime_ms=500)
    sim.run(210_000)
    assert sender.origin_sequence != first_sequence
    assert sender.routes[2].next_hop == 2

    # Force the same volatile packet id used before reboot. Only the source
    # incarnation distinguishes this new message from the old replay entry.
    sender.packet_counter = first_id - 1
    second_id = sender.send_data(2, payload_size=9, e2e_ack=True, timeout_ms=20_000)
    sim.run(240_000)

    assert second_id == first_id
    assert sim.metrics.delivered_app == 2
    assert sim.metrics.duplicate_app == 0
    assert sim.metrics.e2e_success >= 2
    assert len(receiver.delivered_ids) == 2



def test_lost_generation_repair_wave_is_retried_until_route_recovers():
    """A reachable destination must not remain missing after five lost repairs."""
    sim = Simulator(seed=20, profile=Profile.crystallized_v2())
    add_nodes(sim, 6)
    for a, b in ((1, 2), (2, 3), (2, 4), (3, 6), (4, 5), (4, 6)):
        sim.add_link(
            a,
            b,
            loss=0.05,
            ack_loss=0.05,
            latency_ms=0,
            jitter_ms=5,
        )

    sim.run(300_000)
    assert sim.audit()["correct"], sim.audit()

    sim.set_link(4, 6, False)
    sim.run(600_000)

    # The old five-request burst left route 6 -> 4 missing forever for this
    # deterministic loss trace. Persistent, backed-off repair converges.
    assert sim.metrics.seq_req_satisfied >= 3
    assert sim.audit()["correct"], sim.audit()
    assert sim.nodes[6].routes[4].distance == 3


def test_successful_link_ack_cancels_stale_retry_callback():
    sim = Simulator(seed=22_001, profile=Profile.crystallized_v2())
    add_nodes(sim, 2)
    sim.add_link(1, 2, latency_ms=0, jitter_ms=0)
    sim.run(100_000)

    trace_start = len(sim.trace)
    duplicate_before = sim.metrics.duplicate_app
    delivered_before = sim.metrics.delivered_app
    success_before = sim.metrics.e2e_success
    sim.nodes[1].send_data(2, payload_size=16, e2e_ack=True, timeout_ms=20_000)
    sim.run(140_000)

    routed = [
        event for event in sim.trace[trace_start:]
        if event.get("event") in ("tx_unicast", "tx_broadcast")
        and event.get("kind") in ("DATA", "ACK", "NACK")
    ]
    assert sum(event.get("kind") == "DATA" for event in routed) == 1, routed
    assert sum(event.get("kind") == "ACK" for event in routed) == 1, routed
    assert not any(event.get("kind") == "NACK" for event in routed), routed
    assert sim.metrics.delivered_app - delivered_before == 1
    assert sim.metrics.duplicate_app == duplicate_before
    assert sim.metrics.e2e_success - success_before == 1


def test_data_delivery_uses_full_finite_metric_hop_budget():
    sim = Simulator(seed=32_001, profile=Profile.crystallized_v2())
    add_nodes(sim, 64)
    for node in range(1, 64):
        sim.add_link(node, node + 1, latency_ms=0, jitter_ms=0)
    sim.run(1_500_000)
    assert sim.nodes[1].routes[64].distance == 63
    success_before = sim.metrics.e2e_success
    sim.nodes[1].send_data(64, payload_size=8, e2e_ack=True, timeout_ms=180_000)
    sim.run(1_900_000)
    assert sim.metrics.e2e_success - success_before == 1
    assert sim.metrics.delivered_app >= 1


def test_cryst_v2_chunks_large_snapshot_within_radio_limit():
    sim = Simulator(seed=32_201, profile=Profile.crystallized_v2())
    node = sim.add_node(1, start=False)
    node.up = True
    node.origin_sequence = 7
    node.route_version = 11
    node.routes = {
        destination: Route(
            next_hop=2,
            advertised_via=2,
            distance=destination - 1,
            sequence=3,
        )
        for destination in range(2, 72)
    }
    node.cryst_token = 1
    node.cryst_scheduled = True

    node._emit_cryst(1)

    packets = [
        request.packet for request in node.txq
        if request.packet.kind == "CRYST"
    ]
    assert len(packets) >= 2
    assert [packet.chunk_index for packet in packets] == list(range(len(packets)))
    assert all(packet.chunk_count == len(packets) for packet in packets)
    assert sum(len(packet.advertisements) for packet in packets) == len(node.routes)
    assert all(
        MAC_OVERHEAD + LCMM_OVERHEAD + packet.wire_dtpk_size <= MAX_PACKET_SIZE
        for packet in packets
    )


def test_cryst_v2_applies_chunks_only_after_complete_snapshot():
    sim = Simulator(seed=32_202, profile=Profile.crystallized_v2())
    receiver = sim.add_node(1, start=False)
    receiver.up = True

    first = Packet(
        "CRYST",
        10,
        advertisements=(AdvertisedRoute(3, 2, 1, 5),),
        sender_sequence=9,
        route_version=12,
        chunk_index=0,
        chunk_count=2,
    )
    second = Packet(
        "CRYST",
        11,
        advertisements=(AdvertisedRoute(4, 2, 2, 5),),
        sender_sequence=9,
        route_version=12,
        chunk_index=1,
        chunk_count=2,
    )

    receiver.receive_cryst(first, 2)
    assert 2 not in receiver.routes_by_neighbor
    assert 2 not in receiver.neighbor_cryst_state
    assert 2 in receiver.cryst_assemblies

    receiver.receive_cryst(second, 2)
    assert 2 not in receiver.cryst_assemblies
    assert receiver.neighbor_cryst_state[2] == (9, 12)
    assert receiver.routes[2].distance == 1
    assert receiver.routes[3].distance == 2
    assert receiver.routes[4].distance == 3


def test_cryst_v2_expires_incomplete_chunk_assembly():
    sim = Simulator(seed=32_203, profile=Profile.crystallized_v2())
    receiver = sim.add_node(1, start=False)
    receiver.up = True
    first = Packet(
        "CRYST",
        10,
        advertisements=(AdvertisedRoute(3, 2, 1, 5),),
        sender_sequence=9,
        route_version=12,
        chunk_index=0,
        chunk_count=2,
    )

    receiver.receive_cryst(first, 2)
    assert 2 in receiver.cryst_assemblies
    sim.run(30_001)
    assert 2 not in receiver.cryst_assemblies
    assert 2 not in receiver.routes_by_neighbor


def test_cryst_v2_newer_in_progress_snapshot_rejects_older_interleaving():
    sim = Simulator(seed=32_204, profile=Profile.crystallized_v2())
    receiver = sim.add_node(1, start=False)
    receiver.up = True
    newer = Packet(
        "CRYST",
        10,
        advertisements=(AdvertisedRoute(3, 2, 1, 5),),
        sender_sequence=9,
        route_version=13,
        chunk_index=0,
        chunk_count=2,
    )
    older = Packet(
        "CRYST",
        11,
        advertisements=(AdvertisedRoute(99, 2, 1, 5),),
        sender_sequence=9,
        route_version=12,
        chunk_index=1,
        chunk_count=2,
    )
    final = Packet(
        "CRYST",
        12,
        advertisements=(AdvertisedRoute(4, 2, 2, 5),),
        sender_sequence=9,
        route_version=13,
        chunk_index=1,
        chunk_count=2,
    )

    receiver.receive_cryst(newer, 2)
    receiver.receive_cryst(older, 2)
    assembly = receiver.cryst_assemblies[2]
    assert assembly.route_version == 13
    assert set(assembly.chunks) == {0}

    receiver.receive_cryst(final, 2)
    assert 99 not in receiver.routes
    assert receiver.routes[3].distance == 2
    assert receiver.routes[4].distance == 3


def test_cryst_v2_expiry_tracks_last_chunk_activity():
    sim = Simulator(seed=32_205, profile=Profile.crystallized_v2())
    receiver = sim.add_node(1, start=False)
    receiver.up = True
    first = Packet(
        "CRYST",
        10,
        sender_sequence=9,
        route_version=12,
        chunk_index=0,
        chunk_count=3,
    )
    late = Packet(
        "CRYST",
        11,
        sender_sequence=9,
        route_version=12,
        chunk_index=1,
        chunk_count=3,
    )

    receiver.receive_cryst(first, 2)
    sim.run(29_000)
    receiver.receive_cryst(late, 2)
    sim.run(30_001)
    assert 2 in receiver.cryst_assemblies
    sim.run(59_001)
    assert 2 not in receiver.cryst_assemblies


def test_sequence_repair_uses_directed_unicast_with_periodic_flood_escape():
    sim = Simulator(seed=181, profile=Profile.crystallized_v2())
    node = sim.add_node(1, start=False)
    node.up = True
    node.routes_by_neighbor = {
        2: {4: AdvertisedRoute(4, 2, 2, 1)},
        3: {4: AdvertisedRoute(4, 3, 4, 2)},
    }

    node.pending_seq_requests[4] = (3, 0)
    node._retry_sequence_request(4, 3)
    assert len(node.txq) == 1
    directed = node.txq[0]
    assert directed.packet.kind == "SEQ_REQ"
    # Both candidates are feasible here; repair uses the shortest one. Seqno
    # freshness is not a path metric. The directed path is still selected, but
    # each logical wave is sent once per hop: persistent DTPK retry and periodic
    # flood escape provide recovery without LCMM amplification.
    assert directed.next_hop == 2
    assert directed.lcmm_ack is False
    assert directed.packet.repair_flood is False

    node.feasibility[4] = FeasibilityState(sequence=1, feasible_distance=1)
    # Route via 2 now fails same-generation feasibility; the newer route via 3
    # is the feasible repair successor.
    assert node._sequence_request_next_hop(4) == 3

    # The fourth retry upgrades the queued directed request instead of being
    # hidden by same-destination coalescing.
    node.pending_seq_requests[4] = (3, 3)
    node._retry_sequence_request(4, 3)
    assert len(node.txq) == 1
    fallback = node.txq[0]
    assert fallback.packet.kind == "SEQ_REQ"
    assert fallback.next_hop is None
    assert fallback.lcmm_ack is False
    assert fallback.packet.repair_flood is True


def test_cryst_receiver_rejects_oversized_chunk_allocation_before_state_change():
    sim = Simulator(seed=45_001, profile=Profile.crystallized_v2())
    receiver = sim.add_node(1, start=False)
    receiver.up = True
    packet = Packet(
        "CRYST",
        1,
        sender_sequence=2,
        route_version=1,
        chunk_index=0,
        chunk_count=17,
        advertisements=(),
        wire_dtpk_size=DTPK_CRYST_V2_HEADER,
    )
    receiver.receive_cryst(packet, 2)
    assert 2 not in receiver.cryst_assemblies
    assert 2 not in receiver.routes_by_neighbor


def test_tx_scheduler_preserves_response_priority_and_repairs_fairness():
    sim = Simulator(seed=46_001, profile=Profile.crystallized_v2())
    node = sim.add_node(1, start=False)
    node.up = True

    for index in range(5):
        node.enqueue(TxRequest(
            Packet("SEQ_REQ", 100 + index, final_target=10 + index),
            None,
            lcmm_ack=False,
            priority=True,
        ))
    node.enqueue(TxRequest(Packet("HELLO", 200), None, lcmm_ack=False))
    node.enqueue(TxRequest(
        Packet("ACK", 300), 2, lcmm_ack=True, priority=True
    ))

    # Response packets are always first, without reversing FIFO repair order.
    assert node.txq[node._select_tx_index()].packet.packet_id == 300
    response = node._pop_tx_index(node._select_tx_index())
    assert response.packet.kind == "ACK"

    selected = []
    for _ in range(4):
        index = node._select_tx_index()
        request = node._pop_tx_index(index)
        selected.append(request.packet.packet_id)
        node.repair_burst += 1
    assert selected == [100, 101, 102, 103]

    # The normal packet now gets one slot before the fifth repair request.
    assert node.txq[node._select_tx_index()].packet.kind == "HELLO"



def test_direct_and_broadcast_cryst_snapshots_coexist_in_python_queue():
    sim = Simulator(seed=191, profile=Profile.crystallized_v2())
    node = sim.add_node(1, start=False)
    node.up = True

    broadcast = Packet(
        "CRYST",
        101,
        advertisements=(),
        wire_dtpk_size=DTPK_CRYST_V2_HEADER,
        sender_sequence=1,
        route_version=1,
        chunk_index=0,
        chunk_count=1,
    )
    direct = Packet(
        "CRYST",
        102,
        advertisements=(),
        wire_dtpk_size=DTPK_CRYST_V2_HEADER,
        sender_sequence=1,
        route_version=1,
        chunk_index=0,
        chunk_count=1,
    )
    node.enqueue(TxRequest(broadcast, None, lcmm_ack=False))
    node.enqueue(TxRequest(direct, 2, lcmm_ack=True, priority=True))

    queued = [request for request in node.txq if request.packet.kind == "CRYST"]
    assert len(queued) == 2
    assert {(request.next_hop, request.lcmm_ack) for request in queued} == {
        (None, False),
        (2, True),
    }

    # A newer direct response replaces only the older direct response, leaving
    # the independently scheduled broadcast snapshot intact.
    newer_direct = direct.clone()
    newer_direct.packet_id = 103
    node.enqueue(TxRequest(newer_direct, 2, lcmm_ack=True, priority=True))
    queued = [request for request in node.txq if request.packet.kind == "CRYST"]
    assert len(queued) == 2
    assert {request.packet.packet_id for request in queued} == {101, 103}


def test_sequence_retry_stops_when_any_feasible_route_returns():
    """SEQ_REQ repairs route starvation; it must not enforce generation uniformity."""
    sim = Simulator(seed=192, profile=Profile.crystallized_v2())
    node = sim.add_node(1, start=False)
    node.up = True
    node.origin_sequence = 10

    # The request was created while no route was feasible. Before its retry,
    # an ordinary older-generation but feasible route returns and is selected.
    node.pending_seq_requests[4] = (7, 3)
    node.routes[4] = Route(2, 2, 2, 6)
    node.enqueue(
        TxRequest(
            Packet(
                "SEQ_REQ",
                501,
                original_sender=1,
                final_target=4,
                requested_sequence=7,
                repair_flood=False,
                hop_limit=255,
            ),
            2,
            lcmm_ack=True,
            priority=True,
        )
    )

    node._retry_sequence_request(4, 7)

    assert 4 not in node.pending_seq_requests
    assert not any(
        request.packet.kind == "SEQ_REQ"
        and request.packet.original_sender == 1
        and request.packet.final_target == 4
        for request in node.txq
    )
