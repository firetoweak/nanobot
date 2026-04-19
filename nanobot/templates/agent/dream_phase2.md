Update layered memory files based on the analysis below.
- [WORKING] entries: update `working/CURRENT.md`
- [REFLECTION] entries: append JSON lines to `archive/reflections.jsonl`
- [OBSERVATION] entries: append JSON lines to `candidate/observations.jsonl`
- [PROMOTION] entries: append JSON lines to `candidate/observations.jsonl`
- [SKILL] entries: create a new skill under `skills/<name>/SKILL.md` using `write_file`

## File paths (relative to workspace root)
- working/CURRENT.md
- archive/reflections.jsonl
- candidate/observations.jsonl
- skills/<name>/SKILL.md (for [SKILL] entries only)

`working/CURRENT.md` is a mirror/handoff output. Keep it concise and human-readable. Do not treat it as the authoritative runtime state.

Do NOT guess paths.
Do NOT edit `identity/*`.

## Editing rules
- Edit directly - file contents provided below, no read_file needed
- Use exact text as old_text, include surrounding blank lines for unique match
- Batch changes to the same file into one edit_file call
- For JSONL appends, use `edit_file` with `old_text=""` when the file is empty, otherwise replace the full current content with the old content plus the new lines appended
- Surgical edits only - never rewrite entire files
- If nothing to update, stop without calling tools

## Skill creation rules (for [SKILL] entries)
- Use write_file to create skills/<name>/SKILL.md
- Before writing, read_file `{{ skill_creator_path }}` for format reference (frontmatter structure, naming conventions, quality standards)
- Dedup check: read existing skills listed below to verify the new skill is not functionally redundant. Skip creation if an existing skill already covers the same workflow.
- Include YAML frontmatter with name and description fields
- Keep SKILL.md under 2000 words - concise and actionable
- Include: when to use, steps, output format, at least one example
- Do NOT overwrite existing skills - skip if the skill directory already exists
- Reference specific tools the agent has access to (read_file, write_file, exec, web_search, etc.)
- Skills are instruction sets, not code - do not include implementation code

## Quality
- Every line must carry standalone value
- Concise bullets under clear headers
- When reducing (not deleting): keep essential facts, drop verbose details
- If uncertain whether to delete, keep but add "(verify currency)"
