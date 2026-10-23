"""End-to-end test of the real voice path, acting like the browser: runs the demo script.

Connects to the running server over WebRTC (aiortc), speaks each demo line into the call as audio
(synthesized with Kokoro in a different voice), and checks every reply against the database
facts. Also checks that tool calls reach the client by name only (no SQL, arguments or results).

  python scripts/e2e_voice.py            (server must be running on :8000)
"""
import asyncio
import fractions
import json
import os
import sys
import time
import uuid
from pathlib import Path

import av
import httpx
import numpy as np
from aiortc import MediaStreamTrack, RTCPeerConnection, RTCSessionDescription

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import KOKORO_MODEL, KOKORO_VOICES  # noqa: E402

SERVER = "http://localhost:8000"
# The demo script: (what the caller says, groups that must all appear in the reply where "a|b"
# means either, words that must NOT appear). A list is spoken as pieces with pauses.
DEMO = [
    ("Where is my order 1001?", ["digit"], ["Priya", "Galaxy", "Blue Dart"]),
    (["It ends in", "four", "eight", "two", "one"],
     ["Priya", "Blue Dart|shipped", "25|Sunday"], []),
    ("Can I return it if I don't like it?", ["10 days|ten days"], []),
    ("Which phones do you have under 30,000 rupees?",
     ["Redmi", "Nothing|2a"], ["iPhone", "Galaxy S24", "Pixel"]),
    ("Can you check order 1002? My phone ends in 0000.",
     ["0 0 0 0|0000|confirm|match|couldn't|could not|unable|double"], ["Rahul", "Sony", "WH-1000"]),
    ("I want to talk to a human about a damaged screen. My name is Asha and my phone number is "
     "98765 43210.", ["1 0 0 1|1001"], []),
]
DIGIT_PAUSE_SECS = 0.7
SAMPLE_RATE = 24000
FRAME = SAMPLE_RATE // 50  # 20 ms


