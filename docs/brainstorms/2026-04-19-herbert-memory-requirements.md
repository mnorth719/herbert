---
title: Herbert persistent memory — requirements
type: feat
status: draft
date: 2026-04-19
---

# Herbert persistent memory — requirements

## Problem frame

Herbert's product claim is that it feels like *a specific little guy* — a retro-futurist home companion that knows Matt, not a generic voice assistant. Today Herbert forgets everything the moment the daemon restarts (`InMemorySession` only). Generic-feeling replies are the failure mode: if Matt says "what about the Lakers?" and Herbert answers as if the topic has never come up, the illusion collapses.

The goal is to make Herbert feel like it *learns more about Matt over time* — names, preferences, ongoing topics, past conversations — without the Pi struggling under the weight of accumulated data or the voice pipeline slowing down.

Origin: v3 scope in `docs/plans/2026-04-18-001-feat-herbert-v1-implementation-plan.md` already earmarks this as "SQLite-backed conversation + user-facts store behind the v1 session abstraction." This document fills in the design.

## Non-goals

- Semantic / embedding-based recall in v1 (can be added later against the same DB via `sqlite-vec`).
- Multi-user memory. Herbert is a single-user home device; no per-profile state.
- Cloud-synced memory. Memory is local-first; export/backup is a file copy.
- Offline operation of Herbert overall — all LLM + TTS + tool calls require network, so "memory works offline" is not a real constraint. We picked local storage for latency, ownership, and inspectability.

## Users + what "success" feels like

Matt, talking to Herbert on a shelf in his house. What breaks the illusion today:

- Has to re-introduce himself every boot ("I'm Matt, I live in Upland")
- Can't reference yesterday's conversation ("what was that stat you gave me about LeBron?")
- Herbert gives a generic answer to a question that has an obvious context-sensitive answer (asks about "the game" and Herbert has no idea which team)

Success means all three of those work naturally — and Herbert feels a little more *his* every week, not overwhelmingly better all at once.

## Architecture: three tiers, one substrate

| Tier | Content | Loaded when | Latency | v1? |
|---|---|---|---|---|
| **1. Facts** | Distilled identity + preferences ("Matt lives in Upland, wife Amy, prefers short replies, Dodgers fan") | Every turn — injected into system prompt | Zero (already in prompt) | ✓ |
| **2. Recent continuity** | Last N session summaries with navigation hooks ("Thursday: Lakers chat, specifically LeBron's ankle") | Every turn — appended to system prompt after facts | Zero | ✓ |
| **3. Experiential recall** | Full raw turn-by-turn history, keyword-searchable | On-demand via `recall_memory` tool when Claude decides | ~200-500ms per lookup + filler ("let me think back a sec") | Fast-follow (v2) |

The substrate is the same across all three tiers: SQLite. v1 uses only the `facts` and `sessions` tables in the always-loaded path. v2 lights up tier 3 by adding FTS5 + a `recall_memory` tool declaration. Raw turns live in the `messages` table from day one so the data is already there when tier 3 is added.

## Storage

**Database**: one SQLite file at `~/.herbert/memory.db`.

Schema:

```sql
CREATE TABLE messages (
    turn_id     TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    ts          INTEGER NOT NULL,            -- unix seconds
    role        TEXT NOT NULL,               -- 'user' | 'assistant'
    content     TEXT NOT NULL
);

CREATE TABLE sessions (
    session_id  TEXT PRIMARY KEY,
    started_at  INTEGER NOT NULL,
    ended_at    INTEGER,
    summary     TEXT                         -- null until summarized
);

CREATE TABLE facts (
    fact_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    content        TEXT NOT NULL UNIQUE,     -- dedup identical facts
    source_session TEXT,                     -- which session first produced it
    first_seen     INTEGER NOT NULL,
    last_confirmed INTEGER NOT NULL          -- updated when reaffirmed
);

CREATE INDEX idx_messages_session ON messages(session_id);
CREATE INDEX idx_messages_ts ON messages(ts);
```

**FTS5 index** (added in v2 — unused in v1 but can land in the same schema so no migration is needed):

```sql
CREATE VIRTUAL TABLE messages_fts USING fts5(
    content,
    content='messages',
    content_rowid='rowid',
    tokenize='porter unicode61'
);
-- plus AI/AD/AU triggers keeping it in sync
```

**Operational choices**:
- `PRAGMA journal_mode=WAL` for concurrent read + write without blocking.
- Writes dispatched to a background task (`asyncio.to_thread` or dedicated writer coroutine) so the audio path never stalls on disk I/O.
- Single file = `cp memory.db memory.db.bak` is the backup plan.

**Storage projections** (raw content + FTS5 index, medium usage at 20 turns/day):

| Horizon | Total size |
|---|---|
| 1 month | ~1-2 MB |
| 1 year | ~15 MB |
| 5 years | ~75 MB |

No pruning policy needed.

## Write triggers

