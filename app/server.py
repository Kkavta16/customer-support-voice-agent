"""FastAPI server: serves the Customer Support web UI and the WebRTC signalling endpoint.
Each browser connection gets its own Pipecat bot session (see bot.py)."""
import asyncio
import uuid
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI
from fastapi.staticfiles import StaticFiles
from loguru import logger
from ollama import AsyncClient
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.request_handler import (
    SmallWebRTCPatchRequest,
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)

from . import config
from .bot import run_bot, warm_up_speech_models

webrtc = SmallWebRTCRequestHandler()


async def warm_up_llm():
    """Load the LLM into memory now, so the first customer isn't slowed down by a cold start."""
    try:
        await AsyncClient(host=config.OLLAMA_HOST).chat(
            model=config.LLM_MODEL, messages=[{"role": "user", "content": "hi"}],
            options={"num_predict": 1}, keep_alive="30m",
        )
        logger.info(f"LLM {config.LLM_MODEL} loaded")
    except Exception as e:
        logger.warning(f"Could not reach Ollama at {config.OLLAMA_HOST} ({e}). Is it running?")


@asynccontextmanager
async def lifespan(_: FastAPI):
    await warm_up_llm()
    logger.info("Loading speech models (Whisper, Kokoro)...")
    await asyncio.to_thread(warm_up_speech_models)
    logger.info("Ready - open http://localhost:8000")
    yield
    await webrtc.close()


app = FastAPI(title="Customer Support Voice Agent", lifespan=lifespan)


@app.get("/api/info")
async def info():
    return {"store": config.STORE_NAME, "stt": config.WHISPER_MODEL, "llm": config.LLM_MODEL,
            "tts": f"kokoro/{config.TTS_VOICE}"}


@app.post("/api/offer")
async def offer(request: SmallWebRTCRequest, background_tasks: BackgroundTasks):
    async def on_connection(connection: SmallWebRTCConnection):
        logger.info(f"New support session {uuid.uuid4().hex[:8]}")
        background_tasks.add_task(run_bot, connection)

    return await webrtc.handle_web_request(request=request, webrtc_connection_callback=on_connection)


@app.patch("/api/offer")
async def ice_candidate(request: SmallWebRTCPatchRequest):
    await webrtc.handle_patch_request(request)
    return {"status": "success"}


app.mount("/", StaticFiles(directory=config.STATIC_DIR, html=True), name="static")
