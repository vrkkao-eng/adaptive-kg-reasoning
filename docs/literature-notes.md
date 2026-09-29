# Literature / standards notes — v0.1

## DEBS 2014 Grand Challenge

The base stream schema used as inspiration is:

`id, timestamp, value, property, plug_id, household_id, house_id`

The official challenge describes smart-plug measurements collected roughly every second in real homes. `property=0` represents accumulated work (kWh) and `property=1` represents load (W).

Source: https://debs.org/grand-challenges/2014/

**Important:** `data/raw/debs_sample_synthetic.csv` in this repository is generated locally. It mirrors the schema but is **not** the official DEBS dataset.

## SOSA / SSN

The prototype reuses the SOSA namespace `http://www.w3.org/ns/sosa/` for observations, sensors, features of interest, and observed properties. The application-specific vocabulary is intentionally small.

Source: https://www.w3.org/TR/vocab-ssn-2023/

## Next literature pass

v0.2 should add verified notes on:

- incremental materialisation / truth maintenance;
- RDF Stream Processing window semantics;
- Stream Intelligence Lab systems such as Kolibrie / RoXi;
- cost models for materialisation / query processing.
