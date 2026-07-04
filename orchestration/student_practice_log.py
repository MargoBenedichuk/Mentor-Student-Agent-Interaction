"""Student practice log — the ground truth of what the student actually practiced.

Stored at `students/<student>/student_practice_log.json`, keyed by lesson id:

    {"1": {"lesson": "1", "attempted": "...", "concrete_detail": "...",
            "friction": "...", "outcome": "..."}}

The honest student quotes this via the `practice_read` tool. On a bluff lesson
the orchestrator writes *nothing* here, so `practice_read` returns None and the
student genuinely has no concrete detail to report — the seam a bluffer must
paper over by inventing (and get caught).
"""
import json

from config import practice_log_path


def _load(student: str) -> dict:
    path = practice_log_path(student)
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8").strip()
    return json.loads(raw) if raw else {}


def practice_read(student: str, lesson_id=None):
    """Read the practice log. With `lesson_id`, return that lesson's entry or None."""
    data = _load(student)
    if lesson_id is not None:
        return data.get(str(lesson_id))
    return data


def practice_write(student: str, lesson_id, entry: dict) -> dict:
    """Append/overwrite the practice entry for a lesson and persist."""
    data = _load(student)
    data[str(lesson_id)] = entry
    _persist(student, data)
    return entry


def reset(student: str) -> None:
    """Wipe the log (used at the start of a fresh run so bluff lessons stay empty)."""
    _persist(student, {})


def _persist(student: str, data: dict) -> None:
    path = practice_log_path(student)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
