# AI Tech Consultant

One system for a software or service business: it **knows the product** (Task 1), **answers the inbox** (Task 2) and **turns client meetings into a scope, estimate and proposal** (Task 3). Tasks 2 and 3 call Task 1 over HTTP only.

| Task | Folder | Port | What it does |
|---|---|---|---|
| 1 Knowledge Brain | [task1-knowledge-brain](task1-knowledge-brain) | 8000 | Reads the Brilliant Directories help center politely, captions screenshots, syncs daily, answers with sources, says "I don't know" and asks a human expert, learns from the answer |
| 2 Inbox Agent | [task2-inbox-agent](task2-inbox-agent) | 8001 | Triages email, drafts with facts from Task 1, learns rules from each human edit, earns auto-send per kind after 20 unchanged drafts and loses it when quality drops |
| 3 Scope Agents | [task3-scope-agents](task3-scope-agents) | 8002 | Transcript → requirements → "does a setting already do this?" (Task 1) → hours → senior review → proposal. A second meeting updates the scope and shows changed / new / extra. Nothing is exportable until a human approves |

All three are Python (Task 2 in Python by choice, so there is no language mix).

## Run
```bash
cp task1-knowledge-brain/.env.example task1-knowledge-brain/.env   # add ANTHROPIC_API_KEY (same for tasks 2 and 3)
./run_all.sh                                                       # needs Python 3.12
```
Tests: `pytest` inside each task folder (offline, no key needed).

## Status, honestly
Built and tested offline: everything in the three READMEs (26 tests pass). Measured offline: retrieval on the 1,536-article index (right article in the top 3 for 27 of 27 questions) and the keyword fallback classifier (78% on 50 emails). **Not yet run with a Claude key:** image captioning, generated answers, Claude classification/drafting/estimation, and therefore the model-comparison evals. See [EVALS.md](EVALS.md) for what is measured and what is not, and [DESIGN.md](DESIGN.md) for the reasoning and known failures.
