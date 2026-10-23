"""The voice customer-support bot, built as a Pipecat pipeline.

  browser mic ─WebRTC─► Silero VAD ─► Whisper STT ─► user context ─► Ollama LLM ⇄ tools
  browser speaker ◄─WebRTC─ Kokoro TTS ◄─ speech clean-up ◄────────────┘

Pipecat handles turn-taking, streaming sentence-by-sentence TTS, and interruptions (barge-in).
The RTVI protocol sends transcripts and status events to the browser UI. Tool calls are reported
by *name only*, so SQL queries, arguments and results never leave the server.
"""
import asyncio
import functools

import numpy as np
import pipecat.services.kokoro.tts as kokoro_module
import pipecat.services.whisper.stt as whisper_module
from loguru import logger
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    Frame,
    TranscriptionFrame,
    TTSSpeakFrame,
    VADUserStartedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frameworks.rtvi.observer import (
    RTVIFunctionCallReportLevel,
    RTVIObserverParams,
)
from pipecat.services.kokoro.tts import KokoroTTSService
from pipecat.services.llm_service import FunctionCallParams
from pipecat.services.ollama.llm import OLLamaLLMService
from pipecat.services.whisper.stt import WhisperSTTService
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.turns.user_stop.turn_analyzer_user_turn_stop_strategy import (
    TurnAnalyzerUserTurnStopStrategy,
)
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.utils.text.base_text_filter import BaseTextFilter

from . import config
from .speech import clean_for_speech, join_spoken_digits, looks_like_unfinished_number
from .support import recover_phone_digits
from .tools import catalog, pipecat_tools, run_tool


# Pipecat builds a new WhisperModel and Kokoro session for every call, which cost ~8 s per
# connection here. Route both constructors through a cache so all sessions share one loaded,
# warmed-up copy (both are safe to use from several threads).
whisper_module.WhisperModel = functools.cache(whisper_module.WhisperModel)
kokoro_module.Kokoro = functools.cache(kokoro_module.Kokoro)


def warm_up_speech_models():
    """Load Whisper and Kokoro and run each once, so the first call starts quickly."""
    whisper = whisper_module.WhisperModel(config.WHISPER_MODEL, device=config.WHISPER_DEVICE,
                                          compute_type=config.WHISPER_COMPUTE)
    list(whisper.transcribe(np.zeros(16000, dtype=np.float32))[0])
    kokoro = kokoro_module.Kokoro(str(config.KOKORO_MODEL), str(config.KOKORO_VOICES))
    kokoro.create("Hello.", voice=config.TTS_VOICE, lang="en-us")


class SpeechCleanupFilter(BaseTextFilter):
    async def filter(self, text: str) -> str:
        return clean_for_speech(text)

    async def update_settings(self, settings):
        pass

    async def handle_interruption(self):
        pass

    async def reset_interruption(self):
        pass


def _user_texts(context: LLMContext) -> list[str]:
    texts = []
    for msg in context.messages:
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, list):
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
        texts.append(str(content or ""))
    return texts


_active_task: PipelineTask | None = None


class DigitAwareTurnStopStrategy(TurnAnalyzerUserTurnStopStrategy):
    """Smart Turn, but it keeps the caller's turn open while they are reading out a number.

    People pause between digits ("four... eight... two... one"), and each pause sounds like the
    end of a sentence to an audio-based turn detector. When the turn so far ends like an
    unfinished number, wait HOLD_SECS before ending it; any new speech cancels the wait."""

    HOLD_SECS = 1.5

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._turn_text = ""
        self._hold_task = None

    async def process_frame(self, frame: Frame):
        if isinstance(frame, TranscriptionFrame) and frame.text:
            self._turn_text = f"{self._turn_text} {frame.text}".strip()
        elif isinstance(frame, VADUserStartedSpeakingFrame):
            await self._cancel_hold()
        return await super().process_frame(frame)

    async def handle_user_turn_started(self):
        self._turn_text = ""
        await self._cancel_hold()
        await super().handle_user_turn_started()

    async def handle_user_turn_stopped(self):
        self._turn_text = ""
        await self._cancel_hold()
        await super().handle_user_turn_stopped()

    async def trigger_user_turn_stopped(self, **kwargs):
        if self._hold_task:
            return
        if looks_like_unfinished_number(self._turn_text):
            logger.debug(f"Holding turn open, number looks unfinished: {self._turn_text!r}")
            self._hold_task = self.task_manager.create_task(self._stop_after_hold(kwargs), f"{self}::hold")
            return
        await super().trigger_user_turn_stopped(**kwargs)

    async def _stop_after_hold(self, kwargs):
        try:
            await asyncio.sleep(self.HOLD_SECS)
        except asyncio.CancelledError:
            return
        self._hold_task = None
        await super().trigger_user_turn_stopped(**kwargs)

    async def _cancel_hold(self):
        if self._hold_task:
            task, self._hold_task = self._hold_task, None
            await self.task_manager.cancel_task(task)


