"""Product catalog backed by SQLite.

The LLM reaches it through three tools:
  search_products      structured filters (reliable even for small models)
  get_product_details  everything about one product
  query_products_sql   free-form read-only SQL for counts, averages, comparisons...

Text-to-SQL guardrails: the database is opened read-only, an authorizer allows only
SELECT/READ operations (no writes, PRAGMA, ATTACH...), queries are time-limited,
and results are capped at MAX_ROWS.
"""
import re
import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path

from .sample_catalog import PRODUCTS

MAX_ROWS = 20
SQL_TIMEOUT_S = 2.0
_ALLOWED_SQL_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
                        sqlite3.SQLITE_RECURSIVE}
SORTS = {
    "relevance": "p.review_count DESC",
    "price_low": "final_price ASC",
    "price_high": "final_price DESC",
    "rating": "p.rating DESC, p.review_count DESC",
    "popularity": "p.review_count DESC",
    "newest": "p.launched DESC",
}

# Everyday words for categories (singular, lower case) -> category stem. Only used when the
# catalog actually has that category.
CATEGORY_SYNONYMS = {
    "phone": "smartphone", "mobile": "smartphone", "cellphone": "smartphone",
    "notebook": "laptop", "tv": "television", "telly": "television",
    "watch": "smartwatche", "watche": "smartwatche", "smartwatch": "smartwatche",
    "earphone": "headphone", "earbud": "headphone", "headset": "headphone",
    "appliance": "home appliance", "accessorie": "accessorie", "accessory": "accessorie",
}

SCHEMA_SQL = """
CREATE TABLE categories (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);
CREATE TABLE products (
    id           INTEGER PRIMARY KEY,
    sku          TEXT NOT NULL UNIQUE,
    name         TEXT NOT NULL,
    brand        TEXT NOT NULL,
    category_id  INTEGER NOT NULL REFERENCES categories(id),
    price        REAL NOT NULL,      -- list price in INR
    discount_pct INTEGER NOT NULL DEFAULT 0,
    stock        INTEGER NOT NULL DEFAULT 0,
    rating       REAL,               -- 0 to 5 stars
    review_count INTEGER NOT NULL DEFAULT 0,
    launched     TEXT,               -- YYYY-MM-DD
    description  TEXT
);
-- Convenience view: one row per product with category name and price after discount.
CREATE VIEW product_view AS
SELECT p.*, c.name AS category,
       ROUND(p.price * (100 - p.discount_pct) / 100.0) AS final_price
FROM products p JOIN categories c ON c.id = p.category_id;
-- Full-text index for keyword search (rowid = products.id). The Porter stemmer makes
-- "cancelling" match "cancellation" and "laptops" match "laptop".
CREATE VIRTUAL TABLE products_fts USING fts5(name, brand, category, description,
                                             tokenize = 'porter unicode61');
"""

_BASE_QUERY = """
SELECT p.*, c.name AS category, ROUND(p.price * (100 - p.discount_pct) / 100.0) AS final_price
FROM products p JOIN categories c ON c.id = p.category_id
"""


