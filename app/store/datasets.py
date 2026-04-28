"""Dataset registry. The reference dataset ships seeded; additional
datasets can be registered at startup via env or config."""

from __future__ import annotations

import csv
import os
import random
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class Dataset:
    id: str
    path: str
    rows: int
    columns: list[str]


def ensure_demo_dataset(target_dir: str | Path) -> Dataset:
    """Generate the 10k-row demo ecommerce dataset if missing.

    The shape mirrors the questions the mock LLM knows how to answer:
    columns = product, category, units, price, revenue, signup_date.
    """
    target = Path(target_dir) / "demo_orders.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        rng = random.Random(42)
        cats = ["apparel", "electronics", "home", "books", "food", "toys"]
        with target.open("w", newline="", encoding="utf-8") as fp:
            w = csv.writer(fp)
            w.writerow(["product", "category", "units", "price", "revenue",
                        "signup_date"])
            for i in range(10_000):
                cat = rng.choice(cats)
                product = f"{cat}-{i:05d}"
                units = rng.randint(1, 12)
                price = round(rng.uniform(2.0, 499.0), 2)
                rev = round(units * price, 2)
                d = rng.randint(1, 365)
                date = f"2024-{((d - 1) // 30) + 1:02d}-{((d - 1) % 30) + 1:02d}"
                w.writerow([product, cat, units, price, rev, date])
    cols = ["product", "category", "units", "price", "revenue", "signup_date"]
    return Dataset(id="demo_orders", path=str(target), rows=10_000, columns=cols)


def discover_datasets(target_dir: str | Path) -> list[Dataset]:
    out: list[Dataset] = []
    for p in Path(target_dir).glob("*.csv"):
        rows, cols = _peek_csv(p)
        out.append(Dataset(id=p.stem, path=str(p), rows=rows, columns=cols))
    return sorted(out, key=lambda d: d.id)


def _peek_csv(p: Path) -> tuple[int, list[str]]:
    with p.open("r", encoding="utf-8") as fp:
        r = csv.reader(fp)
        header = next(r, [])
        n = sum(1 for _ in r)
    return n, list(header)


# Convenience: where the demo dataset lives by default.
DEFAULT_DATA_DIR = os.environ.get("DATACHAT_DATA_DIR", "./data")
