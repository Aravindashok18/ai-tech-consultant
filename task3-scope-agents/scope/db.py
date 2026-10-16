from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (id INTEGER PRIMARY KEY AUTOINCREMENT, client TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS meetings (
  id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER, label TEXT, transcript TEXT, ord INTEGER, analyzed INTEGER DEFAULT 0, created_at TEXT);
CREATE TABLE IF NOT EXISTS scope_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER, version INTEGER, meeting_id INTEGER,
  status TEXT NOT NULL DEFAULT 'draft',        -- draft | approved | rejected
  scope_json TEXT, changes_json TEXT, proposal_md TEXT, approved_by TEXT, approved_at TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS llm_usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, agent TEXT, model TEXT, prompt_version TEXT,
  input_tokens INTEGER, output_tokens INTEGER, cost_usd REAL, latency_ms INTEGER);
"""


def connect(path: Path | str) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(path), check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(SCHEMA)
    return c
