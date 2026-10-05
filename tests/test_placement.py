from dataclasses import replace
import json
import math
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from adaptive_kg_reasoning import placement
from adaptive_kg_reasoning.network_cost import LinkProfile, Network
from adaptive_kg_reasoning.placement import (
    REQUEST_BYTES, PlacementConfig, PlacementScenario, build_reference_trace,
    evaluate_fixed, evaluate_selection, load_config, plan_placement, wire_bytes,
)
from adaptive_kg_reasoning.resources import MemoryModel, NodeProfile, WorkloadEstimate
from adaptive_kg_reasoning.windows import StreamEvent, iter_sliding_windows


def fixture_config(budget=10000):
    nodes = tuple(NodeProfile(name, budget, 1, 2, 3, 4) for name in ("edge", "fog", "cloud"))
    network = Network({n.name for n in nodes}, [LinkProfile("edge", "fog", 10, 1000),
        LinkProfile("edge", "cloud", 40, 1000), LinkProfile("fog", "cloud", 20, 1000)])
    estimate = WorkloadEstimate(1, 2, 1, 2, 0, 1, 2, 1, 100, 100)
    scenario = PlacementScenario("test", "edge", "edge", estimate)
    return PlacementConfig(nodes, network, MemoryModel(10, 20, 30, 40), (scenario,))


def reference_trace():
    events = [StreamEvent(1, 0, 500., 1, 1, 1, 1), StreamEvent(2, 1, 400., 1, 1, 1, 1)]
    windows = list(iter_sliding_windows(events, width_seconds=5, slide_seconds=5, flush=True))
    return build_reference_trace(windows, [2, 0], threshold=450.)


def test_network_hand_calculation_and_locality():
    net = fixture_config().network
    # 2,000 bytes at 1,000 bytes/sec = 2,000 ms, plus half of 10 ms RTT.
    assert net.one_way_ms("edge", "fog", 2000) == 2005
    assert net.one_way_ms("fog", "edge", 2000) == 2005
    assert net.one_way_ms("edge", "fog", 0) == 0
    assert net.one_way_ms("edge", "edge", 2000) == 0
    # One RTT for both legs, not two RTTs.
    assert net.request_response_ms("edge", "fog", 100, 900) == 1010
    assert net.request_response_ms("edge", "fog", 0, 0) == 10
    assert net.request_response_ms("edge", "edge", 100, 900) == 0


def test_planning_hand_calculation():
    config = fixture_config()
    selected, rows = plan_placement(config, config.scenarios[0])
    edge, fog, _ = rows
    assert selected == "edge"
    assert edge["estimated_compute_ms"] == 18
    assert edge["estimated_memory_bytes"] == 120
    assert edge["estimated_total_ms"] == 18
    assert fog["estimated_ingress_ms"] == 205
    assert fog["estimated_query_network_ms"] == 2 * (10 + REQUEST_BYTES + 102)
    assert fog["estimated_total_ms"] == 18 + 205 + 2 * (10 + REQUEST_BYTES + 102)


def test_reference_oracle_threshold_expiration_and_exact_payloads():
    trace = reference_trace()
    first, last = trace
    assert [r["facts"] for r in trace] == [1, 0]
    assert sum(r["reference_checked_queries"] for r in trace) == 2
    assert all(r["reference_state_checked"] for r in trace)
    assert first["event_work"] == 2 and first["affected_entities"] == 1
    assert last["events_expired"] == 2 and last["ingress_payload_bytes"] == 0
    assert last["response_payload_bytes"] == 2  # JSON empty result: []
    assert wire_bytes({"label": "\u00e9"}) == 14  # Bytes, not character count.


def test_evaluation_costs_use_actual_bytes_and_do_not_charge_expiration_transport():
    config = fixture_config()
    trace = reference_trace()
    rows, summary = evaluate_fixed(trace, node=config.nodes[1], config=config, scenario=config.scenarios[0])
    first, last = rows
    assert first["simulated_compute_ms"] == 18
    assert first["simulated_ingress_ms"] == 5 + trace[0]["ingress_payload_bytes"]
    assert first["simulated_query_network_ms"] == 2 * (10 + REQUEST_BYTES + trace[0]["response_payload_bytes"])
    assert first["required_memory_bytes"] == 120
    assert last["simulated_ingress_ms"] == 0
    assert last["simulated_query_network_ms"] == 0
    assert last["simulated_compute_ms"] == 4  # Two expired events + one affected plug.
    assert summary["served_queries"] == 2
    assert summary["simulated_total_ms"] == pytest.approx(sum(r["simulated_total_ms"] for r in rows))
    assert summary["transmitted_ingress_bytes"] == trace[0]["ingress_payload_bytes"]


def test_budget_boundary_and_no_silent_recovery():
    trace = reference_trace()
    config = fixture_config(budget=120)
    _, ok = evaluate_fixed(trace, node=config.nodes[0], config=config, scenario=config.scenarios[0])
    assert ok["status"] == "feasible"
    limited = replace(config.nodes[0], memory_budget_bytes=119)
    rows, bad = evaluate_fixed(trace, node=limited, config=config, scenario=config.scenarios[0])
    assert bad["status"] == "infeasible" and bad["simulated_total_ms"] is None
    assert bad["served_queries"] == 0 and bad["unserved_queries"] == 2
    assert rows[1]["required_memory_bytes"] < 119
    assert rows[1]["reason"] == "stopped_after_budget_failure"


