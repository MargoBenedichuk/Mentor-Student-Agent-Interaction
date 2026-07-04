# Prototype Research Notes

## Architecture Fix

Student mode is now sampled once per trial, not once per turn. A trial is therefore one stable student identity:

- `honest`
- `lazy_cheater`
- `full_context_cheater`

This avoids unrealistic trials where a cheater answers the first question and an honest student answers the follow-up.

## Detection Findings

Lazy cheating is detectable. When the student only sees the latest mentor question, follow-up probes often expose missing context or wrong assumptions.

Full-context cheating is much harder. When the cheater sees the full visible transcript and the mentor's latest question, it can often generate the exact rough calculations, mistake traces, and self-corrections the mentor asks for.

Several attempted improvements helped lazy-cheater detection but did not materially improve full-context detection:

- Requiring rough scratch-work instead of polished answers.
- Asking for the exact wrong intermediate result the student almost wrote.
- Prompting the mentor to treat reconstructed personal traces as suspicious.
- Making `suspected_cheating` a best binary prediction rather than a court-proof accusation.

The main conclusion so far: without a private practice trace, hidden randomized choice, latency constraint, or other non-visible signal, full-context cheating is close to indistinguishable from an honest answer for this one-lesson residual-calculation task.

For the homework submission, this is useful: the final mentor should assign private micro-practice or ask for consistency across details the student generated before the check. Otherwise, the mentor can safely demand more evidence, but cannot reliably classify full-context LLM cheating.
