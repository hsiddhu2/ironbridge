"""Run the measurement plan N times and write results.csv.

The engineer fills in `one_run()` from docs/measurement-plan.md. Everything else stays.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import time
from pathlib import Path

COLUMNS = ["run", "started_at", "metric", "value", "unit", "notes"]


def load_outputs() -> dict:
    p = Path(__file__).resolve().parents[1] / "outputs.json"
    if not p.exists():
        raise SystemExit("outputs.json missing. Run `make deploy` first.")
    data = json.loads(p.read_text())
    return next(iter(data.values()))


def one_run(run: int, outputs: dict) -> list[dict]:
    """Return one or more rows for this run. Replace this body."""
    started = time.time()
    # TODO: call the system under test using values from outputs
    elapsed_ms = (time.time() - started) * 1000
    return [
        {
            "run": run,
            "started_at": int(started),
            "metric": "elapsed",
            "value": round(elapsed_ms, 2),
            "unit": "ms",
            "notes": "",
        }
    ]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--out", default="results.csv")
    args = ap.parse_args()

    outputs = load_outputs()
    rows: list[dict] = []
    for i in range(1, args.runs + 1):
        rows.extend(one_run(i, outputs))

    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)

    by_metric: dict[str, list[float]] = {}
    for r in rows:
        by_metric.setdefault(r["metric"], []).append(float(r["value"]))
    print(f"{'metric':<20}{'n':>4}{'mean':>12}{'stdev':>12}{'min':>12}{'max':>12}")
    for m, vals in by_metric.items():
        sd = statistics.pstdev(vals) if len(vals) > 1 else 0.0
        print(f"{m:<20}{len(vals):>4}{statistics.mean(vals):>12.2f}{sd:>12.2f}{min(vals):>12.2f}{max(vals):>12.2f}")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
