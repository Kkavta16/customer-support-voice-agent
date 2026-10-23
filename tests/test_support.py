"""Customer-support data tests, run against a throwaway copy of the sample data."""
from datetime import date

import pytest

from app.support import SupportDB, extract_digits, recover_phone_digits, seed


def test_extract_digits_from_spoken_forms():
    assert extract_digits("4-8") == "48"
    assert extract_digits("four eight two one") == "4821"
    assert extract_digits("It ends in 48 21.") == "4821"
    assert extract_digits("Oh one two") == "012"


def test_recover_phone_digits_split_across_turns():
    # Real transcripts from a call where pauses split the digits into separate turns.
    turns = ["Hello", "Where is my order 1001 and my phone number ends with...", "4A Tool", "4-8", "2-1"]
    assert recover_phone_digits("21", turns, "1001") == "4821"
    turns = ["Where is my order 1001?", "It ends in four", "8", "2", "1"]
    assert recover_phone_digits("1", turns, "1001") == "4821"


def test_recover_phone_digits_keeps_complete_answer():
    assert recover_phone_digits("4821", ["something 9999"], "1001") == "4821"
    assert recover_phone_digits("21", ["hi", "21"], "1001") == "21"  # not enough digits: leave as is


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "support.db"
    seed(path)
    return SupportDB(path, today=date(2026, 10, 23))


def test_order_lookup_requires_matching_phone(db):
    ok = db.order_status("ORD-1001", "4821")
    assert ok.startswith("Verified") and "Priya" in ok and "Blue Dart" in ok and "25 October" in ok
    wrong = db.order_status("ORD-1001", "0000")
    missing = db.order_status("ORD-9999", "0000")
    assert "Priya" not in wrong and "Galaxy" not in wrong
    assert wrong == missing.replace("9999", "1001")  # can't tell "wrong phone" from "no such order"
    assert "0 0 0 0" in wrong  # digits are read back so the caller can correct a mishearing


def test_order_id_and_phone_normalization(db):
    assert db.order_status("order 1001", "my number ends in 4821").startswith("Verified")
    assert "order number" in db.order_status("", "4821")
    assert "last four digits" in db.order_status("1001", "")


def test_return_window(db):
    assert "Return window open until" in db.order_status("1002", "1934")       # delivered 14 Oct
    assert "return window closed" in db.order_status("1006", "9045")           # delivered 20 Sep


def test_policy_lookup_with_aliases(db):
    assert "10 days" in db.policy("returns")
    assert "10 days" in db.policy("can I exchange this")
    assert "EMI" in db.policy("pay")
    assert "Available topics" in db.policy("loyalty points")


def test_ticket_creation(db):
    assert "phone number or email" in db.create_ticket("Broken screen")
    first = db.create_ticket("Broken screen", "Asha", "9876543210", "1001")
    second = db.create_ticket("Refund delay", "Ravi", "ravi@example.com")
    assert "TCK-1001" in first and "T C K 1 0 0 1" in first
    assert "TCK-1002" in second
