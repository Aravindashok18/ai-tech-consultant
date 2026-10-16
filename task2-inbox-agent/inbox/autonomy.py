"""When may a kind of email go out without a human, and when does it lose that right?

Rules (all numbers configurable):
- "Same kind"  = the classifier's `kind` (12 kinds). Risky kinds (refund, cancellation, complaint, legal, billing, quote) never auto-send.
- "Unchanged"  = human sent the draft with similarity >= SAME_THRESHOLD (0.97). Casing/whitespace do not count.
- Earn:   AUTO_STREAK (20) unchanged in a row for that kind. Any edit or own reply resets the streak to 0.
- Use:    only when confidence >= AUTO_MIN_CONFIDENCE, no risk flags, no needs_human, KB had a verified answer.
- Audit:  SPOT_CHECK_RATE of auto-sent mail is re-read by a human.
- Lose:   one 'bad' spot check, or >=3 edits among the last 10 human-handled emails of that kind since it was enabled.
          After losing it the streak restarts from 0.
"""
from __future__ import annotations

import random
from datetime import UTC, datetime

from .config import RISKY_KINDS, Config


def _now() -> str:
    return datetime.now(UTC).isoformat()


def state(db, kind: str):
    db.execute("INSERT OR IGNORE INTO autonomy(kind) VALUES (?)", (kind,))
    return db.execute("SELECT * FROM autonomy WHERE kind=?", (kind,)).fetchone()


def is_enabled(db, kind: str) -> bool:
    return bool(state(db, kind)["enabled"])


def revoke(db, kind: str, reason: str) -> None:
    db.execute("UPDATE autonomy SET enabled=0, streak=0, revoked_reason=?, revoked_at=? WHERE kind=?", (reason, _now(), kind))
    db.commit()


def record_human_action(db, cfg: Config, kind: str, action: str) -> dict:
    """Call after every human send. Returns {'enabled': bool, 'event': 'earned'|'revoked'|None}."""
    s = state(db, kind)
    event = None
    if action == "sent_unchanged":
        streak = s["streak"] + 1
        db.execute("UPDATE autonomy SET streak=? WHERE kind=?", (streak, kind))
        if streak >= cfg.auto_streak and not s["enabled"] and kind not in RISKY_KINDS:
            db.execute("UPDATE autonomy SET enabled=1, enabled_at=?, revoked_reason=NULL WHERE kind=?", (_now(), kind))
            event = "earned"
    else:  # edited | own_reply
        db.execute("UPDATE autonomy SET streak=0 WHERE kind=?", (kind,))
        if s["enabled"]:
            recent = db.execute(
                "SELECT action FROM actions WHERE kind=? AND action!='auto_sent' AND ts>=? ORDER BY id DESC LIMIT 10",
                (kind, s["enabled_at"])).fetchall()
            if sum(r["action"] != "sent_unchanged" for r in recent) >= 3:
                revoke(db, kind, "3+ edits in the last 10 human-handled emails")
                event = "revoked"
    db.commit()
    return {"enabled": bool(state(db, kind)["enabled"]), "event": event}


def may_auto_send(db, cfg: Config, kind: str, confidence: float, risk_flags: list, needs_human: str) -> bool:
    return (kind not in RISKY_KINDS and is_enabled(db, kind) and confidence >= cfg.auto_min_conf
            and not risk_flags and not needs_human)


def pick_for_spot_check(cfg: Config, rng: random.Random | None = None) -> bool:
    return (rng or random).random() < cfg.spot_check_rate


def record_review(db, email_id: int, kind: str, verdict: str, note: str = "") -> bool:
    """Returns True if the review cost the kind its autonomy."""
    db.execute("INSERT INTO reviews(email_id,kind,verdict,note,ts) VALUES (?,?,?,?,?)", (email_id, kind, verdict, note, _now()))
    db.commit()
    if verdict == "bad" and is_enabled(db, kind):
        revoke(db, kind, f"spot check failed on email {email_id}: {note[:100]}")
        return True
    return False
