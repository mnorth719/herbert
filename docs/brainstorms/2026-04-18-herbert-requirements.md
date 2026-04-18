---
date: 2026-04-18
topic: herbert-voice-assistant-device
---

# Herbert — Claude-Powered Voice Assistant Device

## Problem Frame

Herbert is a personal tinker project: a Claude-powered, voice-enabled AI assistant that lives in the home as a dedicated piece of hardware. The *device existing* is the point — features serve a retro-futurist, character-forward vibe rather than trying to be Alexa-better. The namesake is Rodney Brooks' 1988 MIT soda-can-collecting robot, itself named for AI pioneer Herbert Simon.

Single user (the builder). Lives on a Raspberry Pi 5 in a form factor TBD (smart-mirror, homepod-like, hollowed-Mac replica are all candidates). Success is a device that feels like *a specific little guy* sitting on a shelf, not a generic Claude client.

## Requirements

**Activation & Hardware (v1)**
- R1. Push-to-talk activation via a hardware button (GPIO momentary switch) on the Pi. No always-listening, no wake-word.
- R2. Activation is exposed via a pluggable event-source interface so PIR/gesture/camera can be added later without refactoring.
- R3. Runs on a Raspberry Pi 5 with a USB or HAT microphone, a speaker, a display (HDMI or DSI), WiFi, and Bluetooth available for debugging (e.g., attaching a keyboard).

**Conversation Pipeline (v1)**
- R4. **Hybrid default:** audio is captured on button press, transcribed **locally** via `whisper.cpp` (privacy), sent to Claude (Haiku 4.5 by default) via the Anthropic API using `messages.stream`, and the streamed response is synthesized by **ElevenLabs streaming TTS** (character) and played through the speaker. Piper remains available as a local-TTS fallback (R5).
- R5. STT and TTS are accessed through provider interfaces that allow swapping by config change. Named providers for v1: **STT** = `whisper.cpp` (local, default), Whisper API, or Deepgram streaming; **TTS** = ElevenLabs streaming (default), Piper (local fallback), or OpenAI TTS. Each interface supports streaming where the provider does.
- R6. **Latency targets**, measured from button *release* to first audible sample:

  | Mode | p50 | p95 |
  |---|---|---|
  | Pi 5, hybrid default (whisper.cpp + ElevenLabs) | ≤ 2.5s | ≤ 3.0s |
  | Pi 5, full-local fallback (whisper.cpp + Piper) | ≤ 3.5s | ≤ 4.0s |
  | Pi 5, full-cloud (Deepgram + ElevenLabs) | ≤ 1.5s | ≤ 2.0s |
  | macOS dev, hybrid | ≤ 1.5s | ≤ 2.0s |

  **Per-stage ceilings** (Pi 5 hybrid default — sum ≈ 2.6s, fits under the 3.0s p95):

  | Stage | Ceiling |
  |---|---|
  | Audio flush after button release | ≤ 50ms |
  | whisper.cpp STT (base or q4-quantized) | ≤ 1200ms |
  | Claude TTFT (Haiku, warm connection) | ≤ 600ms |
  | First sentence complete (for TTS handoff) | ≤ 400ms |
  | ElevenLabs first audio chunk (websocket streaming) | ≤ 300ms |
  | Audio playback start | ≤ 50ms |

  Per-stage ceilings are fail thresholds for individual exchanges (any single stage exceeding its ceiling triggers `latency_miss`, R6a). Mode p50/p95 targets are statistical aggregates. Given streaming Claude + streaming TTS, Herbert starts speaking before Claude finishes generating.
- R6a. **Latency instrumentation.** Every exchange records per-stage durations plus total button-release-to-first-audio time. Each measurement is emitted as a structured `exchange_latency` log event at INFO level. When any stage *or* the total exceeds its R6 ceiling for the active mode, an additional WARN-level `latency_miss` event is emitted, naming the stage(s), actual vs. target durations, active STT/TTS providers, and a short exchange ID so the matching transcript can be cross-referenced. The web frontend (R15) surfaces recent latency misses in a small corner indicator so Matt notices drift without having to read logs.
- R7. Claude's system prompt is read from a persona config file on disk (`~/.herbert/persona.md`, permissions `0600`). Changes are picked up on the next exchange (file-watch or mtime check on read). Secrets are never stored in this file — see R23.

**Persona (v1)**
- R8. Herbert has a restrained, warm, character-forward persona with occasional subtle nods to the can-collecting lore. The persona is defined in the system prompt config file, not hard-coded.

