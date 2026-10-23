"""Product database tests, run against a throwaway copy of the sample catalog."""
import pytest

from app.products import ProductDB, seed


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    path = tmp_path_factory.mktemp("db") / "products.db"
    seed(path)
    return ProductDB(path)


def test_search_by_category_and_budget(db):
    out = db.search(category="Smartphones", max_price=30000)
    assert "Redmi Note 13 Pro" in out and "Phone (2a)" in out
    assert "iPhone" not in out


def test_search_keywords_handle_plurals(db):
    out = db.search(query="noise cancelling headphones", limit=10)
    assert "WH-1000XM5" in out


def test_search_in_stock_and_sort(db):
    out = db.search(category="Laptops", in_stock_only=True, sort_by="price_low")
    assert "Aspire 7" not in out  # out of stock
    assert out.index("Pavilion 15") < out.index("XPS 14")


def test_search_accepts_string_arguments(db):
    out = db.search(category="Laptops", max_price="70,000", in_stock_only="true", limit="2")
    assert "Pavilion 15" in out and "XPS" not in out


def test_search_equal_min_and_max_means_budget(db):
    out = db.search(query="smartphone", min_price=30000, max_price=30000)
    assert "Redmi Note 13 Pro" in out


def test_search_keyword_naming_a_category_filters_to_it(db):
    out = db.search(query="laptop", max_price=70000, in_stock_only=True, sort_by="price_low")
    assert "Pavilion" in out and "ThinkPad" in out
    assert "Power Bank" not in out  # its description says "can charge laptops"
    assert "Redmi" in db.search(query="phones", max_price=30000)
    # The exact call the model made in the demo: keyword repeats the category.
    both = db.search(query="phone", category="Smartphones", max_price=30000, in_stock_only=True)
    assert "Redmi" in both and "Phone (2a)" in both


def test_search_no_results(db):
    assert "No products matched" in db.search(query="hoverboard")


def test_details_by_name_and_sku(db):
    assert "Snapdragon 8 Gen 3" in db.details("galaxy s24")
    assert "MacBook Air" in db.details("LP-001")
    assert "No product found" in db.details("Nokia 3310")


def test_sql_aggregate(db):
    out = db.run_sql("SELECT COUNT(*) AS n FROM products WHERE stock = 0")
    assert out == "n=5"


def test_sql_view_with_final_price(db):
    out = db.run_sql("SELECT name, final_price FROM product_view WHERE sku = 'PH-001'")
    assert out == "name=Galaxy S24, final_price=67499.0"


@pytest.mark.parametrize("sql", [
    "DELETE FROM products",
    "UPDATE products SET price = 1",
    "DROP TABLE products",
    "INSERT INTO categories (name) VALUES ('x')",
    "SELECT 1; DROP TABLE products",
    "PRAGMA table_info(products)",
    "ATTACH DATABASE 'evil.db' AS evil",
])
def test_sql_rejects_writes_and_side_effects(db, sql):
    out = db.run_sql(sql)
    assert out.startswith(("SQL error", "Only a single"))
    assert db.run_sql("SELECT COUNT(*) AS n FROM products") == "n=38"


def test_sql_times_out_on_runaway_query(db):
    out = db.run_sql(
        "WITH RECURSIVE r(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM r) SELECT MAX(x) FROM r"
    )
    assert "took too long" in out


def test_sql_caps_rows(db):
    out = db.run_sql("SELECT sku FROM products")
    assert "showing first 20 rows" in out
