"""Persistent memory substrate for Herbert.

Three tiers, one SQLite file at ``~/.herbert/memory.db`` (configurable):

  Tier 1  ``facts``          distilled identity + preferences, always in prompt
  Tier 2  ``sessions``       closed-session summaries, always in prompt
  Tier 3  ``messages``       raw turn-by-turn history (v2 FTS5 hooks onto this)

The substrate exposes:

  - ``open_writer_connection`` / ``open_reader_connection`` — the two
    connections Herbert uses at runtime. Writes live on a dedicated
    writer thread; reads happen on whatever thread the caller is already
    on (event loop, web thread, extractor task) and share one reader
    connection opened with ``check_same_thread=False``.
  - ``migrate`` — idempotent schema migration, invoked automatically by
    ``open_writer_connection``.
"""

from herbert.memory.db import (
    migrate,
    open_reader_connection,
    open_writer_connection,
)
from herbert.memory.extractor import extract_session_summary
from herbert.memory.prompt import build_system_prompt
from herbert.memory.store import MemoryStore

__all__ = [
    "MemoryStore",
    "build_system_prompt",
    "extract_session_summary",
    "migrate",
    "open_reader_connection",
    "open_writer_connection",
]