**Visual / UI (v1)**
- R9. On system boot (not daemon restart — detected via uptime or a first-run marker), the display plays a fake retro-futurist boot sequence, runs startup health checks during the sequence, and hands over to the main UI in `idle` when complete. Boot is non-interruptible (~3-6s target). If a health check fails, the sequence completes and the main UI enters error state (R12) with the relevant diagnostic. In `herbert dev`, the boot sequence is skippable via `--no-boot` for fast iteration.
- R10. Main UI has two view modes: **character view** (default) and **diagnostic view** (R13). In character view, a pixel-art character visibly reacts to four pipeline states — `idle`, `listening`, `thinking`, `speaking` — plus an `error` pose that replaces the current animation when a pipeline failure occurs (R12). Returning from error resumes the normal state cycle from `idle`. The `listening` state shows a visible audio-capture signal (level meter or amplitude-reactive mouth/eye) so it's obvious the mic is working.
- R11. The most recent exchange transcript (user speech + Herbert's reply) is visible in character view, updating live as Claude tokens stream. It persists until the next button press, at which point it clears. Long responses wrap and scroll within a capped transcript region that occupies no more than ~40% of the display — the character remains the dominant element. Empty state (before any exchange) shows the character in `idle` with no transcript overlay.
- R12. The `error` state is a distinct character pose plus a short diagnostic line identifying the class: `wifi down`, `api error (auth)`, `api error (rate limit)`, `api error (other)`, `mic not detected`, `speaker error`. The diagnostic line remains visible for the duration of the error state.
- R13. Voice commands toggle diagnostic view. **Trigger phrases** (locally regex-matched on the final STT transcript, BEFORE calling Claude): enter = `herbert,? (show (me )?the )?logs` or `herbert,? diagnostic mode`; exit = `herbert,? (back to )?normal( mode)?` or `herbert,? character mode`. In diagnostic view, the character is replaced with a live log tail; audio I/O remains fully active (so Herbert can be returned to character mode by voice). Transitions are instant cuts with a soft tonal cue. A non-matching utterance that mentions the trigger words incidentally passes through to Claude as a normal question. Mis-trigger behavior (false positives / negatives) is logged at INFO.

**Observability & Resilience (v1)**
- R14. Structured pipeline events and errors are written to `~/.herbert/herbert.log`, rotated daily, capped at 7 days' retention by default. Transcripts are logged at INFO by default and can be disabled via `log_transcripts: false`. A redaction layer scrubs known secret patterns (`sk-*`, Bearer tokens, fields tagged as secret) before writing — never commit raw audio, API keys, or bearer tokens to the log.
- R15. The web frontend binds to `127.0.0.1` by default (localhost-only). Remote debugging from another device requires explicit `--expose` (or `expose: true` in config), which binds to `0.0.0.0` *and* requires a URL-embedded bearer token auto-generated at first boot, stored in `~/.herbert/secrets.env`, and displayed on the device screen at startup. Unauthenticated requests to the exposed endpoint return 401. v1 exposes **read-only** observation endpoints only — no write operations.
- R16. Any single failure degrades gracefully: Herbert enters the error state (R12) with spoken+visual diagnostic, then attempts recovery per class:
  - **network / wifi down, Claude API 5xx / transient**: auto-retry with exponential backoff (1s → 2s → 5s → 10s → idle-wait). On restoration, play a soft "I'm back" cue and return to `idle`.
  - **Claude API auth / rate-limit / content-policy errors**: do NOT auto-retry — these need Matt's attention. Stay in error until the next button press.
  - **mic / speaker errors**: stay in error until next button press (retry on request).
  - The daemon never crashes on a single failure.

**Cross-Platform Development (v1, first-class)**
- R17. Backend daemon and frontend run natively on macOS (Apple Silicon + Intel) and Raspberry Pi OS (aarch64) from the same codebase. "Native" means *functionally equivalent given the same input*, not bit-identical.
- R18. Platform-sensitive components are isolated behind platform-neutral interfaces: GPIO button, Pi audio routing (ALSA/PipeWire) vs macOS CoreAudio (both via `sounddevice`/PortAudio), Chromium kiosk auto-launch, **whisper.cpp build target** (Metal on Apple Silicon vs NEON on aarch64), **Piper/onnxruntime backend**, and PortAudio backend differences. macOS dev uses a keyboard shortcut and/or on-screen dev button in place of GPIO. A smoke test exercises the STT and TTS paths on both targets at dev-setup time.
- R19. Two entry points exist:
  - `herbert dev` — macOS-friendly: binds frontend to `127.0.0.1`, prints URL, keyboard activation, no kiosk, boot sequence skippable.
  - `herbert run` — Pi production: GPIO activation, gated Chromium kiosk auto-launch (kiosk waits for daemon health before navigating, so cold-boot is deterministic — see R9).

**Extensibility Seams (v1 non-behavior, required structure)**
- R20. **External** tool-calling uses MCP as the *only* protocol (no custom external-tool scaffolding). v1 ships with zero MCPs enabled; the code path passes `mcp_servers` when configured. **In-process device control** (character state changes, view-mode toggles, hardware feedback, latency instrumentation) is NOT routed through MCP — it is direct function calls within the daemon.
- R21. Remote MCPs use the Anthropic API's `mcp_servers` parameter (Anthropic-hosted proxy). Stdio MCPs via the MCP Python SDK are deferred to v2. When MCPs are enabled (v2+), the config schema enforces an allowlist of explicit server URLs/commands, MCP credentials are stored in the secrets file (R23), and MCP responses are treated as untrusted input for prompt-injection purposes.
- R22. Conversation state for v1 is in-memory per session (no persistence). A session abstraction exists so memory persistence (v3) can be plugged in without changing the pipeline.

**Security & Secrets (v1)**
- R23. All secrets (Anthropic API key, ElevenLabs API key, frontend bearer token, any future provider or MCP credentials) live in `~/.herbert/secrets.env`, permissions `0600`, owned by the service user. Never committed to source; never stored in the persona config (R7) or log file (R14). The daemon loads this file at startup and fails closed if required keys are missing (Herbert boots into error state with `missing secrets` diagnostic rather than running half-configured).

## Success Criteria

- Matt presses the button, speaks a question, and hears Herbert respond within the R6 p50 target for the active mode. The pixel-art character visibly reacts through all four states during the exchange.
- Latency instrumentation (R6a) shows `latency_miss` events below 5% of exchanges over a week of normal use; when they occur, the frontend miss-indicator surfaces them and logs make the bottleneck stage obvious.
- On system cold boot, the fake boot sequence plays, startup health checks run, and Herbert lands in `idle` (or in error state with a clear diagnostic if a check fails).
- Saying `"Herbert, show me the logs"` swaps to diagnostic view; saying `"Herbert, back to normal"` returns.
- Unplugging wifi mid-conversation shows an error with `wifi down`; reconnecting auto-retries and Herbert returns to `idle` with a soft audible cue.
- Editing `~/.herbert/persona.md` and waiting for the next exchange produces a visibly different persona — no rebuild.
- The exposed web frontend rejects requests without the bearer token (401).
- The same code runs on Matt's Mac via `herbert dev` — full voice loop, keyboard-as-button, browser as UI — without Pi hardware.
- **Identity test:** after two weeks of daily use, Matt can name 2-3 specific Herbert-isms (phrases, reactions, quirks) that make Herbert recognizable as Herbert, distinct from a generic Claude wrapper. (Qualitative; Matt's judgment.)

## Scope Boundaries (explicit non-goals for v1)

- No MCP servers enabled by default (the `mcp_servers` pass-through exists but is unexercised until v2 — accepted risk that the first v2 enable may flush out bugs).
- No persistent memory (conversations are forgotten between sessions).
- No camera, PIR sensor, or gesture activation.
- No voice-commanded persona rewriting (that's v2).
- No specific enclosure, form factor, or industrial design work.
- No multi-user support, user profiles, or account system.
- No mobile app, cloud control surface, or remote access beyond local-network debugging.
- No wake-word / always-on listening, ever (explicit product choice).

## Key Decisions

- **Stack: Python daemon + Chromium kiosk web frontend.** Python has first-class libraries for every component (Anthropic SDK, whisper.cpp bindings, Piper bindings, MCP SDK, sounddevice). Web frontend is the fastest path to an iterable pixel-art UI and runs identically on Mac (browser) and Pi (Chromium kiosk). Rust rewrite is a conceivable v6+; Pygame was considered but rejected for UI iteration speed.
- **MCP is the only protocol for *external* tool-calling.** In-process device control (UI toggles, character state, hardware feedback) stays as direct function calls. Anthropic's `mcp_servers` parameter handles remote MCPs with zero client code; the Python MCP SDK handles stdio MCPs when we get there (v2+).
- **Hybrid STT/TTS for v1: local STT, cloud TTS.** Your voice never leaves the device (whisper.cpp), but Herbert's voice comes from ElevenLabs — voice character is load-bearing for the "specific little guy" goal and Piper, while solid, is not character-tier. Local-only (Piper) and full-cloud (Deepgram + ElevenLabs) are both available as config swaps behind R5's provider interface.
- **Default LLM is Claude Haiku 4.5.** Fastest TTFT, adequate depth for conversational use. Config-swappable to Sonnet/Opus.
- **Push-to-talk button only for v1.** Simpler, more private, more tactile, and fits the retro vibe. PIR/gesture is a v4 extension behind the same activation interface.
- **Cross-platform dev parity is first-class, not an afterthought.** Developing on a Pi is painful; keeping macOS as an equal-tier runtime preserves sanity and enforces clean hardware abstractions.
- **Persona lives in a config file.** Iteration on Herbert's voice (both literal and figurative) should not require code changes. Voice-commanded self-rewriting in v2 is a natural follow-on once this is in place.

## Dependencies / Assumptions

- Raspberry Pi 5 hardware is available. A cheaper Pi 4/Zero 2 W is not in scope — transcription latency would dominate the experience.
- Anthropic API access with a valid API key is available and stable; internet is assumed present (graceful degradation on outage per R16, but Herbert is not usable offline without Claude).
- **ElevenLabs API access is available** for the v1 hybrid default. If ElevenLabs is unavailable or Matt opts out, Herbert falls back to Piper with R6's full-local latency budget.
- whisper.cpp and Piper have suitable models for American English at conversational volume; model selection is a tuning concern for implementation.
- A suitable USB or HAT mic + speaker combination is purchasable; specific part selection is a planning concern.
- **[Unverified]** Anthropic's `mcp_servers` API parameter is reachable from Matt's account in the v2 timeframe. This is currently a beta feature requiring a beta header (e.g. `anthropic-beta: mcp-client-2025-04-04` at time of writing) and only supports remote HTTP/SSE MCP servers — not stdio — via this route. Verify at v2 planning time that the header and feature flag are still current.

## Outstanding Questions

### Resolve Before Planning

*(none — all product decisions locked.)*

### Deferred to Planning

- [Affects R3][Technical] Specific mic/speaker hardware recommendation for the Pi 5 (USB vs HAT, cardioid vs omni, quality bar).
- [Affects R1, R2, R18][Technical] GPIO library choice for Pi 5. RPi.GPIO is not Pi 5 compatible due to the RP1 chip; candidates are `gpiozero` on the `lgpio` backend, or `libgpiod`/`python-periphery`. The chosen library's event model shapes the activation `EventSource` interface signature used by R2/R18 (and the Mac keyboard-shim must mirror it).
- [Affects Stack / R10-R13, R15][Technical] Backend ↔ frontend transport. Python daemon pushes state transitions, transcripts, and log frames to the browser — choose WebSocket vs SSE vs long-poll, name the web server (FastAPI + `websockets`, aiohttp, Flask-SocketIO, etc.), and define the event schema. Same HTTP server should host R15's remote-reachable debugging UI.
- [Affects R4, R16][Technical] Audio playback path and device pinning. Use `sounddevice` for both capture and playback (PortAudio-based, works on macOS CoreAudio and Pi ALSA/PipeWire). Add a device-pinning config key so the chosen USB mic and speaker are addressable by stable name rather than default-device ordering.
- [Affects R5][Technical] STT/TTS provider-interface shape supporting streaming semantics (async iterator of chunks) across whisper.cpp, Deepgram, ElevenLabs, Piper, and OpenAI TTS — design the smallest common shape that still allows low-latency streaming.
- [Affects R4][Needs research] Which whisper.cpp model size + quantization hits the R6 STT ceiling (≤1.5s) on Pi 5 for American English at conversational volume (candidates: base.en, base.en q4/q5, small.en q4).
- [Affects R4, R8][Needs research] Which ElevenLabs voice (or custom clone) best embodies "warm, restrained Herbert" — voice-ID is a config-level choice, but the selection process needs an A/B test procedure.
- [Affects R9, R10][Design] Specific pixel-art character design, boot-sequence copy, and listening-state audio-level treatment — iterated during implementation with reference mockups for each of the 4 + error states.
- [Affects R9, R16][Technical] Startup health-check menu — which components are probed (mic input-level test, speaker output-level test, network reachability to Anthropic + ElevenLabs, whisper model file present, Piper voice file present, GPIO button responsive) and what each failure surfaces.
- [Affects R15][Technical] Discovery mechanism when the frontend is exposed — mDNS (`herbert.local`), static IP, or QR-code-on-boot displayed with the token URL.
- [Affects R17, R19][Technical] Packaging/install approach that works on both Pi OS and macOS — candidates: `uv` + systemd unit on Pi, `uv` + launchd agent on Mac, or Docker/Compose for isolation. Pick one for v1.

## Next Steps

-> `/ce:plan` for structured implementation planning.
