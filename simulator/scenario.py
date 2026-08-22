from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Literal, Optional, Tuple

from model import Profile
from shared_backends import SharedCppNetwork, SharedPythonNetwork


BackendName = Literal["python", "cpp"]


@dataclass(frozen=True)
class NodeSpec:
    node_id: int
    position: Tuple[float, float] = (0.0, 0.0)
    k_limit: int = 20


@dataclass(frozen=True)
class LinkSpec:
    a: int
    b: int
    loss: float = 0.0
    ack_loss: Optional[float] = None
    latency_ms: float = 5.0
    jitter_ms: float = 0.0
    up: bool = True
    max_range: Optional[float] = None


@dataclass(frozen=True)
class LinkStateEvent:
    t_ms: float
    a: int
    b: int
    up: bool


@dataclass(frozen=True)
class NodeStateEvent:
    t_ms: float
    node_id: int
    up: bool


@dataclass(frozen=True)
class AppSendEvent:
    t_ms: float
    sender: int
    target: int
    payload_hex: str = "68656c6c6f"
    timeout_ms: int = 10_000
    e2e_ack: bool = True


@dataclass
class Scenario:
    """Backend-independent experiment definition.

    Topology, continuous trajectories, environmental failures and application
    demand are defined before protocol execution. ``build("python")`` and
    ``build("cpp")`` attach different protocol implementations to the same
    EnvironmentKernel semantics and keyed physical randomness.
    """

    seed: int = 1
    nodes: List[NodeSpec] = field(default_factory=list)
    links: List[LinkSpec] = field(default_factory=list)
    trajectories: Dict[int, List[Tuple[float, float, float]]] = field(default_factory=dict)
    link_events: List[LinkStateEvent] = field(default_factory=list)
    node_events: List[NodeStateEvent] = field(default_factory=list)
    app_events: List[AppSendEvent] = field(default_factory=list)

    def build(
        self,
        backend: BackendName,
        *,
        profile: Optional[Profile] = None,
        binary: Optional[str] = None,
        tick_ms: float = 50.0,
    ):
        if backend == "python":
            network = SharedPythonNetwork(
                seed=self.seed,
                profile=profile or Profile.current(),
            )
        elif backend == "cpp":
            network = SharedCppNetwork(
                seed=self.seed,
                binary=binary,
                tick_ms=tick_ms,
            )
        else:
            raise ValueError(f"unknown backend {backend!r}")

        for node in self.nodes:
            if backend == "cpp":
                network.add_node(
                    node.node_id,
                    k_limit=node.k_limit,
                    position=node.position,
                )
            else:
                network.add_node(node.node_id, position=node.position)

        for link in self.links:
            network.add_link(
                link.a,
                link.b,
                loss=link.loss,
                ack_loss=link.ack_loss,
                latency_ms=link.latency_ms,
                jitter_ms=link.jitter_ms,
                up=link.up,
                max_range=link.max_range,
            )

        for node_id, points in self.trajectories.items():
            network.set_trajectory(node_id, points)

        for event in self.link_events:
            network.set_link_at(event.t_ms, event.a, event.b, event.up)
        for event in self.node_events:
            if event.up:
                network.recover_node_at(event.t_ms, event.node_id)
            else:
                network.fail_node_at(event.t_ms, event.node_id)

        for event in self.app_events:
            payload = bytes.fromhex(event.payload_hex)
            network.schedule_at(
                event.t_ms,
                network.send,
                event.sender,
                event.target,
                payload,
                event.timeout_ms,
                event.e2e_ack,
                priority=network.PROTOCOL_PRIORITY,
            )

        return network

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(asdict(self), indent=indent, sort_keys=True)

    @staticmethod
    def from_json(text: str) -> "Scenario":
        raw = json.loads(text)
        return Scenario(
            seed=raw.get("seed", 1),
            nodes=[NodeSpec(**x) for x in raw.get("nodes", [])],
            links=[LinkSpec(**x) for x in raw.get("links", [])],
            trajectories={
                int(k): [tuple(p) for p in v]
                for k, v in raw.get("trajectories", {}).items()
            },
            link_events=[LinkStateEvent(**x) for x in raw.get("link_events", [])],
            node_events=[NodeStateEvent(**x) for x in raw.get("node_events", [])],
            app_events=[AppSendEvent(**x) for x in raw.get("app_events", [])],
        )

    @staticmethod
    def line(
        n: int,
        *,
        seed: int = 1,
        loss: float = 0.0,
        ack_loss: Optional[float] = None,
        latency_ms: float = 5.0,
        jitter_ms: float = 0.0,
        spacing: float = 1.0,
        max_range: Optional[float] = None,
    ) -> "Scenario":
        scenario = Scenario(seed=seed)
        scenario.nodes = [
            NodeSpec(i, (float(i - 1) * spacing, 0.0))
            for i in range(1, n + 1)
        ]
        scenario.links = [
            LinkSpec(
                i,
                i + 1,
                loss=loss,
                ack_loss=ack_loss,
                latency_ms=latency_ms,
                jitter_ms=jitter_ms,
                max_range=max_range,
            )
            for i in range(1, n)
        ]
        return scenario


def route_snapshot(network) -> Dict[int, Dict[int, Tuple[int, int]]]:
    return {
        node_id: network.routes(node_id)
        for node_id in sorted(network.nodes)
    }
