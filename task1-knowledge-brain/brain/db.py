from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
  url TEXT PRIMARY KEY,
  kind TEXT NOT NULL DEFAULT 'article',          -- article | expert
  title TEXT NOT NULL DEFAULT '',
  category TEXT NOT NULL DEFAULT '',
  folder TEXT NOT NULL DEFAULT '',
  lastmod TEXT,
  content_hash TEXT,
  text TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active',         -- active | removed
  fetched_at TEXT,
  removed_at TEXT
);
CREATE TABLE IF NOT EXISTS chunks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  page_url TEXT NOT NULL REFERENCES pages(url) ON DELETE CASCADE,
  ord INTEGER NOT NULL,
  kind TEXT NOT NULL DEFAULT 'text',             -- text | image
  image_id INTEGER,
  text TEXT NOT NULL,
  embedding BLOB
);
CREATE INDEX IF NOT EXISTS idx_chunks_page ON chunks(page_url);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, content='chunks', content_rowid='id', tokenize='porter unicode61');
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
  INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TABLE IF NOT EXISTS images (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  page_url TEXT NOT NULL REFERENCES pages(url) ON DELETE CASCADE,
  src TEXT NOT NULL,
  alt TEXT NOT NULL DEFAULT '',
  sha256 TEXT,
  caption TEXT,
  caption_source TEXT                            -- vision | alt | none
);
CREATE INDEX IF NOT EXISTS idx_images_page ON images(page_url);
CREATE TABLE IF NOT EXISTS caption_cache (       -- one vision call per unique image
  sha256 TEXT PRIMARY KEY, caption TEXT NOT NULL, model TEXT
);
CREATE TABLE IF NOT EXISTS crawl_queue (
  url TEXT PRIMARY KEY,
  state TEXT NOT NULL DEFAULT 'pending',         -- pending | done | failed
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  lastmod TEXT,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  question TEXT NOT NULL,
  question_emb BLOB,
  reason TEXT,
  status TEXT NOT NULL DEFAULT 'open',           -- open | answered
  asked_count INTEGER NOT NULL DEFAULT 1,
  answer TEXT,
  expert TEXT,
  page_url TEXT,
  created_at TEXT, answered_at TEXT
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL, started_at TEXT, finished_at TEXT, status TEXT, stats TEXT
);
CREATE TABLE IF NOT EXISTS llm_usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT, purpose TEXT, model TEXT, prompt_version TEXT,
  input_tokens INTEGER, output_tokens INTEGER, cost_usd REAL, latency_ms INTEGER
);
CREATE TABLE IF NOT EXISTS feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  question TEXT, answer TEXT, helpful INTEGER, note TEXT, ts TEXT
);
"""


def connect(path: Path | str) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn
