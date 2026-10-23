"""Measure how reliably an LLM answers product and customer-support questions from the databases.

Text in, text out (no audio), so it isolates the LLM + tool layer. Each case checks the
final spoken reply for words that must (and must not) appear.

  python scripts/eval_products.py                        # model from config
  python scripts/eval_products.py qwen2.5:3b qwen3:4b    # compare models
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.agent import Agent  # noqa: E402
from app.config import LLM_MODEL  # noqa: E402

# (question, all of these must appear, none of these may appear) - case-insensitive
CASES = [
    ("Which smartphones do you have under 30,000 rupees?", ["Redmi"], ["iPhone", "Galaxy S24", "Pixel"]),
    ("Is the OnePlus 12R in stock?", ["out of stock|not in stock|not available|unavailable"], []),
    ("How much does the MacBook Air cost?", ["106,857|106857|1,06,857"], []),
    ("Do you sell any Samsung TVs?", ["Crystal"], []),
    ("What's the cheapest laptop that is in stock?", ["Pavilion"], ["Aspire"]),
    ("Which brand has the most products?", ["Samsung"], []),
    ("How many products are out of stock?", ["5|five"], []),
    ("What is the best rated smartwatch?", ["Apple Watch"], []),
    ("Recommend noise cancelling headphones under 30,000 rupees.", ["WH-1000XM5|Sony|AirPods"], ["Bose"]),
    ("Does the Galaxy S24 have a Snapdragon processor?", ["yes|Snapdragon"], []),
    ("What gaming laptops do you have?", ["ROG|Strix|Aspire"], []),
    ("Do you have any Nokia phones?", ["no|don't|do not|not|couldn't|unable"], []),
    # Customer-support cases
    ("Where is my order ORD-1001? My phone number ends in 4821.", ["Blue Dart|25|shipped"], []),
    ("Can you check order 1001 for me? The last four digits are 0000.", [], ["Priya", "Galaxy", "Blue Dart"]),
    ("What is your return policy?", ["10 days|ten days"], []),
    ("Do you offer EMI?", ["EMI|no-cost|installment"], []),
]


def check(reply: str, must: list[str], must_not: list[str]) -> bool:
    text = reply.lower()
    ok_must = all(any(alt.lower() in text for alt in group.split("|")) for group in must)
    return ok_must and not any(bad.lower() in text for bad in must_not)


async def run_case(model: str, question: str) -> tuple[str, list[str], float]:
    agent, reply, tools = Agent(model), "", []
    start = time.perf_counter()
    async for ev in agent.respond(question):
        if ev["type"] == "token":
            reply += ev["text"]
        else:
            tools.append(f"{ev['name']}({ev['args']})")
    return reply.strip(), tools, time.perf_counter() - start


async def evaluate(model: str) -> tuple[int, float]:
    print(f"\n=== {model} ===")
    passed, total_time = 0, 0.0
    for question, must, must_not in CASES:
        reply, tools, secs = await run_case(model, question)
        ok = check(reply, must, must_not)
        passed += ok
        total_time += secs
        print(f"{'PASS' if ok else 'FAIL'} ({secs:4.1f}s) {question}")
        if not ok:
            print(f"     tools: {'; '.join(tools) or 'none'}\n     reply: {reply[:200]}")
    print(f"--> {passed}/{len(CASES)} passed, avg {total_time / len(CASES):.1f}s per question")
    return passed, total_time / len(CASES)


async def main(models: list[str]):
    results = {m: await evaluate(m) for m in models}
    if len(results) > 1:
        print("\nSummary:")
        for m, (p, t) in results.items():
            print(f"  {m:20s} {p}/{len(CASES)}  avg {t:.1f}s")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or [LLM_MODEL]))
