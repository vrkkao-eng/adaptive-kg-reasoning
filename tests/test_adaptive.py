import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from adaptive_kg_reasoning import adaptive
from adaptive_kg_reasoning.adaptive import CostModel, benchmark_strategy, choose_mode, query_schedule
from adaptive_kg_reasoning.generate_data import generate_debs_shaped_csv
from adaptive_kg_reasoning.windows import StreamEvent, iter_sliding_windows, load_events


@pytest.fixture
def windows(tmp_path):
    path = generate_debs_shaped_csv(tmp_path / "input.csv", n_events=300, seed=19)
    return list(iter_sliding_windows(load_events(path, property_filter=1),
                                    width_seconds=60, slide_seconds=15, flush=True))


@pytest.mark.parametrize("strategy", adaptive.STRATEGIES)
@pytest.mark.parametrize("workload", ["none", "sparse", "dense", "bursty"])
def test_all_strategies_match_oracle(windows, strategy, workload):
    counts = query_schedule(workload, len(windows))
    rows = benchmark_strategy(windows, counts, strategy=strategy)
    assert sum(r["checked_queries"] for r in rows) == sum(counts)
    assert all(r["symmetric_difference_count"] == 0 for r in rows)
    for row in rows:
        assert row["total_ms"] == pytest.approx(sum(row[k] for k in (
            "decision_ms", "bootstrap_ms", "maintenance_ms", "release_ms", "query_ms")))


def test_policy_uses_past_demand_and_rebuilds_after_idle(windows):
    counts = [8] * 4 + [0] * 4 + [8] * (len(windows) - 8)
    rows = benchmark_strategy(windows, counts, strategy="adaptive")
    assert [r["predicted_queries"] for r in rows] == [0, *counts[:-1]]
    assert rows[0]["selected_mode"] == "query_time"
    assert rows[1]["selected_mode"] == "incremental"
    assert rows[5]["selected_mode"] == "query_time"
    assert rows[9]["selected_mode"] == "incremental"
    for index in (1, 9):
        assert rows[index]["event_work"] == rows[index]["active_events"]
        assert rows[index]["maintenance_ms"] == 0


def test_memory_price_changes_choice_and_ties_are_explicit():
    args = dict(active=100, changed=5, retained=True, previous_facts=10, previous_queries=2)
    assert choose_mode(**args, model=CostModel()).mode == "incremental"
    assert choose_mode(**args, model=CostModel(retain_item=100)).mode == "query_time"
    tie = choose_mode(active=0, changed=0, retained=False, previous_facts=0,
                      previous_queries=0, model=CostModel(switch=0))
    assert tie.mode == "query_time" and tie.reason == "tie_query_time"


def test_zero_queries_avoids_query_time_work(windows):
    rows = benchmark_strategy(windows, [0] * len(windows), strategy="query_time")
    assert all(r["event_work"] == 0 and r["retained_items"] == 0 for r in rows)
    assert all(r["result_count"] is None and not r["state_checked"] for r in rows)


def test_divergence_fails_loudly(windows, monkeypatch):
    original = adaptive.IncrementalHighRecentState.apply
    def corrupt(self, window):
        from dataclasses import replace
        result = original(self, window)
        return replace(result, facts=result.facts | {("bad", "bad", "bad")})
    monkeypatch.setattr(adaptive.IncrementalHighRecentState, "apply", corrupt)
    with pytest.raises(AssertionError, match="state divergence"):
        benchmark_strategy(windows, [0] * len(windows), strategy="incremental")


@pytest.mark.parametrize("value", [-1, math.nan, math.inf])
def test_invalid_cost_coefficients(value):
    with pytest.raises(ValueError):
        CostModel(retain_item=value)


def test_misaligned_and_invalid_queries_rejected(windows):
    with pytest.raises(ValueError):
        benchmark_strategy(windows, [0], strategy="adaptive")
    with pytest.raises(ValueError):
        benchmark_strategy(windows, [-1] * len(windows), strategy="adaptive")


def test_threshold_equality_and_expiration():
    events = [StreamEvent(1, 0, 450., 1, 1, 1, 1),
              StreamEvent(2, 5, 100., 1, 1, 1, 1)]
    windows = list(iter_sliding_windows(events, width_seconds=5, slide_seconds=5, flush=True))
    for strategy in adaptive.STRATEGIES:
        rows = benchmark_strategy(windows, [8] * len(windows), strategy=strategy)
        assert [r["result_count"] for r in rows] == [1, 0, 0]


def test_realized_costs_match_hand_calculation():
    events = [StreamEvent(1, 0, 500., 1, 1, 1, 1)]
    windows = list(iter_sliding_windows(events, width_seconds=5, slide_seconds=5))
    # One support event, one fact, two full-result reads.
    expected = {"recompute": 1.12, "query_time": 2.1, "incremental": 2.14, "adaptive": 2.1}
    for strategy, units in expected.items():
        row, = benchmark_strategy(windows, [2], strategy=strategy)
        assert row["realized_model_units"] == pytest.approx(units)


def test_current_query_count_cannot_change_current_decision(windows):
    low = benchmark_strategy(windows, [0] * len(windows), strategy="adaptive")
    high = benchmark_strategy(windows, [100] + [0] * (len(windows) - 1), strategy="adaptive")
    for key in ("selected_mode", "predicted_queries", "estimated_utility_units"):
        assert low[0][key] == high[0][key]


@pytest.mark.parametrize("value", [math.nan, math.inf])
def test_nonfinite_events_are_rejected(value):
    windows = list(iter_sliding_windows([StreamEvent(1, 0, value, 1, 1, 1, 1)]))
    with pytest.raises(ValueError, match="finite"):
        benchmark_strategy(windows, [1], strategy="adaptive")
