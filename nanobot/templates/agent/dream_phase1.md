Compare the new archive/history batch against the current layered memory state.

Output one line per finding:
[WORKING] short-lived active state that belongs in working/CURRENT.md
[REFLECTION] durable archive note for archive/reflections.jsonl
[OBSERVATION] JSON object for candidate/observations.jsonl
[PROMOTION] JSON object for candidate/observations.jsonl with status="candidate" and a promotion_target
[SKILL] kebab-case-name: one-line description of the reusable pattern

Files and roles:
- identity/SOUL.md = stable assistant principles, tone, boundaries
- identity/USER_RULES.md = explicit durable user instructions
- identity/USER_PROFILE.md = durable user background and stable preferences
- working/CURRENT.md = active handoff, current focus, recent state
- archive/reflections.jsonl = durable reflection notes and distilled archive observations
- candidate/observations.jsonl = unverified or promotion-ready observations waiting for review
Rules:
- Default to [WORKING], [REFLECTION], or [OBSERVATION]. Do not directly mutate identity files.
- Only emit [PROMOTION] when the evidence is explicit user instruction or clearly repeated across sessions.
- For [OBSERVATION] and [PROMOTION], output a compact JSON object with at least:
  {"type","source","confidence","evidence_count","status","promotion_target","content"}
- Keep facts atomic: "prefers concise replies" not "talked about writing style"
- Prefer user-explicit instructions over inferred preferences
- Do not add transient weather, temporary status, or conversational filler

Skill discovery — emit [SKILL] when ALL are true:
- A repeatable workflow appeared 2+ times
- It has clear steps
- It is substantial enough to deserve a reusable skill

[SKIP] if nothing needs updating.
