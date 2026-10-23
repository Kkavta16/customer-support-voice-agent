"""(Re)build the product database.

  python scripts/seed_products.py                  # load the built-in sample catalog
  python scripts/seed_products.py --csv my.csv     # load your own catalog

CSV columns (header row required):
  sku,name,brand,category,price,discount_pct,stock,rating,review_count,launched,description
"""
import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import PRODUCTS_DB  # noqa: E402
from app.products import seed  # noqa: E402
from app.sample_catalog import COLUMNS, PRODUCTS  # noqa: E402

NUMERIC = {"price": float, "discount_pct": int, "stock": int, "rating": float, "review_count": int}


def load_csv(path: Path) -> list[tuple]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = set(COLUMNS) - set(reader.fieldnames or [])
        if missing:
            sys.exit(f"CSV is missing columns: {', '.join(sorted(missing))}")
        return [
            tuple(NUMERIC[c](row[c] or 0) if c in NUMERIC else row[c].strip() for c in COLUMNS)
            for row in reader
        ]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--csv", type=Path, help="CSV file with your own products")
    parser.add_argument("--db", type=Path, default=PRODUCTS_DB, help=f"output database (default {PRODUCTS_DB})")
    args = parser.parse_args()

    rows = load_csv(args.csv) if args.csv else PRODUCTS
    seed(args.db, rows)
    print(f"Wrote {len(rows)} products to {args.db}. Restart the server to pick up new categories.")
