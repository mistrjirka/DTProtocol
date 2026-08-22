from __future__ import annotations

import copy
from dataclasses import dataclass, replace
from typing import Optional, Tuple

BROADCAST = 0
MAC_OVERHEAD = 8
LCMM_OVERHEAD = 3
LCMM_RX_HEADER = MAC_OVERHEAD + LCMM_OVERHEAD
DTPK_GENERIC_HEADER = 7
DTPK_CRYST_HEADER = 3
NEIGHBOR_RECORD_SIZE = 5
NEIGHBOR_RECORD_V2_SIZE = 7  # dest:u16, via:u16, sequence:u16, metric:u8
MAX_PACKET_SIZE = 255
ROUTE_INFINITY = 255


@dataclass(frozen=True)
class Profile:
    """Behavior switches for protocol experiments.

    `current()` mirrors important behavior/bugs in the reference C++ tree.
    `intended()` removes implementation bugs without changing routing theory.
    `robust()` separates liveness from crystallization and adds repair refreshes.
    `feasible()` adds destination generations + a feasibility condition to make
    next-hop changes loop-safe while retaining crystallization propagation.
    """

    name: str
    k_limit_ms: int = 20_000
    cryst_jitter_min_ms: int = 200
    periodic_cryst_ms: Optional[int] = None
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
    neighbor_expiry_ms: Optional[int] = None
    session_gc_enabled: bool = True
    max_lcmm_attempts: int = 3
    e2e_timeout_ms: int = 5_000

    sequence_numbers: bool = False
    feasibility_condition: bool = False
    origin_seq_period_ms: Optional[int] = None
    advertise_self_route: bool = False

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
            max_metric=ROUTE_INFINITY,
            duplicate_suppression=True,
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
    def feasible() -> "Profile":
        return replace(
            Profile.robust(),
            name="feasible",
            periodic_cryst_ms=30_000,
            origin_seq_period_ms=60_000,
            sequence_numbers=True,
            feasibility_condition=True,
            advertise_self_route=True,
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

    def other(self, x: int) -> int:
        if x == self.a:
            return self.b
        if x == self.b:
            return self.a
        raise KeyError(x)


@dataclass(frozen=True)
class AdvertisedRoute:
    dest: int
    via: int
    distance: int
    sequence: int = 0


@dataclass(frozen=True)
class Route:
    next_hop: int
    advertised_via: int
    distance: int
    sequence: int = 0


@dataclass
class FeasibilityState:
    sequence: int
    feasible_distance: int


def sequence_newer(a: int, b: int) -> bool:
    """RFC1982-style comparison for 16-bit routing sequence numbers."""
    a &= 0xFFFF
    b &= 0xFFFF
    if a == b:
        return False
    return ((a - b) & 0xFFFF) < 0x8000


@dataclass
class Packet:
    kind: str  # CRYST, DATA, ACK, NACK
    packet_id: int
    original_sender: Optional[int] = None
    final_target: Optional[int] = None
    payload_size: int = 0
    advertisements: Tuple[AdvertisedRoute, ...] = ()
    wire_dtpk_size: int = 0
    cryst_record_size: int = NEIGHBOR_RECORD_SIZE

    def clone(self) -> "Packet":
        return copy.copy(self)


@dataclass
class TxRequest:
    packet: Packet
    next_hop: Optional[int]
    lcmm_ack: bool
    dtpk_ack: bool = False
    timeout_ms: int = 5_000
    priority: bool = False


@dataclass
class Metrics:
    radio_data_frames: int = 0
    radio_link_ack_frames: int = 0
    broadcasts: int = 0
    unicast_attempts: int = 0
    bytes_on_air: int = 0
    delivered_app: int = 0
    duplicate_app: int = 0
    e2e_success: int = 0
    e2e_failure: int = 0
    nacks: int = 0
    silent_busy_drops: int = 0
    link_loss_drops: int = 0
    oversize_drops: int = 0
    route_misses: int = 0
    route_changes: int = 0
    crashes: int = 0
    loops_observed: int = 0
    stale_route_observations: int = 0
    cryst_rx: int = 0
    cryst_tx: int = 0
    max_queue: int = 0
    max_route_entries: int = 0
    max_contributions: int = 0
    feasibility_rejects: int = 0
    sequence_resets: int = 0
