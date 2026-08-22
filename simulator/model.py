from __future__ import annotations
import copy
from dataclasses import dataclass, replace
from typing import Optional, Tuple
BROADCAST = 0
MAC_OVERHEAD = 8
LCMM_OVERHEAD = 3
LCMM_RX_HEADER = MAC_OVERHEAD + LCMM_OVERHEAD
DTPK_GENERIC_HEADER = 7  # type:u8, id:u16, originalSender:u16, finalTarget:u16
DTPK_CRYST_HEADER = 3
NEIGHBOR_RECORD_SIZE = 5
MAX_PACKET_SIZE = 255


@dataclass(frozen=True)
class Profile:
    """Behavior switches for the protocol implementation/model.

    `current()` intentionally mirrors important behavior/bugs in the C++ tree.
    `intended()` fixes implementation bugs while preserving the crystallization idea.
    Higher profiles explore protocol-level repairs on the same physical simulator.
    """

    name: str
    k_limit_ms: int = 20_000
    cryst_jitter_min_ms: int = 200
    periodic_cryst_ms: Optional[int] = None
    hello_period_ms: Optional[int] = None
    hello_jitter_fraction: float = 0.0
    propagate_on_route_change: bool = False
    global_e2e_gate: bool = True
    ack_bypasses_e2e_gate: bool = False
    relay_lcmm_ack: bool = False
    forwarding_size_bug: bool = True
    nack_is_success: bool = True
    ack_null_route_crash: bool = True
    noack_releases_lcmm_immediately: bool = True
    mac_busy_silent_drop: bool = True
    distance_uint8_wrap: bool = True
    max_metric: Optional[int] = None
    duplicate_suppression: bool = False
    send_no_route_null_callback_crash: bool = True
    memory_leak_model: bool = True
    feasibility_condition: bool = False
    source_seqno: bool = False
    seqno_requests: bool = False
    seqno_request_cooldown_ms: int = 5_000
    seqno_request_hop_limit: int = 32
    state_digest_requests: bool = False
    neighbor_expiry_ms: Optional[int] = None
    session_gc_enabled: bool = True
    max_lcmm_attempts: int = 3
    e2e_timeout_ms: int = 5_000
    # Real DTPK stores DTPKPacketWaiting::timeLeft as uint32_t and subtracts
    # loop deltas without saturation, so an overshoot wraps to ~49.7 days.
    e2e_timeout_uint32_underflow: bool = True

    @staticmethod
    def current() -> "Profile":
        return Profile(name="current")

    @staticmethod
    def intended() -> "Profile":
        return Profile(
            name="intended",
            propagate_on_route_change=True,
            global_e2e_gate=False,
            ack_bypasses_e2e_gate=True,
            relay_lcmm_ack=True,
            forwarding_size_bug=False,
            nack_is_success=False,
            ack_null_route_crash=False,
            noack_releases_lcmm_immediately=False,
            mac_busy_silent_drop=False,
            distance_uint8_wrap=False,
            max_metric=255,
            duplicate_suppression=True,
            send_no_route_null_callback_crash=False,
            memory_leak_model=False,
            e2e_timeout_uint32_underflow=False,
        )

    @staticmethod
    def heartbeat() -> "Profile":
        return replace(
            Profile.intended(),
            name="heartbeat",
            hello_period_ms=10_000,
            neighbor_expiry_ms=30_000,
            session_gc_enabled=False,
        )

    @staticmethod
    def heartbeat_sync() -> "Profile":
        return replace(
            Profile.heartbeat(),
            name="heartbeat-sync",
            state_digest_requests=True,
        )

    @staticmethod
    def robust() -> "Profile":
        return replace(
            Profile.intended(),
            name="robust",
            periodic_cryst_ms=15_000,
            neighbor_expiry_ms=45_000,
            session_gc_enabled=False,
        )

    @staticmethod
    def hold_down() -> "Profile":
        return replace(
            Profile.robust(),
            name="hold-down",
            feasibility_condition=True,
            source_seqno=False,
        )

    @staticmethod
    def feasible() -> "Profile":
        return replace(
            Profile.robust(),
            name="feasible",
            feasibility_condition=True,
            source_seqno=True,
        )

    @staticmethod
    def crystallized_v2() -> "Profile":
        """Candidate crystallization design under active validation.

        Tiny HELLOs provide liveness and route-version repair. Full CRYST vectors
        remain event-triggered. Feasibility prevents routing loops; when it blocks
        a necessary longer route, a small flooded sequence request asks the
        destination to advance its route generation.
        """
        return replace(
            Profile.heartbeat_sync(),
            name="cryst-v2",
            feasibility_condition=True,
            source_seqno=True,
            seqno_requests=True,
            hello_jitter_fraction=0.20,
        )


