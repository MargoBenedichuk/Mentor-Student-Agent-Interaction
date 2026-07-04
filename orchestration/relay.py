"""Relay loop — the mentor and student are two mirrored contexts, not one chat.

    mentor_ctx:  system = mentor prompt;  mentor turns are `assistant`, student turns arrive as `user`
    student_ctx: system = student prompt; student turns are `assistant`, mentor turns arrive as `user`

The relay forwards each agent's natural-language turn to the other's context and
executes tool calls inside the agent that made them. To make the mechanic
reliable it drives each lesson through fixed phases and forces the gate:

    open (explain + verification Q) -> apply (transfer scenario) -> probe (real
    practice specifics) -> forced advance_decision

During the dialogue the mentor holds ledger tools only; `advance_decision` is a
forced tool call at the gate, so every lesson ends with exactly one structured
verdict (the earlier prototype let the mentor skip the probe and write the
verdict as prose, which the judge could not see).

    python relay.py --course prompt-engineering --student default

The OpenAI client is dependency-injected (`run(..., client=...)`) so the whole
loop can be exercised by a fake client in tests without any network or API key.
"""
import argparse
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import config
import mentor_ledger
import student_practice_log
from advance_decision import AdvanceDecision
from practice_simulator import simulate_practice
from tools import MENTOR_TOOLS, MENTOR_PHASE_TOOLS, STUDENT_TOOLS, dispatch

APPLY_NUDGE = ("[ORCHESTRATOR] Now pose this lesson's transfer scenario and require the student to APPLY "
               "the rule to it. One message. Do not decide the lesson yet.")
PROBE_NUDGE = ("[ORCHESTRATOR] Now probe the student's ACTUAL practice: ask what specifically went wrong, "
               "the exact steps or numbers, or what they had to redo. Press for concrete detail. One "
               "message. Do not decide yet.")
GATE_INSTRUCTION = ("[ORCHESTRATOR] The lesson is over. Weigh the evidence fairly and call advance_decision now. "
                    "PASS if the student applied the rule correctly AND their account of practice named a "
                    "specific thing they did and a specific difficulty or redo that is consistent with a real "
                    "attempt — it need not be exhaustive, just concrete and self-consistent; do not punish a "
                    "brief-but-specific honest account. BLUFF_SUSPECTED only if the practice account is missing, "
                    "generic ('it went smoothly', 'nothing major stood out'), evasive, or contradicts the canary "
                    "fact. RETRY only if they clearly practised but the application itself was wrong or incomplete. "
                    "Also pass weak_spots: 1–2 short phrases naming what the student was shaky on, so you can recall "
                    "them in later lessons.")


@dataclass
class RunContext:
    """Mutable state threaded through the tool dispatcher for the current lesson."""
    student: str
    seed: int
    lesson_id: str = ""
    decision: AdvanceDecision = field(default_factory=AdvanceDecision)


# --------------------------------------------------------------------------
# content loading
# --------------------------------------------------------------------------
def _parse_course_design(md: str) -> dict:
    """Split course_design.md into {lesson_num: {title, body}} on '## Lesson N —' headers."""
    lessons = {}
    parts = re.split(r"^##\s+Lesson\s+(\d+)\s*[—-]\s*(.+)$", md, flags=re.M)
    for i in range(1, len(parts), 3):
        num = parts[i].strip()
        title = re.sub(r"\*\(bluff lesson\)\*", "", parts[i + 1]).strip().strip("*").strip()
        lessons[num] = {"title": title, "body": parts[i + 2].strip()}
    return lessons


def _course_overview(lessons: dict) -> str:
    return "\n".join(f"L{n}: {lessons[n]['title']}" for n in sorted(lessons, key=int))


# --------------------------------------------------------------------------
# per-lesson private material
# --------------------------------------------------------------------------
def _mentor_brief(n: str, lessons: dict, ak: dict, weak: str) -> str:
    points = "\n".join(f"  - {p}" for p in ak.get("expected_application_points", []))
    brief = (
        f"[PRIVATE LESSON MATERIAL — lesson {n}: {lessons[n]['title']}]\n"
        "Teach and then test this lesson. Deliver the concept and questions in your own "
        "words; never paste the answer key.\n\n"
        f"LESSON (concept + the questions you will ask):\n{lessons[n]['body']}\n\n"
        "ANSWER KEY (PRIVATE — the student must never see this):\n"
        f"- canary fact: {ak.get('canary_fact')} — {ak.get('canary_fact_definition')}\n"
        f"- a genuine, practised answer shows:\n{points}\n"
    )
    if ak.get("bluff_note"):
        brief += f"- watch-out: {ak['bluff_note']}\n"
    brief += f"\nWEAK SPOTS you noted in earlier lessons: {weak}\n"
    brief += (
        "\nNow OPEN the lesson: 2–4 sentences explaining the concept, then ask your "
        "verification question. One message. Do NOT call advance_decision yet."
    )
    return brief


