---
name: memory
description: Permission-layered memory system with identity, working, archive, and candidate memory.
always: true
---

# Memory

## Structure

- `identity/SOUL.md` — Stable assistant principles and boundaries. Do not auto-edit from normal task flow.
- `identity/USER_RULES.md` — Durable user instructions and stable workflow preferences.
- `identity/USER_PROFILE.md` — Durable user background and stable preferences.
- `working/CURRENT.md` — Active handoff notes and short-lived working state.
- `archive/history.jsonl` — Append-only event archive. Prefer the built-in `grep` tool to search it.
- `archive/reflections.jsonl` — Dream and heartbeat reflection notes.
- `candidate/observations.jsonl` — Candidate observations and promotion proposals awaiting review.

## Search Past Events

`archive/history.jsonl`, `archive/reflections.jsonl`, and `candidate/observations.jsonl` are JSONL files.

- For broad searches, start with `grep(..., path="archive", glob="*.jsonl", output_mode="count")` or `path="candidate"`
- Use `output_mode="content"` plus `context_before` / `context_after` when you need the exact matching lines
- Use `fixed_strings=true` for literal timestamps or JSON fragments
- Use `head_limit` / `offset` to page through long histories
- Use `exec` only as a last-resort fallback when the built-in search cannot express what you need

Examples (replace `keyword`):
- `grep(pattern="keyword", path="archive/history.jsonl", case_insensitive=true)`
- `grep(pattern="prefers concise", path="candidate/observations.jsonl", case_insensitive=true)`
- `grep(pattern="keyword", path="archive", glob="*.jsonl", output_mode="count", case_insensitive=true)`
- `grep(pattern="explicit_user_statement", path="candidate/observations.jsonl", output_mode="content")`

## Important

- Default writes belong in `working/CURRENT.md`, `archive/reflections.jsonl`, or `candidate/observations.jsonl`.
- Do not directly edit `identity/*` unless the task is explicitly about reviewing or promoting memory.
- `archive/*` and `candidate/*` are search/review stores, not always-injected prompt memory.
- Users can view Dream's activity with the `/dream-log` command.
