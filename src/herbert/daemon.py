"""Daemon orchestrator — wires HAL + STT + LLM + TTS + audio into a voice loop.

This is the first real composition of every provider into a single running
process. The shape:

  event loop:
    on PressStarted:
      if a turn is mid-flight → cancel it (barge-in), reconcile session
      start a new Turn task; transition idle → listening
    on PressEnded:
      set Turn.release_event so AudioIn stops capturing
    (the Turn task handles thinking → speaking → idle by itself)

Each Turn task:
  1. Wait for AudioIn to drain (release_event fired by PressEnded)
  2. transition listening → thinking
  3. STT
  4. If transcript non-empty: stream LLM sentences → TTS PCM → AudioOut.play()
     transition thinking → speaking on the first TTS chunk
  5. transition speaking → idle (or thinking → idle if no LLM output)

Error handling: any exception inside the Turn task is classified and
published as `ErrorOccurred`; the state machine transitions to `error`.
Recovery from `error` happens on the next PressStarted (manual retry).
Automatic retry for transient network errors is deferred to Unit 11 per
plan R16 scope.

Cancellation invariants (barge-in):
- Cancelling the Turn task raises CancelledError at whatever await is
  current (LLM generator, TTS generator, audio buffer write). Each
  provider's finally-block / context-manager closes its connection
  cleanly on the way out.
- Session reconciliation post-cancel: if llm_state.tokens_received == 0
  the user message is popped (keeps role alternation valid for next turn);
  otherwise the partial assistant response is replaced with
  "<partial> [interrupted]".
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from herbert.config import HerbertConfig
from herbert.errors import classify_error, is_retryable
from herbert.events import (
    AsyncEventBus,
    ErrorOccurred,
    ExchangeLatency,
    LatencyMiss,
    TranscriptDelta,
    TurnCompleted,
    TurnStarted,
)
from herbert.hal import AudioIn, AudioOut, EventSource, Hal, PressEnded, PressStarted
from herbert.llm.claude import stream_turn
from herbert.llm.local_tools import LocalToolDispatcher
from herbert.session import InMemorySession, Message, Session
from herbert.state import StateMachine
from herbert.stt import SttProvider
from herbert.tts import TtsProvider
from herbert.turn import Turn

log = logging.getLogger(__name__)


# Default system prompt used when no persona file exists on disk.
# Also imported by scripts/demo-voice.py so the fallback is in one place.
#
# Design notes on every line of this prompt:
#   - Opens with role + tone so Claude knows "who" it is before it knows "how."
#   - The "Hard rules" section tells Claude that its output literally becomes
#     audio; Piper/ElevenLabs will read stray `*`, `_`, `#`, etc. as the words
#     "asterisk", "underscore", "hash" — so every markdown shortcut is banned.
#   - Rules against lists + parentheticals keep sentence-boundary flushing
#     predictable for the voice pipeline (Unit 5's SentenceBuffer).
#   - The numbers/abbreviations guidance stops "Dr.", "e.g.", and years from
#     being spelled out letter-by-letter.
#   - Style section keeps answers short so R6 latency is achievable.
DEFAULT_PERSONA = """You are Herbert, a retro-futurist home companion — friendly, a little dry, never a lecture. Matt speaks to you through a microphone and you reply through a speaker. Everything you write will be read aloud by a text-to-speech voice, so write only what should be spoken.

Hard rules (the voice reads any stray character literally):
- No markdown. No asterisks, underscores, backticks, pound signs, angle brackets, pipes, or square brackets anywhere.
- No bullet points or numbered lists. Speak in running prose.
- No parentheticals or stage directions such as (laughing), (pause), or [sighs].
- No emoji, no ASCII art, no code, no URLs.
- Spell out letter-by-letter abbreviations Matt would expect to hear as words: say "for example" not "e.g.", "doctor" not "Dr.", "roughly" not "approx.".
- Write numbers the way a person would say them ("twenty twenty six", "three point one four", "ten thousand").

