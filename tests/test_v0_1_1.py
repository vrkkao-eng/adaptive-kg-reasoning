from pathlib import Path
import sys

from rdflib import RDF

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adaptive_kg_reasoning.entailment import (
    benchmark_owlrl_entailment,
    materialise_owlrl_closure,
)
from adaptive_kg_reasoning.generate_data import generate_debs_shaped_csv
from adaptive_kg_reasoning.graph_builder import build_graph
from adaptive_kg_reasoning.namespaces import EX, SOSA


def _sample_graph(tmp_path):
    csv_path = generate_debs_shaped_csv(
        tmp_path / "sample.csv",
        n_events=400,
        seed=31,
        n_houses=2,
        households_per_house=1,
        plugs_per_household=2,
    )
    return build_graph(csv_path)


def test_owlrl_adds_transitive_type_entailment(tmp_path):
    graph = _sample_graph(tmp_path)
    plug = next(graph.subjects(RDF.type, EX.SmartPlug))

    assert (plug, RDF.type, EX.EnergyDevice) not in graph
    assert (plug, RDF.type, EX.Device) not in graph

    closure = materialise_owlrl_closure(graph)

    assert (plug, RDF.type, EX.EnergyDevice) in closure
    assert (plug, RDF.type, EX.Device) in closure


def test_owlrl_infers_sosa_sensor_type(tmp_path):
    graph = _sample_graph(tmp_path)
    load_sensor = next(graph.subjects(RDF.type, EX.LoadSensor))

    assert (load_sensor, RDF.type, SOSA.Sensor) not in graph

    closure = materialise_owlrl_closure(graph)

    assert (load_sensor, RDF.type, SOSA.Sensor) in closure


def test_entailment_benchmark_reports_inferred_triples(tmp_path):
    graph = _sample_graph(tmp_path)
    result = benchmark_owlrl_entailment(graph)

    assert result.closure_triples > result.explicit_triples
    assert result.inferred_triples > 0
    assert result.smart_plugs_as_energy_devices > 0
    assert result.smart_plugs_as_devices == result.smart_plugs_as_energy_devices
    assert result.load_sensors_as_sensors > 0
