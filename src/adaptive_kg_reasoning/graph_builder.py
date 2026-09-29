from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from rdflib import Graph, Literal, RDF, RDFS, OWL, XSD

from .namespaces import EX, QUDT, SOSA, UNIT


def bind_prefixes(graph: Graph) -> None:
    graph.bind("ex", EX)
    graph.bind("sosa", SOSA)
    graph.bind("qudt", QUDT)
    graph.bind("unit", UNIT)
    graph.bind("rdfs", RDFS)
    graph.bind("owl", OWL)


def add_schema(graph: Graph) -> None:
    # Small, explicit application schema. SOSA terms are reused rather than copied.
    for cls in [EX.Device, EX.EnergyDevice, EX.SmartPlug, EX.LoadSensor, EX.WorkSensor]:
        graph.add((cls, RDF.type, OWL.Class))

    graph.add((EX.SmartPlug, RDFS.subClassOf, EX.EnergyDevice))
    graph.add((EX.LoadSensor, RDFS.subClassOf, SOSA.Sensor))
    graph.add((EX.WorkSensor, RDFS.subClassOf, SOSA.Sensor))

    graph.add((EX.locatedIn, RDF.type, OWL.ObjectProperty))
    graph.add((EX.attachedTo, RDF.type, OWL.ObjectProperty))
    graph.add((EX.deployedAt, RDF.type, OWL.ObjectProperty))
    graph.add((EX.hasState, RDF.type, OWL.ObjectProperty))

    graph.add((EX.Load, RDF.type, SOSA.Property))
    graph.add((EX.Work, RDF.type, SOSA.Property))
    graph.add((EX.HighRecentConsumption, RDF.type, EX.ConsumptionState))
    graph.add((EX.CurrentOverload, RDF.type, EX.ConsumptionState))


def _dt_literal(timestamp: int) -> Literal:
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    return Literal(dt.isoformat().replace("+00:00", "Z"), datatype=XSD.dateTime)


def build_graph(csv_path: str | Path, ontology_path: str | Path | None = None) -> Graph:
    graph = Graph()
    bind_prefixes(graph)
    add_schema(graph)
    if ontology_path and Path(ontology_path).exists():
        graph.parse(ontology_path, format="turtle")

    seen_houses = set()
    seen_households = set()
    seen_plugs = set()

    with Path(csv_path).open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            event_id = int(row["id"])
            timestamp = int(row["timestamp"])
            value = float(row["value"])
            prop = int(row["property"])
            plug_id = int(row["plug_id"])
            household_id = int(row["household_id"])
            house_id = int(row["house_id"])

            house = EX[f"house-{house_id}"]
            household = EX[f"house-{house_id}-household-{household_id}"]
            plug = EX[f"house-{house_id}-household-{household_id}-plug-{plug_id}"]

            if house not in seen_houses:
                graph.add((house, RDF.type, EX.House))
                seen_houses.add(house)
            if household not in seen_households:
                graph.add((household, RDF.type, EX.Household))
                graph.add((household, EX.locatedIn, house))
                seen_households.add(household)
            if plug not in seen_plugs:
                graph.add((plug, RDF.type, EX.SmartPlug))
                graph.add((plug, EX.locatedIn, household))
                load_sensor = EX[f"{plug.split('/')[-1]}-load-sensor"]
                work_sensor = EX[f"{plug.split('/')[-1]}-work-sensor"]
                graph.add((load_sensor, RDF.type, EX.LoadSensor))
                graph.add((work_sensor, RDF.type, EX.WorkSensor))
                graph.add((load_sensor, EX.attachedTo, plug))
                graph.add((work_sensor, EX.attachedTo, plug))
                seen_plugs.add(plug)

            sensor_suffix = "load-sensor" if prop == 1 else "work-sensor"
            sensor = EX[f"{plug.split('/')[-1]}-{sensor_suffix}"]
            obs = EX[f"obs-{event_id}"]
            observed_property = EX.Load if prop == 1 else EX.Work
            unit = UNIT.W if prop == 1 else UNIT["KiloW-HR"]

            graph.add((obs, RDF.type, SOSA.Observation))
            graph.add((obs, SOSA.madeBySensor, sensor))
            graph.add((obs, SOSA.hasFeatureOfInterest, plug))
            graph.add((obs, SOSA.observedProperty, observed_property))
            graph.add((obs, SOSA.hasSimpleResult, Literal(value, datatype=XSD.decimal)))
            graph.add((obs, SOSA.resultTime, _dt_literal(timestamp)))
            graph.add((obs, EX.originalTimestamp, Literal(timestamp, datatype=XSD.integer)))
            graph.add((obs, EX.unit, unit))

    return graph


def clone_graph(source: Graph) -> Graph:
    clone = Graph()
    bind_prefixes(clone)
    for triple in source:
        clone.add(triple)
    return clone
