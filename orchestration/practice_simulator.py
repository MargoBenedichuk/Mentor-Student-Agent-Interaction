"""Deterministic, seeded simulator of practice outcomes.

Given a lesson id and a seed it returns a concrete practice result *with
friction* (never a clean success) — the kind of specific, slightly-messy detail
an honest student can recount but a bluffer cannot invent convincingly. The
outcomes are hand-authored to line up with each lesson's canary fact in
`answer_key.json`, so an honest recollection matches the mentor's private key
and an invented one drifts.

Same (lesson_id, seed) always yields the same outcome.
"""
import random

# Per-lesson practice scenarios. Each variant is one plausible practice session.
_BANK = {
    "1": [
        {"attempted": "rewrote the messy 'look at this email and fix it up and check the tone and redo the subject' prompt",
         "concrete_detail": "split it into 3 single-verb prompts: 'Correct the grammar', 'Assess whether the tone is too casual', 'Rewrite the subject line'",
         "friction": "my first split still had 'fix it up', which isn't a real verb — had to replace it with 'Correct the grammar'",
         "outcome": "worked once every instruction had exactly one imperative verb"},
        {"attempted": "applied 1-task-1-verb to a 'summarize this and also translate it' prompt",
         "concrete_detail": "turned it into a 2-step numbered list, one verb per step (1. Summarize 2. Translate the summary)",
         "friction": "wasn't sure whether to use two separate prompts or one numbered list; went with the list",
         "outcome": "the model stopped dropping the translation half"},
    ],
    "2": [
        {"attempted": "applied Role-before-task to 'Explain the difference between REST and GraphQL'",
         "concrete_detail": "prepended 'You are a backend architect explaining trade-offs to a junior developer' before the task sentence",
         "friction": "I first tacked the role on at the end and the answer stayed generic; moving it to the front changed the depth",
         "outcome": "front-loaded role gave concrete trade-offs instead of a textbook definition"},
        {"attempted": "used Role-before-task on a code-review prompt",
         "concrete_detail": "put 'You are a senior security reviewer' first, task second",
         "friction": "my initial role was just 'an expert', too generic to help until I made it specific",
         "outcome": "specific front role sharpened the review focus"},
    ],
    "3": [  # bluff lesson by default — normally not simulated
        {"attempted": "tried the Contrast rule on a tagline prompt",
         "concrete_detail": "one good tagline example + one counter-example marked wrong, with a one-line why",
         "friction": "kept wanting to add a second good example instead of a counter-example",
         "outcome": "counter-example made the boundary explicit"},
    ],
    "4": [
        {"attempted": "rewrote 'Is this business plan viable?' for Chain-from-data",
         "concrete_detail": "added a 'Step 0: list the given facts' block and a rule 'do not state the verdict until the last line'",
         "friction": "even with 'think step by step' the model wrote the verdict first; only the explicit last-line rule stopped it",
         "outcome": "reasoning derived from the facts, verdict landed last"},
        {"attempted": "practised Chain-from-data on a debugging prompt",
         "concrete_detail": "enumerated the observed symptoms as step 0 before any hypothesis",
         "friction": "hard to resist letting the model guess the bug up front",
         "outcome": "fact-first ordering cut the jumped-to-conclusion errors"},
    ],
    "5": [
        {"attempted": "built an invoice extractor with Template-before-instruction",
         "concrete_detail": "put the JSON skeleton {\"name\": \"str\", \"date\": \"YYYY-MM-DD\", \"amount\": \"float\"} first, explanation after",
         "friction": "when I described the fields before the skeleton, the model returned prose; flipping the order fixed it",
         "outcome": "skeleton-first gave clean parseable JSON"},
        {"attempted": "used Template-before-instruction for a table output",
         "concrete_detail": "showed the empty header row with type hints before explaining each column",
         "friction": "forgot inline type hints on the first pass, got string dates",
         "outcome": "hints in the skeleton fixed the date typing"},
    ],
    "6": [  # bluff lesson by default
        {"attempted": "tried 3-negatives-max on a support-reply prompt",
         "concrete_detail": "kept 3 negatives tied to the top failure modes, reframed the 4th as a positive",
         "friction": "had five 'do nots' at first and had to prioritise",
         "outcome": "three targeted negatives read cleaner"},
    ],
    "7": [
        {"attempted": "practised Diagnose-then-version on 'Summarize this article' that came back too long and casual",
         "concrete_detail": "diagnosis: v1 set no length or tone constraint; v2 added '<=3 sentences, professional tone' and I kept v1 next to it",
         "friction": "my instinct was to rewrite the whole prompt; the diagnosis forced me to change only the two things at fault",
         "outcome": "the v1/v2 diff made it obvious which change fixed what"},
    ],
    "8": [
        {"attempted": "applied Context-in-brackets to a 'reply referencing their history' support prompt",
         "concrete_detail": "wrapped the last 3 tickets in [CONTEXT: prior support tickets, may be incomplete] ... [/CONTEXT], placed between the role and the task",
         "friction": "the first time I left off the label and the model treated a ticket line as an instruction",
         "outcome": "labelled, delimited block stopped the context/instruction confusion"},
    ],
    "9": [  # bluff lesson by default
        {"attempted": "built a tutor persona with the Voice+prohibition+behavior triad",
         "concrete_detail": "voice = warm and plain; prohibition = never give the final answer outright; behavior = on a vague answer, ask one clarifying question first",
         "friction": "I kept forgetting the behavior leg and stopping at voice+prohibition",
         "outcome": "the concrete behavior rule is what made the persona act, not just sound, right"},
    ],
    "10": [
        {"attempted": "wrote a meta-prompt with Prompt-generating-prompts",
         "concrete_detail": "it takes (task_type, audience, one_constraint) and emits a single-verb L1 prompt; example output obeyed 1-task-1-verb",
         "friction": "my first version output a template with blanks instead of a ready-to-use prompt; had to force a fully filled example",
         "outcome": "validated it by running its output through the 1-task-1-verb check"},
    ],
}


def simulate_practice(lesson_id, seed: int = 0) -> dict:
    """Return a concrete, friction-bearing practice outcome for a lesson."""
    lid = str(lesson_id)
    variants = _BANK.get(lid)
    if not variants:  # generic fallback for lessons without hand-authored content
        variants = [{
            "attempted": f"practised the lesson {lid} skill on a small example",
            "concrete_detail": "worked one concrete case end to end",
            "friction": "the first attempt missed part of the rule and needed a fix",
            "outcome": "got it right after the correction",
        }]
    rng = random.Random(f"{lid}-{seed}")
    chosen = rng.choice(variants)
    return {"lesson": lid, **chosen}
