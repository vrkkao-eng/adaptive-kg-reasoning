# v0.2.3 comparative benchmark — CI reference run

Deterministic 5,000-event synthetic DEBS-shaped stream on GitHub Actions.

| Scenario | Windows | Equivalent | Recompute total (ms) | Incremental total (ms) | Total speedup | Recompute median (ms) | Incremental median (ms) | Recompute p95 (ms) | Incremental p95 (ms) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 3600/60 | 84 | 84/84 | 367.007 | 20.838 | 17.613x | 4.821 | 0.122 | 6.844 | 0.268 |
| 3600/300 | 17 | 17/17 | 76.301 | 14.006 | 5.448x | 4.896 | 0.293 | 6.893 | 2.070 |
| 3600/900 | 6 | 6/6 | 28.092 | 12.909 | 2.176x | 5.288 | 1.064 | 6.894 | 5.986 |

Fact churn:
- 3600/60: 17 additions, 10 retractions
- 3600/300: 14 additions, 4 retractions
- 3600/900: 13 additions, 4 retractions

Maximum Python incremental-state object-size proxy was approximately 392 KiB in all three scenarios.

These values are environment-specific reference measurements, not general performance claims. The correctness result is exact equivalence in every evaluated window.
