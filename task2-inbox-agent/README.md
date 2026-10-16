# Task 2 — Inbox Agent (Python)

Reads each customer email, gets facts from the Knowledge Brain (Task 1) over HTTP, drafts a reply in the team's voice, and learns from every human edit. Language: Python (FastAPI), as you chose.

## Run
```bash
# terminal 1: Task 1 on :8000      terminal 2:
cp .env.example .env               # add ANTHROPIC_API_KEY and KB_URL
./run.sh                           # inbox UI on http://localhost:8001
pytest                             # 10 offline tests
python emails/make_seed.py         # regenerates the 50 sample emails (emails/seed_emails.json)
```

## Flow
email → **classify** (kind, urgency, sentiment, risk flags; Haiku) → **KB lookup** (`POST /v1/ask`) → **draft** (Sonnet, JSON schema, learned rules + 3 past replies of the same kind in the prompt) → human clicks Send / edits / writes own → **learn**.

## What it learns, where it is stored
| Human did | Stored | Effect |
|---|---|---|
| Sent unchanged (similarity ≥ 0.97) | `actions`, streak +1 for that kind | moves toward autopilot |
| Edited | `actions` + 0–2 **rules** ("Open with the answer") derived from the diff | rules go into the next draft prompt; each rule can be undone in the UI |
| Wrote own reply | same as edited; draft is ignored | streak resets |

## Autopilot (defined in `inbox/autonomy.py`)
- Same kind = the classifier's kind (12 kinds). Unchanged = similarity ≥ 0.97 (case/whitespace ignored, a changed number is not).
- 20 unchanged in a row → that kind may auto-send, only if confidence ≥ 0.8, no risk flags, KB had a verified answer.
- Refund, cancellation, complaint, legal, billing and quote **never** auto-send.
- 10% of auto-sent mail is queued for a human re-read. One "wrong" verdict, or 3 edits in the last 10 human-handled mails of that kind, removes autopilot and the streak restarts at 0.

## Safety
Email text is treated as data (prompt-injection flagged and forced to human review). KB down or "I don't know" → confidence capped at 0.5 and a human must check; the draft never guesses. A failed classify or draft holds the email instead of losing it.

## Not done yet (honest)
- Claude paths (classify/draft/lessons) are unrun: no API key. Offline fallbacks (keyword classifier ≈ crude, template draft) are tested.
- No real mailbox: sending writes to an `outbox` table; swap `Inbox._send` for Gmail/SMTP.
- No evals yet: need a simulated-human script to show edit-similarity rising over the 50 emails, plus model comparison (Sonnet vs Haiku) with cost and speed.
