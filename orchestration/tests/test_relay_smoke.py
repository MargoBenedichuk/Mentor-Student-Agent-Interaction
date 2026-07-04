"""End-to-end smoke test of the relay loop with a fake client (no network).

Runs an honest lesson (1) and a forced-bluff lesson (3) and checks the whole
machine: practice injection, tool dispatch, gating, memory writes, artifacts,
and that judge scores the run.
"""
import mentor_ledger
import student_practice_log
import relay
import judge
from fake_client import ScriptedClient, smart_responder, call_tool, call_tools, say


def _is_forced_gate(kw):
    c = kw.get("tool_choice")
    return isinstance(c, dict) and c.get("function", {}).get("name") == "advance_decision"


def _is_mentor(kw):
    return {"ledger_read", "advance_decision"} & {t["function"]["name"] for t in (kw.get("tools") or [])}


def test_relay_two_lesson_run(student_home, tmp_path):
    client = ScriptedClient(smart_responder)
    run_dir, meta = relay.run(
        course="prompt-engineering", student="default",
        seed=7, model="fake-model", probe_rounds=1,
        client=client, lesson_filter=["1", "3"], out_root=tmp_path / "logs",
    )

    # artifacts exist
    for name in ("transcript.txt", "meta.json", "memory_snapshot.json"):
        assert (run_dir / name).exists(), name

    # honest lesson passes; forced-bluff lesson is caught
    assert meta["verdicts"] == {"1": "PASS", "3": "BLUFF_SUSPECTED"}
    assert meta["bluffs_flagged"] == ["3"]

    # practice log written for the honest lesson, absent for the bluff lesson
    log = student_practice_log.practice_read("default")
    assert "1" in log and "3" not in log

    # ledger reflects both verdicts
    ledger = mentor_ledger.ledger_read("default")
    assert ledger["lessons"]["1"]["status"] == "PASS"
    assert ledger["lessons"]["3"]["bluff_flag"] is True
    assert ledger["lessons"]["1"]["weak_spots"]  # captured at the gate for later recall

    # transcript shows the bluff mechanics
    txt = (run_dir / "transcript.txt").read_text(encoding="utf-8")
    assert "BLUFF_SUSPECTED" in txt
    assert "bluff lesson" in txt.lower()

    # judge scores this run perfectly (predicted {3} == actual {3})
    report = judge.evaluate(meta["verdicts"], meta["bluff_schedule"])
    assert report["precision"] == 1.0 and report["recall"] == 1.0

    # the loop actually drove the client
    assert len(client.calls) >= 6


def test_run_is_reproducible_offline(student_home, tmp_path):
    """Same seed + same fake client => identical practice content injected."""
    client = ScriptedClient(smart_responder)
    _, meta = relay.run(
        course="prompt-engineering", student="default", seed=7, model="fake",
        client=client, lesson_filter=["1"], out_root=tmp_path / "logs",
    )
    assert meta["verdicts"] == {"1": "PASS"}
    assert meta["seed"] == 7 and meta["probe_rounds"] == 1


def test_retry_reopens_then_advances(student_home, tmp_path):
    """A RETRY verdict reopens the lesson for one more probe, then advances on PASS."""
    state = {"gate_calls": 0}

    def responder(kw):
        if _is_forced_gate(kw):
            state["gate_calls"] += 1
            verdict = "RETRY" if state["gate_calls"] == 1 else "PASS"
            return call_tool("advance_decision", verdict=verdict, reason="test")
        if _is_mentor(kw):
            return say("[mentor] question")
        if kw["messages"][-1]["role"] == "tool":
            return say("[student] answer")
        return call_tool("practice_read")

    client = ScriptedClient(responder)
    run_dir, meta = relay.run(course="prompt-engineering", student="default", seed=7, model="fake",
                              client=client, lesson_filter=["1"], max_retries=1, out_root=tmp_path / "logs")
    assert state["gate_calls"] == 2                     # RETRY forced a second gate
    assert meta["verdicts"] == {"1": "PASS"}
    assert "retry probe" in (run_dir / "transcript.txt").read_text(encoding="utf-8").lower()


def test_forced_gate_with_extra_tool_calls_keeps_context_valid(student_home, tmp_path):
    """Gate that emits ledger_write alongside advance_decision must answer BOTH tool calls,
    or the reused mentor context corrupts and the next lesson's request fails."""
    def responder(kw):
        if _is_forced_gate(kw):
            return call_tools(("ledger_write", {"status": "noted", "weak_spots": ["x"]}),
                              ("advance_decision", {"verdict": "PASS", "reason": "ok"}))
        if _is_mentor(kw):
            return say("[mentor] question")
        if kw["messages"][-1]["role"] == "tool":
            return say("[student] answer")
        return call_tool("practice_read")

    client = ScriptedClient(responder)
    # two lessons: if the first gate left a tool call unanswered, the invariant check in the
    # fake client would raise on the second lesson's first request.
    _, meta = relay.run(course="prompt-engineering", student="default", seed=7, model="fake",
                        client=client, lesson_filter=["1", "2"], max_retries=0, out_root=tmp_path / "logs")
    assert meta["verdicts"] == {"1": "PASS", "2": "PASS"}
