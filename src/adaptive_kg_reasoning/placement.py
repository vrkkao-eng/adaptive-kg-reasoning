"""Fixed placement planning and evaluation over a shared, oracle-checked trace."""
from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .incremental import IncrementalHighRecentState
from .network_cost import LinkProfile, Network
from .recompute import plug_uri, recompute_window
from .resources import MemoryModel, NodeProfile, WorkloadEstimate, nonnegative_int
from .windows import WindowTransition


def wire_bytes(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8"))


REQUEST_BYTES = wire_bytes({"query": "HighRecentConsumption"})


@dataclass(frozen=True)
class PlacementScenario:
    name: str
    source: str
    client: str
    estimate: WorkloadEstimate


@dataclass(frozen=True)
class PlacementConfig:
    nodes: tuple[NodeProfile, ...]
    network: Network
    memory: MemoryModel
    scenarios: tuple[PlacementScenario, ...]


def load_config(path: Path) -> PlacementConfig:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if set(raw) != {"schema_version", "nodes", "links", "memory", "scenarios"} or type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        raise ValueError("Expected placement profile schema_version=1 and exactly the documented fields")
    nodes = tuple(NodeProfile(**row) for row in raw["nodes"])
    names = {n.name for n in nodes}
    if len(names) != len(nodes) or names != {"edge", "fog", "cloud"}:
        raise ValueError("Profiles must define edge, fog and cloud exactly once")
    network = Network(names, [LinkProfile(**row) for row in raw["links"]])
    scenarios = []
    for row in raw["scenarios"]:
        if set(row) != {"name", "source", "client", "estimate"}:
            raise ValueError("Unexpected scenario fields")
        if not isinstance(row["name"], str) or not row["name"].strip():
            raise ValueError("Scenario name must be non-empty")
        scenario = PlacementScenario(row["name"], row["source"], row["client"],
                                     WorkloadEstimate(**row["estimate"]))
        # Validate topology up front, including links for candidates later excluded by memory.
        for node in nodes:
            network.one_way_ms(scenario.source, node.name, 1)
            network.request_response_ms(scenario.client, node.name, 1, 1)
        scenarios.append(scenario)
    if not scenarios or len({s.name for s in scenarios}) != len(scenarios):
        raise ValueError("Scenario names must be non-empty and unique")
    return PlacementConfig(nodes, network, MemoryModel(**raw["memory"]), tuple(scenarios))


def plan_placement(config: PlacementConfig, scenario: PlacementScenario) -> tuple[str | None, list[dict]]:
    """Choose once using declared estimates; no evaluation trace is an input."""
    forecast = scenario.estimate
    memory = config.memory.retained_bytes(forecast.active_events, forecast.distinct_entities,
                                          forecast.result_facts)
    candidates = []
    for node in config.nodes:
        initial_compute = node.compute_ms(event_work=forecast.active_events,
                                          affected_entities=forecast.distinct_entities,
                                          queries=forecast.queries_per_window, facts=forecast.result_facts)
        steady_compute = node.compute_ms(event_work=forecast.added_events + forecast.expired_events,
                                         affected_entities=forecast.affected_entities,
                                         queries=forecast.queries_per_window, facts=forecast.result_facts)
        initial_bytes = forecast.active_events * forecast.event_payload_bytes
        steady_bytes = forecast.added_events * forecast.event_payload_bytes
        response_bytes = 2 + forecast.result_facts * forecast.fact_payload_bytes
        compute = initial_compute + (forecast.windows - 1) * steady_compute
        ingress = config.network.one_way_ms(scenario.source, node.name, initial_bytes)
        ingress += (forecast.windows - 1) * config.network.one_way_ms(scenario.source, node.name, steady_bytes)
        query_network = forecast.windows * forecast.queries_per_window * config.network.request_response_ms(
            scenario.client, node.name, REQUEST_BYTES, response_bytes)
        total = compute + ingress + query_network
        if not math.isfinite(total):
            raise ValueError("Estimated cost overflow")
        feasible = memory <= node.memory_budget_bytes
        candidates.append({"scenario": scenario.name, "node": node.name,
                           "estimated_compute_ms": compute, "estimated_ingress_ms": ingress,
                           "estimated_query_network_ms": query_network, "estimated_total_ms": total,
                           "estimated_memory_bytes": memory, "memory_budget_bytes": node.memory_budget_bytes,
                           "predicted_feasible": feasible,
                           "reason": "within_estimated_budget" if feasible else "estimated_memory_exceeds_budget"})
    feasible = [row for row in candidates if row["predicted_feasible"]]
    # Explicit lexical tie-break makes selection independent of configuration order.
    selected = min(feasible, key=lambda r: (r["estimated_total_ms"], r["node"]))["node"] if feasible else None
    for row in candidates:
        row["selected"] = row["node"] == selected
    return selected, candidates


def build_reference_trace(windows: list[WindowTransition], queries: list[int], *, threshold: float) -> list[dict]:
    """Execute incremental semantics once on this host, not once per simulated node."""
    if not math.isfinite(threshold):
        raise ValueError("Threshold must be finite")
    state = IncrementalHighRecentState(threshold_watts=threshold)
    trace = []
    for position, (window, count) in enumerate(zip(windows, queries, strict=True)):
        nonnegative_int("query count", count)
        if any(e.property != 1 or not math.isfinite(e.value) for e in window.events):
            raise ValueError("Placement benchmark requires finite load-only events")
        oracle = recompute_window(window, threshold_watts=threshold)
        start = time.perf_counter()
        result = state.apply(window)
        reference_maintenance_ms = (time.perf_counter() - start) * 1000
        if result.facts != oracle.facts:
            raise AssertionError(f"State divergence at window {window.index}")
        reference_query_ms = 0.0
        for _ in range(count):
            start = time.perf_counter()
            answer = tuple(result.facts)
            reference_query_ms += (time.perf_counter() - start) * 1000
            if frozenset(answer) != oracle.facts:
                raise AssertionError(f"Query divergence at window {window.index}")
        # Transport only new events; deterministic expiration happens at the chosen node.
        batch_bytes = wire_bytes([asdict(e) for e in window.added]) if window.added else 0
        response_bytes = wire_bytes(sorted([list(map(str, fact)) for fact in result.facts]))
        trace.append({
            "window_index": window.index, "start_timestamp": window.start_timestamp,
            "end_timestamp": window.end_timestamp, "bootstrap": position == 0,
            "active_events": len(window.events), "entities": len(state.aggregates),
            "facts": len(result.facts), "events_added": len(window.added),
            "events_expired": len(window.expired),
            "event_work": len(window.added) + len(window.expired),
            "affected_entities": len({plug_uri(e) for e in (*window.added, *window.expired)}),
            "queries": count, "ingress_payload_bytes": batch_bytes,
            "response_payload_bytes": response_bytes,
            "reference_maintenance_ms": reference_maintenance_ms,
            "reference_query_ms": reference_query_ms, "reference_checked_queries": count,
            "reference_state_checked": True, "symmetric_difference_count": 0,
        })
    if not trace:
        raise ValueError("Placement benchmark requires at least one window")
    return trace


def evaluate_fixed(trace: list[dict], *, node: NodeProfile, config: PlacementConfig,
                   scenario: PlacementScenario) -> tuple[list[dict], dict]:
    """Model a fixed site's service demand; stop service at first budget violation."""
    rows = []
    failed_at = None
    for record in trace:
        required = config.memory.retained_bytes(record["active_events"], record["entities"], record["facts"])
        if failed_at is None and required > node.memory_budget_bytes:
            failed_at = record["window_index"]
        status = "feasible" if failed_at is None else "infeasible"
        compute = ingress = query_network = None
        ingress_bytes = query_bytes = 0
        if status == "feasible":
            compute = node.compute_ms(event_work=record["event_work"], affected_entities=record["affected_entities"],
                                      queries=record["queries"], facts=record["facts"])
            ingress = config.network.one_way_ms(scenario.source, node.name, record["ingress_payload_bytes"])
            query_network = record["queries"] * config.network.request_response_ms(
                scenario.client, node.name, REQUEST_BYTES, record["response_payload_bytes"])
            if not math.isfinite(compute + ingress + query_network):
                raise ValueError("Simulated cost overflow")
            if scenario.source != node.name:
                ingress_bytes = record["ingress_payload_bytes"]
            if scenario.client != node.name:
                query_bytes = record["queries"] * (REQUEST_BYTES + record["response_payload_bytes"])
        rows.append({
            "scenario": scenario.name, "node": node.name, "window_index": record["window_index"],
            "status": status, "reason": "within_budget" if status == "feasible" else
                ("memory_budget_exceeded" if record["window_index"] == failed_at else "stopped_after_budget_failure"),
            "required_memory_bytes": required, "memory_budget_bytes": node.memory_budget_bytes,
            "requested_queries": record["queries"],
            "served_queries": record["queries"] if status == "feasible" else 0,
            "simulated_compute_ms": compute, "simulated_ingress_ms": ingress,
            "simulated_query_network_ms": query_network,
            "simulated_total_ms": compute + ingress + query_network if status == "feasible" else None,
            "transmitted_ingress_bytes": ingress_bytes, "transmitted_query_bytes": query_bytes,
        })
    served = [r for r in rows if r["status"] == "feasible"]
    partial = sum(r["simulated_total_ms"] for r in served)
    summary = {"scenario": scenario.name, "placement": f"fixed_{node.name}", "node": node.name,
               "status": "feasible" if failed_at is None else "infeasible", "failed_at_window": failed_at,
               "requested_queries": sum(r["queries"] for r in trace),
               "served_queries": sum(r["served_queries"] for r in rows),
               "unserved_queries": sum(r["requested_queries"] - r["served_queries"] for r in rows),
               "evaluated_windows": len(rows), "served_windows": len(served),
               "max_required_memory_bytes": max(r["required_memory_bytes"] for r in rows),
               "simulated_compute_ms": sum(r["simulated_compute_ms"] for r in served),
               "simulated_ingress_ms": sum(r["simulated_ingress_ms"] for r in served),
               "simulated_query_network_ms": sum(r["simulated_query_network_ms"] for r in served),
               "simulated_partial_total_ms": partial,
               "simulated_total_ms": partial if failed_at is None else None,
               "transmitted_ingress_bytes": sum(r["transmitted_ingress_bytes"] for r in rows),
               "transmitted_query_bytes": sum(r["transmitted_query_bytes"] for r in rows)}
    return rows, summary


def evaluate_selection(selected: str | None, summaries: list[dict]) -> dict:
    """Reference an evaluated fixed candidate without executing or counting it twice."""
    if selected is not None:
        chosen = next(row for row in summaries if row["node"] == selected)
        return {**chosen, "placement": "budget_aware"}
    example = summaries[0]
    return {**example, "placement": "budget_aware", "node": None,
            "status": "no_feasible_estimate", "failed_at_window": None,
            "served_queries": 0, "unserved_queries": example["requested_queries"],
            "served_windows": 0, "max_required_memory_bytes": None,
            "simulated_compute_ms": None, "simulated_ingress_ms": None,
            "simulated_query_network_ms": None, "simulated_partial_total_ms": None,
            "simulated_total_ms": None, "transmitted_ingress_bytes": 0, "transmitted_query_bytes": 0}