def seed(path: Path, rows=PRODUCTS):
    """Create a fresh catalog database at `path` from (sku, name, brand, category, ...) rows."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executescript(SCHEMA_SQL)
        for category in sorted({r[3] for r in rows}):
            conn.execute("INSERT INTO categories (name) VALUES (?)", (category,))
        conn.executemany(
            """INSERT INTO products (sku, name, brand, category_id, price, discount_pct, stock,
                                     rating, review_count, launched, description)
               VALUES (?, ?, ?, (SELECT id FROM categories WHERE name = ?), ?, ?, ?, ?, ?, ?, ?)""",
            [tuple(r) for r in rows],
        )
        conn.execute(
            """INSERT INTO products_fts (rowid, name, brand, category, description)
               SELECT p.id, p.name, p.brand, c.name, p.description
               FROM products p JOIN categories c ON c.id = p.category_id"""
        )


def _number(value) -> float | None:
    try:
        return float(str(value).replace(",", "")) if value not in (None, "") else None
    except ValueError:
        return None


def _rupees(amount: float) -> str:
    return f"{int(round(amount)):,} rupees"


def _title(row) -> str:
    return row["name"] if row["brand"].lower() in row["name"].lower() else f"{row['brand']} {row['name']}"


def _stock(row) -> str:
    return "out of stock" if row["stock"] <= 0 else f"in stock ({row['stock']} left)"


def _summary(row) -> str:
    price = _rupees(row["final_price"])
    if row["discount_pct"]:
        price += f" after {row['discount_pct']}% off (list price {_rupees(row['price'])})"
    return (f"{_title(row)} ({row['category']}): {price}, "
            f"{row['rating']} stars from {row['review_count']:,} reviews, {_stock(row)}")


class ProductDB:
    def __init__(self, path: Path):
        self.path = Path(path)

    def ensure_seeded(self):
        if not self.path.exists():
            seed(self.path)

    @contextmanager
    def _connect(self):
        # A new connection per call: tools run in worker threads, and connections are cheap.
        conn = sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def categories(self) -> list[str]:
        with self._connect() as conn:
            return [r[0] for r in conn.execute("SELECT name FROM categories ORDER BY name")]

    def _category_named_in(self, words: list[str]) -> str | None:
        """The category a search word names: exactly ("laptops" -> Laptops) or via a common
        synonym ("phone" -> Smartphones, "tv" -> Televisions)."""
        by_stem = {name.lower().rstrip("s"): name for name in self.categories()}
        for word in words:
            stem = word.lower().rstrip("s")
            stem = CATEGORY_SYNONYMS.get(stem, stem)
            if stem in by_stem:
                return by_stem[stem]
        return None

    def vocabulary(self) -> list[str]:
        """Brand and product names, used to help speech recognition spell them correctly."""
        with self._connect() as conn:
            brands = [r[0] for r in conn.execute("SELECT DISTINCT brand FROM products ORDER BY brand")]
            names = [r[0] for r in conn.execute("SELECT name FROM products ORDER BY review_count DESC")]
        return brands + names

    def schema_text(self) -> str:
        """Compact schema description that goes into the SQL tool's description."""
        with self._connect() as conn:
            objects = conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') "
                "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'products_fts%' ORDER BY type, name"
            ).fetchall()
            lines = []
            for (name,) in objects:
                cols = ", ".join(f"{c['name']} {c['type']}".strip()
                                 for c in conn.execute(f"PRAGMA table_info('{name}')"))
                lines.append(f"{name}({cols})")
        return "\n".join(lines)

    # ---------- tools ----------

    def search(self, query: str = "", category: str = "", brand: str = "",
               min_price: float | None = None, max_price: float | None = None,
               in_stock_only: bool = False, sort_by: str = "relevance", limit: int = 5) -> str:
        # Small LLMs often send numbers and booleans as strings ("50000", "true").
        min_price, max_price = _number(min_price), _number(max_price)
        if min_price is not None and max_price is not None and min_price >= max_price:
            min_price = None  # "under 30000" often arrives as min=max=30000
        in_stock_only = str(in_stock_only).lower() in ("true", "1", "yes")
        query, category, brand = query or "", category or "", brand or ""
        where, params, joins, order = [], [], "", SORTS.get(sort_by, SORTS["relevance"])
        words = re.findall(r"\w+", query)
        if not category:
            # "laptop" should mean the Laptops category, not anything whose description mentions
            # laptops (like a power bank that "can charge laptops").
            category = self._category_named_in(words) or ""
        if category:
            # A keyword that just names the category ("phone" with Smartphones) is covered by the
            # category filter; as a keyword it would only match products with "phone" in the name.
            words = [w for w in words if (self._category_named_in([w]) or "").lower() != category.lower()]
        if words:
            # Any keyword may match; BM25 ranks products that match more (and rarer) words first.
            joins = "JOIN products_fts f ON f.rowid = p.id"
            where.append("products_fts MATCH ?")
            params.append(" OR ".join(f'"{w}"' for w in words))
            if sort_by in ("relevance", "", None):
                order = "bm25(products_fts, 4.0, 3.0, 2.0, 1.0)"
        if category:
            where.append("c.name LIKE ?")
            params.append(f"%{category.rstrip('s')}%")
        if brand:
            where.append("p.brand LIKE ?")
            params.append(f"%{brand}%")
        if min_price is not None:
            where.append("final_price >= ?")
            params.append(min_price)
        if max_price is not None:
            where.append("final_price <= ?")
            params.append(max_price)
        if in_stock_only:
            where.append("p.stock > 0")

        limit = max(1, min(int(_number(limit) or 5), 10))
        sql = (f"{_BASE_QUERY} {joins} {'WHERE ' + ' AND '.join(where) if where else ''} "
               f"ORDER BY {order} LIMIT ?")
        with self._connect() as conn:
            rows = conn.execute(sql, [*params, limit]).fetchall()
        if not rows:
            return "No products matched those filters. Try a broader search."
        return f"Found {len(rows)} product(s):\n" + "\n".join(_summary(r) for r in rows)

    def details(self, product: str) -> str:
        with self._connect() as conn:
            row = conn.execute(
                f"{_BASE_QUERY} WHERE p.sku = ? COLLATE NOCASE "
                f"OR (p.brand || ' ' || p.name) LIKE ? OR p.name LIKE ? "
                f"ORDER BY p.sku = ? COLLATE NOCASE DESC, length(p.name) LIMIT 1",
                (product, f"%{product}%", f"%{product}%", product),
            ).fetchone()
        if row is None:
            return f"No product found matching '{product}'. Use search_products to find it first."
        return (f"{_summary(row)}\nSKU: {row['sku']}\nLaunched: {row['launched']}\n"
                f"Details: {row['description']}")

    def run_sql(self, sql: str) -> str:
        sql = sql.strip().rstrip(";").strip()
        if ";" in sql:
            return "Only a single SELECT statement is allowed."

        def authorizer(action, *_):
            return sqlite3.SQLITE_OK if action in _ALLOWED_SQL_ACTIONS else sqlite3.SQLITE_DENY

        deadline = time.monotonic() + SQL_TIMEOUT_S
        try:
            with self._connect() as conn:
                conn.set_authorizer(authorizer)
                conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
                cur = conn.execute(sql)
                rows = cur.fetchmany(MAX_ROWS + 1)
                columns = [d[0] for d in cur.description or []]
        except sqlite3.DatabaseError as e:
            reason = "query took too long" if "interrupt" in str(e) else str(e)
            return (f"SQL error: {reason}. Only read-only SELECT queries on this schema work:\n"
                    f"{self.schema_text()}")
        if not rows:
            return "The query returned no rows."
        lines = [", ".join(f"{col}={row[i]}" for i, col in enumerate(columns))
                 for row in rows[:MAX_ROWS]]
        if len(rows) > MAX_ROWS:
            lines.append(f"(showing first {MAX_ROWS} rows only)")
        return "\n".join(lines)
