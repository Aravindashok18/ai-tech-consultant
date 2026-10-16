# Task 3 — Scope Agents (Python)

Turns client meeting transcripts into a requirement list, an hours estimate and a draft proposal. A second meeting updates the scope instead of starting again. Asks Task 1 whether an existing product setting already does the job before pricing custom code. Language: Python (FastAPI).

## Run
```bash
# Task 1 must be running on :8000 (KB_URL)
cp .env.example .env               # add ANTHROPIC_API_KEY; set HOURLY_RATE, CURRENCY if different
./run.sh                           # UI and API on http://localhost:8002 (API docs at /docs)
pytest                             # 8 offline tests
```

## API
| Call | What it does |
|---|---|
| `POST /v1/projects` `{client}` | new project |
| `POST /v1/projects/{id}/meetings` `{label, transcript}` | adds a transcript and analyses it; the first creates the scope, later ones update it. Returns the new version number |
| `GET /v1/projects/{id}/versions`, `GET .../versions/{v}` | version list; one version with scope, `changes` (changed / new / dropped / unchanged) and proposal markdown |
| `POST .../versions/{v}/approve` `{approved_by}`, `POST .../reject` | the human gate |
| `GET .../versions/{v}/export` | client-ready proposal; **409 until a person has approved that version** |

## Flow
transcript → **extractor** (split on speaker turns into ~6k-char windows with 2 turns overlap, 4 in parallel, near-duplicates merged) → **KB check** per requirement (`POST /v1/ask` on Task 1: "does a setting already do this?") → **estimator** (hours low–high; `configure` via a setting or `custom`) → **clarifier** (open questions) → **senior reviewer** (missing work, estimates that look wrong, risks) → **proposal writer** → version saved as `draft`.

**Meeting 2** goes through a **reconciler** against the agreed scope: `same` (kept, not re-estimated), `changed` (only that item is re-estimated), `removes` (marked dropped), `new` (becomes a `change_request`, priced separately and labelled extra). Requirement ids (R1…) and task ids (T1…) stay stable across versions.

## Rules that live in code, not in the model
- Prices, totals, day counts and the 15% project-management/QA margin are computed in `pipeline._totals`. The model supplies task hours and text only.
- A task solved by an existing setting is shown with its help-center article and the hours saved against custom work.
- Versions start as `draft` and carry a "not for the client" banner; export is refused until a named person approves.
- Task 1 unreachable: the estimate still runs and a risk note says estimates may be too high.

## Known limits
- Claude paths (extractor, estimator, reviewer, reconciler, proposal) have **not been run with a key**. Without a key an offline fallback runs (regex extractor, flat default hours by type, no review or clarifying questions). Those numbers are placeholders and must not be shown to a client.
- `approved_by` is a typed name, not a logged-in user; there is no authentication on this API.
- `transcripts/` holds three scripted samples written by the author, not recorded meetings (see `transcripts/README.md`).
- No model-comparison eval for this task yet; see [EVALS.md](../EVALS.md).