@dataclass
class Link:
    a: int
    b: int
    loss: float = 0.0
    ack_loss: Optional[float] = None
    latency_ms: float = 25.0
    jitter_ms: float = 5.0
    up: bool = True
    max_range: Optional[float] = None
    rx_power_dbm: Optional[float] = None
    epoch: int = 0

    def other(self, x: int) -> int:
        if x == self.a:
            return self.b
        if x == self.b:
            return self.a
        raise KeyError(x)


@dataclass
class NodeEnvironment:
    """Compatibility structure for older environment experiments."""
    powered: bool = True
    radio_enabled: bool = True
    x: float = 0.0
    y: float = 0.0
    io_epoch: int = 0


@dataclass(frozen=True)
class AdvertisedRoute:
    dest: int
    via: int
    distance: int
    seqno: int = 0
    neighbor_metric: Optional[int] = None


@dataclass(frozen=True)
class Route:
    next_hop: int
    advertised_via: int
    distance: int
    seqno: int = 0
    neighbor_metric: int = 0


@dataclass
class Packet:
    kind: str  # CRYST, DATA, ACK, NACK, HELLO, CRYST_REQ, SEQ_REQ
    packet_id: int
    original_sender: Optional[int] = None
    final_target: Optional[int] = None
    payload_size: int = 0
    advertisements: Tuple[AdvertisedRoute, ...] = ()
    wire_dtpk_size: int = 0
    sender_seqno: int = 0
    route_version: int = 0
    requested_seqno: int = 0
    hop_limit: int = 0

    def clone(self) -> "Packet":
        return copy.copy(self)


@dataclass
class TxRequest:
    packet: Packet
    next_hop: Optional[int]  # None means broadcast
    lcmm_ack: bool
    dtpk_ack: bool = False
    timeout_ms: int = 5_000
    priority: bool = False


@dataclass
class Metrics:
    radio_data_frames: int = 0
    radio_link_ack_frames: int = 0
    data_plane_frames: int = 0
    control_plane_frames: int = 0
    data_plane_link_ack_frames: int = 0
    control_plane_link_ack_frames: int = 0
    broadcasts: int = 0
    unicast_attempts: int = 0
    bytes_on_air: int = 0
    delivered_app: int = 0
    duplicate_app: int = 0
    duplicate_forward_drops: int = 0
    e2e_success: int = 0
    e2e_failure: int = 0
    nacks: int = 0
    silent_busy_drops: int = 0
    link_loss_drops: int = 0
    collision_drops: int = 0
    half_duplex_drops: int = 0
    lbt_scans: int = 0
    lbt_busy_scans: int = 0
    lbt_timeouts: int = 0
    oversize_drops: int = 0
    route_misses: int = 0
    route_changes: int = 0
    crashes: int = 0
    loops_observed: int = 0
    stale_route_observations: int = 0
    cryst_rx: int = 0
    cryst_tx: int = 0
    hello_rx: int = 0
    hello_tx: int = 0
    cryst_req_rx: int = 0
    cryst_req_tx: int = 0
    seq_req_rx: int = 0
    seq_req_tx: int = 0
    seq_req_satisfied: int = 0
    seq_req_duplicates: int = 0
    max_queue: int = 0
    max_route_entries: int = 0
    max_contributions: int = 0
    leaked_heap_bytes: int = 0
    infeasible_updates: int = 0
    midair_invalidations: int = 0
    environment_drops: int = 0
    node_failure_events: int = 0
    node_recovery_events: int = 0
    radio_failure_events: int = 0
    link_failure_events: int = 0
    link_recovery_events: int = 0
    mobility_events: int = 0
    send_calls: int = 0
    send_while_node_down: int = 0
    send_route_found: int = 0
    send_physically_reachable_at_start: int = 0
    send_physically_unreachable_at_start: int = 0
    e2e_success_started_reachable: int = 0
    e2e_failure_started_reachable: int = 0
    e2e_success_started_unreachable: int = 0
    e2e_failure_started_unreachable: int = 0
    e2e_aborted_by_reboot: int = 0
    e2e_timeout_underflows: int = 0