async def handle_tool_call(params: FunctionCallParams):
    args = dict(params.arguments or {})
    if params.function_name == "get_order_status":
        args["phone_last4"] = recover_phone_digits(
            args.get("phone_last4", ""), _user_texts(params.context), args.get("order_id", "")
        )
    result = await asyncio.to_thread(run_tool, params.function_name, args)
    logger.info(f"tool {params.function_name}({args}) -> {result[:120]!r}")
    await params.result_callback(result)


async def run_bot(webrtc_connection: SmallWebRTCConnection):
    transport = SmallWebRTCTransport(
        webrtc_connection=webrtc_connection,
        params=TransportParams(audio_in_enabled=True, audio_out_enabled=True),
    )

    stt = WhisperSTTService(
        device=config.WHISPER_DEVICE,
        compute_type=config.WHISPER_COMPUTE,
        settings=WhisperSTTService.Settings(
            model=config.WHISPER_MODEL,
            # Bias recognition toward the store's brands and products ("OnePlus 12R", not "1 plus 12R").
            # Names with screen sizes are left out: "55-inch" made Whisper hear "four" as "4-inch".
            hotwords=" ".join(w for w in catalog.vocabulary() if "inch" not in w)[:600],
            # Example sentence that steers Whisper to write order numbers and digits as numerals.
            initial_prompt="Customer support call. My order number is 1001 and my phone number "
                           "ends in 4 8 2 1.",
        ),
    )

    llm = OLLamaLLMService(
        base_url=f"{config.OLLAMA_HOST}/v1",
        settings=OLLamaLLMService.Settings(
            model=config.LLM_MODEL,
            system_instruction=config.SYSTEM_PROMPT,
            temperature=0,  # same question, same answer: no creative embellishment
        ),
    )
    llm.register_function(None, handle_tool_call)  # one handler for every tool

    tts = KokoroTTSService(
        model_path=str(config.KOKORO_MODEL),
        voices_path=str(config.KOKORO_VOICES),
        settings=KokoroTTSService.Settings(voice=config.TTS_VOICE, speed=config.TTS_SPEED),
        text_filters=[SpeechCleanupFilter()],
    )

    context = LLMContext(tools=pipecat_tools())
    aggregators = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            # 0.5 s instead of the 0.2 s default before asking Smart Turn whether the caller is done,
            # so the short pauses people leave between spoken digits don't end their turn.
            vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.5)),
            user_turn_strategies=UserTurnStrategies(
                stop=[DigitAwareTurnStopStrategy(turn_analyzer=LocalSmartTurnAnalyzerV3())]
            ),
        ),
    )

    # Join digits read out one by one before the caller's words reach the LLM (see join_spoken_digits).
    user_aggregator = aggregators.user()
    raw_aggregation = user_aggregator.aggregation_string
    user_aggregator.aggregation_string = lambda: join_spoken_digits(raw_aggregation())

    pipeline = Pipeline([
        transport.input(),
        stt,
        aggregators.user(),
        llm,
        tts,
        transport.output(),
        aggregators.assistant(),
    ])

    task = PipelineTask(
        pipeline,
        params=PipelineParams(enable_metrics=True),
        rtvi_observer_params=RTVIObserverParams(
            function_call_report_level={"*": RTVIFunctionCallReportLevel.NAME},
        ),
    )

    # One call at a time: a refreshed page or a second tab used to leave the old agent running,
    # so two agents heard the same mic and answered over each other.
    global _active_task
    if _active_task is not None:
        logger.info("New call started; ending the previous one")
        await _active_task.cancel()
    _active_task = task

    @transport.event_handler("on_client_connected")
    async def on_client_connected(_transport, _client):
        logger.info("Customer connected")
        await task.queue_frames([TTSSpeakFrame(config.GREETING)])  # also added to the context

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(_transport, _client):
        logger.info("Customer disconnected")
        await task.cancel()

    await PipelineRunner(handle_sigint=False).run(task)
