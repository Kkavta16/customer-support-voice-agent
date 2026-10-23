"""Customer-support data: orders, store policies and support tickets.

Kept in its own SQLite file (data/support.db), separate from the product catalog, so the
free-form SQL tool, which can only see the catalog, can never read customer data.
Order lookups require the order number *and* the last four digits of the phone number.
"""
import re
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path

RETURN_WINDOW_DAYS = 10

SCHEMA_SQL = """
CREATE TABLE orders (
    order_id          TEXT PRIMARY KEY,   -- e.g. ORD-1001
    customer_name     TEXT NOT NULL,
    phone_last4       TEXT NOT NULL,
    status            TEXT NOT NULL,      -- processing | shipped | out_for_delivery | delivered | cancelled | return_requested
    placed_on         TEXT NOT NULL,
    expected_delivery TEXT,
    delivered_on      TEXT,
    carrier           TEXT,
    tracking_number   TEXT,
    items             TEXT NOT NULL,
    total             REAL NOT NULL,
    notes             TEXT
);
CREATE TABLE tickets (
    ticket_id     TEXT PRIMARY KEY,       -- e.g. TCK-1001
    created       TEXT NOT NULL,
    customer_name TEXT,
    contact       TEXT,
    order_id      TEXT,
    issue         TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'open'
);
"""

SAMPLE_ORDERS = [
    ("ORD-1001", "Priya Sharma", "4821", "shipped", "2026-10-19", "2026-10-25", None,
     "Blue Dart", "BD778123456", "Samsung Galaxy S24", 67499, None),
    ("ORD-1002", "Rahul Verma", "1934", "delivered", "2026-10-10", "2026-10-14", "2026-10-14",
     "Delhivery", "DL552019833", "Sony WH-1000XM5 headphones", 25492, None),
    ("ORD-1003", "Ananya Iyer", "7710", "processing", "2026-10-22", "2026-10-28", None,
     None, None, "Apple MacBook Air 13 M3, Logitech MX Master 3S", 115352, None),
    ("ORD-1004", "Arjun Mehta", "5502", "out_for_delivery", "2026-10-18", "2026-10-23", None,
     "Ekart", "EK901276554", "LG 55-inch C3 OLED TV", 104993, "Installation visit will be scheduled after delivery"),
    ("ORD-1005", "Sneha Reddy", "3367", "cancelled", "2026-10-12", None, None,
     None, None, "boAt Rockerz 450", 899, "Cancelled by customer; refund of 899 rupees issued on 2026-10-13"),
    ("ORD-1006", "Vikram Singh", "9045", "delivered", "2026-09-15", "2026-09-20", "2026-09-20",
     "Blue Dart", "BD771100293", "Philips Air Fryer HD9252", 6029, None),
    ("ORD-1007", "Kavya Nair", "2288", "return_requested", "2026-10-05", "2026-10-09", "2026-10-09",
     "Delhivery", "DL550087121", "Noise ColorFit Pro 5", 2999,
     "Return pickup scheduled for 2026-10-24; refund within 5 to 7 business days after pickup"),
]

STATUS_TEXT = {
    "processing": "is being processed and has not shipped yet",
    "shipped": "has shipped",
    "out_for_delivery": "is out for delivery today",
    "delivered": "was delivered",
    "cancelled": "was cancelled",
    "return_requested": "has a return in progress",
}

POLICIES = {
    "returns": (
        f"Most products can be returned within {RETURN_WINDOW_DAYS} days of delivery if they are "
        "unused and in the original packaging. Opened headphones and earbuds can only be returned if "
        "defective. To start a return, the customer needs their order number; we arrange a free pickup."
    ),
    "refunds": (
        "Refunds go back to the original payment method within 5 to 7 business days after the "
        "returned item is picked up and inspected. Cash on delivery orders are refunded by bank transfer."
    ),
    "warranty": (
        "All products carry the manufacturer's warranty, usually 1 year (2 years for laptops and "
        "TVs from LG, Samsung and Sony). Warranty claims are handled at the brand's service centre; "
        "we can raise a support ticket to help arrange it."
    ),
    "shipping": (
        "Delivery is free on orders above 499 rupees, otherwise 49 rupees. Metro cities usually get "
        "delivery in 2 to 3 days, other locations in 4 to 7 days. Large TVs and appliances include "
        "free installation."
    ),
    "payment": (
        "We accept UPI, credit and debit cards, net banking, and cash on delivery for orders up to "
        "50,000 rupees. No-cost EMI is available on cards from major banks for orders above 10,000 rupees."
    ),
    "cancellation": (
        "Orders can be cancelled for free until they ship. After shipping, the customer can refuse "
        "delivery or request a return once it arrives."
    ),
    "contact": (
        "Human support agents are available from 9 AM to 9 PM, Monday to Saturday, at "
        "1800-123-4567 or support@novaelectronics.example. Outside those hours we can raise a "
        "ticket and an agent will call back."
    ),
}
_POLICY_ALIASES = {
    "return": "returns", "exchange": "returns", "refund": "refunds", "money back": "refunds",
    "warrant": "warranty", "guarantee": "warranty", "repair": "warranty", "deliver": "shipping",
    "ship": "shipping", "install": "shipping", "pay": "payment", "emi": "payment", "upi": "payment",
    "cod": "payment", "cash": "payment", "cancel": "cancellation", "contact": "contact",
    "phone": "contact", "hour": "contact", "human": "contact", "email": "contact",
}


