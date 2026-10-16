from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS emails (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sender TEXT, sender_name TEXT, subject TEXT, body TEXT, received_at TEXT,
  kind TEXT, urgency TEXT, sentiment TEXT, risk_flags TEXT,   -- risk_flags: JSON list
  status TEXT NOT NULL DEFAULT 'new'    -- new | drafted | sent | auto_sent | held | spam
);
CREATE TABLE IF NOT EXISTS drafts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, email_id INTEGER REFERENCES emails(id),
  subject TEXT, body TEXT, confidence REAL, needs_human TEXT, kb_json TEXT,
  model TEXT, prompt_version TEXT, rules_used TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS actions (      -- what the human (or the agent) did with a draft
  id INTEGER PRIMARY KEY AUTOINCREMENT, email_id INTEGER, draft_id INTEGER, kind TEXT,
  action TEXT,                            -- sent_unchanged | edited | own_reply | auto_sent
  final_body TEXT, similarity REAL, ts TEXT
);
CREATE TABLE IF NOT EXISTS rules (        -- learned style/policy rules, individually revertible
  id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, kind TEXT,   -- kind NULL = applies to all
  active INTEGER NOT NULL DEFAULT 1, source_action INTEGER, created_at TEXT, deactivated_at TEXT
);
CREATE TABLE IF NOT EXISTS autonomy (
  kind TEXT PRIMARY KEY, streak INTEGER NOT NULL DEFAULT 0, enabled INTEGER NOT NULL DEFAULT 0,
  enabled_at TEXT, revoked_reason TEXT, revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS reviews (      -- human re-reads of auto-sent mail
  id INTEGER PRIMARY KEY AUTOINCREMENT, email_id INTEGER, kind TEXT, verdict TEXT, -- ok | bad
  note TEXT, ts TEXT
);
CREATE TABLE IF NOT EXISTS llm_usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, purpose TEXT, model TEXT, prompt_version TEXT,
  input_tokens INTEGER, output_tokens INTEGER, cost_usd REAL, latency_ms INTEGER
);
CREATE TABLE IF NOT EXISTS outbox (id INTEGER PRIMARY KEY AUTOINCREMENT, email_id INTEGER, to_addr TEXT, subject TEXT, body TEXT, auto INTEGER, ts TEXT);
"""


def connect(path: Path | str) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(path), check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(SCHEMA)
    return c
