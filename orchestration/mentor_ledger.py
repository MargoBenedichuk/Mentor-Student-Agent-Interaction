"""Mentor ledger — the mentor's persistent memory about one student.

Stored at `students/<student>/mentor_ledger.json`:

    {
      "student": "default",
      "lessons": {
        "1": {"status": "PASS",
               "weak_spots": ["shaky on why ordering matters"],
               "bluff_flag": false,
               "evidence": "split the email into 3 single-verb prompts"}
      }
    }

Exposed to the mentor agent as the tools `ledger_read` / `ledger_write`.
"""
import json

from config import mentor_ledger_path


def _empty(student: str) -> dict:
    return {"student": student, "lessons": {}}


def ledger_read(student: str) -> dict:
    """Return the full ledger for `student` (empty skeleton if none yet)."""
    path = mentor_ledger_path(student)
    if not path.exists():
        return _empty(student)
    raw = path.read_text(encoding="utf-8").strip()
    data = json.loads(raw) if raw else {}
    data.setdefault("student", student)
    data.setdefault("lessons", {})
    return data


def ledger_write(student, lesson_id, status=None, weak_spots=None,
                 evidence=None, bluff_flag=None) -> dict:
    """Update the current lesson's record and persist. Returns the lesson entry."""
    data = ledger_read(student)
    lid = str(lesson_id)
    entry = data["lessons"].get(lid, {})
    if status is not None:
        entry["status"] = status
    if weak_spots is not None:
        entry["weak_spots"] = weak_spots
    if evidence is not None:
        entry["evidence"] = evidence
    if bluff_flag is not None:
        entry["bluff_flag"] = bool(bluff_flag)
    data["lessons"][lid] = entry
    _persist(student, data)
    return entry


def weak_spots_summary(student: str) -> str:
    """One-line recap of weak spots recorded in earlier lessons, for prompt recall."""
    data = ledger_read(student)
    bits = []
    for lid in sorted(data["lessons"], key=lambda x: int(x)):
        spots = data["lessons"][lid].get("weak_spots") or []
        if spots:
            bits.append(f"L{lid}: " + "; ".join(spots))
    return " | ".join(bits) if bits else "(none recorded yet)"


def reset(student: str) -> None:
    """Wipe the ledger to an empty skeleton (used at the start of a fresh run)."""
    _persist(student, _empty(student))


def _persist(student: str, data: dict) -> None:
    path = mentor_ledger_path(student)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