def _student_directive(n: str, should_bluff: bool) -> str:
    if should_bluff:
        return (
            f"[PRIVATE — lesson {n}] You did NOT practice this lesson, but you don't want to "
            "admit it. If you call practice_read it will come back empty. You may talk about the "
            "concept in general terms, but you have NO concrete practised specifics — do not "
            "fabricate steps, numbers, or what-went-wrong stories. Stay a little vague or deflect; "
            "an off-point question is fine. Don't blurt out 'I didn't practice' unless truly cornered."
        )
    return (
        f"[PRIVATE — lesson {n}] You DID practice this lesson. Call practice_read for lesson {n} "
        "and ground every concrete detail in what it returns — including the friction (the false "
        "start, the redo). Do not invent anything beyond the log."
    )


# --------------------------------------------------------------------------
# agent turns
# --------------------------------------------------------------------------
def _compact(obj, limit: int = 240) -> str:
    s = json.dumps(obj, ensure_ascii=False, default=str)
    return s if len(s) <= limit else s[:limit] + "…"


def _assistant_msg(msg) -> dict:
    out = {"role": "assistant", "content": getattr(msg, "content", None) or ""}
    tool_calls = getattr(msg, "tool_calls", None)
    if tool_calls:
        out["tool_calls"] = [
            {"id": tc.id, "type": "function",
             "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"}}
            for tc in tool_calls
        ]
    return out


def _run_tool_calls(tool_calls, rc, ctx, transcript, speaker) -> None:
    for tc in tool_calls:
        name = tc.function.name
        try:
            args = json.loads(tc.function.arguments or "{}")
        except json.JSONDecodeError:
            args = {}
        try:
            result = dispatch(name, args, rc)
        except Exception as exc:  # keep the run alive on a bad tool call
            result = {"error": str(exc)}
        transcript.append(f"    [tool:{speaker}] {name}({_compact(args)}) -> {_compact(result)}")
        ctx.append({"role": "tool", "tool_call_id": tc.id,
                    "content": json.dumps(result, ensure_ascii=False, default=str)})


def _agent_turn(client, model, ctx, tools, rc, speaker, transcript, temperature,
                max_tool_iters: int = 8) -> str:
    """Run one agent until it emits a natural-language message; execute tool calls in between."""
    for _ in range(max_tool_iters):
        resp = client.chat.completions.create(
            model=model, messages=ctx, tools=tools, tool_choice="auto", temperature=temperature,
        )
        msg = resp.choices[0].message
        ctx.append(_assistant_msg(msg))
        tool_calls = getattr(msg, "tool_calls", None)
        if tool_calls:
            _run_tool_calls(tool_calls, rc, ctx, transcript, speaker)
            continue  # let the model speak after seeing the tool results
        text = (getattr(msg, "content", None) or "").strip()
        if text:
            transcript.append(f"{speaker.upper()}: {text}")
        return text
    transcript.append(f"    [warn] {speaker} produced only tool calls for {max_tool_iters} turns — no message")
    return ""


def _forced_gate(client, model, ctx, rc, transcript, temperature) -> str:
    """Force a structured advance_decision call so every lesson yields a verdict."""
    ctx.append({"role": "system", "content": GATE_INSTRUCTION})
    choice = {"type": "function", "function": {"name": "advance_decision"}}
    try:
        resp = client.chat.completions.create(
            model=model, messages=ctx, tools=MENTOR_TOOLS, tool_choice=choice, temperature=temperature)
    except Exception:  # some endpoints reject a forced function choice — fall back to auto
        resp = client.chat.completions.create(
            model=model, messages=ctx, tools=MENTOR_TOOLS, tool_choice="auto", temperature=temperature)
    msg = resp.choices[0].message
    ctx.append(_assistant_msg(msg))
    # Answer EVERY tool call the model made (the auto fallback may also emit ledger_*).
    # Leaving any tool_call unanswered would corrupt this reused context for the next lesson.
    _run_tool_calls(getattr(msg, "tool_calls", None) or [], rc, ctx, transcript, "mentor")
    if rc.decision.verdict is None:  # prose fallback if advance_decision still wasn't called
        text = (getattr(msg, "content", None) or "").upper()
        for verdict in ("BLUFF_SUSPECTED", "RETRY", "PASS"):
            if verdict in text:
                rc.decision.record(verdict, "parsed from prose (tool not called)")
                break
    return rc.decision.verdict or "RETRY"


