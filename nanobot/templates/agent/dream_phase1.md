Compare the new structured Dream inputs against the current layered memory state.

The structured Dream inputs are the primary evidence source. Do not reconstruct current short-term state from `working/CURRENT.md` or `archive/history.jsonl`.

Map:
- authoritative input = committed structured turn data
- `working/CURRENT.md` = mirror only
- `candidate/observations.jsonl` = staging area for promotion candidates
- `identity/*` = protected long-term memory; promotion goes through `Promoter`

Output one line per finding:
[WORKING] short-lived mirror or handoff state that belongs in working/CURRENT.md
[REFLECTION] durable archive note for archive/reflections.jsonl
[OBSERVATION] JSON object for candidate/observations.jsonl
[PROMOTION] JSON object for candidate/observations.jsonl with status="candidate" and a promotion_target
[SKILL] kebab-case-name: one-line description of the reusable pattern

Files and roles:
- identity/SOUL.md = stable assistant principles, tone, boundaries
- identity/USER_RULES.md = explicit durable user instructions
- identity/USER_PROFILE.md = durable user background and stable preferences
- working/CURRENT.md = human-readable mirror and handoff summary, not a source of truth
- archive/reflections.jsonl = durable reflection notes and distilled archive observations
- candidate/observations.jsonl = unverified or promotion-ready observations waiting for review

Rules:
- Use the structured Dream inputs as the source of truth for the processed turn.
- Treat `working/CURRENT.md` as a mirror-only output; do not infer missing facts from it.
- Default to [WORKING], [REFLECTION], or [OBSERVATION]. Do not directly mutate identity files.
- Only emit [PROMOTION] when the evidence is explicit user instruction or clearly repeated across sessions.
- For [OBSERVATION] and [PROMOTION], output a compact JSON object with at least:
  {"type","source","confidence","evidence_count","status","promotion_target","content"}
- Keep facts atomic: "prefers concise replies" not "talked about writing style"
- Prefer user-explicit instructions over inferred preferences
- Do not add transient weather, temporary status, or conversational filler

Skill discovery - emit [SKILL] when ALL are true:
- A repeatable workflow appeared 2+ times
- It has clear steps
- It is substantial enough to deserve a reusable skill

[SKIP] if nothing needs updating.
