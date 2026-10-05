"""Direct symmetric-link service-demand model with explicit units."""
from __future__ import annotations

from dataclasses import dataclass

from .resources import finite_nonnegative, nonnegative_int


@dataclass(frozen=True)
class LinkProfile:
    a: str
    b: str
    rtt_ms: float
    bandwidth_bytes_per_second: float

    def __post_init__(self) -> None:
        if not isinstance(self.a, str) or not isinstance(self.b, str) or not self.a or not self.b or self.a == self.b:
            raise ValueError("Link endpoints must be distinct non-empty node names")
        finite_nonnegative("rtt_ms", self.rtt_ms)
        finite_nonnegative("bandwidth_bytes_per_second", self.bandwidth_bytes_per_second)
        if self.bandwidth_bytes_per_second == 0:
            raise ValueError("Link bandwidth must be positive")


class Network:
    def __init__(self, nodes: set[str], links: list[LinkProfile]) -> None:
        self.nodes = frozenset(nodes)
        self.links: dict[frozenset[str], LinkProfile] = {}
        for link in links:
            key = frozenset((link.a, link.b))
            if not key <= self.nodes:
                raise ValueError("Link references unknown node")
            if key in self.links:
                raise ValueError("Duplicate symmetric link")
            self.links[key] = link

    def _link(self, source: str, target: str) -> LinkProfile | None:
        if source not in self.nodes or target not in self.nodes:
            raise ValueError("Unknown network node")
        if source == target:
            return None
        try:
            return self.links[frozenset((source, target))]
        except KeyError as exc:
            raise ValueError(f"Missing direct link: {source} -> {target}") from exc

    def one_way_ms(self, source: str, target: str, payload_bytes: int) -> float:
        nonnegative_int("payload_bytes", payload_bytes)
        link = self._link(source, target)
        if link is None or payload_bytes == 0:
            return 0.0
        return link.rtt_ms / 2 + payload_bytes / link.bandwidth_bytes_per_second * 1000

    def request_response_ms(self, client: str, target: str,
                            request_bytes: int, response_bytes: int) -> float:
        nonnegative_int("request_bytes", request_bytes)
        nonnegative_int("response_bytes", response_bytes)
        link = self._link(client, target)
        if link is None:
            return 0.0
        # One RTT covers both legs, even when the response is an empty result.
        return link.rtt_ms + (request_bytes + response_bytes) / link.bandwidth_bytes_per_second * 1000