class MicTrack(MediaStreamTrack):
    """Outgoing audio: silence, except while a queued utterance is playing."""
    kind = "audio"

    def __init__(self):
        super().__init__()
        self.queue = np.zeros(0, dtype=np.int16)
        self.pts = 0
        self.start = None

    def say(self, samples: np.ndarray):
        pad = np.zeros(SAMPLE_RATE // 2, dtype=np.int16)
        self.queue = np.concatenate([self.queue, pad, samples, pad])

    @property
    def speaking(self) -> bool:
        return self.queue.size > 0

    async def recv(self):
        self.start = self.start or time.perf_counter()
        delay = self.start + self.pts / SAMPLE_RATE - time.perf_counter()
        if delay > 0:
            await asyncio.sleep(delay)  # pace in real time like a microphone
        chunk, self.queue = self.queue[:FRAME], self.queue[FRAME:]
        chunk = np.pad(chunk, (0, FRAME - chunk.size))
        frame = av.AudioFrame.from_ndarray(chunk.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate, frame.pts, frame.time_base = SAMPLE_RATE, self.pts, fractions.Fraction(1, SAMPLE_RATE)
        self.pts += FRAME
        return frame


class Session:
    def __init__(self):
        self.pc = RTCPeerConnection()
        self.mic = MicTrack()
        self.dc = None
        self.bot_speaking = False
        self.last_bot_activity = time.perf_counter()
        self.bot_audio_secs = 0.0
        self.leaks: list[str] = []
        self.ready = asyncio.Event()
        self.user_stopped_at = None
        self.response_gaps: list[float] = []
        self.turn_text: list[str] = []

    def send(self, msg_type: str, data: dict):
        self.dc.send(json.dumps({"label": "rtvi-ai", "type": msg_type, "id": uuid.uuid4().hex[:8], "data": data}))

    def on_message(self, raw: str):
        msg = json.loads(raw)
        t, d = msg.get("type"), msg.get("data") or {}
        if t == "bot-ready":
            self.ready.set()
        elif t == "user-stopped-speaking":
            self.user_stopped_at = time.perf_counter()
        elif t == "bot-started-speaking":
            self.bot_speaking = True
            if self.user_stopped_at:
                gap = time.perf_counter() - self.user_stopped_at
                self.response_gaps.append(gap)
                print(f"  [latency]     agent started speaking {gap:.1f}s after you stopped")
                self.user_stopped_at = None
        elif t == "bot-stopped-speaking":
            self.bot_speaking = False
        elif t == "user-transcription" and d.get("final"):
            print(f"  YOU (heard):  {d['text']}")
        elif t == "bot-output" and d.get("spoken_status") in (None, "new") and d.get("text"):
            print(f"  AGENT:        {d['text']}")
            self.turn_text.append(d["text"])
        elif t.startswith("llm-function-call"):
            if {"arguments", "result"} & set(d):
                self.leaks.append(raw)
            if t == "llm-function-call-started":
                print(f"  [activity]    {d.get('function_name')}  (payload keys: {sorted(d)})")
        if t.startswith(("bot-", "llm-")):
            self.last_bot_activity = time.perf_counter()

    async def record_bot_audio(self, track):
        while True:
            try:
                frame = await track.recv()
            except Exception:
                return
            pcm = frame.to_ndarray().astype(np.float32) / 32768
            if np.sqrt(np.mean(pcm ** 2)) > 0.01:
                self.bot_audio_secs += frame.samples / frame.sample_rate

    async def connect(self):
        self.pc.addTrack(self.mic)
        self.dc = self.pc.createDataChannel("chat", ordered=True)
        self.dc.on("open", lambda: self.send("client-ready", {"version": "2.2.0", "about": {"library": "e2e-test"}}))
        self.dc.on("message", self.on_message)
        self.pc.on("track", lambda track: track.kind == "audio" and asyncio.ensure_future(self.record_bot_audio(track)))
        await self.pc.setLocalDescription(await self.pc.createOffer())
        async with httpx.AsyncClient(timeout=60) as http:
            answer = (await http.post(f"{SERVER}/api/offer", json={
                "sdp": self.pc.localDescription.sdp, "type": "offer", "pc_id": None, "restart_pc": False,
            })).json()
        await self.pc.setRemoteDescription(RTCSessionDescription(sdp=answer["sdp"], type=answer["type"]))
        await asyncio.wait_for(self.ready.wait(), 60)

    async def wait_for_reply(self, timeout: float = 120):
        """Wait for the agent to start answering, then for it to finish."""
        start = time.perf_counter()
        while not self.bot_speaking and time.perf_counter() - start < timeout:
            await asyncio.sleep(0.2)
        await self.wait_until_idle(timeout=timeout)

    async def wait_until_idle(self, quiet_secs: float = 8.0, timeout: float = 120):
        start = time.perf_counter()
        while time.perf_counter() - start < timeout:
            idle = not self.bot_speaking and not self.mic.speaking
            if idle and time.perf_counter() - self.last_bot_activity > quiet_secs:
                return
            await asyncio.sleep(0.2)
        print("  (timed out waiting for the agent)")


async def main():
    from kokoro_onnx import Kokoro
    kokoro = Kokoro(str(KOKORO_MODEL), str(KOKORO_VOICES))

    def voice(text):
        if isinstance(text, list):
            pause = np.zeros(int(SAMPLE_RATE * DIGIT_PAUSE_SECS), dtype=np.int16)
            pieces = [voice(t) for t in text]
            return np.concatenate([x for p in pieces for x in (p, pause)][:-1])
        samples, sr = kokoro.create(text, voice="am_michael", speed=1.0, lang="en-us")
        assert sr == SAMPLE_RATE
        return (np.clip(samples, -1, 1) * 32767).astype(np.int16)

    # Synthesize up front: running Kokoro during the call would stall the outgoing audio.
    audio = [voice(q) for q, _, _ in DEMO]

    s = Session()
    print("Connecting…")
    await s.connect()
    await s.wait_for_reply()  # greeting

    results = []
    for (q, must, must_not), clip in zip(DEMO, audio):
        print(f"\n>>> {' ... '.join(q) if isinstance(q, list) else q}")
        s.turn_text = []
        s.mic.say(clip)
        await s.wait_for_reply()
        reply = " ".join(s.turn_text).lower()
        ok = all(any(alt.lower() in reply for alt in group.split("|")) for group in must) \
            and not any(bad.lower() in reply for bad in must_not)
        results.append(ok)
        print(f"  ==> {'PASS' if ok else 'FAIL'}")

    await s.pc.close()
    print(f"\nDemo script: {sum(results)}/{len(results)} steps passed")
    if s.response_gaps:
        print(f"Response latency (you stop -> agent speaks): {', '.join(f'{g:.1f}s' for g in s.response_gaps)}")
    print("Tool payload leaks to client:", "NONE" if not s.leaks else s.leaks)


if __name__ == "__main__":
    asyncio.run(main())
    sys.stdout.flush()
    os._exit(0)  # aiortc can leave non-daemon threads behind that keep the process alive
