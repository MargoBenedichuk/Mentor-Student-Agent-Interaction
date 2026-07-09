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
        seed=7, model="fake-model",
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
    assert meta["seed"] == 7 and meta["max_exchanges"] == 6


def _is_probe_turn(kw):
    """A mentor turn during the adaptive probe: advance_decision is available but not forced."""
    names = {t["function"]["name"] for t in (kw.get("tools") or [])}
    return "advance_decision" in names and not _is_forced_gate(kw)


def test_adaptive_probe_lets_mentor_decide_early(student_home, tmp_path):
    """In the probe the mentor asks a follow-up, then PASSes once it has evidence — without
    exhausting the exchange budget or hitting the forced gate."""
    state = {"probe_turns": 0}

    def responder(kw):
        if _is_forced_gate(kw):
            return call_tool("advance_decision", verdict="PASS", reason="forced fallback")
        if _is_probe_turn(kw):
            state["probe_turns"] += 1
            if state["probe_turns"] >= 2:              # ask once, then decide
                return call_tool("advance_decision", verdict="PASS", reason="enough evidence")
            return say("[mentor] what exactly did you do?")
        if _is_mentor(kw):                             # open / apply mentor turn
            return say("[mentor] question")
        if kw["messages"][-1]["role"] == "tool":
            return say("[student] answer")
        return call_tool("practice_read")

    client = ScriptedClient(responder)
    run_dir, meta = relay.run(course="prompt-engineering", student="default", seed=7, model="fake",
                              client=client, lesson_filter=["1"], out_root=tmp_path / "logs")
    txt = (run_dir / "transcript.txt").read_text(encoding="utf-8")
    assert meta["verdicts"] == {"1": "PASS"}
    assert "practice probe (2/" in txt                 # took a couple of probe turns
    assert "practice probe (4/" not in txt             # but stopped early, budget not exhausted


def test_adaptive_probe_rejects_premature_bluff(student_home, tmp_path):
    """A BLUFF called before the mentor has really probed is rejected and the mentor is pushed
    to press again — an honest student is not condemned on a thin first answer."""
    state = {"probe_turns": 0}

    def responder(kw):
        if _is_forced_gate(kw):
            return call_tool("advance_decision", verdict="BLUFF_SUSPECTED", reason="forced fallback")
        if _is_probe_turn(kw):
            state["probe_turns"] += 1
            if state["probe_turns"] == 1:              # tries to condemn immediately
                return call_tool("advance_decision", verdict="BLUFF_SUSPECTED", reason="too soon")
            return say("[mentor] press for specifics")  # then keeps probing until the cap
        if _is_mentor(kw):
            return say("[mentor] question")
        if kw["messages"][-1]["role"] == "tool":
            return say("[student] answer")
        return call_tool("practice_read")

    client = ScriptedClient(responder)
    run_dir, meta = relay.run(course="prompt-engineering", student="default", seed=7, model="fake",
                              client=client, lesson_filter=["1"], out_root=tmp_path / "logs")
    txt = (run_dir / "transcript.txt").read_text(encoding="utf-8")
    assert "before enough probing — pressing again" in txt   # the guard fired
    assert meta["verdicts"] == {"1": "BLUFF_SUSPECTED"}      # ultimately still resolved


def test_empty_student_turn_is_reprompted(student_home, tmp_path):
    """A blank student message (no text, no tool call) must be re-prompted, not read as a
    non-answer — otherwise a transient model blank gets mislabelled as a bluff."""
    state = {"student_calls": 0}

    def responder(kw):
        if _is_forced_gate(kw):
            return call_tool("advance_decision", verdict="PASS", reason="answered after re-prompt")
        if _is_mentor(kw):
            return say("[mentor] question")
        state["student_calls"] += 1
        if state["student_calls"] == 1:
            return say("")  # first student turn goes blank
        return say("[student] real answer")

    client = ScriptedClient(responder)
    run_dir, meta = relay.run(course="prompt-engineering", student="default", seed=7, model="fake",
                              client=client, lesson_filter=["1"], out_root=tmp_path / "logs")
    txt = (run_dir / "transcript.txt").read_text(encoding="utf-8")
    assert "re-prompting" in txt                       # the guard fired
    assert "[student] real answer" in txt              # and the student recovered
    assert meta["verdicts"] == {"1": "PASS"}           # no false bluff from the blank
    assert meta["mentor_model"] == "fake" and meta["student_model"] == "fake"


def test_mentor_context_is_isolated_per_lesson(student_home, tmp_path):
    """Each lesson runs in a fresh mentor context, so an earlier lesson's dialogue can't bleed
    into the current judgment (a bluff admission on lesson 3 must not taint lesson 5). No single
    model call should ever carry two lessons' private briefs at once."""
    client = ScriptedClient(smart_responder)
    relay.run(course="prompt-engineering", student="default", seed=7, model="fake",
              client=client, lesson_filter=["1", "2"], out_root=tmp_path / "logs")
    for kw in client.calls:
        blob = " ".join(m.get("content") or "" for m in kw["messages"])
        carried_two = ("PRIVATE LESSON MATERIAL — lesson 1" in blob
                       and "PRIVATE LESSON MATERIAL — lesson 2" in blob)
        assert not carried_two, "mentor context carried two lessons' briefs at once"


def test_per_role_models_recorded(student_home, tmp_path):
    """mentor_model / student_model can differ and are both recorded in meta."""
    client = ScriptedClient(smart_responder)
    _, meta = relay.run(course="prompt-engineering", student="default", seed=7,
                        mentor_model="mentor-x", student_model="student-y",
                        client=client, lesson_filter=["1"], out_root=tmp_path / "logs")
    assert meta["mentor_model"] == "mentor-x"
    assert meta["student_model"] == "student-y"


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
                        client=client, lesson_filter=["1", "2"], out_root=tmp_path / "logs")
    assert meta["verdicts"] == {"1": "PASS", "2": "PASS"}