def seed(path: Path, orders=SAMPLE_ORDERS):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executescript(SCHEMA_SQL)
        conn.executemany(f"INSERT INTO orders VALUES ({', '.join('?' * 12)})", orders)


def _spoken_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.strftime('%A')}, {d.day} {d.strftime('%B')}"


_DIGIT_WORDS = {"zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4",
                "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9"}
_DIGIT_WORD_RE = re.compile(r"\b(" + "|".join(_DIGIT_WORDS) + r")\b", re.I)


def extract_digits(text: str) -> str:
    """All digits in spoken-style text, in order: '4-8', 'four eight two one', '48 21' -> digits."""
    text = _DIGIT_WORD_RE.sub(lambda m: _DIGIT_WORDS[m.group(1).lower()], str(text))
    return re.sub(r"\D", "", text)


def recover_phone_digits(given: str, recent_user_texts: list[str], order_id: str = "") -> str:
    """Callers often say phone digits with pauses, so speech recognition splits them across
    several turns ("ends with...", "4-8", "2-1") and the model may pass only the last fragment.
    If fewer than four digits were given, rebuild them from the caller's latest turns."""
    digits = extract_digits(given)
    if len(digits) >= 4:
        return digits[-4:]
    order_digits = extract_digits(order_id)
    pool = ""
    for text in recent_user_texts[-4:]:
        found = extract_digits(text)
        if order_digits and order_digits in found:
            found = found.split(order_digits)[-1]  # drop the order number itself
        pool += found
    return pool[-4:] if len(pool) >= 4 else digits


def _normalize_order_id(order_id: str) -> str:
    digits = extract_digits(order_id)
    return f"ORD-{digits}" if digits else ""


class SupportDB:
    def __init__(self, path: Path, today: date | None = None):
        self.path = Path(path)
        self._today = today  # fixed date for tests; real "today" otherwise

    @property
    def today(self) -> date:
        return self._today or date.today()

    def ensure_seeded(self):
        if not self.path.exists():
            seed(self.path)

    def _connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return closing(conn)

    # ---------- tools ----------

    def order_status(self, order_id: str, phone_last4: str) -> str:
        oid = _normalize_order_id(order_id)
        last4 = extract_digits(phone_last4)[-4:]
        if not oid:
            return "Ask the customer for their order number."
        if len(last4) != 4:
            return "Ask the customer for the last four digits of the phone number on the order."
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM orders WHERE order_id = ?", (oid,)).fetchone()
        # Same message for "no such order" and "wrong phone" so order numbers can't be probed.
        if row is None or row["phone_last4"] != last4:
            return (f"No order matches order number {oid} with phone digits {' '.join(last4)}. Read "
                    "both back to the customer and ask them to confirm, saying all four digits "
                    "together. Do not reveal whether the order exists.")

        first_name = row["customer_name"].split()[0]
        parts = [f"Verified. Customer first name: {first_name}. Order {oid} for {row['items']} "
                 f"(total {int(row['total']):,} rupees, placed {_spoken_date(row['placed_on'])}) "
                 f"{STATUS_TEXT.get(row['status'], row['status'])}"]
        if row["status"] == "delivered" and row["delivered_on"]:
            delivered = date.fromisoformat(row["delivered_on"])
            parts[0] += f" on {_spoken_date(row['delivered_on'])}"
            last_day = delivered + timedelta(days=RETURN_WINDOW_DAYS)
            parts.append(f"Return window open until {_spoken_date(last_day.isoformat())}"
                         if self.today <= last_day else
                         f"The {RETURN_WINDOW_DAYS}-day return window closed on "
                         f"{_spoken_date(last_day.isoformat())}; only warranty support is possible")
        elif row["status"] in ("processing", "shipped", "out_for_delivery") and row["expected_delivery"]:
            parts.append(f"Expected delivery {_spoken_date(row['expected_delivery'])}")
        if row["carrier"] and row["status"] in ("shipped", "out_for_delivery"):
            parts.append(f"Carrier {row['carrier']}, tracking number {row['tracking_number']}")
        if row["notes"]:
            parts.append(row["notes"])
        return ". ".join(parts) + "."

    def policy(self, topic: str) -> str:
        t = (topic or "").lower()
        key = t if t in POLICIES else next((v for k, v in _POLICY_ALIASES.items() if k in t), None)
        if key is None:
            return f"No policy found for '{topic}'. Available topics: {', '.join(POLICIES)}."
        return POLICIES[key]

    def create_ticket(self, issue: str, customer_name: str = "", contact: str = "",
                      order_id: str = "") -> str:
        if not (issue or "").strip():
            return "Ask the customer to describe the issue first."
        if not (contact or "").strip():
            return "Ask the customer for a phone number or email so a human agent can reach them."
        with self._connect() as conn, conn:
            n = conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
            ticket_id = f"TCK-{1001 + n}"
            conn.execute(
                "INSERT INTO tickets (ticket_id, created, customer_name, contact, order_id, issue) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (ticket_id, datetime.now().isoformat(timespec="seconds"), customer_name.strip(),
                 contact.strip(), _normalize_order_id(order_id) or None, issue.strip()),
            )
        spelled = " ".join(ticket_id.split("-")[1])  # "1 0 0 1" reads clearly over voice
        return (f"Created ticket {ticket_id} (say it as: T C K {spelled}). A human support agent "
                "will contact the customer within 24 hours.")
