"""Generate synthetic, seeded sample data. Replace the record shape per post."""

from __future__ import annotations

import argparse
import csv
import random


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10_000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="data/sample.csv")
    args = ap.parse_args()
    rng = random.Random(args.seed)
    with open(args.out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "account_id", "amount", "merchant"])
        for i in range(args.n):
            w.writerow([i, rng.randint(1, 500), round(rng.uniform(1, 999), 2), f"M{rng.randint(1, 50):03d}"])
    print(f"wrote {args.n} rows to {args.out}")


if __name__ == "__main__":
    main()
