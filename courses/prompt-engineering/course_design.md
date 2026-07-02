# Course Design — Prompt Engineering

> Owner: Role 1 (Course Designer). The table below is the agreed draft from `PLAN.md` — a starting point, not a blank page.
> Task: flesh out each lesson (skill explanation → verification question → transfer scenario) and keep it in sync with `answer_key.json` / `bluff_schedule.json`.

## Lesson table

| Lesson | Skill | Canary fact | Transfer question | Bluff lesson |
|------|-------|-------------|-----------------|-----------|
| 1 | Clear instruction | "1-task-1-verb" rule (our term) | "Rewrite this vague prompt for a different task" | — |
| 2 | Role assignment | "Role-before-task" (our order) | "Assign a role that improves this broken prompt — explain why" | — |
| 3 | Few-shot examples | "Contrast rule" (example + counter-example required) | "Add 2 examples, explain what each teaches the model" | **YES** |
| 4 | Chain-of-thought | "Chain from data, not from conclusion" (our principle) | "Rewrite the prompt for step-by-step reasoning on a new task" | — |
| 5 | Format control | "Template-before-instruction" (our order) | "A prompt forcing JSON with specific fields — show the template" | — |
| 6 | Constraints | "3-negatives-max" (our rule) | "Add constraints closing the top-3 failure modes of this prompt" | **YES** |
| 7 | Iterative refinement | "Diagnose-then-version" (our v1→v2 format) | "Diagnose a broken prompt, show v1 and v2" | — |
| 8 | Context injection | "Context-in-brackets" (our syntax) | "Embed context into the template for a case I'll give you" | — |
| 9 | Persona design | "Voice + prohibition + behavior" (our triad) | "Write a tutor system prompt with verification behavior" | **YES** |
| 10 | Meta-prompting | "Prompt-generating-prompts" (our term) | "Write a meta-prompt that generates L1 prompts for a new task" | — |

## TODO for Role 1

For each lesson, write out a subsection with:
- **Explanation** — what the mentor teaches (2-3 paragraphs, anchored on the canary fact as a reference term)
- **Verification question** — checks understanding of the definition (not application)
- **Transfer scenario** — the full task wording from the table + the expected signs of genuine application (what an honest answer should contain)

Expected application points per lesson go into `answer_key.json` (private, the student never sees it).
