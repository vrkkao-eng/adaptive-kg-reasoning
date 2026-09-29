# Research question — v0.1

## Core question

**When is an inferred fact worth materialising?**

The v0.1 prototype compares three inference classes with different temporal stability:

1. **Stable topology inference** — sensor deployment can be derived from sensor → plug → household → house relations.
2. **Semi-dynamic inference** — `HighRecentConsumption` is derived from a one-hour load window.
3. **Highly volatile inference** — `CurrentOverload` is derived from a ten-second load window.

It compares three strategies:

- **Full materialisation**: persist all three derived fact classes for the current snapshot.
- **Query-time**: persist none of the derived facts; recompute the requested class on demand.
- **Selective materialisation**: persist stable and semi-dynamic facts; compute the volatile class on demand.

## v0.1 hypothesis

The expected value of materialisation should increase with reuse and temporal stability, and decrease as update/maintenance frequency rises.

This is a learning/experimental hypothesis, not a claim of algorithmic novelty.