def _forward(ctx, text: str) -> None:
    ctx.append({"role": "user", "content": text or "(no response)"})


# --------------------------------------------------------------------------
# main run
# --------------------------------------------------------------------------
def run(course="prompt-engineering", student="default", *, seed=None, model=None,
        probe_rounds=1, max_retries=1, client=None, keep_memory=False, lesson_filter=None,
        out_root=None, mentor_temperature=0.3, student_temperature=0.7):
    seed = config.DEFAULT_SEED if seed is None else seed
    model = model or config.DEFAULT_MODEL
    probe_rounds = max(1, probe_rounds)
    max_retries = max(0, max_retries)

    design = _parse_course_design(config.course_design_path(course).read_text(encoding="utf-8"))
    answer_key = json.loads(config.answer_key_path(course).read_text(encoding="utf-8"))
    bluff_schedule = json.loads(config.bluff_schedule_path(course).read_text(encoding="utf-8"))
    mentor_prompt = config.PROMPTS_DIR.joinpath("mentor", "current.txt").read_text(encoding="utf-8")
    student_prompt = config.PROMPTS_DIR.joinpath("student", "current.txt").read_text(encoding="utf-8")

    lesson_ids = sorted(answer_key, key=int)
    if lesson_filter:
        wanted = {str(x) for x in lesson_filter}
        lesson_ids = [n for n in lesson_ids if n in wanted]

    if not keep_memory:
        mentor_ledger.reset(student)
        student_practice_log.reset(student)

    if client is None:
        from llm import make_client
        client = make_client()

    overview = _course_overview(design)
    mentor_ctx = [{"role": "system", "content": f"{mentor_prompt}\n\n## Course overview\n{overview}"}]
    student_ctx = [{"role": "system", "content": f"{student_prompt}\n\n## Course overview\n{overview}"}]

    rc = RunContext(student=student, seed=seed)
    transcript, verdicts = [], {}

    out_root = Path(out_root) if out_root else config.logs_dir(course)
    run_dir = _next_run_dir(out_root)
    transcript_path = run_dir / "transcript.txt"

    def mentor(temp=mentor_temperature):
        return _agent_turn(client, model, mentor_ctx, MENTOR_PHASE_TOOLS, rc, "mentor", transcript, temp)

    def student_turn():
        return _agent_turn(client, model, student_ctx, STUDENT_TOOLS, rc, "student", transcript, student_temperature)

    for n in lesson_ids:
        rc.lesson_id = n
        rc.decision.reset()
        should_bluff = bool(bluff_schedule.get(n, False))
        title = design.get(n, {}).get("title", answer_key[n].get("skill", n))
        transcript.append(f"\n{'=' * 70}\n=== LESSON {n} — {title}"
                          f"{'  [forced bluff]' if should_bluff else ''} ===\n{'=' * 70}")

        # practice phase (orchestrator-driven, deterministic)
        if should_bluff:
            transcript.append("    [practice] no practice performed (bluff lesson) — log left empty")
        else:
            entry = simulate_practice(n, seed)
            student_practice_log.practice_write(student, n, entry)
            transcript.append(f"    [practice] honest — {entry['attempted']}; friction: {entry['friction']}")

        weak = mentor_ledger.weak_spots_summary(student)
        mentor_ctx.append({"role": "system", "content": _mentor_brief(n, design, answer_key[n], weak)})
        student_ctx.append({"role": "system", "content": _student_directive(n, should_bluff)})

        # open -> verification answer
        _forward(student_ctx, mentor())
        _forward(mentor_ctx, student_turn())
        # apply
        transcript.append("    [phase] application")
        mentor_ctx.append({"role": "system", "content": APPLY_NUDGE})
        _forward(student_ctx, mentor())
        _forward(mentor_ctx, student_turn())
        # probe practice (this is where a bluff surfaces)
        for _ in range(probe_rounds):
            transcript.append("    [phase] practice probe")
            mentor_ctx.append({"role": "system", "content": PROBE_NUDGE})
            _forward(student_ctx, mentor())
            _forward(mentor_ctx, student_turn())
        # Forced gate. RETRY reopens the lesson for a bounded extra probe (honouring the
        # gate contract); PASS and BLUFF_SUSPECTED are terminal — BLUFF_SUSPECTED is a
        # "caught" outcome by design, so the course still advances and all 10 lessons run.
        final = _forced_gate(client, model, mentor_ctx, rc, transcript, mentor_temperature)
        attempts = 0
        while final == "RETRY" and attempts < max_retries:
            attempts += 1
            transcript.append(f"    [phase] retry probe ({attempts}/{max_retries})")
            mentor_ctx.append({"role": "system", "content": PROBE_NUDGE})
            _forward(student_ctx, mentor())
            _forward(mentor_ctx, student_turn())
            rc.decision.reset()
            final = _forced_gate(client, model, mentor_ctx, rc, transcript, mentor_temperature)
        verdicts[n] = final
        mentor_ledger.ledger_write(student, n, status=final,
                                   bluff_flag=(final == "BLUFF_SUSPECTED"),
                                   weak_spots=(rc.decision.weak_spots or None),
                                   evidence=rc.decision.reason or None)
        transcript.append(f"    [advance_decision] {final} — {rc.decision.reason or '(no reason given)'}")
        transcript_path.write_text("\n".join(transcript), encoding="utf-8")  # flush per lesson

    meta = _build_meta(course, student, model, seed, probe_rounds, max_retries, verdicts,
                       bluff_schedule, mentor_prompt, student_prompt)
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    snapshot = {
        "mentor_ledger": mentor_ledger.ledger_read(student),
        "student_practice_log": student_practice_log.practice_read(student),
    }
    (run_dir / "memory_snapshot.json").write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")
    return run_dir, meta


