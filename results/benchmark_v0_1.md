# v0.1 benchmark result

Synthetic DEBS-shaped stream; values depend on machine/runtime.

| strategy | base_triples | explicit_triples_after_materialisation | derived_triples_materialised | materialisation_ms | peak_materialisation_kib | stable_query_ms_median | semi_query_ms_median | volatile_query_ms_median | stable_results | semi_results | volatile_results |
|---|---|---|---|---|---|---|---|---|---|---|---|
| full | 40228 | 40335 | 107 | 120.306 | 1096.392 | 0.068 | 0.009 | 0.004 | 64 | 10 | 1 |
| query_time | 40228 | 40228 | 0 | 0.005 | 0.070 | 0.595 | 11.796 | 8.070 | 64 | 10 | 1 |
| selective | 40228 | 40334 | 106 | 68.989 | 1076.942 | 0.041 | 0.009 | 7.950 | 64 | 10 | 1 |
