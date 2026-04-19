---
title: Herbert v1 Implementation Plan
type: feat
status: active
date: 2026-04-18
origin: docs/brainstorms/2026-04-18-herbert-requirements.md
---

# Herbert v1 Implementation Plan

## Overview

Build v1 of Herbert — a Claude-powered, voice-enabled AI assistant device that runs on a Raspberry Pi 5 and is developed on macOS from the same codebase. This plan covers the full v1 scope from the origin document: push-to-talk button → local `whisper.cpp` STT → streaming Claude Haiku → streaming ElevenLabs TTS → speaker, with a Chromium-kiosk web frontend showing a pixel-art character that reacts to pipeline state, plus observability, error recovery, and cross-platform dev parity.

This is a tinker project: craft matters more than throughput, but the pipeline has real latency and reliability targets that cannot slip. See origin: `docs/brainstorms/2026-04-18-herbert-requirements.md`.

## Problem Frame

Matt wants a dedicated physical device that feels like *a specific little guy* — a retro-futurist home companion named after Rodney Brooks' MIT soda-can-collecting robot. The product claim in the origin's Problem Frame ("feels like a specific little guy sitting on a shelf, not a generic Claude client") is load-bearing: every technical choice should serve that feel. Sub-second-to-respond, polished error surfaces, a boot sequence that's part of the character — these are the point, not window dressing.

Developing on a Raspberry Pi directly is painful. First-class macOS parity is not a nice-to-have; it's a prerequisite for iteration speed.

## Requirements Trace

All 23 requirements (R1-R23) plus R6a from the origin document flow through this plan. Each implementation unit cites the R-numbers it advances. The origin's success criteria are the acceptance bar.

Rather than enumerate every R-number here, the plan maps implementation units → R-numbers below and verifies full coverage in Unit 14 (end-to-end smoke tests).

## Scope Boundaries

This plan implements v1 only. Explicitly out of scope (carried from origin):

- No MCP servers enabled — only the `mcp_servers` pass-through (the call site exists but zero servers are configured)
- No persistent memory across sessions (session abstraction exists for v3)
- No camera / PIR / gesture activation
- No voice-commanded persona rewriting (v2)
- No enclosure or industrial design
- No multi-user, profiles, or accounts
- No mobile app or cloud control surface
- No wake-word (ever)

### Deferred to Separate Tasks

- **v2 — MCP enablement end-to-end.** Remote MCPs via `mcp_servers`, then stdio via `mcp` SDK. Adaptive latency-miss fallback (if needed). Voice-commanded persona tweaks.
- **v3 — Memory persistence.** SQLite-backed conversation + user-facts store behind the v1 session abstraction.
- **v4 — PIR / gesture activation.** Additional implementation of the same `EventSource` interface (R2).
- **v5 — Enclosure / form factor.** Physical work; may feed back minor spec changes (display size, button placement).

## Context & Research

### Relevant Code and Patterns

No prior Herbert code exists (greenfield). Conventions borrowed from Matt's `rattlesnake` project at `/Users/matt/dev/rattlesnake`:
- `src/<package>/` layout with parallel `tests/`
- Python 3.12+, pinned via `.python-version` and `pyproject.toml`
- `[project.scripts]` CLI entry point → `herbert.cli:main`
- Protocol-based adapter pattern for anything with multiple backends (`@runtime_checkable Protocol` from `typing`)
- TOML config at `~/.config/herbert/config.toml` with env-var override
- Rich `CLAUDE.md` at repo root as living design doc

One deliberate divergence: adopt `ruff` instead of `black + flake8 + mypy` (ruff includes all three in one tool; fresh project, no migration cost).

### Institutional Learnings

No `docs/solutions/` exists yet at `/Users/matt/dev/herbert/` or `~/.claude/`. Solutions directory will be seeded as Herbert hits lessons worth capturing (e.g., PortAudio XRun behavior on PipeWire, secret-redaction edge cases). The `compound-engineering:ce-compound` skill is the intended capture mechanism.

### External References