Style:
- One or two short sentences. Longer only when Matt explicitly asks.
- Contractions are good. Sentence fragments are fine.
- Emphasize with word choice and rhythm, never with typography.
- If you must quote something, use the word "quote" rather than quotation marks when the quoted bit contains characters that would trip the voice."""


@dataclass
class DaemonDeps:
    """Everything the daemon needs, bundled for easy injection in tests.

    `persona` accepts either a static `str` (test-friendly) or a callable
    that returns the current persona text (production path, backed by
    `PersonaCache` for hot-reload). The daemon resolves it per turn and
    appends `TOOLS_PERSONA_ADDENDUM` when tools are active.
    """

    config: HerbertConfig
    bus: AsyncEventBus
    hal: Hal
    stt: SttProvider
    tts: TtsProvider
    llm_client: Any  # anthropic.AsyncAnthropic or a stub
    persona: str | Callable[[], str]
    mcp_servers: list[dict[str, str]] | None = None
    tools: list[dict[str, Any]] | None = None
    beta_headers: list[str] | None = None
    web_server: Any | None = None  # herbert.web.server.WebServer, set when CLI --expose or always-on


class Daemon:
    """Coordinates event source, pipeline workers, and the state machine."""

    def __init__(self, deps: DaemonDeps, session: Session | None = None) -> None:
        self._deps = deps
        self._state = StateMachine(deps.bus)
        self._session: Session = session or InMemorySession()
        self._current_turn: Turn | None = None
        self._current_task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self._bus_forward_task: asyncio.Task[None] | None = None
        self._recovery_task: asyncio.Task[None] | None = None
        self._transcript_log_task: asyncio.Task[None] | None = None
        # Mode label that shows up on every TurnStarted / ExchangeLatency
        # event. Used by the R6 ceiling lookup in `TurnSpan.evaluate_ceilings`.
        self._mode = "pi_hybrid" if deps.hal.platform == "pi" else "mac_hybrid"
        # False until daemon.run() starts (after model warmup). The frontend
        # reads this via /healthz so it can show a "warming" look instead
        # of claiming Herbert is ready while models are still loading.
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def state(self) -> str:
        return self._state.state

    @property
    def session(self) -> Session:
        return self._session

    async def run(self) -> None:
        """Main loop. Consumes the event source until `stop()` fires."""
        source: EventSource = self._deps.hal.event_source
        self._ready = True
        log.info("daemon ready, listening for button events")
        if self._deps.web_server is not None:
            self._bus_forward_task = asyncio.create_task(self._forward_bus_to_web())
        # Optional transcript audit log — gated by config.logging.log_transcripts.
        # Writes user/assistant lines to the file log for easy `grep` after the fact.
        self._transcript_log_task: asyncio.Task[None] | None = None
        if self._deps.config.logging.log_transcripts:
            self._transcript_log_task = asyncio.create_task(self._log_transcripts())
        events = source.events()
        try:
            async for event in events:
                if self._stopping.is_set():
                    break
                if isinstance(event, PressStarted):
                    await self._on_press_started()
                elif isinstance(event, PressEnded):
                    self._on_press_ended()
        finally:
            await self._cancel_current_turn(reason="daemon shutdown")
            if self._recovery_task is not None and not self._recovery_task.done():
                self._recovery_task.cancel()
                try:
                    await self._recovery_task
                except asyncio.CancelledError:
                    pass
            if self._bus_forward_task is not None:
                self._bus_forward_task.cancel()
                try:
                    await self._bus_forward_task
                except asyncio.CancelledError:
                    pass
            if self._transcript_log_task is not None and not self._transcript_log_task.done():
                self._transcript_log_task.cancel()
                try:
                    await self._transcript_log_task
                except asyncio.CancelledError:
                    pass
            await source.close()

    async def _log_transcripts(self) -> None:
        """Subscribe to TranscriptDelta events and append them to the file log.

        Gated by `config.logging.log_transcripts`. Per-sentence for the
        assistant side, one line per user-utterance. Safe to cancel — the
        subscription unregisters cleanly on exit.
        """
        async with self._deps.bus.subscribe() as sub:
            while True:
                event = await sub.receive()
                if isinstance(event, TranscriptDelta):
                    log.info(
                        "transcript turn=%s role=%s text=%r",
                        event.turn_id,
                        event.role,
                        event.text.strip(),
                    )

    async def _forward_bus_to_web(self) -> None:
        """Subscribe to the bus and forward every event to the web thread.

        The web server drains its janus queue from the other thread and
        fans events out to connected WS clients.
        """
        web = self._deps.web_server
        async with self._deps.bus.subscribe() as sub:
            while True:
                event = await sub.receive()
                try:
                    web.send_event(event)
                except Exception as exc:
                    log.warning("bus→web forward failed: %s", exc)

    async def stop(self) -> None:
        self._stopping.set()
        await self._cancel_current_turn(reason="stop requested")

    # --- Event handlers ---------------------------------------------------

    async def _on_press_started(self) -> None:
        await self._cancel_current_turn(reason="barge-in")
        # Any outstanding recovery monitor is moot the instant the user
        # presses — they're retrying manually, and we don't want two
        # concurrent paths out of the error state.
        if self._recovery_task is not None and not self._recovery_task.done():
            self._recovery_task.cancel()
        turn = Turn()
        self._current_turn = turn
        await self._state.transition("listening", turn_id=turn.turn_id)
        await self._deps.bus.publish(TurnStarted(turn_id=turn.turn_id, mode=self._mode))
        self._current_task = asyncio.create_task(self._run_turn(turn))

    def _on_press_ended(self) -> None:
        if self._current_turn is not None:
            self._current_turn.release_event.set()

    async def _cancel_current_turn(self, *, reason: str) -> None:
        task = self._current_task
        turn = self._current_turn
        if task is None or task.done():
            return
        log.info("cancelling active turn (%s)", reason)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log.warning("turn task raised during cancel: %s", exc)
        if turn is not None:
            self._reconcile_session_after_cancel(turn)

    def _reconcile_session_after_cancel(self, turn: Turn) -> None:
        """Preserve the alternating-role invariant on the Session.

        - 0 tokens received: the user message is the last entry → pop it.
        - ≥1 token: the stream_turn path may or may not have appended the
          assistant message yet. If the last message is the assistant's,
          replace it with a trailing "[interrupted]" marker; if it's the
          user's (assistant append never happened), replace the prior
          user-only state with (user, interrupted-assistant) pair.
        """
        if not turn.llm_state.tokens_received:
            last = self._session.messages[-1] if self._session.messages else None
            if last is not None and last.role == "user" and last.content == turn.transcript:
                self._session.pop_last()
            return
        partial = turn.llm_state.accumulated_text.strip()
        if not partial:
            return
        marker = f"{partial} [interrupted]"
        last = self._session.messages[-1] if self._session.messages else None
        if last is not None and last.role == "assistant":
            if hasattr(self._session, "replace_last"):
                self._session.replace_last(Message(role="assistant", content=marker))  # type: ignore[attr-defined]
        else:
            self._session.append(Message(role="assistant", content=marker))

    # --- Turn pipeline ----------------------------------------------------

    async def _run_turn(self, turn: Turn) -> None:
        """Drive STT → LLM → TTS → Playback for a single exchange."""
        audio_in: AudioIn = self._deps.hal.audio_in
        turn_start = asyncio.get_running_loop().time()
        try:
            pcm = await audio_in.capture_until_released(turn.release_event)
            await self._state.transition("thinking", turn_id=turn.turn_id)
            transcript = await self._run_stt(turn, pcm)
            turn.transcript = transcript
            if not transcript.strip():
                log.info("empty transcript; skipping LLM call")
                await self._state.transition("idle", turn_id=turn.turn_id)
                await self._finalize_and_publish_latency(turn, turn_start)
                await self._publish_turn_completed(turn, outcome="success")
                return

            await self._deps.bus.publish(
                TranscriptDelta(turn_id=turn.turn_id, role="user", text=transcript)
            )
            await self._run_llm_and_speak(turn)
            await self._state.transition("idle", turn_id=turn.turn_id)
            await self._finalize_and_publish_latency(turn, turn_start)
            await self._publish_turn_completed(turn, outcome="success")
        except asyncio.CancelledError:
            log.info("turn %s cancelled", turn.turn_id)
            await self._publish_turn_completed(turn, outcome="cancelled")
            raise
        except Exception as exc:
            await self._on_turn_error(turn, exc)

    async def _run_stt(self, turn: Turn, pcm: bytes) -> str:
        stt: SttProvider = self._deps.stt
        sample_rate = self._deps.hal.audio_in.sample_rate
        result = await stt.transcribe(pcm, sample_rate=sample_rate)
        turn.span.record("stt", result.duration_ms)
        return result.text

    def _resolve_persona(self) -> str:
        """Resolve the persona string for this turn.

        `DaemonDeps.persona` may be a static string (tests) or a callable
        (production PersonaCache). We fetch fresh text per turn so mid-
        session edits to `~/.herbert/persona.md` take effect next turn.
        `TOOLS_PERSONA_ADDENDUM` is appended when tools are active so the
        addendum text isn't baked into cache and doesn't get duplicated.
        """
        from herbert.llm.tools import TOOLS_PERSONA_ADDENDUM

        source = self._deps.persona
        base = source() if callable(source) else source
        if self._deps.tools:
            return base.rstrip() + TOOLS_PERSONA_ADDENDUM
        return base


    async def _run_llm_and_speak(self, turn: Turn) -> None:
        tts: TtsProvider = self._deps.tts
        audio_out: AudioOut = self._deps.hal.audio_out
        bus = self._deps.bus

        raw_sentences = stream_turn(
            turn.transcript,
            self._session,
            self._resolve_persona(),
            client=self._deps.llm_client,
            model=self._deps.config.llm.model,
            max_tokens=self._deps.config.llm.max_tokens,
            mcp_servers=self._deps.mcp_servers,
            tools=self._deps.tools,
            beta_headers=self._deps.beta_headers,
            local_dispatcher=LocalToolDispatcher(self._deps.bus),
            turn_id=turn.turn_id,
            state=turn.llm_state,
        )

        async def _broadcast_sentences() -> AsyncIterator[str]:
            """Fork each LLM sentence to both the TTS stream and the event bus.

            Without this the frontend transcript only shows the user turn;
            the assistant text never reaches the UI. One event per sentence
            keeps delta traffic bounded (vs. per-token).
            """
            async for sentence in raw_sentences:
                # Trailing space keeps sentences visually separated in the UI
                text = sentence if sentence.endswith(" ") else sentence + " "
                await bus.publish(
                    TranscriptDelta(turn_id=turn.turn_id, role="assistant", text=text)
                )
                yield sentence

        pcm_stream = tts.stream(_broadcast_sentences(), state=turn.tts_state)
        state = self._state  # local alias for the inner closure

        async def _instrumented_pcm() -> AsyncIterator[bytes]:
            first = True
            async for chunk in pcm_stream:
                if first:
                    await state.transition("speaking", turn_id=turn.turn_id)
                    first = False
                yield chunk

        await audio_out.play(_instrumented_pcm(), sample_rate=tts.sample_rate)
        if turn.llm_state.ttft_ms is not None:
            turn.span.record("llm_ttft", turn.llm_state.ttft_ms)
        if turn.llm_state.first_sentence_ms is not None:
            turn.span.record("first_sentence", turn.llm_state.first_sentence_ms)
        if turn.tts_state.ttfb_ms is not None:
            turn.span.record("tts_ttfb", turn.tts_state.ttfb_ms)

    async def _on_turn_error(self, turn: Turn, exc: BaseException) -> None:
        klass = classify_error(exc)
        log.warning("turn %s failed: %s (class=%s)", turn.turn_id, exc, klass)
        # Reconcile the session the same way cancellation does so a failed
        # turn doesn't leave an orphan user message that breaks the
        # user→assistant alternation on the next retry.
        self._reconcile_session_after_cancel(turn)
        await self._deps.bus.publish(
            ErrorOccurred(turn_id=turn.turn_id, error_class=klass, message=str(exc))
        )
        await self._state.transition_to_error(turn_id=turn.turn_id)
        await self._publish_turn_completed(turn, outcome="error")
        if is_retryable(klass):
            # Kick off a background monitor that pings the network at
            # 1→2→5→10s and transitions back to idle if connectivity
            # recovers. Non-retryable classes (auth, policy) stay put
            # until the user presses again.
            self._recovery_task = asyncio.create_task(
                self._monitor_recovery(turn.turn_id)
            )

    async def _monitor_recovery(self, turn_id: str) -> None:
        """Poll for network recovery after a retryable error; on success,
        transition error → idle so the user can PTT again without having
        to "kick" Herbert first.

        If a new turn starts (user presses the button) or the state leaves
        `error` for any other reason, we stop early. Delays of 1/2/5/10s
        match plan R16.
        """
        delays = (1.0, 2.0, 5.0, 10.0)
        for delay in delays:
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                return
            if self._state.state != "error":
                return  # user pressed the button or daemon shut down
            if await self._probe_network_ok():
                await self._state.transition("idle", turn_id=turn_id)
                log.info("network recovered after error; back to idle")
                return
        log.info("recovery monitor exhausted; staying in error until button")

    async def _probe_network_ok(self) -> bool:
        """Tiny HEAD against Anthropic — fast, auth-independent signal."""
        try:
            import httpx

            async with httpx.AsyncClient(timeout=2.0) as client:
                r = await client.head("https://api.anthropic.com/")
                return r.status_code < 500
        except Exception:
            return False

    async def _publish_turn_completed(self, turn: Turn, outcome: str) -> None:
        await self._deps.bus.publish(
            TurnCompleted(turn_id=turn.turn_id, outcome=outcome)  # type: ignore[arg-type]
        )

    async def _finalize_and_publish_latency(self, turn: Turn, turn_start: float) -> None:
        """Finalize TurnSpan, emit LatencyMiss per missed stage, ExchangeLatency total.

        Called only on the success + empty-transcript paths (cancelled and
        errored turns have incomplete stage data and don't need R6 review).
        """
        loop = asyncio.get_running_loop()
        turn.span.total_ms = int((loop.time() - turn_start) * 1000)
        misses = turn.span.evaluate_ceilings(self._mode)
        providers = {
            "stt": self._deps.config.stt.provider,
            "tts": self._deps.config.tts.provider,
            "llm": self._deps.config.llm.model,
        }
        for stage, actual, ceiling in misses:
            await self._deps.bus.publish(
                LatencyMiss(
                    turn_id=turn.turn_id,
                    stage=stage,
                    actual_ms=actual,
                    ceiling_ms=ceiling,
                    mode=self._mode,
                    providers=providers,
                )
            )
        await self._deps.bus.publish(
            ExchangeLatency(
                turn_id=turn.turn_id,
                total_ms=turn.span.total_ms,
                stage_durations=dict(turn.span.stage_durations),
                misses=list(turn.span.misses),
                mode=self._mode,
            )
        )


# --- Factory + CLI entry -----------------------------------------------------


async def build_and_run(
    config: HerbertConfig,
    *,
    bus: AsyncEventBus,
    expose: bool = False,
) -> int:
    """Wire up the live providers on the current platform and run the daemon.

    When `expose=True` (or `config.web.expose`), the web server binds to
    0.0.0.0 and requires a bearer token. Otherwise it stays on localhost
    unauthenticated.
    """
    from anthropic import AsyncAnthropic

    from herbert.hal import build_hal, detect_platform
    from herbert.llm.mcp_passthrough import build_mcp_servers
    from herbert.secrets import ensure_frontend_bearer_token, load_secrets
    from herbert.stt.whisper_cpp import WhisperCppProvider
    from herbert.tts.elevenlabs_stream import ElevenLabsProvider
    from herbert.tts.piper import PiperProvider
    from herbert.web.server import WebServer

    platform = detect_platform()
    hal = build_hal(
        platform,
        input_device_name=config.stt.input_device_name,
        output_device_name=config.tts.output_device_name,
    )
    secrets = load_secrets(config.secrets_path)

    # Health checks (herbert.health.run_startup_checks) are intentionally
    # not run on boot. The HTTP probes contend with the first turn's
    # Anthropic + ElevenLabs network calls, and the audio probes open
    # real mic/speaker streams which can collide with the daemon's own
    # capture/playback if the user presses the button early. The module
    # still exists for future opt-in use via a `/healthz` diagnostic
    # endpoint or a voice-triggered self-test; we just don't run it
    # automatically. Truly fatal prerequisites (missing secrets) fail
    # fast at the secrets layer, which happens above.

    anthropic_key = secrets.require("ANTHROPIC_API_KEY")
    import os as _os

    _os.environ["ANTHROPIC_API_KEY"] = anthropic_key
    llm_client = AsyncAnthropic()

    if config.tts.provider == "elevenlabs":
        eleven_key = secrets.require("ELEVENLABS_API_KEY")
        voice_id = config.tts.voice_id or secrets.get("ELEVENLABS_VOICE_ID")
        if not voice_id:
            raise RuntimeError("ELEVENLABS_VOICE_ID not set in config or secrets")
        tts: TtsProvider = ElevenLabsProvider(api_key=eleven_key, voice_id=voice_id)
    elif config.tts.provider == "piper":
        voice_path = Path.home() / ".herbert" / "voices" / "en_US-lessac-medium.onnx"
        tts = PiperProvider(voice_path=voice_path)
    else:
        raise RuntimeError(f"unknown tts.provider {config.tts.provider!r}")

    stt = WhisperCppProvider(
        model_path=Path.home() / ".herbert" / "models" / "ggml-base.en-q5_1.bin"
    )

    effective_expose = expose or config.web.expose
    bind_host = "0.0.0.0" if effective_expose else config.web.bind_host
    bearer_token = ensure_frontend_bearer_token(config.secrets_path) if effective_expose else None

    web_server = WebServer(
        bind_host=bind_host,
        port=config.web.port,
        expose=effective_expose,
        bearer_token=bearer_token,
        health_provider=lambda: _build_health_payload(config, _daemon_ref),
    )
    web_server.start()
    log.info("web server listening on %s (expose=%s)", web_server.url, effective_expose)

    from herbert.llm.tools import build_tool_beta_headers, build_tools
    from herbert.persona import PersonaCache

    tools = build_tools(
        web_search_enabled=config.llm.web_search_enabled,
        web_fetch_enabled=config.llm.web_fetch_enabled,
        code_execution_enabled=config.llm.code_execution_enabled,
    )
    tool_betas = build_tool_beta_headers(
        web_fetch_enabled=config.llm.web_fetch_enabled,
        code_execution_enabled=config.llm.code_execution_enabled,
    )
    # PersonaCache handles hot-reload + last-good-cached fallback. Priming
    # here raises PersonaMissingError if the file exists but is unreadable
    # or empty — the user is told loudly at boot rather than on first turn.
    persona_cache = PersonaCache(config.persona_path, default=DEFAULT_PERSONA)
    persona_cache.prime_at_startup()

    deps = DaemonDeps(
        config=config,
        bus=bus,
        hal=hal,
        stt=stt,
        tts=tts,
        llm_client=llm_client,
        persona=persona_cache.get_current,  # callable — daemon resolves per turn
        mcp_servers=build_mcp_servers(config.mcp) or None,
        tools=tools or None,
        beta_headers=tool_betas or None,
        web_server=web_server,
    )
    daemon = Daemon(deps)
    # Capture the daemon reference so the health provider can read its state
    _daemon_ref["daemon"] = daemon

    # Pre-load models SYNCHRONOUSLY before we enter the event loop. Herbert
    # is meant to stay on between sessions, so paying the one-time model
    # load at boot (instead of on the user's first button press) is the
    # right tradeoff. STT + TTS warmups run in parallel; any missing files
    # or unreadable voices raise here and fail startup loudly.
    warmup_start = asyncio.get_running_loop().time()
    warmups: list[asyncio.Task[None]] = []
    if hasattr(stt, "warmup"):
        warmups.append(asyncio.create_task(stt.warmup()))  # type: ignore[attr-defined]
    if hasattr(tts, "warmup"):
        warmups.append(asyncio.create_task(tts.warmup()))  # type: ignore[attr-defined]
    if warmups:
        await asyncio.gather(*warmups)
        warmup_ms = int((asyncio.get_running_loop().time() - warmup_start) * 1000)
        log.info("models warmed in %dms; daemon ready", warmup_ms)

    try:
        await daemon.run()
    finally:
        web_server.stop()
    return 0


# Mutable holder so the health closure can observe the daemon after it's built
_daemon_ref: dict[str, Any] = {}


def _build_health_payload(config: HerbertConfig, ref: dict[str, Any]) -> dict[str, Any]:
    daemon = ref.get("daemon")
    return {
        "status": "ok",
        "ready": bool(daemon and daemon.ready),
        "state": daemon.state if daemon is not None else "starting",
        "stt_provider": config.stt.provider,
        "tts_provider": config.tts.provider,
        "llm_model": config.llm.model,
    }
