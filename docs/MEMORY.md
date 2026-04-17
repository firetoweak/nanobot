# Memory in nanobot

nanobot's memory is built around one idea: not every remembered fact deserves the same privilege.

Some information should shape the assistant's stable identity. Some belongs only to the current working state. Some should be searchable but not injected into every prompt. Some should remain a candidate until it earns promotion.

That is why nanobot uses a permission-layered memory system.

## The Design

nanobot separates memory into layers:

- `session.messages` holds the live short-term conversation.
- `identity/` holds stable assistant identity and durable user context that is allowed to live in the prompt.
- `working/CURRENT.md` holds active handoff notes and short-lived working state.
- `archive/` holds machine-friendly history and reflections for search and review.
- `candidate/observations.jsonl` holds candidate observations and promotion proposals that are not yet trusted enough for identity.
- `GitStore` records changes to the primary prompt memory files so they can be inspected and restored.

This keeps the system light in the moment, but durable over time without letting every summary become permanent identity.

## The Files

```text
workspace/
├── identity/
│   ├── SOUL.md              # Stable assistant principles, tone, and boundaries
│   ├── USER_RULES.md        # Durable user instructions and workflow constraints
│   └── USER_PROFILE.md      # Durable user background and stable preferences
├── working/
│   └── CURRENT.md           # Active handoff and short-lived working context
├── archive/
│   ├── history.jsonl        # Append-only summarized history
│   ├── reflections.jsonl    # Dream and heartbeat reflection notes
│   ├── .cursor              # Consolidator write cursor
│   └── .dream_cursor        # Dream consumption cursor
├── candidate/
│   └── observations.jsonl   # Candidate observations awaiting review or promotion
```

## Prompt Injection Boundary

Only the following memory is injected into the core system prompt by default:

- `identity/SOUL.md`
- `identity/USER_RULES.md`
- `identity/USER_PROFILE.md`
- `working/CURRENT.md`

The following are **not** prompt-default memory sources:

- `archive/history.jsonl`
- `archive/reflections.jsonl`
- `candidate/observations.jsonl`

Those stores exist for search, review, and promotion workflows.

## The Flow

Memory now moves through four stages rather than one undifferentiated long-term store.

### Stage 1: Consolidator

When a conversation grows large enough to pressure the context window, nanobot summarizes the oldest safe slice and appends it to `archive/history.jsonl`.

This file is:

- append-only
- cursor-based
- optimized for machine consumption first, human inspection second

Each line is a JSON object:

```json
{"cursor": 42, "timestamp": "2026-04-03 00:02", "content": "- User prefers dark mode\n- Decided to use PostgreSQL"}
```

### Stage 2: Dream

`Dream` is the slower reflective layer. It runs on a schedule by default and can also be triggered manually.

Dream reads:

- new entries from `archive/history.jsonl`
- the current layered memory state

Dream does **not** directly rewrite identity by default. Its normal write targets are:

- `working/CURRENT.md`
- `archive/reflections.jsonl`
- `candidate/observations.jsonl`

This keeps reflection and observation separate from authority.

### Stage 3: Promoter

`Promoter` is the privilege boundary between candidate memory and identity memory.

It reviews `candidate/observations.jsonl` and decides whether an observation should remain a candidate, be rejected, or be promoted into:

- `identity/SOUL.md`
- `identity/USER_RULES.md`
- `identity/USER_PROFILE.md`

The first implementation favors hard rules, such as:

- explicit user statements
- repeated evidence across sessions

This prevents a single bad Dream abstraction from immediately mutating long-lived prompt identity.

### Stage 4: Heartbeat and Background Tasks

Heartbeat jobs may write to tightly limited memory targets such as `working/CURRENT.md` and `archive/reflections.jsonl`.

They do not receive broad write authority over the whole memory tree.

## Why Layered Memory

The old single-bucket approach was simple, but it created a dangerous privilege pattern: a summary extracted from history could become stable identity too easily.

The layered design fixes that by separating:

- identity that is allowed to shape future behavior
- working state that should expire
- archive that should be searchable but not always injected
- candidates that must earn promotion

In short: memory pollution is often a privilege-escalation problem, not just a factual-error problem.

## Searching Past Events

Use the JSONL stores for historical lookup:

- `archive/history.jsonl`
- `archive/reflections.jsonl`
- `candidate/observations.jsonl`

Typical searches:

```bash
# Search summarized history
rg -i "keyword" archive/history.jsonl

# Search candidate observations
rg -i "prefers concise" candidate/observations.jsonl

# Count matches across archive JSONL files
rg -i --glob "*.jsonl" "keyword" archive
```

## Commands

Memory is not hidden behind the curtain. Users can inspect and guide it.

| Command | What it does |
|---------|--------------|
| `/dream` | Run Dream immediately |
| `/dream-log` | Show the latest Dream memory change |
| `/dream-log <sha>` | Show a specific Dream change |
| `/dream-restore` | List recent Dream memory versions |
| `/dream-restore <sha>` | Restore memory to the state before a specific change |

These commands exist because automatic memory is powerful, but users should retain the right to inspect, understand, and restore it.

## Versioned Memory

`GitStore` tracks the primary prompt memory files:

- `identity/SOUL.md`
- `identity/USER_RULES.md`
- `identity/USER_PROFILE.md`
- `working/CURRENT.md`

This makes prompt-critical memory auditable:

- you can inspect what changed
- you can compare versions
- you can restore a previous state

## Configuration

Dream is configured under `agents.defaults.dream`:

```json
{
  "agents": {
    "defaults": {
      "dream": {
        "intervalH": 2,
        "modelOverride": null,
        "maxBatchSize": 20,
        "maxIterations": 10
      }
    }
  }
}
```

| Field | Meaning |
|-------|---------|
| `intervalH` | How often Dream runs, in hours |
| `modelOverride` | Optional Dream-specific model override |
| `maxBatchSize` | How many history entries Dream processes per run |
| `maxIterations` | The tool budget for Dream's editing phase |

In practical terms:

- `modelOverride: null` means Dream uses the same model as the main agent.
- `maxBatchSize` controls how many new `archive/history.jsonl` entries Dream consumes in one run.
- `maxIterations` limits how many read/edit steps Dream can take while updating working, archive, and candidate outputs.
- `intervalH` is the normal way to configure Dream. Internally it runs as an `every` schedule, not as a cron expression.

## In Practice

What this means in daily use is simple:

- conversations can stay fast without carrying infinite context
- durable facts become clearer without every observation becoming identity
- users can inspect and restore prompt-critical memory

Memory should not feel like a dump. It should feel like continuity with permissions.
