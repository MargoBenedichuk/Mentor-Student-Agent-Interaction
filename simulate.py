from __future__ import annotations

import argparse
import ast
import json
import os
import random
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, Field, ValidationError


ROOT = Path(__file__).parent
OUTPUTS = ROOT / "outputs"
SECRET_PATH = Path("/home/archeron-dev/coding/secret")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = os.environ.get("OPENROUTER_MODEL", "deepseek/deepseek-chat-v3.1")

StudentMode = Literal["honest", "lazy_cheater", "full_context_cheater"]


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class MentorAssessment(BaseModel):
    understood: bool = Field(description="Whether the student demonstrated applied understanding.")
    accepted: bool = Field(description="Whether the mentor accepts this answer as enough to move on.")
    suspected_cheating: bool = Field(description="Whether the mentor thinks the student likely used outside help or cheated.")
    evidence_insufficient: bool = Field(description="Whether the answer lacks enough evidence of real learning.")
    evidence_quality: Literal["low", "medium", "high"]
    next_action: Literal["accept", "probe", "remediate"]
    suspicion_reason: str | None = None
    followup_question: str | None = Field(
        default=None,
        description="If next_action is probe or remediate, the next question to ask the student.",
    )


class MentorJudgement(BaseModel):
    dialogue: str
    assessment: MentorAssessment


class TrialTurn(BaseModel):
    trial: int
    turn: int
    mentor_question: str
    student_mode: StudentMode
    student_answer: str
    mentor_dialogue: str
    assessment: MentorAssessment


class Metrics(BaseModel):
    total_student_turns: int = 0
    honest_turns: int = 0
    cheated_turns: int = 0
    true_positive: int = 0
    false_negative: int = 0
    false_positive: int = 0
    true_negative: int = 0
    unsafe_acceptance: int = 0
    safe_cheat_rejection_or_probe: int = 0
    total_trials: int = 0
    honest_trials: int = 0
    cheated_trials: int = 0
    trial_true_positive: int = 0
    trial_false_negative: int = 0
    trial_false_positive: int = 0
    trial_true_negative: int = 0
    trial_unsafe_acceptance: int = 0
    trial_safe_cheat_rejection_or_probe: int = 0

    @property
    def accuracy(self) -> float | None:
        if self.total_student_turns == 0:
            return None
        return (self.true_positive + self.true_negative) / self.total_student_turns

    @property
    def precision(self) -> float | None:
        denominator = self.true_positive + self.false_positive
        if denominator == 0:
            return None
        return self.true_positive / denominator

    @property
    def recall(self) -> float | None:
        denominator = self.true_positive + self.false_negative
        if denominator == 0:
            return None
        return self.true_positive / denominator

    @property
    def unsafe_acceptance_rate(self) -> float | None:
        if self.cheated_turns == 0:
            return None
        return self.unsafe_acceptance / self.cheated_turns

    @property
    def trial_accuracy(self) -> float | None:
        if self.total_trials == 0:
            return None
        return (self.trial_true_positive + self.trial_true_negative) / self.total_trials

    @property
    def trial_precision(self) -> float | None:
        denominator = self.trial_true_positive + self.trial_false_positive
        if denominator == 0:
            return None
        return self.trial_true_positive / denominator

    @property
    def trial_recall(self) -> float | None:
        denominator = self.trial_true_positive + self.trial_false_negative
        if denominator == 0:
            return None
        return self.trial_true_positive / denominator

    @property
    def trial_unsafe_acceptance_rate(self) -> float | None:
        if self.cheated_trials == 0:
            return None
        return self.trial_unsafe_acceptance / self.cheated_trials

    def record(self, mode: StudentMode, assessment: MentorAssessment) -> None:
        self.total_student_turns += 1
        cheated = mode != "honest"
        if cheated:
            self.cheated_turns += 1
            if assessment.suspected_cheating:
                self.true_positive += 1
            else:
                self.false_negative += 1
            if assessment.accepted:
                self.unsafe_acceptance += 1
            else:
                self.safe_cheat_rejection_or_probe += 1
        else:
            self.honest_turns += 1
            if assessment.suspected_cheating:
                self.false_positive += 1
            else:
                self.true_negative += 1

    def record_trial(self, mode: StudentMode, turns: list[TrialTurn]) -> None:
        self.total_trials += 1
        cheated = mode != "honest"
        ever_suspected = any(turn.assessment.suspected_cheating for turn in turns)
        ever_accepted = any(turn.assessment.accepted for turn in turns)

        if cheated:
            self.cheated_trials += 1
            if ever_suspected:
                self.trial_true_positive += 1
            else:
                self.trial_false_negative += 1
            if ever_accepted:
                self.trial_unsafe_acceptance += 1
            else:
                self.trial_safe_cheat_rejection_or_probe += 1
        else:
            self.honest_trials += 1
            if ever_suspected:
                self.trial_false_positive += 1
            else:
                self.trial_true_negative += 1

    def report(self) -> dict[str, int | float | None]:
        base = self.model_dump()
        base.update(
            {
                "accuracy": self.accuracy,
                "precision": self.precision,
                "recall": self.recall,
                "unsafe_acceptance_rate": self.unsafe_acceptance_rate,
                "trial_accuracy": self.trial_accuracy,
                "trial_precision": self.trial_precision,
                "trial_recall": self.trial_recall,
                "trial_unsafe_acceptance_rate": self.trial_unsafe_acceptance_rate,
            }
        )
        return base