**v1: session-end extraction.** A session closes after 5 minutes of inactivity (no button press and no in-flight turn). At session close, a *second* Claude call reads that session's turns and produces two outputs:

1. A 1-2 sentence summary with navigation hooks (dates, topics, named entities, what was concluded) — written to `sessions.summary`.
2. A list of new durable facts worth adding to the `facts` table (deduplicated against existing rows). Format: short declarative sentences.

The extraction call uses a dedicated prompt that produces structured output. Failure modes:
- Claude API error → retry once, then skip; session summary stays null.
- No new facts → normal outcome.
- Empty session (0 turns) → skip entirely.

**v2 addition (not v1)**: an inline `remember(fact)` tool Claude can call mid-turn for load-bearing things it shouldn't wait for session close to save ("My birthday is April 30").

**Explicitly deferred**:
- Fact contradiction/reconciliation logic. v1 behavior: most recent wins; don't delete old facts, let them age out via the `last_confirmed` timestamp. If this becomes a problem we revisit.
- Periodic compaction of the `messages` table. Text is cheap; compaction isn't needed for many years.

## Runtime: what gets loaded every turn

System prompt assembled per turn (order matters for prompt caching):

```
1. Static persona text                        (~300 tokens)
2. Tool definitions                           (~500 tokens)
3. ## What I know about Matt                  (~100-200 tokens)
   <facts rendered as bullet list>
4. ## Recent sessions                         (~150-300 tokens)
   <last 3-5 session summaries>
5. <conversation messages for current session>
```

Sections 1-4 are stable across turns within a ~5-min burst. Anthropic's prompt caching makes them effectively free after the first request in the burst: cached tokens cost ~10% of normal and process ~2x faster.

Memory additions per turn: ~300-500 tokens total, cached after turn 1.

**Session boundary** = 5 minutes of inactivity (no button events, no in-flight turn). When a session closes:
1. Record `sessions.ended_at`.
2. Schedule extraction (background).
3. A new `session_id` is allocated on the next press.

Within a session, `InMemorySession` (already in place) holds the live message list. Tier 1 (facts) + tier 2 (summaries) come from the memory DB. Session messages go directly to Claude untouched.

## Observability — what Claude actually sees

Prompt construction is opaque by default, and "Herbert suddenly feels off" almost always traces back to *what landed in the system prompt on that turn*. We make the prompt inspectable at three points:

### 1. Boot-time snapshot (log file)

When the daemon starts and the first system prompt is assembled, the full text is logged at INFO in a structured block:

```
2026-04-19 14:32:10 INFO prompt.snapshot turn=none stage=boot
=== SYSTEM PROMPT ===
<full assembled text — persona + tool defs + facts + summaries>
=== END SYSTEM PROMPT ===
input_tokens_estimate=1248 (persona=312, tools=501, facts=185, summaries=250)
```

This always runs on boot regardless of log level — one snapshot per daemon lifetime is cheap and answers "what did we start with." Token estimates use a simple word-to-token heuristic (no real tokenizer; close enough for detecting drift).

### 2. Per-turn delta (structured log at INFO)

Every turn logs a compact line showing which memory sections changed since the boot snapshot and the size of each:

```
2026-04-19 14:33:05 INFO prompt.turn turn=<id> persona=312 tools=501 facts=185 summaries=250 live=340 total=1588 cached=hit
```

This catches drift: if facts grow to 800 tokens after a few weeks or summaries suddenly spike, it's visible in the log. `cached=hit` indicates Anthropic's cache is warm; `cached=miss` means we rebuilt something cacheable.

### 3. Diagnostic view on the frontend

Diagnostic mode (Unit 12 — voice trigger "Herbert, show me the logs") already streams the log tail. We extend it with a *Prompt* subview — either a button within the diagnostic overlay or a second voice trigger ("Herbert, show me the prompt") — that shows:

- The current full system prompt, formatted and scrollable
- Each section labeled (persona / tools / facts / summaries)
- Token estimates per section
- The last N message pairs from the current session

The data comes from a new `/api/prompt/snapshot` endpoint served by the FastAPI web server. It returns a JSON payload reconstructed from `MemoryStore` + the live `Session` + the static persona / tool config. Safe to expose on the unauthenticated localhost bind; gated by the bearer token in `--expose` mode.

This is the same "what does Herbert actually know about me right now" view Matt needs for debugging AND for the product itself — e.g. asking "what do you know about me?" and confirming against what's in the prompt.

## User-facing UX

**Discovery command — "what do you know about me?"**: handled the same way as any other voice question. The facts block is already in the system prompt; Claude just reads it back in its own voice. No special path needed.

**Corrections — "forget that I'm a Dodgers fan"**: Claude can handle this naturally via an inline `forget(fact_query)` tool in v2. In v1, Matt can:
- Edit `memory.db` directly via the `sqlite3` CLI.
- We add a simple `herbert memory` subcommand (`herbert memory list-facts`, `herbert memory forget <id>`) later if it's painful.

