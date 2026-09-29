from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from statistics import mean

from rdflib import Graph, RDF

from .namespaces import EX

Triple = tuple


def derive_stable_facts(graph: Graph) -> set[Triple]:
    """Derive stable topology facts.

    Rule: sensor attachedTo plug; plug locatedIn household; household locatedIn house
    => sensor deployedAt house.
    """
    derived: set[Triple] = set()
    for sensor, _, plug in graph.triples((None, EX.attachedTo, None)):
        for household in graph.objects(plug, EX.locatedIn):
            for house in graph.objects(household, EX.locatedIn):
                derived.add((sensor, EX.deployedAt, house))

    # Minimal subclass closure for SmartPlug -> EnergyDevice.
    for plug in graph.subjects(RDF.type, EX.SmartPlug):
        derived.add((plug, RDF.type, EX.EnergyDevice))
    return derived


def _load_events(csv_path: str | Path) -> tuple[list[dict], int]:
    events = []
    max_ts = 0
    with Path(csv_path).open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if int(row["property"]) != 1:
                continue
            event = {
                "timestamp": int(row["timestamp"]),
                "value": float(row["value"]),
                "plug_id": int(row["plug_id"]),
                "household_id": int(row["household_id"]),
                "house_id": int(row["house_id"]),
            }
            max_ts = max(max_ts, event["timestamp"])
            events.append(event)
    return events, max_ts


def _plug_uri(event: dict):
    return EX[
        f"house-{event['house_id']}-household-{event['household_id']}-plug-{event['plug_id']}"
    ]


def derive_semi_dynamic_facts(
    csv_path: str | Path,
    window_seconds: int = 3_600,
    threshold_watts: float = 450.0,
) -> set[Triple]:
    """Derive a one-hour average-load state from the current stream snapshot."""
    events, now = _load_events(csv_path)
    values = defaultdict(list)
    cutoff = now - window_seconds
    for event in events:
        if event["timestamp"] >= cutoff:
            values[_plug_uri(event)].append(event["value"])

    return {
        (plug, EX.hasState, EX.HighRecentConsumption)
        for plug, samples in values.items()
        if samples and mean(samples) >= threshold_watts
    }


def derive_volatile_facts(
    csv_path: str | Path,
    window_seconds: int = 10,
    threshold_watts: float = 800.0,
) -> set[Triple]:
    """Derive a short-window overload state from the current stream snapshot."""
    events, now = _load_events(csv_path)
    values = defaultdict(list)
    cutoff = now - window_seconds
    for event in events:
        if event["timestamp"] >= cutoff:
            values[_plug_uri(event)].append(event["value"])

    return {
        (plug, EX.hasState, EX.CurrentOverload)
        for plug, samples in values.items()
        if samples and mean(samples) >= threshold_watts
    }


def materialise(graph: Graph, facts: set[Triple]) -> int:
    before = len(graph)
    for triple in facts:
        graph.add(triple)
    return len(graph) - before
