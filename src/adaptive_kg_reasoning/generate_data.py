from __future__ import annotations

import argparse
import csv
import random
from collections import defaultdict
from pathlib import Path

FIELDS = ["id", "timestamp", "value", "property", "plug_id", "household_id", "house_id"]


def generate_debs_shaped_csv(
    output: str | Path,
    n_events: int = 5_000,
    seed: int = 42,
    n_houses: int = 4,
    households_per_house: int = 2,
    plugs_per_household: int = 4,
    start_timestamp: int = 1_379_879_533,
) -> Path:
    """Generate a deterministic DEBS-2014-shaped *synthetic* stream.

    The schema mirrors the DEBS 2014 Grand Challenge base stream, but the
    observations are generated locally and are not copies of the official data.
    """
    rng = random.Random(seed)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    plugs: list[tuple[int, int, int]] = []
    profiles: dict[tuple[int, int, int], tuple[float, float]] = {}
    for house in range(1, n_houses + 1):
        for household in range(1, households_per_house + 1):
            for plug in range(1, plugs_per_household + 1):
                key = (house, household, plug)
                plugs.append(key)
                # Different baseline loads make the rules produce heterogeneous states.
                baseline = rng.choice([35.0, 90.0, 180.0, 420.0, 680.0])
                variability = rng.choice([15.0, 40.0, 90.0, 160.0])
                profiles[key] = (baseline, variability)

    accumulated_work = defaultdict(float)

    with output.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        for event_id in range(1, n_events + 1):
            house, household, plug = rng.choice(plugs)
            key = (house, household, plug)
            timestamp = start_timestamp + event_id
            baseline, variability = profiles[key]

            # ~80% load events, ~20% accumulated-work events.
            is_load = True if event_id % 50 == 0 else rng.random() < 0.8
            if is_load:
                spike = rng.uniform(650.0, 1_100.0) if (event_id % 50 == 0 or rng.random() < 0.04) else 0.0
                value = max(0.0, rng.gauss(baseline, variability) + spike)
                prop = 1
                accumulated_work[key] += value / 3_600_000.0  # rough kWh per second
            else:
                value = accumulated_work[key]
                prop = 0

            writer.writerow(
                {
                    "id": event_id,
                    "timestamp": timestamp,
                    "value": f"{value:.6f}",
                    "property": prop,
                    "plug_id": plug,
                    "household_id": household,
                    "house_id": house,
                }
            )
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="data/raw/debs_sample_synthetic.csv")
    parser.add_argument("--events", type=int, default=5_000)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    path = generate_debs_shaped_csv(args.output, args.events, args.seed)
    print(path)


if __name__ == "__main__":
    main()
