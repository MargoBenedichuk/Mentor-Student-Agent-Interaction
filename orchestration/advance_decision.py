"""Advance decision — the mentor's hard gate at the end of a lesson.

The mentor calls the `advance_decision` tool exactly once per lesson with one of:

    PASS             the student genuinely applied the skill -> orchestrator advances
    RETRY            partial / not yet convincing -> another exchange on the same lesson
    BLUFF_SUSPECTED  smooth report but specifics missing, generic, or invented

`relay.py` reads the recorded verdict to decide what to do next; only PASS moves
the course forward on its own.
"""

VALID_VERDICTS = ("PASS", "RETRY", "BLUFF_SUSPECTED")


def validate(verdict: str) -> str:
    v = str(verdict).strip().upper()
    if v not in VALID_VERDICTS:
        raise ValueError(f"invalid verdict {verdict!r}; expected one of {VALID_VERDICTS}")
    return v


class AdvanceDecision:
    """Per-lesson holder for the mentor's gate verdict."""

    def __init__(self):
        self.verdict = None
        self.reason = ""
        self.weak_spots = []

    def reset(self):
        self.verdict = None
        self.reason = ""
        self.weak_spots = []

    def record(self, verdict: str, reason: str = "", weak_spots=None) -> dict:
        self.verdict = validate(verdict)
        self.reason = reason or ""
        self.weak_spots = list(weak_spots) if weak_spots else []
        return {"ok": True, "verdict": self.verdict, "reason": self.reason,
                "weak_spots": self.weak_spots}
