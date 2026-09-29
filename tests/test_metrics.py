from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))

from adaptive_kg_reasoning.generate_data import generate_debs_shaped_csv
from adaptive_kg_reasoning.incremental import IncrementalHighRecentState
from adaptive_kg_reasoning.metrics import compare_window, percentile, summarise
from adaptive_kg_reasoning.windows import iter_sliding_windows, load_events


def test_percentile_interpolation():
    assert percentile([1.0,2.0,3.0,4.0,5.0],0.5)==3.0
    assert percentile([1.0,2.0,3.0,4.0,5.0],0.95)==4.8
    assert percentile([],0.95)==0.0


def test_comparison_rows_are_strictly_equivalent(tmp_path):
    csv_path=generate_debs_shaped_csv(tmp_path/"sample.csv",n_events=700,seed=101)
    events=load_events(csv_path,property_filter=1)
    windows=list(iter_sliding_windows(events,width_seconds=120,slide_seconds=30,flush=True))
    state=IncrementalHighRecentState(threshold_watts=450.0)
    rows=[]
    for window in windows:
        row,_,_=compare_window(window,state,threshold_watts=450.0)
        assert row.result_equivalent
        assert row.symmetric_difference_count==0
        assert row.false_additions==0
        assert row.missed_facts==0
        rows.append(row)
    summary=summarise(rows,scenario="120/30")
    assert summary.windows==len(windows)
    assert summary.equivalent_windows==len(windows)
    assert summary.recomputation_ms_total>=0.0
    assert summary.incremental_ms_total>=0.0
    assert summary.max_state_size_proxy_kib>0.0


def test_overlap_scenarios_preserve_equivalence(tmp_path):
    csv_path=generate_debs_shaped_csv(tmp_path/"sample.csv",n_events=900,seed=202)
    events=load_events(csv_path,property_filter=1)
    for width,slide in [(180,30),(180,60),(180,90)]:
        state=IncrementalHighRecentState(threshold_watts=450.0)
        for window in iter_sliding_windows(events,width_seconds=width,slide_seconds=slide,flush=True):
            row,_,_=compare_window(window,state,threshold_watts=450.0)
            assert row.result_equivalent
