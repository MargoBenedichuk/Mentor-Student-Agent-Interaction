# TODO (Role 4 — Orchestration Engineer) — build step 1-2
#
# Main loop: two mirrored message lists, not one shared chat.
#   mentor_ctx:  mentor = "assistant", student = "user"
#   student_ctx: student = "assistant", mentor = "user"
#
# Each turn: model call (gpt-4o-mini) -> handle tool call if any -> append
# the result to both contexts -> write combined transcript.
#
# On advance_decision(PASS) -> increment current_lesson.
# On should_bluff=true (per bluff_schedule.json) -> inject a private directive
# into student_ctx only.
#
# CLI: python relay.py --course prompt-engineering --student default
# Output: courses/<course>/logs/run_NNN/{transcript.txt, memory_snapshot.json, meta.json}
