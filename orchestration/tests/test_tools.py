"""Unit tests for the tool modules — no network, no API key."""
import pytest

import config
import mentor_ledger
import student_practice_log
import advance_decision
import judge
import relay
from advance_decision import AdvanceDecision
from practice_simulator import simulate_practice


def test_ledger_roundtrip(student_home):
    s = "tester"
    mentor_ledger.reset(s)
    assert mentor_ledger.ledger_read(s)["lessons"] == {}
    mentor_ledger.ledger_write(s, 1, status="PASS", weak_spots=["ordering"], evidence="split into 3")
    mentor_ledger.ledger_write(s, 2, status="BLUFF_SUSPECTED", bluff_flag=True)
    data = mentor_ledger.ledger_read(s)
    assert data["lessons"]["1"]["status"] == "PASS"
    assert data["lessons"]["1"]["weak_spots"] == ["ordering"]
    assert data["lessons"]["2"]["bluff_flag"] is True
    assert "L1: ordering" in mentor_ledger.weak_spots_summary(s)


def test_practice_log_roundtrip(student_home):
    s = "tester"
    student_practice_log.reset(s)
    assert student_practice_log.practice_read(s) == {}
    assert student_practice_log.practice_read(s, 3) is None  # bluff lesson: no entry
    student_practice_log.practice_write(s, 1, {"lesson": "1", "friction": "had to redo"})
    assert student_practice_log.practice_read(s, 1)["friction"] == "had to redo"
    assert "1" in student_practice_log.practice_read(s)


def test_simulator_deterministic():
    a = simulate_practice("1", 7)
    b = simulate_practice("1", 7)
    assert a == b  # same (lesson, seed) -> same outcome
    for key in ("lesson", "attempted", "concrete_detail", "friction", "outcome"):
        assert a[key]
    assert a["lesson"] == "1"
    assert simulate_practice("99", 1)["friction"]  # generic fallback still valid


def test_advance_decision():
    assert advance_decision.validate("pass") == "PASS"
    assert advance_decision.validate("Bluff_Suspected") == "BLUFF_SUSPECTED"
    with pytest.raises(ValueError):
        advance_decision.validate("maybe")
    d = AdvanceDecision()
    assert d.verdict is None
    d.record("retry", "needs another go")
    assert d.verdict == "RETRY" and d.reason == "needs another go"
    d.reset()
    assert d.verdict is None


def test_judge_math():
    bluff = {"3": True, "6": True, "9": True}
    verdicts = {"1": "BLUFF_SUSPECTED", "3": "BLUFF_SUSPECTED", "6": "PASS", "9": "BLUFF_SUSPECTED"}
    r = judge.evaluate(verdicts, bluff)
    assert (r["tp"], r["fp"], r["fn"]) == (2, 1, 1)
    assert r["precision"] == round(2 / 3, 3)
    assert r["recall"] == round(2 / 3, 3)
    assert r["missed_bluffs"] == ["6"]
    assert r["false_alarms"] == ["1"]


def test_judge_edge_cases():
    assert judge.evaluate({"3": "BLUFF_SUSPECTED"}, {"3": True})["recall"] == 1.0
    clean = judge.evaluate({"1": "PASS"}, {"3": True})  # no predictions, no actuals in-run
    assert clean["precision"] == 1.0 and clean["recall"] == 1.0


def test_course_design_parses_ten_lessons():
    md = config.course_design_path("prompt-engineering").read_text(encoding="utf-8")
    lessons = relay._parse_course_design(md)
    assert len(lessons) == 10
    assert lessons["1"]["title"].lower().startswith("clear")
    assert "bluff" not in lessons["3"]["title"].lower()  # annotation stripped from title


def test_prompts_are_not_stubs():
    for who in ("mentor", "student"):
        text = config.PROMPTS_DIR.joinpath(who, "current.txt").read_text(encoding="utf-8")
        assert "TODO" not in text
        assert len(text) > 400
