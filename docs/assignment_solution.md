% Multi-Agent Mentor-Student Simulation — Assignment Solution
% Margarita Benedichuk
% 2026-07-17

**Assignment.** Two LLM agents run a 10-lesson "Prompt Engineering" course: a **mentor**
checks *application* of each skill (not recall), using questions only — no files/screenshots
shown to the student. The **student** sometimes bluffs (claims practice it didn't do). The
mentor must catch this from conversation alone.

## Architecture

- **Two mirrored contexts, not one shared chat** — `mentor_ctx`/`student_ctx` are separate
  message lists; the relay forwards each turn into the other's context. Fresh per lesson;
  cross-lesson memory rides durable tools (`mentor_ledger`, `student_practice_log`), not raw
  chat history.
- **Fixed-phase flow + forced gate**: greet/segue &rarr; open (introduce + verify) &rarr; apply
  (transfer scenario) &rarr; probe (adaptive follow-ups) &rarr; forced `advance_decision`.
  Guarantees one structured verdict (`PASS`/`BLUFF_SUSPECTED`) per lesson.
- **Bluff detection, three levers**: (1) *canary facts* — every rule uses a made-up term
  ("1-task-1-verb rule", "Contrast rule", ...) absent from any model's pretraining; (2)
  `answer_key.json` — private to the mentor, canary facts + expected application points; (3)
  `student_practice_log.json` — on bluff lessons (3, 6, 9) nothing is written, so
  `practice_read` returns null and the student must fabricate or admit it.

## Conversational quality (feedback fixes)

Two changes so the transcript reads like a real chat rather than a lecture:

- **A user never replies with long messages.** Brevity is now the student prompt's top rule
  (1–2 sentences), with a `STUDENT_MAX_TOKENS` backstop. Longest student reply: **70 words**,
  down from ~200-word paragraphs.
- **Never open a conversation with a lesson and a task** — that loses the customer. The session
  now opens with a greeting exchange, and every lesson eases in with a segue before the concept:

```
Mentor:  Hey there! How's it going? Have you had a chance to dive into any practice lately?
Student: Hey! I'm doing well, thanks! Yes, I did some practice recently.
Mentor:  Great to hear! Today, I want to talk about the "1-task-1-verb" rule...
```

## Models & tools

Mentor/student both `openai/gpt-4o-mini` (OpenRouter); per-role models are supported
(`MENTOR_MODEL`/`STUDENT_MODEL`), and the student runs with reasoning disabled (kills
empty-message blank-outs). Judge is a deterministic precision/recall calculator (no LLM).
Tools: `ledger_read`/`ledger_write` (mentor memory), `practice_read` (student's only source of
concrete detail), `practice_simulator` (deterministic, seeded practice-outcome generator),
`advance_decision` (the hard gate), `judge` (post-run scoring).

## Result (best run — `run_018`, 10/10 lessons, seed 7)

```
precision=1.0  recall=1.0     TP=3 FP=0 FN=0
caught=[3, 6, 9]  missed=[]   (bluffs forced on 3, 6, 9)
```

Across 5 seeds: **recall 1.0 every run** — all three forced bluffs caught every time; precision
ranged **0.6–1.0** (0–2 honest lessons over-flagged). The previous version reported recall 0.667
on a different model (`deepseek-v4-pro`), so this is not a like-for-like comparison.

## Reflection

Making the student terse traded directly against detection: shorter replies give the mentor less
concrete detail to judge, so it began over-flagging *honest* students (precision fell to 0.6
until a "brevity is not evasion" rule in the mentor prompt pulled it back). There is a real
tension between "text like a real user" and "give enough evidence that you practised" — the two
fixes above are not free. The honest ceiling of conversation-only detection still stands: a
strong model told to bluff can manufacture *plausible* friction, not just a smooth denial, and
nothing here separates manufactured friction from real friction once both are equally specific.
Mentor prompt is at 5 iterations, student at 4 (early mentors accepted a correct transfer answer
alone, or condemned a thin-but-honest first answer too soon).

## Full detail in the repository

- Mentor / student prompts: `prompts/mentor/current.txt`, `prompts/student/current.txt`
- Tools implementation: `orchestration/tools.py`, `orchestration/relay.py`
- Full best-run transcript: `courses/prompt-engineering/logs/run_018/transcript.txt`
- Full 5-section writeup incl. the complete dialog: `docs/final_submission.md`
- Change history: `CHANGELOG.md`