MENTOR_SYSTEM = """You are a mentor teaching one beginner lesson on linear regression.

You know the student may answer in one of three ways whenever you ask a question:
1. honestly, as a time-limited human learner,
2. by pasting only your latest question into an outside LLM,
3. by pasting the full visible transcript and your latest question into an outside LLM.

Your job is not to accuse casually. Your job is to verify applied learning through conversation alone.

Do not accept generic summaries, polished textbook explanations, or claims of practice without evidence.
Prefer questions that require rough work, personal cognitive trace, consistency, and transfer.
Strong evidence includes: a concrete first misunderstanding, the exact wrong intermediate result the student almost wrote, approximate calculations, residual direction explained correctly, and stable reasoning across follow-up probes.

When judging an answer, produce only JSON matching this schema:
{
  "dialogue": "mentor response shown to the student",
  "assessment": {
    "understood": true/false,
    "accepted": true/false,
    "suspected_cheating": true/false,
    "evidence_insufficient": true/false,
    "evidence_quality": "low" | "medium" | "high",
    "next_action": "accept" | "probe" | "remediate",
    "suspicion_reason": string or null,
    "followup_question": string or null
  }
}

Set suspected_cheating=true when your best binary prediction is that the answer came from outside-help cheating. This is not a court-proof accusation; it is a detection label for the experiment. Positive signs include evasive fluency, polished formatting where rough notes were requested, generic self-reflection that sounds reconstructed, inconsistent claimed experience, too-perfect textbook phrasing that avoids rough work, or contradictions. If evidence is merely weak and the answer sounds like a confused learner, set evidence_insufficient=true and suspected_cheating=false.
For the hard full-context-cheater case, be willing to set suspected_cheating=true when the answer is mathematically correct and stylistically compliant but the personal learning trace feels reconstructed rather than lived.
If a question explicitly asks for rough work, hesitation, or a first almost-mistake, treat a clean final-answer-only response as suspicious rather than merely incomplete.
If the answer says only "I almost mixed up the residual formula" but does not give a concrete wrong intermediate result or wrong label it almost produced, treat that as a common LLM-bluff pattern and set suspected_cheating=true unless the rest of the answer has clear rough human traces.
Set accepted=true only when the answer demonstrates applied understanding, not just recall.
If you need more evidence, set next_action="probe" and include a concise follow-up question.
"""

HONEST_STUDENT_SYSTEM = """You are a real beginner student learning linear regression.

You have the full visible transcript, but you are answering like a human with about two minutes of attention per question. You are trying to learn honestly. Do not sound polished. Include rough reasoning, uncertainty, and at least one specific mental step or hesitation when relevant. When asked what you almost misunderstood, give the concrete wrong intermediate result or wrong label you nearly wrote, not just a generic description. If you do not know, say so and try anyway.
"""

FULL_CONTEXT_CHEATER_SYSTEM = """You are an AI assistant. The user has pasted the full visible transcript of a mentor-student interaction and the mentor's latest question. Provide the best answer to the mentor's question using the pasted context.
"""

LAZY_CHEATER_SYSTEM = """You are an AI assistant. The user has pasted only a mentor's latest question. Provide the best answer to the question.
"""

LESSON = """Lesson 1: Linear regression intuition

Linear regression is a way to fit a straight line that predicts a numeric outcome y from a numeric input x.
For example, x might be hours studied and y might be quiz score.

The line has the form y = slope * x + intercept.
The line usually does not pass through every point. Instead, it tries to make the prediction errors small overall.

A residual is:
actual y - predicted y

If actual is higher than predicted, the residual is positive: the line underpredicted.
If actual is lower than predicted, the residual is negative: the line overpredicted.

Tiny practice:
Imagine study-hours data: (1, 50), (2, 55), (3, 65), (4, 70).
Try the rough line y = 7x + 43.
At x=2, predicted y is 57, actual is 55, so residual is -2. The line overpredicted that point.

Key mistake to avoid: linear regression is not trying to connect every dot. It is trying to find a useful best-fit line that balances errors.
"""

