from __future__ import annotations

import time
from dataclasses import dataclass

from owlrl import DeductiveClosure, OWLRL_Semantics
from rdflib import Graph, RDF

from .graph_builder import clone_graph
from .namespaces import EX, SOSA


@dataclass(frozen=True)
class EntailmentResult:
    explicit_triples: int
    closure_triples: int
    inferred_triples: int
    closure_ms: float
    smart_plugs_as_energy_devices: int
    smart_plugs_as_devices: int
    load_sensors_as_sensors: int


def materialise_owlrl_closure(graph: Graph) -> Graph:
    """Return a cloned graph expanded with OWL-RL deductive closure.

    This is intentionally separate from the Python window/topology derivations
    used elsewhere in v0.1. Aggregation over time windows is not represented as
    OWL entailment.
    """
    closure = clone_graph(graph)
    DeductiveClosure(
        OWLRL_Semantics,
        axiomatic_triples=False,
        datatype_axioms=False,
    ).expand(closure)
    return closure


def benchmark_owlrl_entailment(graph: Graph) -> EntailmentResult:
    """Materialise OWL-RL closure and report a small auditable baseline."""
    explicit_triples = len(graph)
    start = time.perf_counter()
    closure = materialise_owlrl_closure(graph)
    closure_ms = (time.perf_counter() - start) * 1000

    smart_plugs_as_energy_devices = sum(
        1
        for subject in closure.subjects(RDF.type, EX.EnergyDevice)
        if (subject, RDF.type, EX.SmartPlug) in graph
    )
    smart_plugs_as_devices = sum(
        1
        for subject in closure.subjects(RDF.type, EX.Device)
        if (subject, RDF.type, EX.SmartPlug) in graph
    )
    load_sensors_as_sensors = sum(
        1
        for subject in closure.subjects(RDF.type, SOSA.Sensor)
        if (subject, RDF.type, EX.LoadSensor) in graph
    )

    return EntailmentResult(
        explicit_triples=explicit_triples,
        closure_triples=len(closure),
        inferred_triples=len(closure) - explicit_triples,
        closure_ms=closure_ms,
        smart_plugs_as_energy_devices=smart_plugs_as_energy_devices,
        smart_plugs_as_devices=smart_plugs_as_devices,
        load_sensors_as_sensors=load_sensors_as_sensors,
    )