def test_underestimated_demand_can_make_selected_node_infeasible():
    config = fixture_config(budget=100)
    scenario = replace(config.scenarios[0], estimate=WorkloadEstimate(1, 1, 1, 1, 0, 1, 2, 0, 100, 100))
    selected, _ = plan_placement(config, scenario)
    assert selected == "edge"  # Estimated memory is only 60 bytes.
    summaries = [evaluate_fixed(reference_trace(), node=n, config=config, scenario=scenario)[1] for n in config.nodes]
    choice = evaluate_selection(selected, summaries)
    assert choice["node"] == "edge" and choice["status"] == "infeasible"
    assert choice["simulated_total_ms"] is None  # No hindsight relocation.


def test_late_budget_failure_preserves_only_completed_prefix():
    config = fixture_config(budget=120)
    first = reference_trace()[0]
    trace = [first, {**first, "window_index": 1, "active_events": 3},
             {**first, "window_index": 2}]
    rows, summary = evaluate_fixed(trace, node=config.nodes[0], config=config,
                                   scenario=config.scenarios[0])
    assert summary["failed_at_window"] == 1
    assert summary["served_windows"] == 1
    assert summary["served_queries"] == 2 and summary["unserved_queries"] == 4
    assert summary["simulated_partial_total_ms"] == rows[0]["simulated_total_ms"]
    assert summary["simulated_total_ms"] is None
    assert rows[2]["reason"] == "stopped_after_budget_failure"


@pytest.mark.parametrize("field,value", [("windows", 0), ("queries_per_window", math.nan),
    ("result_facts", 2), ("distinct_entities", 3), ("added_events", 3),
    ("expired_events", 3), ("affected_entities", 3), ("event_payload_bytes", 0)])
def test_invalid_workload_estimates(field, value):
    with pytest.raises(ValueError):
        replace(fixture_config().scenarios[0].estimate, **{field: value})


def test_cost_overflow_is_rejected():
    config = fixture_config()
    huge = replace(config.nodes[0], update_ms_per_event=1e308)
    config = replace(config, nodes=(huge, *config.nodes[1:]))
    with pytest.raises(ValueError, match="Estimated cost overflow"):
        plan_placement(config, config.scenarios[0])
    with pytest.raises(ValueError, match="Simulated cost overflow"):
        evaluate_fixed(reference_trace(), node=huge, config=config, scenario=config.scenarios[0])


def test_no_feasible_candidate_is_explicit():
    config = fixture_config(budget=0)
    selected, candidates = plan_placement(config, config.scenarios[0])
    assert selected is None and not any(r["predicted_feasible"] for r in candidates)
    summaries = [evaluate_fixed(reference_trace(), node=n, config=config, scenario=config.scenarios[0])[1] for n in config.nodes]
    result = evaluate_selection(selected, summaries)
    assert result["status"] == "no_feasible_estimate"
    assert result["node"] is None and result["simulated_total_ms"] is None
    assert result["served_queries"] == 0 and result["unserved_queries"] == 2


def test_default_profiles_demonstrate_three_distinct_choices():
    config = load_config(ROOT / "configs/placement_profiles.json")
    assert [plan_placement(config, s)[0] for s in config.scenarios] == ["edge", "fog", "cloud"]
    reverse = replace(config, nodes=tuple(reversed(config.nodes)))
    assert [plan_placement(reverse, s)[0] for s in config.scenarios] == ["edge", "fog", "cloud"]


def test_tie_break_is_lexical():
    config = fixture_config()
    scenario = replace(config.scenarios[0], estimate=WorkloadEstimate(1, 0, 0, 0, 0, 0, 0, 0, 100, 100))
    assert plan_placement(config, scenario)[0] == "cloud"


def test_reference_divergence_is_not_a_performance_tradeoff(monkeypatch):
    original = placement.IncrementalHighRecentState.apply
    def corrupt(self, window):
        result = original(self, window)
        return replace(result, facts=result.facts | {("bad", "bad", "bad")})
    monkeypatch.setattr(placement.IncrementalHighRecentState, "apply", corrupt)
    with pytest.raises(AssertionError, match="State divergence"):
        reference_trace()


@pytest.mark.parametrize("value", [-1, math.nan, math.inf, True])
def test_invalid_compute_cost(value):
    with pytest.raises(ValueError):
        NodeProfile("edge", 1, value, 0, 0, 0)


@pytest.mark.parametrize("value", [-1, 1.5, True])
def test_invalid_memory_budget(value):
    with pytest.raises(ValueError):
        NodeProfile("edge", value, 0, 0, 0, 0)


@pytest.mark.parametrize("value", [0, -1, math.nan, math.inf])
def test_invalid_bandwidth(value):
    with pytest.raises(ValueError):
        LinkProfile("edge", "fog", 0, value)


def test_unknown_missing_and_duplicate_network_links():
    net = Network({"edge", "fog", "cloud"}, [LinkProfile("edge", "fog", 1, 1)])
    with pytest.raises(ValueError, match="Missing direct link"):
        net.one_way_ms("edge", "cloud", 10)
    with pytest.raises(ValueError, match="Unknown network node"):
        net.request_response_ms("unknown", "edge", 1, 1)
    with pytest.raises(ValueError, match="Duplicate"):
        Network({"edge", "fog"}, [LinkProfile("edge", "fog", 1, 1), LinkProfile("fog", "edge", 1, 1)])


def test_profile_rejects_missing_topology_and_duplicate_nodes(tmp_path):
    raw = json.loads((ROOT / "configs/placement_profiles.json").read_text())
    raw["links"] = raw["links"][:1]
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="Missing direct link"):
        load_config(path)
    raw["nodes"].append(raw["nodes"][0])
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="exactly once"):
        load_config(path)
