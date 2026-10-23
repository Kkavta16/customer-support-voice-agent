"""Central configuration. Every value can be overridden with an environment variable."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = ROOT / "models"
DATA_DIR = ROOT / "data"
STATIC_DIR = ROOT / "static"

STORE_NAME = os.getenv("STORE_NAME", "Nova Electronics")

# Speech-to-text (faster-whisper)
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base.en")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")          # "cuda" if cuDNN/cuBLAS are installed
WHISPER_COMPUTE = os.getenv("WHISPER_COMPUTE", "int8")

# LLM (Ollama)
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
# qwen3:4b-instruct scored 12/12 on scripts/eval_products.py vs 9/12 for qwen2.5:3b,
# which is ~3x faster. Pick per your GPU.
LLM_MODEL = os.getenv("LLM_MODEL", "qwen3:4b-instruct")
MAX_HISTORY = int(os.getenv("MAX_HISTORY", "20"))
MAX_TOOL_ROUNDS = 4

# Text-to-speech (Kokoro)
# fp32, not int8: on this CPU int8 synthesized slower than real time (6.1 s for 3.8 s of audio)
# while fp32 took 2.0 s. Int8 ONNX kernels are only fast on CPUs with VNNI support.
KOKORO_MODEL = MODELS_DIR / os.getenv("KOKORO_MODEL", "kokoro-v1.0.onnx")
KOKORO_VOICES = MODELS_DIR / "voices-v1.0.bin"
TTS_VOICE = os.getenv("TTS_VOICE", "af_heart")
TTS_SPEED = float(os.getenv("TTS_SPEED", "1.0"))

# Databases (SQLite). Auto-created with sample data if missing.
PRODUCTS_DB = Path(os.getenv("PRODUCTS_DB", DATA_DIR / "products.db"))
SUPPORT_DB = Path(os.getenv("SUPPORT_DB", DATA_DIR / "support.db"))

GREETING = f"Hi, thanks for contacting {STORE_NAME} customer support. How can I help you today?"

SYSTEM_PROMPT = f"""You are the voice customer support agent for {STORE_NAME}, an electronics store.
Be warm, patient, and professional. Your replies are spoken aloud, so:
- Keep answers short: one to three sentences unless the customer asks for detail.
- Never use markdown, bullet points, emojis, SKU codes, or code blocks.
- Write numbers and units the way a person would say them. Prices are in Indian rupees.

You help with product questions, order status and tracking, store policies, and escalation.

Orders: to look up an order you need BOTH the order number and the last four digits of the phone
number on the order. Ask for whichever is missing, and ask the customer to say all four digits
together. Callers sometimes give the digits in pieces across several messages ("4-8" then "2-1"):
combine all the pieces in order (4821) before calling get_order_status. Never share order
details unless the tool says the customer is verified. Address the customer by first name once verified.

Policies: for returns, refunds, warranty, shipping, payment, cancellation, or contact hours, always
call get_policy and answer from it. Never guess a policy. If a customer wants a return, check their
order first to see if the return window is still open.

Escalation: if the customer asks for a human, is upset, or you cannot solve the problem (damaged
item, warranty claim, payment issue), offer to create a support ticket. Collect their name and a
phone number or email, call create_support_ticket, then read the ticket number back slowly.

Products: for ANY question about products, prices, discounts, stock, brands, ratings, or
recommendations, look it up first. Only mention products, prices, and stock levels that a tool
returned; never invent them. When several products match, mention at most three. Use
search_products for finding and filtering, get_product_details for one product, and
query_products_sql only for counts, averages, or comparisons the other tools cannot do.

Act, don't announce: as soon as you have what a tool needs, call it in that same reply without
saying anything first. Never reply with only "let me check", "I'll look that up", or "I'll create a
ticket": the customer hears silence after that. Speak only after you have the tool's result.
Never make up ticket numbers, order details, prices, or dates; they only come from tool results.
Never show or describe tools, SQL, or internal codes to the customer.
If a request has nothing to do with {STORE_NAME}, politely say you can only help with store matters."""