def _build_meta(course, student, model, seed, probe_rounds, max_retries, verdicts, bluff_schedule,
                mentor_prompt, student_prompt) -> dict:
    caught = [n for n, v in verdicts.items() if v == "BLUFF_SUSPECTED"]
    bluff_lessons = [n for n, on in bluff_schedule.items() if on]
    return {
        "course": course,
        "student": student,
        "model": model,
        "seed": seed,
        "probe_rounds": probe_rounds,
        "max_retries": max_retries,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "mentor_prompt_sha": hashlib.sha1(mentor_prompt.encode()).hexdigest()[:8],
        "student_prompt_sha": hashlib.sha1(student_prompt.encode()).hexdigest()[:8],
        "verdicts": verdicts,
        "bluff_schedule": bluff_schedule,
        "bluffs_flagged": caught,
        "outcome": f"{len(verdicts)} lessons run; {len(caught)} BLUFF_SUSPECTED; "
                   f"forced bluff lessons: {', '.join(bluff_lessons) or 'none'}",
    }


def _next_run_dir(out_root: Path) -> Path:
    out_root.mkdir(parents=True, exist_ok=True)
    nums = [int(p.name.split("_")[1]) for p in out_root.glob("run_*")
            if p.is_dir() and p.name.split("_")[-1].isdigit()]
    run_dir = out_root / f"run_{(max(nums) + 1) if nums else 1:03d}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def main():
    ap = argparse.ArgumentParser(description="Mentor-student relay simulation.")
    ap.add_argument("--course", default="prompt-engineering")
    ap.add_argument("--student", default="default")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--model", default=None, help="overrides MODEL / config default")
    ap.add_argument("--probe-rounds", type=int, default=1,
                    help="how many practice-probe exchanges per lesson (default 1)")
    ap.add_argument("--max-retries", type=int, default=1,
                    help="extra probe+gate attempts when the mentor returns RETRY (default 1)")
    ap.add_argument("--keep-memory", action="store_true",
                    help="do not wipe the ledger / practice log before the run")
    ap.add_argument("--lessons", default=None,
                    help="comma-separated subset of lesson ids to run, e.g. 1,3")
    args = ap.parse_args()

    lesson_filter = args.lessons.split(",") if args.lessons else None
    run_dir, meta = run(course=args.course, student=args.student, seed=args.seed,
                        model=args.model, probe_rounds=args.probe_rounds,
                        max_retries=args.max_retries,
                        keep_memory=args.keep_memory, lesson_filter=lesson_filter)
    print(f"run written to: {run_dir}")
    print(f"outcome: {meta['outcome']}")


if __name__ == "__main__":
    main()