**Privacy**: the DB is local, at `~/.herbert/memory.db`, readable only by Matt's user account (SQLite respects filesystem perms). No sync, no telemetry, no export by default.

**Transparency**: the `config.logging.log_transcripts` flag (already planned for Unit 12) controls whether raw turns get written to the log file. It does NOT affect memory writes — memory is part of Herbert's normal function, not surveillance. If Matt doesn't want memory at all, `config.memory.enabled = false` disables the whole subsystem and Herbert reverts to `InMemorySession`-only behavior.

## Composing with existing plumbing

- `Session` Protocol in `src/herbert/session.py` gains a new impl `SqliteSession` (the placeholder in plan Unit 13 lights up). `InMemorySession` remains, used when memory is disabled or in tests.
- `Persona` builder gains a `render_with_memory(facts, summaries) -> str` helper that injects the memory sections into the system prompt.
- Tools list (currently `web_search`, `web_fetch`, `code_execution`) gains `recall_memory` in v2. Same filler pattern covers the lookup latency.
- `build_and_run` in `src/herbert/daemon.py` opens the DB connection at startup, runs schema migration if needed, passes the `SqliteSession` to `Daemon`, and schedules session-close extraction on inactivity.
- Diagnostic overlay in `frontend/src/diagnostic.js` gets a prompt-inspection subview; `src/herbert/web/app.py` adds `/api/prompt/snapshot`.

## v1 scope (what ships in the first memory PR)

1. `src/herbert/memory/` package:
   - `db.py` — SQLite connection, schema migration, WAL setup
   - `store.py` — `MemoryStore` class with `append_turn`, `close_session`, `get_facts`, `get_recent_summaries`, `add_fact`
   - `extractor.py` — Claude-call wrapper that takes a closed session and returns `(summary, new_facts)`
   - `prompt.py` — `build_system_prompt(persona, tools, facts, summaries) -> (str, token_breakdown)` helper shared by the LLM path and the observability endpoint
2. `SqliteSession` in `src/herbert/session.py` — implements the `Session` Protocol, writes turns through to `MemoryStore`.
3. Daemon wires up:
   - Session boundary detection (5-min inactivity timer) and extraction scheduling on close
   - Boot-time prompt snapshot logging
   - Per-turn prompt-delta structured log line
4. `src/herbert/web/app.py` serves `/api/prompt/snapshot` (JSON).
5. `frontend/src/diagnostic.js` gets a Prompt subview that renders the snapshot.
6. Config:
   - `memory.enabled: bool = True`
   - `memory.inactivity_seconds: int = 300`
   - `memory.recent_sessions_count: int = 5`
7. Tests:
   - Unit: `MemoryStore` CRUD, schema migration, fact dedup, `build_system_prompt` output shape + token estimates
   - Integration: end-to-end turn with memory enabled, facts accumulate across simulated sessions
   - E2E: add a replay fixture that spans two sessions with a gap; assert tier-2 summary appears in the second session's system prompt

## v2 scope (fast-follow, separate PR)

1. Add FTS5 virtual table + sync triggers to the schema (migration step).
2. `recall_memory(query, after=None, limit=5)` tool declaration + handler.
3. Inline `remember(fact)` tool for mid-turn saves.
4. Inline `forget(fact_query)` tool for corrections.
5. Persona addendum teaches Claude when to reach for each.

## Open questions

1. **Who writes the extraction prompt?** Options: hardcode in Python as a string literal / put in `assets/prompts/extraction.md` / make it configurable. Leaning: hardcoded string with a decent extraction prompt, tune over time based on observed output quality.
2. **What happens if extraction runs during the *next* session?** Matt takes a 6-minute break, returns, presses the button. The previous session's extraction might not have completed. Should the new session see the previous facts? Leaning: start session immediately with the facts available *at the time* (so brand-new facts from the previous session may not be visible until the extraction completes, which is fine — they'll be there next time).
3. **Do we expose memory to the web UI beyond the diagnostic snapshot?** Could add a first-class "Memory" panel showing facts + recent summaries + search box + delete buttons. Out of scope for v1, but the snapshot endpoint is a natural seed for it.

## Success criteria

Qualitative (Matt's felt sense):
- After a week of daily use, Herbert knows Matt's name without being told again.
- After a month, Herbert can reference yesterday's conversation naturally.
- Generic-feeling responses to personal questions decrease noticeably.
- When something feels off about a reply, Matt can open the Prompt subview and see exactly what Herbert was working with.

Quantitative:
- `herbert dev` boot-to-ready time unchanged (warmup is STT+TTS, memory DB open is <100ms).
- Per-turn LLM latency unchanged within noise (~50 tokens of new prompt at cached rates ≈ <20ms added).
- Memory DB stays under 100MB even at 10 years of heavy (50 turns/day) use.
- `pytest tests/` stays green; new tests cover the memory paths.