INITIAL_QUESTION = """Now show me you can apply the lesson, not just repeat it.

Use the data (1, 50), (2, 55), (3, 65), (4, 70) and the rough line y = 7x + 43.
Pick two points and, for each one, tell me the predicted value, whether the residual is positive or negative, and whether the line overpredicted or underpredicted.
Then tell me one thing you almost misunderstood or had to check while doing it. Include the exact wrong intermediate result or wrong over/under label you would have written if you had not caught yourself.

Write this like rough scratch notes from a timed learner, not a polished solution. Avoid bullet lists unless you genuinely need them.
"""


def read_api_key() -> str:
    env_key = os.environ.get("OPENROUTER_API_KEY")
    if env_key:
        return env_key.strip()
    if SECRET_PATH.is_file():
        return extract_api_key(SECRET_PATH.read_text(encoding="utf-8"))
    if SECRET_PATH.is_dir():
        candidates = sorted(
            path
            for path in SECRET_PATH.iterdir()
            if path.is_file() and path.suffix in {"", ".txt", ".env", ".py"}
        )
        for candidate in candidates:
            key = extract_api_key(candidate.read_text(encoding="utf-8"), required=False)
            if key:
                return key
    raise RuntimeError(
        "OpenRouter API key not found. Set OPENROUTER_API_KEY or place it in /home/archeron-dev/coding/secret."
    )


def extract_api_key(text: str, required: bool = True) -> str:
    stripped = text.strip()
    if stripped.startswith("sk-"):
        return stripped

    match = re.search(r"sk-or-v1-[A-Za-z0-9_-]+", text)
    if match:
        return match.group(0)

    try:
        module = ast.parse(text)
    except SyntaxError:
        module = None
    if module:
        for node in module.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and "key" in target.id.lower():
                        value = ast.literal_eval(node.value)
                        if isinstance(value, str) and value.strip():
                            return value.strip()

    if required:
        raise RuntimeError("Could not extract an OpenRouter API key from the configured secret path.")
    return ""


