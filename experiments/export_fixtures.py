from __future__ import annotations

import csv
import sys
from pathlib import Path

from rdflib import Graph, RDF

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adaptive_kg_reasoning.graph_builder import build_graph, bind_prefixes
from adaptive_kg_reasoning.namespaces import EX, SOSA


def main() -> None:
    raw = ROOT / "data" / "raw" / "debs_sample_synthetic.csv"
    full = build_graph(raw)

    static = Graph()
    bind_prefixes(static)
    observation_nodes = set(full.subjects(RDF.type, SOSA.Observation))
    for s, p, o in full:
        if s in observation_nodes:
            continue
        # Skip triples whose object is an observation (none are expected in v0.1,
        # but this keeps the fixture rule explicit).
        if o in observation_nodes:
            continue
        static.add((s, p, o))

    static_path = ROOT / "data" / "static" / "topology.ttl"
    static.serialize(static_path, format="turtle")

    with raw.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    max_ts = max(int(r["timestamp"]) for r in rows)
    window = [r for r in rows if int(r["timestamp"]) >= max_ts - 10]
    stream_path = ROOT / "data" / "streams" / "window_10s_sample.csv"
    with stream_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(window)

    print(f"static={static_path} triples={len(static)}")
    print(f"window={stream_path} rows={len(window)}")


if __name__ == "__main__":
    main()
