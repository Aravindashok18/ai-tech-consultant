# Task 1 — Knowledge Brain

A memory and chatbot for the Brilliant Directories help center (1,537 articles, 145 folders, 13 categories). It reads the sitemap politely, understands screenshots, updates itself daily, answers with sources, admits when it does not know, and learns from human experts. Tasks 2 and 3 call it over HTTP.

## Run

```bash
cp .env.example .env        # add ANTHROPIC_API_KEY (without it you get extractive answers and no image captions)
./run.sh                    # venv + install + first crawl (background, resumable) + server on :8000
```

- Chat: http://localhost:8000 · Expert queue: http://localhost:8000/experts · API docs: http://localhost:8000/docs
- `python -m brain crawl [--limit N]` first full read (safe to stop and restart)
- `python -m brain sync` the daily diff (the server also runs it at `SYNC_HOUR_UTC`)
- `pytest` offline tests (no network, no key)

## How it works

| Need | What it does |
|---|---|
| Polite first read | One global rate limit (1 req/s + jitter), 2 workers, robots.txt, honours `Retry-After`, doubles its delay on 429/5xx. Every URL sits in a `crawl_queue` table; a page is committed before it is marked done, so a crash resumes where it stopped. ~1,537 pages ≈ 30 min. |
| Images | Each screenshot is downloaded once, captioned by Claude (Haiku) and cached by image hash. The caption becomes its own searchable chunk tied to the article, and is shown beside the answer. |
| Daily freshness | Compare sitemap `lastmod` with memory: new → fetch, changed → fetch, hash same → skip embedding, gone → delete chunks and vectors (tombstone kept). Refuses to delete more than 25% (min 5) in one run, because that means a broken sitemap, not a real purge. |
| Answers | Hybrid search (BM25 + local `bge-small` vectors, rank-fused) → Claude answers from numbered sources in a JSON schema → citations come from `used_sources`. If sources do not contain the answer it says so. |
| Don't know → expert | The question becomes a task (duplicates are merged, with a counter). The expert answer is saved as a first-class page and ranks slightly above articles on the next ask. |

## API (details at `/docs`)

`POST /v1/ask` · `POST /v1/search` · `GET /v1/articles?url=` · `GET /v1/tasks` · `POST /v1/tasks/{id}/answer` · `POST /v1/feedback` · `POST /v1/admin/sync` · `GET /v1/stats`. Set `BRAIN_API_KEY` to require an `X-API-Key` header.

```bash
curl -s localhost:8000/v1/ask -H 'content-type: application/json' \
  -d '{"question":"How do I add a custom domain?"}'
# {"answer":"...","confidence":"high","abstained":false,"sources":[{"title":"...","url":"...","kind":"text"}],...}
```

## Known limits (honest)

- Image captioning and generated answers are untested until a key is set; only the offline path is verified (7 tests, plus a 30-page live crawl).
- The relevance gate (`MIN_COSINE`) is a first guess: on 30 pages, off-topic questions still scored ~0.58 with `bge-small`. The real "I don't know" decision comes from Claude's `can_answer`. Tune on the 20+ eval cases once the full corpus is loaded.
- Prices in `.env` default to 0, so cost reports show $0 until you fill them in.
