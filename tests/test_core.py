"""Offline unit tests for the tool layer and speech clean-up (no models, no network)."""
import pytest

from app.speech import clean_for_speech, join_spoken_digits, looks_like_unfinished_number


def test_join_spoken_digits():
    assert join_spoken_digits("It ends in   4   8   2   1") == "It ends in 4821"
    assert join_spoken_digits("My phone ends in 0 0 0 0") == "My phone ends in 0000"
    assert join_spoken_digits("It's 4-8-2-1") == "It's 4821"
    assert join_spoken_digits("Where is my order 1001?") == "Where is my order 1001?"
    assert join_spoken_digits("phones under 30,000 rupees") == "phones under 30,000 rupees"


@pytest.mark.parametrize("text", [
    "My phone number ends in", "my phone number ends with...", "It ends in 4", "It ends in four eight",
    "4", "4-8", "8 2", "My order number is 10",
])
def test_unfinished_numbers_keep_turn_open(text):
    assert looks_like_unfinished_number(text)


@pytest.mark.parametrize("text", [
    "My phone number ends in 4 8 2 1", "It ends in 4821", "Where is my order 1001?",
    "What is your return policy?", "Which laptops do you have under 70,000 rupees?", "Hello", "",
])
def test_finished_turns_end_normally(text):
    assert not looks_like_unfinished_number(text)
from app.tools import TOOL_SCHEMAS, run_tool


def test_clean_for_speech():
    assert clean_for_speech("- [HP-001] **Sony WH-1000XM5**: ₹25,492") == "Sony WH-1000XM5: 25,492 rupees"
    assert clean_for_speech("About Rs. 94,429.") == "About 94,429 rupees."


def test_tool_schemas_cover_support_tools():
    names = {s["function"]["name"] for s in TOOL_SCHEMAS}
    assert names == {"search_products", "get_product_details", "query_products_sql",
                     "get_order_status", "get_policy", "create_support_ticket"}


def test_run_tool_unknown_and_invented_args():
    assert run_tool("nope", {}) == "Unknown tool: nope"
    # Arguments the model invents are dropped instead of crashing the call.
    assert "Galaxy S24" in run_tool("get_product_details", {"product": "Galaxy S24", "color": "red"})