- **pywhispercpp** — [GitHub](https://github.com/absadiki/pywhispercpp) · [PyPI](https://pypi.org/project/pywhispercpp/) (prebuilt aarch64 wheels, active)
- **whisper.cpp** — [GitHub](https://github.com/ggml-org/whisper.cpp); models at `huggingface.co/ggerganov/whisper.cpp`
- **piper-tts** — rhasspy/piper (wraps onnxruntime; per-sentence streaming via `synthesize_stream_raw`)
- **elevenlabs (Python)** — [GitHub](https://github.com/elevenlabs/elevenlabs-python); [WebSocket cookbook](https://elevenlabs.io/docs/developers/guides/cookbooks/multi-context-web-socket); [Latency optimization](https://elevenlabs.io/docs/best-practices/latency-optimization)
- **anthropic (Python)** — [GitHub](https://github.com/anthropics/anthropic-sdk-python); [MCP connector](https://platform.claude.com/docs/en/agents-and-tools/mcp-connector); model `claude-haiku-4-5`
- **MCP Python SDK** — [GitHub](https://github.com/modelcontextprotocol/python-sdk) (deferred to v2, but pinned version will be chosen in v1 scaffolding)
- **FastAPI WebSockets** — [docs](https://fastapi.tiangolo.com/advanced/websockets/)
- **sounddevice / PortAudio** — [PortAudio on Pi](https://github.com/PortAudio/portaudio/wiki/Platforms_RaspberryPi); [PipeWire](https://www.pipewire.org/)
- **gpiozero + lgpio on Pi 5** — [docs](https://gpiozero.readthedocs.io/); Pi 5 requires `LGPIOFactory(chip=4)` for the RP1 south bridge
- **Chromium kiosk on Bookworm** — Wayland + labwc default; autostart via labwc or systemd user unit
- **uv** — [configuration](https://docs.astral.sh/uv/concepts/projects/config/)
- **PIXI.js v8** — [docs](https://pixijs.com/8.x/guides/components/textures); use nearest-neighbor scaling + `roundPixels: true` for crisp pixel art
- **Voice pipeline prior art** — Pipecat (sentence-boundary LLM→TTS handoff, adaptive TTS), LiveKit Agents (interruption semantics, sequential pipeline), Home Assistant Assist (local-first voice + HAL pattern)
- **OpenTelemetry GenAI semantic conventions** — inspiration for latency span taxonomy (R6a)

## Key Technical Decisions

| Decision | Rationale |
|---|---|
| **Python 3.12, uv-managed project, single package `herbert`** | Matches Matt's `rattlesnake` conventions; uv handles platform-conditional deps cleanly via PEP 508 markers. (see origin: Key Decisions) |
| **Protocol-based adapter pattern for STT, TTS, EventSource, Audio IO, Display** | Matches R5/R2/R17-R18 requirements; follows `rattlesnake`'s `@runtime_checkable Protocol` precedent; enables swapping implementations by config without `if platform == ...` noise. |
| **Web server = FastAPI + uvicorn single worker, WebSocket for state + SSE-style log tail** | Research consensus for a daemon-plus-browser pattern; Pydantic integration pays for itself on R15's observation endpoints; single worker keeps daemon state in one asyncio loop. |
| **Backend ↔ frontend transport = WebSocket** (one bidi channel per browser client) | Event-push is required by R10-R13 (real-time state). Single channel keeps the protocol trivial; JSON messages with a `type` discriminator. |
| **STT = `pywhispercpp` + `ggml-base.en-q5_1.bin`** | Research shows q5_1 hits the R6 STT ≤1.2s ceiling on Pi 5 with NEON while preserving accuracy (q4_0 loses too much at conversational volume). Active maintenance, prebuilt aarch64 wheels. |
| **TTS = ElevenLabs streaming via WebSocket (`text_to_speech.stream_input`, model `eleven_flash_v2_5`)** | 75-150ms TTFB vs ~300-500ms on HTTP chunked; fits R6's 300ms first-chunk ceiling. Async iterator integration with asyncio daemon is clean. |
| **Sentence-boundary LLM→TTS handoff** (flush on `.!?;` or 20-word threshold) | Standard voice-pipeline pattern; lets Herbert start speaking at first sentence while Claude is still generating; sub-1s apparent response latency. |
| **LLM = `claude-haiku-4-5` via `anthropic.AsyncAnthropic().messages.stream()`** | Haiku 4.5 TTFT ~400-600ms fits R6's 600ms ceiling with warm connection. Config-swappable (R5 equivalent seam via `llm.model`). |
| **Push-to-talk HOLD semantics** (release = end of utterance) | R6 says "button release" explicitly; consistent with retro PTT radio feel; simpler than tap-to-start/tap-to-stop or VAD tails. Locked. |
| **Barge-in on mid-exchange button press** | Cancellation via one `asyncio.Event` per turn, fanning out to all three streams (STT, LLM, TTS). Matches PTT-radio mental model. |
| **Diagnostic-mode trigger = whole-utterance regex** | Avoids false positives on questions like "Herbert, show me the logs from yesterday's deploy." After `.strip().lower()` and de-punctuation, match against a fixed phrase list. |
| **Error + diagnostic-view rendering** | When error fires while in diagnostic view, show a compact error banner *above* the log tail. Error pose itself is suppressed (no character slot in diagnostic view). |
| **Persona-file bad = last-good-cached + WARN** | Daemon caches the last successfully-loaded persona in-memory; if the file is missing/malformed, keep using the cache and log WARN. Fail-closed only on startup (R23-style). |
| **GPIO library = `gpiozero` + `LGPIOFactory(chip=4)`** | Pi 5 RP1 chip makes `RPi.GPIO` non-functional; `lgpio` backend is the supported path. gpiozero's `MockFactory` gives us a host-side test double for free. |
| **macOS activation shim = `pynput` + frontend-side spacebar** | `herbert dev` listens for spacebar keydown/keyup via `pynput` at the daemon, AND the frontend forwards browser spacebar events over the WS (redundant paths — whichever the user's focus is on works). |
| **Audio I/O = `sounddevice` with separate `InputStream` + `OutputStream`, device pinning by name substring** | Duplex mode forces matched SR/devices — wrong for USB mic + different USB speaker. Name-based pinning survives USB reenumeration. Callback API feeds an `asyncio.Queue` via `loop.call_soon_threadsafe`. |
| **Chromium kiosk startup = systemd user unit with `ExecStartPre=wait-for-herbert`** | Cleanly gates kiosk launch on daemon health (R9, R19); survives logout with `loginctl enable-linger pi`; preferred over labwc autostart per current Bookworm guidance. |
| **Discovery when exposed = mDNS via `zeroconf` (`herbert.local`) + QR code on display at boot** | zeroconf is the standard; QR code gives Matt a one-scan URL with the bearer token embedded. Works without router DNS config. |
| **Latency instrumentation = typed `TurnSpan` with sub-spans, emitted as structured log events** | Modeled on OpenTelemetry GenAI semantic conventions; emits `exchange_latency` at INFO and `latency_miss` at WARN per R6a. Frontend corner indicator updates from WS events. |
| **Anthropic MCP beta header = `anthropic-beta: mcp-client-2025-11-20`** | Updated from the origin doc's `mcp-client-2025-04-04` (deprecated). Shape: pass `mcp_servers=[{"type":"url","url":..,"name":..,"authorization_token":..}]` as a kwarg to `client.beta.messages.stream()`; tool allowlist/denylist lives inside each MCP server entry. **Header + shape are unverified against live Anthropic docs** — a planning-time spike at the start of Unit 13 must re-confirm before building v2 against this shape. In v1 the list is always empty, so the risk is v2-only. |
| **Packaging = `uv` workspace, `[project.scripts]` CLI, systemd user unit on Pi, bare `uv run` on Mac dev** | uv is cross-platform; `uv sync` on each platform produces the right extras from the same `uv.lock`. launchd is overkill for dev — `herbert dev` runs in a terminal and Ctrl-C stops it. |

## Open Questions

### Resolved During Planning

- **Backend-frontend transport** → FastAPI + WebSocket, single uvicorn worker.
- **GPIO library for Pi 5** → `gpiozero` + `LGPIOFactory(chip=4)`.
- **whisper.cpp model size + quantization** → `ggml-base.en-q5_1.bin`.
- **Audio I/O path** → `sounddevice` separate InputStream/OutputStream, name-substring device pinning.
- **STT/TTS interface shape** → async iterator of byte chunks; per-stage `Protocol` (`SttProvider.transcribe()`, `TtsProvider.stream()`).
- **Packaging/install** → `uv` + systemd user unit (Pi), `uv run` (Mac dev). No Docker in v1.
- **Discovery when exposed** → mDNS (`zeroconf`) + QR on boot.
- **Health-check menu** → mic probe, speaker probe, Anthropic reachability, ElevenLabs reachability, whisper model file present, Piper voice file present (for fallback), persona file present+parseable, GPIO button responsive (Pi only). Each reports pass/fail with a diagnostic string.
- **MCP beta header** → `mcp-client-2025-11-20` (update from origin's `2025-04-04`).
- **Button gesture** → hold-to-talk (release = EoU).
- **Mid-exchange button press** → barge-in (cancel current turn, start new).
- **Trigger-phrase match scope** → whole-utterance after strip+lowercase+de-punctuate.
- **Error + diagnostic view interaction** → error banner overlays log tail; no character pose in diagnostic.
- **Persona-file degenerate cases** → last-good-cached + WARN on read failure. Hard fail at startup if NO persona ever loaded.
- **"I'm back" cue suppression** → suppressed during `listening` and `speaking`; played on `idle` and `error` only.
- **Boot-sequence replay guard** → per-boot marker file at `/tmp/herbert-booted` (or equivalent on macOS) that persists until reboot.
- **Latency-miss remediation policy** → observational only in v1; no automatic provider switch or model downgrade.

### Deferred to Implementation

- **Exact mic/speaker hardware** — Matt will use whatever's on hand; recommended: USB omnidirectional desktop mic (Fifine K669 class) + USB-powered speaker. Device-pinning config makes this late-binding.
- **ElevenLabs voice ID** — requires A/B listening test once the pipeline runs. Stored in `~/.herbert/secrets.env` alongside the API key; swap = edit file, next exchange picks it up.
- **Pixel-art character sprite sheet** — TexturePacker or Aseprite output committed at `frontend/assets/herbert.json`. Design iteration happens after the frame slots (idle, listening, thinking, speaking, error) are wired in; placeholders land first.
- **Boot sequence visual copy** — placeholder text ("HERBERT v0.1 / MEMORY OK / AUDIO OK / HELLO.") ships first; iteration follows.
- **Piper fallback voice file** — `en_US-lessac-medium.onnx` is a fine default; lives at `~/.herbert/voices/`. Only exercised when ElevenLabs is unavailable or via config.
- **Exact Chromium kiosk startup flags** — base set documented; may need tuning on the actual Pi (V3D driver, Wayland quirks). Shipped in the systemd unit with comments.
- **Specific log redaction patterns** — starting set: `sk-*`, `sk_*` (ElevenLabs uses `sk_`), `xi_*`, `Bearer [A-Za-z0-9._-]+`, URL query params `[?&](token|key|bearer)=[^&\s]+`, fields named `api_key`/`token`/`bearer`/`authorization`. Uvicorn access logging is **routed through the RedactingFilter** (not disabled — its request info is useful, the redactor just sanitizes URL tokens before write). Additions happen as secrets surface.

## Output Structure

```text
herbert/
  pyproject.toml                 # uv-managed, platform-conditional deps
  uv.lock
  .python-version                # 3.12
  .gitignore
  README.md                      # user-facing: install, run, swap voices
  CLAUDE.md                      # design doc / agent primer
  config/
    herbert.example.toml         # TOML with comments
    systemd/
      herbert-daemon.service     # Pi: user unit
      herbert-kiosk.service      # Pi: user unit (kiosk)
      wait-for-herbert.sh        # health-wait helper
  src/
    herbert/
      __init__.py
      __main__.py                # `python -m herbert`
      cli.py                     # argparse; `herbert dev` / `herbert run`
      config.py                  # TOML loader + env override + validation
      secrets.py                 # loader for ~/.herbert/secrets.env + fail-closed
      logging.py                 # structured logger, rotation, redaction filter
      daemon.py                  # asyncio entrypoint; orchestrates components
      events.py                  # typed event bus (pydantic models)
      state.py                   # StateMachine: idle/listening/thinking/speaking/error
      turn.py                    # Turn object with cancel_event, latency span
      audio/
        __init__.py
        capture.py               # InputStream → asyncio.Queue
        playback.py              # OutputStream from async iter of PCM bytes
        devices.py               # name-substring pinning; platform variants
      stt/
        __init__.py              # SttProvider Protocol
        whisper_cpp.py           # local via pywhispercpp
        whisper_api.py           # stub (future)
        deepgram.py              # stub (future)
      tts/
        __init__.py              # TtsProvider Protocol
        elevenlabs_stream.py     # WebSocket streaming
        piper.py                 # local fallback
        openai.py                # stub (future)
      llm/
        __init__.py
        claude.py                # streaming Anthropic client + sentence buffer
        mcp_passthrough.py       # mcp_servers construction (zero enabled in v1)
      diagnostic/
        __init__.py
        triggers.py              # regex matcher, whole-utterance scope
        mode.py                  # view-mode switcher
      hal/
        __init__.py              # EventSource, Display Protocols; detect_platform()
        pi.py                    # gpiozero Button + Pi-specific bits
        mac.py                   # pynput keyboard shim
        mock.py                  # for tests
      web/
        __init__.py
        app.py                   # FastAPI app factory
        ws.py                    # WebSocket endpoint: state events, log tail
        auth.py                  # bearer token verification (for --expose mode)
        static/                  # frontend build artifacts mounted here
      health.py                  # startup checks; diagnostic builder
      session.py                 # in-memory session (v1); plug point for v3 memory
  frontend/
    index.html
    assets/
      herbert.json               # PIXI spritesheet metadata (placeholder v1)
      herbert.png                # sprite sheet (placeholder v1)
      boot-seq.txt               # boot-sequence text lines
    src/
      main.js                    # PIXI Application, scene setup
      state.js                   # character view: sprite per state
      transcript.js              # HTML div updates from WS
      diagnostic.js              # log-tail view
      boot.js                    # fake boot animation
      ws.js                      # WebSocket client
      latency.js                 # corner miss indicator
    package.json                 # minimal; PIXI + esbuild or Vite
    vite.config.js               # Vite bundler (or esbuild — decide during M3)
  tests/
    unit/
      test_config.py
      test_secrets.py
      test_state_machine.py
      test_turn_cancellation.py
      test_triggers.py
      test_health.py
      test_redaction.py
      ...
    integration/
      test_pipeline_replay.py    # replay-fixture-based voice loop
      test_error_recovery.py     # network drop, API 5xx, mic fail
      test_diagnostic_mode.py
      test_latency_instrumentation.py
    fixtures/
      turns/
        hello-world/
          input.wav
          stt.json
          llm_stream.jsonl
          tts_chunks.bin
          tts_manifest.json
    conftest.py                  # pytest fixtures: mock HAL, replay transport
  scripts/
    dev-install.sh               # one-shot dev setup (Mac)
    pi-install.sh                # one-shot Pi setup (installs systemd units)
    fetch-models.py              # download + SHA256 verify whisper + piper models
  docs/
    brainstorms/
      2026-04-18-herbert-requirements.md  # (already exists)
    plans/
      2026-04-18-001-feat-herbert-v1-implementation-plan.md  # (this file)
```

This structure is a scope declaration, not a constraint — the per-unit `**Files:**` lists below are authoritative. If implementation reveals a cleaner layout, adjust.

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

### Event-driven pipeline with cancellable turns

```
+-----------------+          +----------------------+           +---------------------+
|  HAL.EventSrc   |          |    StateMachine      |           |  Pipeline workers   |
|  (Pi GPIO /     | ----->   |  idle → listening    |  ----->   |  stt.whisper_cpp    |
|   Mac pynput /  |  press   |  listening→thinking  |  spawn    |  llm.claude.stream  |
|   Frontend WS)  |          |  thinking→speaking   |  tasks    |  tts.elevenlabs     |
+-----------------+          |  any→error on failure|           |  audio.playback     |
                             +----------+-----------+           +---------------------+
                                        |
                                        | publishes typed events
                                        v
                            +--------------------------+
                            |   AsyncEventBus          |
                            |   (pub/sub, per-type)    |
                            +--+------+--------+-------+
                               |      |        |
                     subscribes|      |        |
                               v      v        v
                        +-----------+  +---------+  +-----------+
                        | WS → UI   |  | Logger  |  | Latency   |
                        | state sync|  | +redact |  | spans/R6a |
                        +-----------+  +---------+  +-----------+
```

Each turn has:
- A unique `turn_id` (ULID)
- A `TurnSpan` that collects per-stage timings
- An `asyncio.Event cancel_event` — if the button is pressed mid-turn, `cancel_event.set()` fans out to all three pipeline workers, each of which treats it as a soft interrupt (close WS, abort stream, flush audio buffer)
- A lifecycle: `LISTENING → TRANSCRIBING → THINKING → SPEAKING → (IDLE | ERROR)` with explicit state transitions emitting `StateChanged` events

### Sentence-boundary LLM → TTS handoff

```
Claude stream (text deltas)
     |
     v
SentenceBuffer.feed(delta)
     |  flushes on ".!?;" or 20-word gap
     v
ElevenLabs WS.send(sentence)  ──► WS yields PCM chunks ──► AudioPlayback.enqueue(chunk)
```

Herbert starts speaking at the first sentence while Claude keeps generating. This is the critical lever for R6's <2.5s Pi p50.

### View model

Two orthogonal view modes:
- **Character view (default):** PIXI.js AnimatedSprite showing one of `idle | listening | thinking | speaking` with an `error` variant that replaces whichever is active. Transcript HTML div sits beside or below the character (≤40% of display per R11).
- **Diagnostic view:** log tail in a monospace HTML div; no PIXI render. If an error is active when diagnostic view is shown, a compact banner above the log displays the error class.

View transitions are triggered locally on the client from WS `ViewChanged` events; the daemon owns the authoritative view-mode state.

### Error recovery state machine

```
 network/5xx error     ─retry (1,2,5,10s)─►  success ─►  idle + "I'm back" (if not currently listening/speaking)
 auth/rate-limit/policy ──► stay in error until button press
 mic/speaker error     ──► stay in error until button press
```

## Implementation Units

### Milestone M1 — Scaffold & foundations

- [ ] **Unit 1: Project scaffold + config + secrets + CLI**

**Goal:** A runnable `herbert dev` entry point that loads config and secrets, initializes logging, and prints a "Herbert v0.1 ready" line. No audio, no UI, no Claude call yet. Establishes conventions for everything else.

**Requirements:** R7 (persona file path), R17 (same codebase), R19 (two entry points), R23 (secrets loader + fail-closed)

**Dependencies:** none

**Files:**
- Create: `pyproject.toml`, `.python-version`, `.gitignore`, `uv.lock`
- Create: `src/herbert/__init__.py`, `src/herbert/__main__.py`, `src/herbert/cli.py`
- Create: `src/herbert/config.py`, `src/herbert/secrets.py`, `src/herbert/logging.py`
- Create: `config/herbert.example.toml`, `README.md`, `CLAUDE.md`
- Test: `tests/unit/test_config.py`, `tests/unit/test_secrets.py`, `tests/unit/test_redaction.py`

**Approach:**
- `pyproject.toml` with PEP 508 markers: `gpiozero` + `lgpio` only on `sys_platform=='linux' and platform_machine=='aarch64'`; `pynput` only on `sys_platform=='darwin'`
- `[tool.uv] environments = ["sys_platform=='darwin'", "sys_platform=='linux' and platform_machine=='aarch64'"]`
- CLI uses `argparse`; subcommands `dev` and `run` both route to the same `daemon.run()` with a `Mode` flag
- Config loader: TOML from `~/.config/herbert/config.toml` (search order: CLI flag → env `HERBERT_CONFIG` → default path). Matches `rattlesnake`'s precedent
- Secrets loader: reads `~/.herbert/secrets.env` (python-dotenv flavored — simple key=value). Fails closed if required keys (Anthropic, ElevenLabs if cloud-TTS selected) are missing; error message names the missing key
- Logging: stdlib `logging` + `logging.handlers.TimedRotatingFileHandler`; `RedactingFilter` on root logger scrubs patterns; structured JSON formatter for file, human-readable for console
- Ruff config in `pyproject.toml`

**Patterns to follow:**
- `/Users/matt/dev/rattlesnake/pyproject.toml` — `requires-python`, `[project.scripts]`, layout
- `/Users/matt/dev/rattlesnake/src/rattlesnake/cli/main.py` — CLI shape

**Execution note:** Lightweight TDD is appropriate here — write tests for config precedence, secrets fail-closed, and redaction before implementing.

**Test scenarios:**
- Happy path: given a valid config TOML and secrets file, `herbert dev --help` exits 0 and prints subcommands.
- Happy path: config precedence — CLI flag > env var > default path.
- Error path: secrets file missing → daemon exits with non-zero and a clear "missing secrets: ANTHROPIC_API_KEY" message.
- Error path: secrets file has wrong permissions (0644) → WARN log and continue (permission drift is not a hard fail, per decision).
- Edge case: persona file mentioned in config but file missing → logger WARN, daemon continues (fail mode is handled in Unit 7, not here).
- Happy path: redaction filter scrubs `sk-abc123` from log records; surviving record contains `[REDACTED]`.
- Happy path: redaction leaves non-secret fields untouched.
- Edge case: redaction handles nested dict fields (`{"auth": {"token": "..."}}`).

**Verification:**
- `uv sync` completes on both macOS and Pi OS (aarch64)
- `uv run herbert dev` exits cleanly with "ready" line
- `uv run herbert --help` shows `dev` and `run` subcommands
- Tests pass via `uv run pytest`
- Lint passes via `uv run ruff check`

---

- [ ] **Unit 2: Event bus + typed events + logging integration**

**Goal:** A typed pub/sub event bus using Pydantic models that all other components subscribe to. State machine, logger, latency instrumentation, and WebSocket all publish/subscribe through this single surface.

**Requirements:** R14 (structured events in logs), R6a (latency span events published on bus), R10 (state transitions as events)

**Dependencies:** Unit 1

**Files:**
- Create: `src/herbert/events.py` (event models, `AsyncEventBus`)
- Modify: `src/herbert/logging.py` (subscribe to bus, format events for log)
- Test: `tests/unit/test_event_bus.py`

**Approach:**
- Pydantic models for each event type: `StateChanged`, `TurnStarted`, `TurnCompleted`, `ExchangeLatency`, `LatencyMiss`, `ErrorOccurred`, `ViewChanged`, `TranscriptDelta`, `LogLine`
- Each event has a common envelope: `turn_id: str | None`, `timestamp: datetime`, `event_type: Literal[...]`
- `AsyncEventBus`: `subscribe(event_type) -> AsyncIterator`, `publish(event) -> None`. Simple topic-per-type fan-out using `asyncio.Queue` per subscriber
- Logger subscribes to everything and writes a structured line per event

**Test scenarios:**
- Happy path: publish `StateChanged(idle→listening)`; two subscribers each receive the event.
- Happy path: logger subscribes and writes a JSON line with `event_type`, `turn_id`, `timestamp`.
- Edge case: subscriber is slow — queue grows; bus does not block publisher.
- Edge case: publish event with no subscribers — no error.
- Edge case: subscriber raises — other subscribers still get the event; error is logged but not propagated.

**Verification:**
- Tests pass.
- A smoke test script publishes 1000 events across 5 subscribers in under 100ms on Mac.

---

### Milestone M2 — Voice loop on Mac

- [ ] **Unit 3: HAL — EventSource, AudioIn, AudioOut protocols + Mac adapters**

**Goal:** Establish the cross-platform hardware abstraction with Mac-side implementations of all three protocols. No Pi implementations yet — those land in M4.

**Requirements:** R2 (pluggable event source), R17-R18 (platform-neutral interfaces), R19 (`herbert dev` keyboard activation)

**Dependencies:** Unit 1

**Files:**
- Create: `src/herbert/hal/__init__.py` (protocols + `detect_platform()`)
- Create: `src/herbert/hal/mac.py` (pynput-based `EventSource`, sounddevice adapters)
- Create: `src/herbert/audio/capture.py`, `src/herbert/audio/playback.py`, `src/herbert/audio/devices.py`
- Create: `src/herbert/hal/mock.py` (for tests)
- Test: `tests/unit/test_hal_mock.py`, `tests/unit/test_audio_devices.py`

**Approach:**
- `EventSource` Protocol: `async def events() -> AsyncIterator[ButtonEvent]`; `ButtonEvent = PressStarted | PressEnded`
- `AudioIn` Protocol: `async def capture_until_released(max_seconds: float) -> bytes` (16kHz mono PCM)
- `AudioOut` Protocol: `async def play(chunks: AsyncIterator[bytes]) -> None`
- Mac impl: pynput listens for spacebar; captures PCM via `sd.InputStream` callback pushing to asyncio queue
- `detect_platform()` returns `"mac" | "pi" | "mock"` from env var `HERBERT_HAL` or auto-detect (`platform.machine()`)
- Device pinning: config keys `audio.input_device_name`, `audio.output_device_name`; substring match against `sd.query_devices()`; error lists available devices on miss

**Patterns to follow:**
- Best-practices research: `platform/` factory with Protocols; no `if platform ==` in application code
- sounddevice callback pattern using `loop.call_soon_threadsafe`

**Test scenarios:**
- Happy path: `MockEventSource` yields press/release pair; test consumer receives both in order.
- Happy path: `AudioIn.capture_until_released` with a mock PortAudio backend returns exactly the PCM bytes pushed.
- Edge case: press without release → capture terminates at `max_seconds`.
- Edge case: device name substring matches no device → error lists available devices.
- Edge case: device name matches multiple devices → uses first match, logs WARN.
- Integration: on a Mac with a real mic, `herbert dev` captures audio on spacebar hold (smoke test, run once manually).

**Verification:**
- `tests/unit/test_hal_mock.py` and `tests/unit/test_audio_devices.py` pass.
- Manual smoke: holding spacebar captures audio, verified by saving capture to a WAV and playing it back.

---

- [ ] **Unit 4: STT provider — whisper.cpp via pywhispercpp**

**Goal:** Implement `SttProvider.transcribe(pcm_bytes) -> str` using `pywhispercpp`. Model file lives at `~/.herbert/models/ggml-base.en-q5_1.bin`; `scripts/fetch-models.py` downloads and verifies SHA256.

**Requirements:** R4 (local STT via whisper.cpp), R5 (provider interface), R6 per-stage ceiling (≤1.2s on Pi 5 — per R6 table)

**Dependencies:** Unit 2

**Files:**
- Create: `src/herbert/stt/__init__.py` (`SttProvider` Protocol)
- Create: `src/herbert/stt/whisper_cpp.py`
- Create: `scripts/fetch-models.py`
- Test: `tests/unit/test_stt_whisper.py`, `tests/integration/test_stt_fixture.py`
- Test fixture: `tests/fixtures/turns/hello-world/input.wav`, `stt.json`

**Approach:**
- Protocol: `class SttProvider(Protocol): async def transcribe(self, pcm: bytes, sample_rate: int) -> SttResult` where `SttResult = { text: str, duration_ms: int }`
- Whisper impl: async wrapper runs `model.transcribe()` in a thread pool executor (pywhispercpp is sync); emits `ExchangeLatency` event with `stage="stt"` on completion
- Args per research: `n_threads=4`, `no_context=True`, `single_segment=True`
- `fetch-models.py`: downloads from huggingface.co/ggerganov/whisper.cpp, verifies SHA256 against a small manifest dict in the script itself (`MODELS = {"base.en-q5_1": ("url", "sha256")}`)

**Patterns to follow:**
- Best-practices: thread pool for sync inference; don't block the event loop
- Test fixture capture: record one real `input.wav` of "hello Herbert" and its ground-truth transcript into `tests/fixtures/turns/hello-world/`

**Test scenarios:**
- Happy path (unit): `transcribe(silence)` returns empty or near-empty text.
- Happy path (integration): `transcribe(fixtures/turns/hello-world/input.wav)` returns text matching the fixture's ground-truth (loose match: lowercased, stripped, Levenshtein ≤ 2).
- Happy path: emits `ExchangeLatency(stage="stt", duration_ms=...)` event on bus.
- Error path: model file missing → clear error message naming the expected path + fetch script.
- Edge case: 0-byte PCM input → returns empty text, no exception.
- Edge case: sample rate mismatch → resamples or errors with explicit message.

**Verification:**
- Fixture-based integration test passes on Mac.
- Manual Pi smoke (deferred to M4): `transcribe(hello-world/input.wav)` on Pi 5 completes in ≤1.2s (R6 STT ceiling).

---

- [ ] **Unit 5: LLM — streaming Anthropic client + sentence buffer**

**Goal:** Implement the LLM layer: takes STT text + session context, calls `claude-haiku-4-5` via `messages.stream`, yields complete sentences as they form. Buffers tokens until `.!?;` or 20-word gap, then flushes.

**Requirements:** R4 (Claude Haiku via streaming API), R6 per-stage ceilings (TTFT ≤600ms, sentence-complete ≤400ms), R7 (persona from file), R8 (persona as system prompt), R20-R21 (MCP `mcp_servers` pass-through, zero enabled in v1), R22 (session abstraction)

**Dependencies:** Unit 2, Unit 4

**Files:**
- Create: `src/herbert/llm/__init__.py`
- Create: `src/herbert/llm/claude.py` (streaming client + `SentenceBuffer`)
- Create: `src/herbert/llm/mcp_passthrough.py` (builds `mcp_servers` from config, always `[]` in v1)
- Create: `src/herbert/session.py` (in-memory `Session` with `messages: list[Message]`; abstract interface for v3)
- Test: `tests/unit/test_sentence_buffer.py`, `tests/unit/test_session.py`
- Test: `tests/integration/test_llm_live.py` (gated by `HERBERT_LIVE=1`)

**Approach:**
- `async def stream_turn(transcript: str, session: Session, persona: str) -> AsyncIterator[SentenceOut]`:
  - Append user message to session
  - Open `messages.stream` with `model=claude-haiku-4-5`, `system=persona`, `messages=session.messages`, optional `mcp_servers=[]` + beta header when MCP config is non-empty
  - Iterate `stream.text_stream`, feed each delta to `SentenceBuffer`
  - `SentenceBuffer` flushes on `.!?;` (with configurable ignore-inside-quotes) or 20-word gap
  - After stream ends, flush remaining buffer (even without boundary)
  - Emit `ExchangeLatency(stage="llm_ttft", ...)` on first token, `ExchangeLatency(stage="first_sentence", ...)` on first flush
- Persona: loaded via `persona.py` helper (not to be confused with session); last-good-cached logic lives there
- MCP passthrough: v1 reads `mcp.servers: []` from config; if ever non-empty in v2, constructs the `mcp_servers` list + beta header — otherwise omits entirely

**Patterns to follow:**
- Anthropic SDK async streaming pattern from framework-docs research
- Best-practices: one-sentence flush + 20-word fallback

**Test scenarios:**
- Happy path (unit): `SentenceBuffer.feed("Hello")` + `feed(" there.")` → flushes `"Hello there."`.
- Happy path: buffer holds text without boundary, flushes on explicit `.flush()` call.
- Edge case: unterminated stream (no boundary punctuation ever) → final `.flush()` emits remaining.
- Edge case: punctuation inside quotes (`'He said "hi." then left.'`) — single-sentence vs split — pick one behavior and test it (recommend: split on outermost, ignore inside quote pairs if cheap).
- Edge case: 20-word gap → flush kicks in even without punctuation.
- Happy path (integration, live): `stream_turn("Say hello.", empty_session, minimal_persona)` yields at least one non-empty sentence; TTFT < 1500ms (loose bar for CI).
- Error path: invalid API key → auth error surfaces; Session not mutated.
- Integration: `Session` round-trip — user message + assistant reply both appended in order.

**Verification:**
- Unit tests pass.
- Live smoke (manual or gated): one turn completes end-to-end with real Claude.
- Latency event with `stage="llm_ttft"` published on bus in a fixture-based replay.

---

- [ ] **Unit 6: TTS providers — ElevenLabs streaming WS + Piper fallback**

**Goal:** Implement the TTS layer: takes sentences (one at a time) and yields PCM chunks. ElevenLabs via WebSocket `text_to_speech.stream_input` is the default; Piper via `piper-tts` is the fallback.

**Requirements:** R4 (ElevenLabs default, Piper fallback), R5 (provider interface), R6 per-stage ceiling (TTS first-chunk ≤300ms on ElevenLabs, ≤600ms on Piper), R16 TTS error = terminal-until-button

**Dependencies:** Unit 2, Unit 3 (for AudioOut)

**Files:**
- Create: `src/herbert/tts/__init__.py` (`TtsProvider` Protocol)
- Create: `src/herbert/tts/elevenlabs_stream.py`
- Create: `src/herbert/tts/piper.py`
- Test: `tests/unit/test_tts_piper.py`, `tests/integration/test_tts_elevenlabs_live.py`

**Approach:**
- `TtsProvider.stream(sentences: AsyncIterator[str]) -> AsyncIterator[bytes]`: consumer provides sentences, provider yields PCM
- ElevenLabs impl: opens WS on first sentence, sends each sentence, streams PCM back; closes on `sentences` exhaust. Uses `eleven_flash_v2_5` + `chunk_length_schedule=[50,90,120,150,200]`
- Piper impl: per-sentence `voice.synthesize_stream_raw(sentence)` → yields PCM bytes
- Emits `ExchangeLatency(stage="tts_ttfb", ...)` on first chunk per sentence
- Provider factory: `get_tts_provider(config)` returns the right one based on `tts.default` config key

**Test scenarios:**
- Happy path (integration, Piper): `stream(["Hello there."])` yields at least one PCM chunk with non-zero length.
- Happy path (live, ElevenLabs): same; TTFB per sentence < 500ms (loose).
- Edge case: empty sentence `""` → skip (no WS send, no PCM out).
- Edge case: multiple sentences across one turn → both synthesized in order; no gap > 200ms between sentences.
- Error path: ElevenLabs WS drops mid-sentence → emits `ErrorOccurred(class="api_error_other")`; current sentence may be partial; caller expected to abort turn.
- Error path: Piper voice file missing → clear error with expected path.

**Verification:**
- Piper-based integration test passes offline.
- Live ElevenLabs smoke once (stored audio played back manually for sanity).

---

- [ ] **Unit 7: State machine + Turn orchestration + pipeline wiring**

**Goal:** The heart. A state machine that wires EventSource → STT → LLM → TTS → AudioOut with a single `asyncio.Event cancel_event` per turn for barge-in. Publishes `StateChanged`, `TurnStarted`, `TurnCompleted` on the bus.

**Requirements:** R4 (pipeline), R6 (latency targets), R10 (4 pipeline states + error), R16 (graceful failure handling per-class), R22 (session threading)

**Dependencies:** Units 3, 4, 5, 6

**Files:**
- Create: `src/herbert/state.py` (StateMachine class)
- Create: `src/herbert/turn.py` (Turn dataclass + TurnSpan)
- Create: `src/herbert/daemon.py` (asyncio entrypoint, wires components)
- Create: `src/herbert/errors.py` (error classification helpers)
- Modify: `src/herbert/cli.py` (`dev` and `run` call into `daemon.run(mode=...)`)
- Test: `tests/unit/test_state_machine.py`, `tests/unit/test_turn_cancellation.py`
- Test: `tests/integration/test_pipeline_replay.py` (first replay fixture)

**Approach:**
- `State = Literal["idle", "listening", "thinking", "speaking", "error"]`
- `StateMachine`: transition table (source state × event → target state), validates transitions, publishes `StateChanged` events on bus. **Unknown transitions (e.g., `PressEnded` arriving in `idle`, caused by duplicate events from pynput + frontend) are logged at DEBUG and ignored** — never raise; never silently change state. The daemon de-duplicates button events within a 50ms window to handle the pynput/frontend redundant-path race
- `Turn`: `turn_id`, `cancel_event: asyncio.Event`, `span: TurnSpan`. Passed through pipeline as context
- `daemon.run()`: creates event bus, state machine, HAL, providers; spawns a loop that waits for `PressStarted` → creates Turn → progresses through states → awaits `PressEnded` → pipelines STT → LLM → TTS
- Barge-in: second `PressStarted` during a turn sets `cancel_event`, which aborts in-flight LLM stream (`stream.close()`), closes TTS WS, flushes audio buffer, starts a new Turn
- Error classification: `classify_error(exc) -> ErrorClass` returns one of: `network_transient`, `api_auth`, `api_rate_limit`, `api_policy`, `mic_error`, `speaker_error`, `whisper_error`, `tts_error`, `missing_secrets`, `persona_invalid`
- On error: transition to `error` state with classified diagnostic; retry policy per R16 (auto-retry network/5xx with 1s→2s→5s→10s exponential; everything else stays until button)

**Technical design:**

```
class Turn:
    turn_id: str                   # ULID
    cancel_event: asyncio.Event    # shared across all pipeline workers
    span: TurnSpan                 # timings
    session: Session               # conversation history

class StateMachine:
    transitions = {
        (idle, press_started):         listening,
        (listening, press_ended):      thinking,
        (thinking, first_tts_chunk):   speaking,
        (speaking, tts_complete):      idle,
        (any, error_occurred):         error,
        (error, press_started):        listening,         # manual retry
        (error, network_restored):     idle,              # auto-retry success
    }
```

*This is directional guidance, not implementation specification — the implementer should refine the transition table based on edge cases that surface during coding.*

**Patterns to follow:**
- Best-practices research: explicit transition table, event-driven, shared `cancel_event`
- Flow analysis: barge-in is the defined mid-exchange behavior

**Execution note:** Start with a characterization test of the happy-path state sequence before implementing — the test becomes the spec for the transition table.

**Test scenarios:**
- Happy path: `PressStarted → PressEnded → transcript → first_sentence → first_tts_chunk → tts_complete` traverses all 4 states in order.
- Happy path (integration via replay fixture): full turn completes; all expected `StateChanged` events emitted in order.
- Edge case: `PressStarted` during `speaking` → current turn's `cancel_event` is set; LLM stream `stream.close()` called; TTS WS closes; new Turn begins in `listening`.
- Edge case: `PressStarted` during `error` (mic-error class) → new Turn attempts `listening` (manual retry behavior).
- Error path: Claude API 529 → transitions to `error(class="network_transient")`; retry after 1s; on success → `idle`.
- Error path: Claude API 401 → transitions to `error(class="api_auth")`; stays until button press.
- Error path: ElevenLabs WS drops mid-speaking → transitions to `error(class="tts_error")`; current audio truncated; stays until button.
- Integration: barge-in interrupts TTS playback audibly (manual smoke).
- Edge case: secrets missing at startup → daemon starts in `error(class="missing_secrets")`; health check surfaces via WS.

**Verification:**
- State-machine unit tests pass.
- Replay-fixture integration test (Unit 4's fixture extended to full turn) passes on Mac.
- Manual smoke: hold spacebar, speak "say hello", hear Herbert reply within ~2s on Mac.
- Manual barge-in smoke: while Herbert is speaking, press spacebar again → old audio stops, Herbert starts listening to new input.

---

### Milestone M3 — Web frontend + fake boot

- [ ] **Unit 8: FastAPI web server + WebSocket state channel + auth scaffolding**

**Goal:** Stand up the HTTP server that serves the frontend and a WebSocket channel for state events. Localhost-only by default; `--expose` + bearer token for LAN.

**Requirements:** R15 (localhost default, `--expose` + bearer), R10-R13 (state events over WS), R14 (log tail endpoint)

**Dependencies:** Unit 2, Unit 7

**Files:**
- Create: `src/herbert/web/app.py`, `src/herbert/web/ws.py`, `src/herbert/web/auth.py`
- Create: `src/herbert/web/static/` (empty — frontend mounts here after Unit 9)
- Modify: `src/herbert/daemon.py` (launch uvicorn as an asyncio task)
- Modify: `src/herbert/cli.py` (add `--expose` flag)
- Modify: `src/herbert/config.py` (add `web.bind_host`, `web.port`, `web.expose`)
- Modify: `src/herbert/secrets.py` (generate `FRONTEND_BEARER_TOKEN` on first boot if missing)
- Test: `tests/unit/test_web_auth.py`, `tests/integration/test_ws_state_events.py`

**Approach:**
- FastAPI app with: `/healthz` (JSON: daemon state, provider status, recent latency), `/ws` (WebSocket for state events), `/api/logs/tail` (SSE for log tail), static mount at `/`
- Bearer token middleware: enforced only when `web.expose=true`; localhost binding → no auth required
- On first boot, if `FRONTEND_BEARER_TOKEN` missing in secrets file, generate a URL-safe 32-byte token and append to `~/.herbert/secrets.env`
- `/ws` subscribes to event bus, pushes `StateChanged`, `TranscriptDelta`, `ExchangeLatency`, `LatencyMiss`, `ViewChanged`, `ErrorOccurred` as JSON
- uvicorn runs inside the main asyncio loop as a task (not a subprocess); `uvicorn.Server` with `Config(loop="asyncio", lifespan="on")`

**Patterns to follow:**
- FastAPI WebSocket docs (lifecycle, broadcasting)
- `hmac.compare_digest` for token comparison

**Test scenarios:**
- Happy path: localhost bind, WS connection without token accepted; state events flow.
- Happy path: exposed mode (bind 0.0.0.0), correct bearer token in URL → WS accepted.
- Error path: exposed mode, wrong or missing token → 401.
- Error path: exposed mode, correct token but request from disallowed method → 405.
- Edge case: WS client disconnects → subscription cleaned up, no leak.
- Happy path: `/healthz` returns JSON with current state + provider names.
- Integration: WS sees `StateChanged` within 100ms of state machine publishing it.

**Verification:**
- Integration test: open WS client, trigger a turn via mock HAL, receive the full event stream.
- Curl `http://localhost:8080/healthz` returns JSON.
- Bearer token generated on first boot, readable in `~/.herbert/secrets.env`.

---

- [ ] **Unit 9: Pixel-art frontend (PIXI.js v8) with state reactions, transcript, and fake boot**

**Goal:** The HTML+JS frontend that renders the pixel-art character, live transcript, fake boot sequence, and diagnostic view toggle. Built with Vite + PIXI.js v8; output goes into `src/herbert/web/static/`.

**Requirements:** R9 (fake boot sequence), R10 (4 states + error pose), R11 (transcript lifecycle, ≤40% display), R13 (diagnostic view UI only — regex trigger lives on daemon), R12 (error pose + diagnostic banner)

**Dependencies:** Unit 8

**Files:**
- Create: `frontend/package.json`, `frontend/vite.config.js`, `frontend/index.html`
- Create: `frontend/src/main.js`, `frontend/src/state.js`, `frontend/src/transcript.js`, `frontend/src/diagnostic.js`, `frontend/src/boot.js`, `frontend/src/ws.js`, `frontend/src/latency.js`
- Create: `frontend/assets/herbert.json`, `frontend/assets/herbert.png` (placeholder sprite sheet — simple single-color shapes for each state is fine for v1)
- Create: `frontend/assets/boot-seq.txt` (4-6 lines, retro-style)
- Create: `scripts/build-frontend.sh` (`cd frontend && npm ci && npm run build` → copies to `src/herbert/web/static/`)
- Test: `frontend/src/__tests__/` (minimal — main validation is visual)

**Approach:**
- `main.js`: PIXI Application with `antialias: false`, `roundPixels: true`, `nearest` scale mode
- `state.js`: AnimatedSprite; on WS `StateChanged` event, swap textures to the matching animation
- `transcript.js`: HTML `<div>` positioned with CSS grid (canvas 60% / transcript 40%); WS `TranscriptDelta` events append streaming text; cleared on next `TurnStarted`
- `boot.js`: on WS `BootSequenceStart`, displays each line of `boot-seq.txt` with a brief typewriter effect, then hands off to main UI
- `diagnostic.js`: on WS `ViewChanged(mode="diagnostic")`, hide PIXI canvas, show log-tail div populated from SSE; banner above if error active
- `latency.js`: small corner badge updates on `LatencyMiss` events; shows the missed stage name + actual ms
- Placeholder sprites v1: 4 filled squares of different colors (idle=grey, listening=green, thinking=amber, speaking=blue) + error=red. Enough to validate the plumbing; Matt iterates the real art afterwards

**Patterns to follow:**
- Framework research: PIXI v8 pattern, HTML div for transcript, not PIXI text
- Research note: Pi 5 GPU handles PIXI fine at 1080p

**Test scenarios:**
- Integration (browser automation or manual smoke): trigger a turn via HAL mock, observe character sprite swap through 4 states + error demo.
- Happy path: boot sequence plays on first load, 3-6s, then main UI visible.
- Edge case: WS drops → reconnect with exponential backoff; UI shows a "reconnecting" state.
- Edge case: rapid state changes (e.g., idle→listening→idle quickly) → UI keeps up without flicker.
- Integration: diagnostic trigger via daemon (simulated WS message) flips the UI.

**Verification:**
- `scripts/build-frontend.sh` builds cleanly.
- `herbert dev` serves the frontend at `http://127.0.0.1:8080`.
- Manual: full voice loop shows all 4 states on-screen; transcript appears as Claude streams.

---

### Milestone M4 — Raspberry Pi deployment

- [ ] **Unit 10: Pi HAL adapters (GPIO button + ALSA/PipeWire audio) + systemd units + kiosk**

**Goal:** Make `herbert run` work on a Raspberry Pi 5. GPIO button via gpiozero; Chromium kiosk auto-launches after daemon health; systemd user units orchestrate startup.

**Requirements:** R1 (GPIO button), R3 (Pi 5 runtime target), R18 (Pi audio routing), R19 (`herbert run` Pi entry), R9 (gated kiosk launch on daemon health)

**Dependencies:** Units 7, 8, 9

**Files:**
- Create: `src/herbert/hal/pi.py` (gpiozero EventSource, Pi audio device defaults)
- Create: `config/systemd/herbert-daemon.service`
- Create: `config/systemd/herbert-kiosk.service`
- Create: `config/systemd/wait-for-herbert.sh`
- Create: `scripts/pi-install.sh`
- Test: `tests/unit/test_hal_pi_mock.py` (uses gpiozero's `MockFactory`)

**Approach:**
- `pi.py`: `GpioEventSource` uses `Button(17, pull_up=True, bounce_time=0.05)`; `when_pressed` / `when_released` callbacks bridge to asyncio via `loop.call_soon_threadsafe`
- `detect_platform()` auto-routes to `pi.py` when `platform.machine()=='aarch64'` and `/sys/firmware/devicetree` exists
- Pi audio defaults: probe for `USB Audio Device`-ish substring at startup; fall back to `bcm2835 Headphones` for output (with WARN that quality is poor)
- `herbert-daemon.service`: `Type=simple`, `WorkingDirectory=/home/pi/herbert`, `ExecStart=/home/pi/herbert/.venv/bin/herbert run`, `Restart=on-failure`
- `herbert-kiosk.service`: `ExecStartPre=/path/to/wait-for-herbert.sh`, `ExecStart=chromium-browser --ozone-platform=wayland --app=http://127.0.0.1:8080 --noerrdialogs ...`
- `wait-for-herbert.sh`: polls `/healthz` with 30s timeout; exits 0 on first 200-OK
- `pi-install.sh`: creates dirs, copies units to `~/.config/systemd/user/`, runs `systemctl --user enable --now ...`, `loginctl enable-linger pi`

**Patterns to follow:**
- Framework research: `LGPIOFactory(chip=4)` is mandatory for Pi 5
- gpiozero's `MockFactory` for host-side tests

**Test scenarios:**
- Happy path (unit, with `MockFactory`): simulated press/release produces `PressStarted`/`PressEnded` events on the bus.
- Happy path (Pi smoke): `herbert run` started under systemd-user; pressing physical button begins a voice turn.
- Happy path: Chromium kiosk auto-launches after `wait-for-herbert.sh` succeeds; frontend loads without "daemon not ready" error.
- Edge case: Chromium starts before daemon → `wait-for-herbert.sh` blocks up to 30s; kiosk only navigates after health.
- Edge case: daemon unhealthy at 30s → `wait-for-herbert.sh` exits non-zero; systemd retries per unit policy.
- Edge case: USB audio device not found → WARN log, fallback to bcm2835 Headphones.

**Verification:**
- Full cold boot on Pi: power-on → daemon up → kiosk loads → button triggers voice turn. Latency measured per R6; should meet Pi 5 hybrid p50 ≤2.5s.
- `systemctl --user status herbert-daemon` shows active.
- Logs clean (no warnings about XRuns after 10 turns).

---

### Milestone M5 — Polish, reliability, observability

- [ ] **Unit 11: Latency instrumentation (R6a) + health checks + error recovery (R16)**

**Goal:** Wire up first-class observability (R6a) and robust error recovery (R16). Every turn records per-stage durations; misses are WARN-logged and surfaced to the frontend; errors recover per class.

**Requirements:** R6a (latency instrumentation), R16 (graceful degradation per error class), R9 + health.py (startup health checks)

**Dependencies:** Units 2, 7, 8, 9, 10

**Files:**
- Create: `src/herbert/health.py` (startup checks)
- Modify: `src/herbert/turn.py` (finalize `TurnSpan`)
- Modify: `src/herbert/state.py` (wire recovery per class)
- Modify: `src/herbert/daemon.py` (run health checks, drive boot sequence)
- Modify: `src/herbert/web/ws.py` (push `LatencyMiss` events)
- Modify: `frontend/src/latency.js` (corner badge)
- Test: `tests/unit/test_health.py`, `tests/integration/test_latency_instrumentation.py`, `tests/integration/test_error_recovery.py`

**Approach:**
- `TurnSpan`: dataclass with `turn_id`, `stage_durations: dict[str, float]`, `total_ms: float`, `misses: list[str]`. Compared against config-driven `R6_CEILINGS[mode]` at turn completion
- On miss: emit `LatencyMiss` event with stage name, actual, target, `turn_id`, providers, mode
- `health.py`: `async def run_startup_checks(config) -> list[HealthCheck]`. Each check returns `HealthCheck(name, ok, message)`. Failures surface as errors in the boot sequence
- Error recovery: a per-turn recovery task that, on network/5xx error, waits 1s → 2s → 5s → 10s then aborts; on each success, transitions back to idle with optional "I'm back" cue (suppressed if now in `listening` or `speaking`)
- "I'm back" cue: pre-synthesized short WAV at `assets/im-back.wav`; generated once via ElevenLabs on first boot

**Patterns to follow:**
- OpenTelemetry GenAI span taxonomy
- Best-practices: suppression rule for recovery cue during active turn

**Test scenarios:**
- Happy path: normal turn emits `exchange_latency` with all stage durations.
- Edge case: turn hits STT ceiling → emits `latency_miss` with `stage="stt"`.
- Edge case: total exceeds p95 but no single stage hits its ceiling → `latency_miss` with `stage="total"`.
- Error path: simulated network drop → auto-retry at 1s, 2s, 5s, 10s; on recovery, plays cue (if idle) → returns to idle.
- Error path: simulated auth error → stays in error until button press; no retries.
- Edge case: network recovers while user is in `listening` → cue suppressed; returns to `idle` only after turn completes.
- Happy path: all startup checks pass → boot sequence completes → `idle`.
- Error path: mic check fails → boot sequence completes → error state with `mic_error` diagnostic.
- Integration: latency corner badge updates on miss events.

**Verification:**
- Integration test drives a full turn and asserts all latency events fired.
- Manual: unplug wifi during a turn, verify error state → plug back in → Herbert recovers.
- Pi smoke: p50 latency measured over 10 turns; meets R6 hybrid target.

---

- [ ] **Unit 12: Diagnostic mode + persona reload + transcript lifecycle + log rotation**

**Goal:** All the UI and persona polish: diagnostic view swap via voice command (regex matched on STT transcript before Claude call), persona file hot-reload, transcript lifecycle per R11, log rotation + redaction finalization.

**Requirements:** R7 (persona reload, last-good-cached), R11 (transcript lifecycle), R13 (diagnostic mode voice trigger), R14 (log rotation, 7-day retention, redaction, transcript opt-out)

**Dependencies:** Units 2, 5, 7, 8, 9

**Files:**
- Create: `src/herbert/diagnostic/__init__.py`, `src/herbert/diagnostic/triggers.py`, `src/herbert/diagnostic/mode.py`
- Create: `src/herbert/persona.py` (load, cache, hot-reload)
- Modify: `src/herbert/llm/claude.py` (use persona.get_current() instead of reading file inline)
- Modify: `src/herbert/logging.py` (finalize rotation, 7-day retention, transcript opt-out)
- Modify: `src/herbert/state.py` (check trigger regex before calling LLM; if match, suppress LLM call and switch view)
- Modify: `src/herbert/web/ws.py` (publish `ViewChanged` events)
- Modify: `frontend/src/diagnostic.js`, `frontend/src/transcript.js`
- Test: `tests/unit/test_triggers.py`, `tests/unit/test_persona_reload.py`, `tests/integration/test_diagnostic_mode.py`

**Approach:**
- `triggers.py`: compile regex list once at startup; `match(transcript) -> "enter_diagnostic" | "exit_diagnostic" | None`. Match against `.strip().lower()` with punctuation stripped; whole-utterance match (no partial)
- `persona.py`: `get_current() -> str`; internal cache + mtime check on read. On read failure (missing/malformed/empty), WARN and return cache. On startup with no cache and no file, fail closed (raises)
- Transcript lifecycle: frontend clears on `TurnStarted` event; server publishes `TurnStarted` when `PressStarted` fires → cleared before new transcript text arrives
- Log rotation: `TimedRotatingFileHandler(when="midnight", backupCount=7)`; file path per R14 (`~/.herbert/herbert.log`)
- Transcript opt-out: config `logging.log_transcripts: bool` (default true); when false, `TranscriptDelta` events are not logged (but still sent to WS)

**Patterns to follow:**
- Flow analysis: whole-utterance trigger match; last-good-cached persona; diagnostic UI shows error banner above log tail if error active

**Test scenarios:**
- Happy path (unit): trigger regex matches "herbert show me the logs" exactly; `match()` returns `enter_diagnostic`.
- Happy path: matches "herbert, show me the logs." (with punctuation + comma).
- Edge case (false positive avoidance): "herbert show me the logs from yesterday's deploy" does NOT match (not whole-utterance).
- Edge case: "HERBERT, DIAGNOSTIC MODE" matches (case-insensitive).
- Happy path: exit trigger works from diagnostic mode.
- Edge case (persona reload): edit `~/.herbert/persona.md` → next turn's Claude system prompt reflects the new content.
- Edge case (persona bad): replace persona file with empty bytes → WARN log; next turn uses last-good-cached content.
- Edge case (persona missing at startup, no cache) → daemon fails closed with `missing persona` error.
- Happy path: transcript clears on new button press; no residue from previous turn.
- Edge case (log rotation): simulate 7+ day-files in log dir → oldest pruned, most recent retained.
- Edge case (transcript opt-out): `log_transcripts=false` → no transcript content in log file, but WS still streams it.

**Verification:**
- Manual: say "Herbert, show me the logs" while in character view → UI flips to log tail.
- Manual: edit persona file while daemon running → observable behavior change within one turn.
- Log file after 10 turns is clean, no secrets, correct rotation.

---

### Milestone M6 — MCP scaffolding + session abstraction + ship

- [ ] **Unit 13: MCP pass-through scaffolding (zero enabled) + session abstraction polish + final discovery (mDNS, QR)**

**Goal:** Wire the `mcp_servers` pass-through so enabling a remote MCP in v2 is a config change (not a code change). Solidify the session abstraction (`Session` interface with v1 in-memory impl). Add mDNS announce + QR code display for exposed frontend.

**Requirements:** R20 (MCP external-only), R21 (`mcp_servers` pass-through), R22 (session abstraction), R15 (discovery when exposed)

**Dependencies:** Units 5, 7, 8, 11, 12

**Files:**
- Modify: `src/herbert/llm/claude.py` (accept `mcp_servers` list, add beta header when non-empty)
- Modify: `src/herbert/llm/mcp_passthrough.py` (config → `mcp_servers` list; validate allowlist)
- Modify: `src/herbert/session.py` (finalize `Session` Protocol — v1 impl is `InMemorySession`; leaves room for v3 `SqliteSession`)
- Create: `src/herbert/discovery.py` (zeroconf announce + QR generation)
- Modify: `src/herbert/daemon.py` (start discovery if `web.expose=true`)
- Modify: `frontend/src/boot.js` (render QR on boot when token URL is present)
- Test: `tests/unit/test_session_memory.py`, `tests/unit/test_mcp_passthrough_empty.py`, `tests/integration/test_mcp_passthrough_stub.py`

**Approach:**
- `mcp_passthrough.build(config) -> list[MCPServerParam]`: reads `mcp.servers: list` from config; validates each entry has `name` + `url`; returns empty list in v1 (config ships with empty)
- `claude.stream_turn(...)` accepts `mcp_servers=...` kwarg; passes through to `client.beta.messages.stream` with beta header when list is non-empty; otherwise uses the standard `client.messages.stream` without the beta header
- `Session` Protocol: `messages: list`, `append(msg) -> None`, `clear() -> None`. `InMemorySession` impl + a `SqliteSession` *placeholder* (raises `NotImplementedError` with a pointer to v3 plan)
- Discovery: `zeroconf` registers `_herbert._tcp.local.` service when `web.expose=true`; daemon prints URL + generates a QR code as PNG to `~/.herbert/qr.png` (permissions `0600`, token-embedded URL is sensitive) and publishes a `BootQrReady` event so the boot sequence can render it briefly. The QR file is deleted on daemon shutdown and regenerated on each expose-mode boot so it reflects the current bearer token

**Patterns to follow:**
- Framework research: `mcp-client-2025-11-20` beta header; `mcp_servers` shape as list of `{type, url, name, authorization_token}`

**Test scenarios:**
- Happy path: v1 config has empty `mcp.servers` → `build(config) == []` → `claude.stream_turn` takes the non-beta path.
- Edge case: config has a malformed MCP entry (missing url) → validation error at startup (fail fast, not runtime).
- Happy path (stub): a test config with one entry routes through the beta path; mock beta API accepts it. (Not a live test — mocked.)
- Happy path (session): `InMemorySession.append(user_msg) then append(assistant_msg)` → `messages` contains both in order.
- Happy path (discovery): `web.expose=true` → mDNS service registered; `herbert.local` resolves from a second device on same LAN.
- Happy path (QR): boot sequence briefly displays QR; scanning it opens the full URL with token.
- Edge case (no expose): discovery + QR not run; bind stays on localhost.

**Verification:**
- Test suite passes.
- Manual: enable expose, scan QR from phone → opens Herbert frontend with token pre-filled.
- `~/.herbert/secrets.env` contains a persistent bearer token that survives daemon restarts.

---

- [ ] **Unit 14: End-to-end smoke tests + README + ship prep**

**Goal:** Comprehensive smoke test suite, user-facing README, CLAUDE.md updates, and final polish. Aim: someone else could clone and run Herbert on both Mac and Pi from the README alone.

**Requirements:** All. This unit verifies the full spec.

**Dependencies:** Units 1-13

**Files:**
- Create: `tests/integration/test_smoke_mac.py`
- Create: `tests/integration/test_smoke_pi.py` (gated by `HERBERT_PI_SMOKE=1`, run on Pi)
- Modify: `README.md` (install, first-time setup, daily use, troubleshooting, config reference)
- Modify: `CLAUDE.md` (architecture overview, design decisions, how-to-contribute for future Matt)
- Modify: `scripts/dev-install.sh` (Mac one-shot setup)
- Modify: `scripts/pi-install.sh` (Pi one-shot setup)

**Approach:**
- Mac smoke: run daemon, mock HAL, drive 10 turns via replay fixtures, assert all success criteria from origin (latency <5% miss, state transitions correct, diagnostic mode works, persona reload works, expose+auth rejects wrong token).
- Pi smoke: same test plus real hardware checks (GPIO button physically wired, audio pinned to USB device, kiosk loads).
- README sections: **Quick start (Mac)**, **Quick start (Pi)**, **Config reference**, **Swapping voices**, **Swapping providers (cloud STT)**, **Troubleshooting** (common failures: missing secrets, mic not detected, kiosk white screen, ElevenLabs rate limit, whisper model not found).
- CLAUDE.md sections: **Architecture at a glance**, **Key decisions and why**, **How to add a provider**, **How to add a new event type**, **How to debug a stuck state**.

**Test scenarios:**
- Happy path: Mac smoke runs all 10 fixtures, asserts success criteria.
- Happy path: Pi smoke (manual trigger) runs full cold-boot → voice loop.
- Identity test (deferred per R8): Matt uses Herbert daily for 2 weeks and names Herbert-isms — evaluated qualitatively.

**Verification:**
- Both smoke suites pass.
- Clean-room install test: wipe Mac environment, follow README, run `herbert dev`, voice loop works on first try.
- Clean-room install test on Pi: same.
- `uv run ruff check` clean; `uv run pytest` clean.

---

## System-Wide Impact

- **Interaction graph:** Button (HAL) → StateMachine → STT/LLM/TTS workers → AudioOut, all mediated by the async event bus. WS subscribes to bus for UI sync. Logger subscribes to bus for structured log. Latency span collector subscribes to bus for R6a.
- **Error propagation:** Every async task in the pipeline is wrapped in a try/except that classifies the error via `classify_error()` and emits an `ErrorOccurred` event. The state machine consumes those and applies the recovery policy. The daemon never dies on a single failure.
- **State lifecycle risks:** Mid-turn cancellation must fan out cleanly — three streams (STT, LLM, TTS) and one audio buffer all observe the same `cancel_event`. Race conditions around "Herbert is speaking and user presses button" are the highest-risk area; tests in Unit 7 must cover this explicitly.
- **API surface parity:** `SttProvider` and `TtsProvider` Protocols define the contract. Future provider impls (Deepgram, Whisper API, OpenAI TTS) must match exactly — the async-iterator-of-bytes shape was chosen for common-denominator streaming support.
- **Integration coverage:** Replay fixtures in `tests/fixtures/turns/` are the primary integration-test mechanism. Live smoke tests are gated by env flags so CI doesn't burn credits. Pi-specific checks are gated separately.
- **Unchanged invariants:** Since this is greenfield, there is no existing invariant to preserve. But decisions that will be painful to revisit later — Protocol shapes for STT/TTS, event schema, WebSocket message format — should stabilize in M2 and NOT churn in M3-M6.

## Risks & Dependencies

| Risk | Mitigation |
|---|---|
| Pi 5 GPIO on RP1 chip has subtle kernel-version interactions; gpiozero + lgpio may need version pinning | Research identified this as a known landmine. Pin `lgpio>=0.2` and `gpiozero>=2.0`. Smoke-test GPIO on actual Pi 5 hardware in Unit 10 before depending on it in M5-M6. |
| PortAudio on PipeWire (Pi OS Bookworm) can produce XRuns under load | Research names this; `sounddevice` callback with small blocksize (256-512) is the mitigation. Unit 10 smoke-test captures 10 turns and asserts no audible glitches. |
| ElevenLabs WS drops mid-synthesis; user experience is a cut-off sentence | Error classification + per-class recovery (Unit 7, Unit 11). Current turn truncates, error state, user presses button to retry. |
| Anthropic `mcp-client-2025-11-20` beta header may change again before we enable MCPs | Low-impact in v1 since no MCPs are enabled. Verify at v2 planning time; if deprecated, update the header in one place. |
| Chromium kiosk on Wayland + labwc has rough edges (white bars, fullscreen quirks) | Documented in Unit 10 approach; fallback is `--app=URL` instead of `--kiosk`. Acceptance: kiosk displays Herbert frontend edge-to-edge. |
| Sentence-boundary heuristic fails on unusual punctuation (ellipses, abbreviations like "Dr.") | Start simple; add ignore-list for known abbreviations if encountered. Log unexpected boundary behavior in Unit 5; refine in a followup if it's audibly wrong. |
| Streaming TTS + streaming LLM race — a sentence flushes to TTS before the next chunk arrives, producing audible gaps | Best-practices research: prebuffer 80-120ms of PCM before playback start. Unit 6 approach includes this. |
| Persona file with prompt-injection attempt (e.g., "ignore previous instructions") — low risk for a single-user device but noted in security-lens review | Accepted. Matt is the only user; there is no "before" for prompt-injection. Documented in scope. |
| First-run model downloads (whisper base.en + Piper voice) require internet; users expect offline from "local STT" framing | `scripts/fetch-models.py` runs as part of install; README covers the one-time requirement. |
| Mac Intel users may see slower whisper.cpp performance (no Metal) | Acceptable — Mac Intel is a dev-only target. Document in README. |

## Documentation / Operational Notes

- **README.md** is the user-facing install and run guide, covering Mac dev + Pi production.
- **CLAUDE.md** is the agent/future-Matt design doc.
- **`config/herbert.example.toml`** is a commented example config; users copy to `~/.config/herbert/config.toml` and edit.
- **`~/.herbert/secrets.env`** stores API keys; `0600` permissions; generated-bearer-token added by daemon on first boot.
- **`~/.herbert/models/`** and **`~/.herbert/voices/`** hold downloaded model files; populated by `scripts/fetch-models.py`.
- **Log location:** `~/.herbert/herbert.log` rotated daily.
- **systemd units** shipped in `config/systemd/`; installed by `scripts/pi-install.sh`.
- **First-run ergonomics:** running `herbert dev` without secrets prints a clear setup message; `scripts/dev-install.sh` automates the full first-run on Mac.

## Phased Delivery

| Milestone | Scope | Demoable outcome |
|---|---|---|
| **M1** (Units 1-2) | Scaffold, config, secrets, logging, event bus | `herbert dev` prints "ready"; logs events to disk with redaction. |
| **M2** (Units 3-7) | Voice loop on Mac end-to-end | Hold spacebar, speak, hear Herbert reply on Mac. First real vertical slice. |
| **M3** (Units 8-9) | Web frontend + fake boot | Pixel-art character reacts to state live; fake boot sequence plays. Full retro vibe visible. |
| **M4** (Unit 10) | Pi 5 deployment | Power on a Pi, press physical button, voice loop works. First "it's a real device" moment. |
| **M5** (Units 11-12) | Observability, recovery, diagnostic mode, transcript polish | Latency instrumented; unplug wifi + recover; voice-triggered diagnostic mode; transcript lifecycle correct. |
| **M6** (Units 13-14) | MCP scaffolding, mDNS, ship | `herbert.local` discoverable; README clean-room install works; v1 ships. |

Each milestone lands in its own PR (or branch-batch, user's choice). M2's PR is the heaviest — it delivers the first real voice-loop demo.

## Sources & References

- **Origin document:** [docs/brainstorms/2026-04-18-herbert-requirements.md](../brainstorms/2026-04-18-herbert-requirements.md)
- Related code patterns: `/Users/matt/dev/rattlesnake/pyproject.toml`, `/Users/matt/dev/rattlesnake/src/rattlesnake/` (Matt's Python conventions — Protocol adapters, CLI shape, config precedence)
- External references: see Context & Research § External References above.
