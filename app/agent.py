"""LLM agent: streams a reply from a local Ollama model and runs tool calls in a loop."""
import asyncio
from collections.abc import AsyncIterator

from ollama import AsyncClient

from .config import LLM_MODEL, MAX_HISTORY, MAX_TOOL_ROUNDS, OLLAMA_HOST, SYSTEM_PROMPT
from .tools import TOOL_SCHEMAS, run_tool

_thinking_support: dict[str, bool] = {}  # model name -> reports the "thinking" capability


class Agent:
    def __init__(self, model: str = LLM_MODEL):
        self.client = AsyncClient(host=OLLAMA_HOST)
        self.model = model
        self.history: list[dict] = []

    async def _supports_thinking(self) -> bool:
        if self.model not in _thinking_support:
            info = await self.client.show(self.model)
            _thinking_support[self.model] = "thinking" in (info.capabilities or [])
        return _thinking_support[self.model]

    def reset(self):
        self.history.clear()

    def _context(self) -> list[dict]:
        recent = self.history[-MAX_HISTORY:]
        # Never start the window mid-exchange (e.g. with an orphaned tool result).
        while recent and recent[0]["role"] != "user":
            recent = recent[1:]
        return [{"role": "system", "content": SYSTEM_PROMPT}, *recent]

    async def respond(self, user_text: str) -> AsyncIterator[dict]:
        """Yield {"type": "token"} and {"type": "tool"} events for one user turn."""
        self.history.append({"role": "user", "content": user_text})

        for round_no in range(MAX_TOOL_ROUNDS + 1):
            # On the last round, withhold tools so the model must answer in words.
            tools = TOOL_SCHEMAS if round_no < MAX_TOOL_ROUNDS else None
            content, tool_calls = "", []

            stream = await self.client.chat(
                model=self.model, messages=self._context(), tools=tools, stream=True,
                options={"temperature": 0.6, "num_ctx": 4096},
                # Reasoning models (Qwen3...) would otherwise "think" silently for seconds
                # before speaking; a voice agent can't afford that latency.
                think=False if await self._supports_thinking() else None,
            )
            async for chunk in stream:
                msg = chunk.message
                if msg.tool_calls:
                    tool_calls.extend(msg.tool_calls)
                if msg.content:
                    content += msg.content
                    yield {"type": "token", "text": msg.content}

            assistant = {"role": "assistant", "content": content}
            if tool_calls:
                assistant["tool_calls"] = [
                    {"function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in tool_calls
                ]
            self.history.append(assistant)
            if not tool_calls:
                return

            for tc in tool_calls:
                name, args = tc.function.name, dict(tc.function.arguments or {})
                result = await asyncio.to_thread(run_tool, name, args)
                yield {"type": "tool", "name": name, "args": args, "result": result}
                self.history.append({"role": "tool", "content": result, "tool_name": name})
