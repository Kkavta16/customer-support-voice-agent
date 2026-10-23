"""Tools available to the customer-support agent.

Each tool is defined once and exported in two formats:
  TOOL_SCHEMAS     OpenAI-style JSON schemas (used by the text agent / eval script)
  pipecat_tools()  Pipecat FunctionSchema objects (used by the voice bot)
"""
from collections.abc import Callable
from dataclasses import dataclass, field

from .config import PRODUCTS_DB, SUPPORT_DB
from .products import SORTS, ProductDB
from .support import POLICIES, SupportDB

catalog = ProductDB(PRODUCTS_DB)
catalog.ensure_seeded()
support = SupportDB(SUPPORT_DB)
support.ensure_seeded()


@dataclass
class Tool:
    name: str
    description: str
    fn: Callable[..., str]
    properties: dict = field(default_factory=dict)
    required: list[str] = field(default_factory=list)


TOOLS = [
    Tool(
        "search_products",
        "Search the store's product catalog. Use for any question about what products exist, "
        "prices, discounts, stock, or recommendations. Every filter is optional: only set the "
        "ones the customer actually mentioned.",
        catalog.search,
        {
            "query": {"type": "string",
                      "description": "Keywords or product name, e.g. 'noise cancelling', 'OnePlus 12R'"},
            "category": {"type": "string", "description": "One of: " + ", ".join(catalog.categories())},
            "brand": {"type": "string", "description": "Brand name, only if the customer named one"},
            "min_price": {"type": "number",
                          "description": "Only if the customer says 'above' or 'at least' a price"},
            "max_price": {"type": "number",
                          "description": "Only if the customer gives a budget, e.g. 'under 30000' -> 30000"},
            "in_stock_only": {"type": "boolean",
                              "description": "Only if the customer asks for available products"},
            "sort_by": {"type": "string", "enum": list(SORTS)},
            "limit": {"type": "integer", "description": "Max results, 1 to 10 (default 5)"},
        },
    ),
    Tool(
        "get_product_details",
        "Get full specifications, price, and stock for one product.",
        catalog.details,
        {"product": {"type": "string", "description": "Product name or SKU, e.g. 'Galaxy S24'"}},
        ["product"],
    ),
    Tool(
        "query_products_sql",
        "Run one read-only SQLite SELECT query on the product catalog, for aggregate questions "
        "like counts, averages, cheapest per category, or totals. Prefer the product_view view: "
        "it has category names and final_price (price after discount). Schema:\n"
        + catalog.schema_text(),
        catalog.run_sql,
        {"sql": {"type": "string", "description": "A single SQLite SELECT statement"}},
        ["sql"],
    ),
    Tool(
        "get_order_status",
        "Look up a customer's order: status, delivery date, tracking, return eligibility. "
        "Requires BOTH the order number and the last four digits of the phone number on the order; "
        "ask the customer for whichever is missing before calling.",
        support.order_status,
        {
            "order_id": {"type": "string", "description": "Order number, e.g. 'ORD-1001' or '1001'"},
            "phone_last4": {"type": "string", "description": "Last four digits of the customer's phone"},
        },
        ["order_id", "phone_last4"],
    ),
    Tool(
        "get_policy",
        "Get the store's official policy text. Always use this instead of guessing about returns, "
        "refunds, warranty, shipping, payment options, cancellation, or contact hours.",
        support.policy,
        {"topic": {"type": "string", "enum": list(POLICIES)}},
        ["topic"],
    ),
    Tool(
        "create_support_ticket",
        "Escalate to a human agent by creating a support ticket. Use when the customer asks for a "
        "human, is unhappy, or the issue can't be solved here (damaged item, warranty claim, "
        "payment problem). Collect a phone number or email first.",
        support.create_ticket,
        {
            "issue": {"type": "string", "description": "Short summary of the problem"},
            "customer_name": {"type": "string"},
            "contact": {"type": "string", "description": "Phone number or email for the callback"},
            "order_id": {"type": "string", "description": "Related order number, if any"},
        },
        ["issue", "contact"],
    ),
]

_BY_NAME = {t.name: t for t in TOOLS}

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": t.name,
            "description": t.description,
            "parameters": {"type": "object", "properties": t.properties, "required": t.required},
        },
    }
    for t in TOOLS
]


def pipecat_tools():
    from pipecat.adapters.schemas.function_schema import FunctionSchema
    from pipecat.adapters.schemas.tools_schema import ToolsSchema

    return ToolsSchema(standard_tools=[
        FunctionSchema(name=t.name, description=t.description, properties=t.properties,
                       required=t.required)
        for t in TOOLS
    ])


def run_tool(name: str, args: dict) -> str:
    tool = _BY_NAME.get(name)
    if tool is None:
        return f"Unknown tool: {name}"
    # Drop arguments the model invented that the function doesn't accept.
    args = {k: v for k, v in (args or {}).items() if k in tool.properties}
    try:
        return tool.fn(**args)
    except Exception as e:  # a failing tool must never crash the conversation
        return f"Tool {name} failed: {e}"