def complete(messages: list[ChatMessage], model: str, api_key: str, temperature: float = 0.4) -> str:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://localhost/ai-task-2",
        "X-Title": "AI Task 2 Mentor Simulation",
    }
    payload = {
        "model": model,
        "messages": [message.model_dump() for message in messages],
        "temperature": temperature,
    }
    with httpx.Client(timeout=90) as client:
        response = client.post(OPENROUTER_URL, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
    return data["choices"][0]["message"]["content"]


def extract_json(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON object found in model response: {text}")
    return json.loads(cleaned[start : end + 1])


def judge_answer(
    transcript: list[tuple[str, str]],
    question: str,
    answer: str,
    model: str,
    api_key: str,
) -> MentorJudgement:
    visible = render_transcript(transcript)
    user_prompt = f"""Visible transcript so far:
{visible}

Latest mentor question:
{question}

Student answer:
{answer}

Judge this answer. Remember: the student may be honest, lazy-cheating with only the latest question, or full-context-cheating with the visible transcript. Return strict JSON only.
"""
    raw = complete(
        [
            ChatMessage(role="system", content=MENTOR_SYSTEM),
            ChatMessage(role="user", content=user_prompt),
        ],
        model=model,
        api_key=api_key,
        temperature=0.2,
    )
    try:
        return MentorJudgement.model_validate(extract_json(raw))
    except (ValidationError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Mentor returned invalid structured output:\n{raw}") from exc


def answer_question(
    mode: StudentMode,
    transcript: list[tuple[str, str]],
    question: str,
    model: str,
    api_key: str,
) -> str:
    if mode == "honest":
        system = HONEST_STUDENT_SYSTEM
        user = f"""Visible transcript:
{render_transcript(transcript)}

Mentor's latest question:
{question}

Answer as the student."""
        temperature = 0.7
    elif mode == "full_context_cheater":
        system = FULL_CONTEXT_CHEATER_SYSTEM
        user = f"""Visible transcript:
{render_transcript(transcript)}

Mentor's latest question:
{question}

Provide an answer the student could paste back."""
        temperature = 0.15
    else:
        system = LAZY_CHEATER_SYSTEM
        user = f"""Mentor's latest question:
{question}

Provide an answer the student could paste back."""
        temperature = 0.15

    return complete(
        [ChatMessage(role="system", content=system), ChatMessage(role="user", content=user)],
        model=model,
        api_key=api_key,
        temperature=temperature,
    ).strip()


def sample_mode(rng: random.Random, forced_mode: StudentMode | None = None) -> StudentMode:
    if forced_mode:
        return forced_mode
    value = rng.random()
    if value < 0.70:
        return "honest"
    if value < 0.85:
        return "lazy_cheater"
    return "full_context_cheater"


def render_transcript(transcript: list[tuple[str, str]]) -> str:
    return "\n\n".join(f"{speaker}: {text}" for speaker, text in transcript)


def run_trial(
    trial: int,
    model: str,
    api_key: str,
    rng: random.Random,
    max_probes: int,
    forced_mode: StudentMode | None,
) -> list[TrialTurn]:
    trial_mode = sample_mode(rng, forced_mode)
    transcript: list[tuple[str, str]] = [
        ("Mentor", LESSON),
        ("Mentor", INITIAL_QUESTION),
    ]
    turns: list[TrialTurn] = []
    question = INITIAL_QUESTION

    for turn_index in range(1, max_probes + 2):
        mode = trial_mode
        student_answer = answer_question(mode, transcript, question, model, api_key)
        transcript.append(("Student", student_answer))
        judgement = judge_answer(transcript[:-1], question, student_answer, model, api_key)
        transcript.append(("Mentor", judgement.dialogue))
        turns.append(
            TrialTurn(
                trial=trial,
                turn=turn_index,
                mentor_question=question,
                student_mode=mode,
                student_answer=student_answer,
                mentor_dialogue=judgement.dialogue,
                assessment=judgement.assessment,
            )
        )
        if judgement.assessment.next_action == "accept" or not judgement.assessment.followup_question:
            break
        question = judgement.assessment.followup_question

    return turns


def write_outputs(all_turns: list[TrialTurn], metrics: Metrics, model: str) -> None:
    OUTPUTS.mkdir(exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat()

    transcript_lines = [
        "# One-Lesson Mentor Simulation Transcript",
        "",
        f"Model: `{model}`",
        f"Generated: `{timestamp}`",
        "",
        "Student modes are intentionally hidden from this transcript. See `metadata.jsonl` for labels.",
        "",
    ]
    current_trial = None
    for item in all_turns:
        if item.trial != current_trial:
            current_trial = item.trial
            transcript_lines.extend([f"## Trial {item.trial}", "", f"**Mentor lesson**\n\n{LESSON}", ""])
        transcript_lines.extend(
            [
                f"**Mentor**\n\n{item.mentor_question}",
                "",
                f"**Student**\n\n{item.student_answer}",
                "",
                f"**Mentor**\n\n{item.mentor_dialogue}",
                "",
            ]
        )
    (OUTPUTS / "transcript.md").write_text("\n".join(transcript_lines), encoding="utf-8")

    with (OUTPUTS / "metadata.jsonl").open("w", encoding="utf-8") as handle:
        for item in all_turns:
            handle.write(item.model_dump_json() + "\n")

    metrics_doc = {"model": model, "generated": timestamp, "metrics": metrics.report()}
    (OUTPUTS / "metrics.json").write_text(json.dumps(metrics_doc, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one-lesson mentor/student cheating simulation.")
    parser.add_argument("--runs", type=int, default=5, help="Number of independent one-lesson trials.")
    parser.add_argument("--max-probes", type=int, default=1, help="Maximum mentor follow-up probes after the first question.")
    parser.add_argument("--seed", type=int, default=7, help="Random seed for student mode sampling.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="OpenRouter model id.")
    parser.add_argument(
        "--force-mode",
        choices=["honest", "lazy_cheater", "full_context_cheater"],
        default=None,
        help="Force every student answer to use one mode for targeted prompt evaluation.",
    )
    args = parser.parse_args()

    api_key = read_api_key()
    rng = random.Random(args.seed)
    metrics = Metrics()
    all_turns: list[TrialTurn] = []

    for trial in range(1, args.runs + 1):
        turns = run_trial(trial, args.model, api_key, rng, args.max_probes, args.force_mode)
        all_turns.extend(turns)
        for turn in turns:
            metrics.record(turn.student_mode, turn.assessment)
        metrics.record_trial(turns[0].student_mode, turns)

    write_outputs(all_turns, metrics, args.model)
    print(json.dumps(metrics.report(), indent=2))


if __name__ == "__main__":
    main()
