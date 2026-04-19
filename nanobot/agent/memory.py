"""Memory system: pure file I/O store, lightweight Consolidator, and Dream processor."""

from __future__ import annotations

import asyncio
import json
import weakref
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from uuid import uuid4

from loguru import logger

from nanobot.utils.prompt_templates import render_template
from nanobot.utils.helpers import ensure_dir, estimate_message_tokens, estimate_prompt_tokens_chain, strip_think

from nanobot.agent.runner import AgentRunSpec, AgentRunner
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.session.state import is_completed_manifest
from nanobot.utils.gitstore import GitStore, TRACKED_WORKSPACE_FILES

if TYPE_CHECKING:
    from nanobot.providers.base import LLMProvider
    from nanobot.session.manager import Session, SessionManager


# ---------------------------------------------------------------------------
# MemoryStore — pure file I/O layer
# ---------------------------------------------------------------------------

class MemoryStore:
    """Pure file I/O for the layered memory files."""

    _DEFAULT_MAX_HISTORY = 1000

    def __init__(self, workspace: Path, max_history_entries: int = _DEFAULT_MAX_HISTORY):
        self.workspace = workspace
        self.max_history_entries = max_history_entries
        self.identity_dir = ensure_dir(workspace / "identity")
        self.working_dir = ensure_dir(workspace / "working")
        self.archive_dir = ensure_dir(workspace / "archive")
        self.candidate_dir = ensure_dir(workspace / "candidate")
        self.state_dir = ensure_dir(workspace / ".nanobot" / "state")

        self.soul_file = self.identity_dir / "SOUL.md"
        self.user_rules_file = self.identity_dir / "USER_RULES.md"
        self.user_profile_file = self.identity_dir / "USER_PROFILE.md"
        self.current_file = self.working_dir / "CURRENT.md"
        self.history_file = self.archive_dir / "history.jsonl"
        self.reflections_file = self.archive_dir / "reflections.jsonl"
        self.observations_file = self.candidate_dir / "observations.jsonl"

        self._cursor_file = self.archive_dir / ".cursor"
        self._dream_cursor_file = self.archive_dir / ".dream_cursor"
        self._git = GitStore(workspace, tracked_files=TRACKED_WORKSPACE_FILES)

    @property
    def git(self) -> GitStore:
        return self._git

    # -- generic helpers -----------------------------------------------------

    @staticmethod
    def read_file(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    # -- identity / working --------------------------------------------------

    # -- SOUL.md -------------------------------------------------------------

    def read_soul(self) -> str:
        return self.read_file(self.soul_file)

    def write_soul(self, content: str) -> None:
        self.soul_file.write_text(content, encoding="utf-8")

    def read_user_rules(self) -> str:
        return self.read_file(self.user_rules_file)

    def write_user_rules(self, content: str) -> None:
        self.user_rules_file.write_text(content, encoding="utf-8")

    def read_user_profile(self) -> str:
        return self.read_file(self.user_profile_file)

    def write_user_profile(self, content: str) -> None:
        self.user_profile_file.write_text(content, encoding="utf-8")

    def read_current(self) -> str:
        """Read the legacy working mirror view."""
        return self.read_file(self.current_file)

    def write_current(self, content: str) -> None:
        """Write the legacy working mirror view."""
        self.current_file.write_text(content, encoding="utf-8")

    def read_current_mirror(self) -> str:
        """Explicit mirror alias for `working/CURRENT.md`."""
        return self.read_current()

    def write_current_mirror(self, content: str) -> None:
        """Explicit mirror alias for `working/CURRENT.md`."""
        self.write_current(content)

    # -- context injection (used by context.py) ------------------------------

    def get_identity_context(self) -> str:
        parts = []
        if soul := self.read_soul().strip():
            parts.append(f"## identity/SOUL.md\n{soul}")
        if user_rules := self.read_user_rules().strip():
            parts.append(f"## identity/USER_RULES.md\n{user_rules}")
        if user_profile := self.read_user_profile().strip():
            parts.append(f"## identity/USER_PROFILE.md\n{user_profile}")
        return "\n\n".join(parts)

    def get_working_context(self) -> str:
        current = self.read_current().strip()
        return f"## working/CURRENT.md\n{current}" if current else ""

    def get_recent_history_context(self, max_entries: int = 20) -> str:
        entries = self._read_entries()[-max_entries:]
        if not entries:
            return ""
        return "\n".join(f"- [{e['timestamp']}] {e['content']}" for e in entries)

    def get_recent_reflections_context(self, max_entries: int = 10) -> str:
        entries = self._read_jsonl(self.reflections_file)[-max_entries:]
        if not entries:
            return ""
        return "\n".join(
            f"- [{e.get('timestamp', '?')}] {e.get('content', '')}".rstrip()
            for e in entries
        )

    def get_candidate_context(self, max_entries: int = 10) -> str:
        entries = self.read_candidate_observations()[-max_entries:]
        if not entries:
            return ""
        lines = []
        for entry in entries:
            lines.append(
                f"- [{entry.get('timestamp', '?')}] "
                f"{entry.get('type', 'observation')} / {entry.get('status', 'candidate')}: "
                f"{entry.get('content', '')}"
            )
        return "\n".join(lines)

    # -- history.jsonl — append-only, JSONL format ---------------------------

    def append_history(self, entry: str) -> int:
        """Append *entry* to history.jsonl and return its auto-incrementing cursor."""
        cursor = self._next_cursor()
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        record = {"cursor": cursor, "timestamp": ts, "content": strip_think(entry.rstrip()) or entry.rstrip()}
        with open(self.history_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._cursor_file.write_text(str(cursor), encoding="utf-8")
        return cursor

    def _next_cursor(self) -> int:
        """Read the current cursor counter and return next value."""
        if self._cursor_file.exists():
            try:
                return int(self._cursor_file.read_text(encoding="utf-8").strip()) + 1
            except (ValueError, OSError):
                pass
        # Fallback: read last line's cursor from the JSONL file.
        last = self._read_last_entry()
        if last:
            return last["cursor"] + 1
        return 1

    def read_unprocessed_history(self, since_cursor: int) -> list[dict[str, Any]]:
        """Return history entries with cursor > *since_cursor*."""
        return [e for e in self._read_entries() if e["cursor"] > since_cursor]

    def compact_history(self) -> None:
        """Drop oldest entries if the file exceeds *max_history_entries*."""
        if self.max_history_entries <= 0:
            return
        entries = self._read_entries()
        if len(entries) <= self.max_history_entries:
            return
        kept = entries[-self.max_history_entries:]
        self._write_entries(kept)

    # -- reflection / candidate helpers -------------------------------------

    def append_reflection(
        self,
        content: str,
        *,
        reflection_type: str = "archive_note",
        source: str = "dream",
    ) -> dict[str, Any]:
        record = {
            "id": f"ref_{uuid4().hex[:12]}",
            "timestamp": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "type": reflection_type,
            "source": source,
            "content": strip_think(content.rstrip()) or content.rstrip(),
        }
        self._append_jsonl(self.reflections_file, record)
        return record

    def append_candidate_observation(self, observation: dict[str, Any]) -> dict[str, Any]:
        record = {
            "id": observation.get("id") or f"obs_{uuid4().hex[:12]}",
            "timestamp": observation.get("timestamp")
            or datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "type": observation.get("type", "observation"),
            "scope": observation.get("scope", ""),
            "content": observation.get("content", ""),
            "source": observation.get("source", "dream_inference"),
            "source_ref": observation.get("source_ref", []),
            "confidence": observation.get("confidence", 0.5),
            "evidence_count": observation.get("evidence_count", 1),
            "status": observation.get("status", "candidate"),
            "promotion_target": observation.get("promotion_target", "identity.USER_PROFILE"),
            "reversible": observation.get("reversible", True),
            "risk": observation.get("risk", "low"),
        }
        self._append_jsonl(self.observations_file, record)
        return record

    def read_candidate_observations(self) -> list[dict[str, Any]]:
        return self._read_jsonl(self.observations_file)

    def write_candidate_observations(self, entries: list[dict[str, Any]]) -> None:
        self._write_jsonl(self.observations_file, entries)

    # -- JSONL helpers -------------------------------------------------------

    def _append_jsonl(self, path: Path, record: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _read_jsonl(self, path: Path) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except FileNotFoundError:
            pass
        return entries

    def _write_jsonl(self, path: Path, entries: list[dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _read_entries(self) -> list[dict[str, Any]]:
        """Read all entries from history.jsonl."""
        return self._read_jsonl(self.history_file)

    def _read_last_entry(self) -> dict[str, Any] | None:
        """Read the last entry from the JSONL file efficiently."""
        try:
            with open(self.history_file, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                if size == 0:
                    return None
                read_size = min(size, 4096)
                f.seek(size - read_size)
                data = f.read().decode("utf-8")
                lines = [l for l in data.split("\n") if l.strip()]
                if not lines:
                    return None
                return json.loads(lines[-1])
        except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
            return None

    def _write_entries(self, entries: list[dict[str, Any]]) -> None:
        """Overwrite history.jsonl with the given entries."""
        self._write_jsonl(self.history_file, entries)

    # -- dream cursor --------------------------------------------------------

    def get_last_dream_cursor(self) -> int:
        if self._dream_cursor_file.exists():
            try:
                return int(self._dream_cursor_file.read_text(encoding="utf-8").strip())
            except (ValueError, OSError):
                pass
        sessions_root = self.state_dir / "sessions"
        total = 0
        if sessions_root.exists():
            for session_dir in sessions_root.iterdir():
                cursor_file = session_dir / "indexes" / "dream-cursor.json"
                if not cursor_file.exists():
                    continue
                try:
                    data = json.loads(cursor_file.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                processed_keys = data.get("processed_keys") or []
                if isinstance(processed_keys, list):
                    total += len([key for key in processed_keys if isinstance(key, str) and key])
        if total:
            return total
        return 0

    def set_last_dream_cursor(self, cursor: int) -> None:
        self._dream_cursor_file.write_text(str(cursor), encoding="utf-8")

    # -- message formatting utility ------------------------------------------

    @staticmethod
    def _format_messages(messages: list[dict]) -> str:
        lines = []
        for message in messages:
            if not message.get("content"):
                continue
            tools = f" [tools: {', '.join(message['tools_used'])}]" if message.get("tools_used") else ""
            lines.append(
                f"[{message.get('timestamp', '?')[:16]}] {message['role'].upper()}{tools}: {message['content']}"
            )
        return "\n".join(lines)

    def raw_archive(self, messages: list[dict]) -> None:
        """Fallback: dump raw messages to history.jsonl without LLM summarization."""
        self.append_history(
            f"[RAW] {len(messages)} messages\n"
            f"{self._format_messages(messages)}"
        )
        logger.warning(
            "Memory consolidation degraded: raw-archived {} messages", len(messages)
        )



# ---------------------------------------------------------------------------
# Consolidator — lightweight token-budget triggered consolidation
# ---------------------------------------------------------------------------


class Consolidator:
    """Lightweight consolidation: summarizes evicted messages into history.jsonl."""

    _MAX_CONSOLIDATION_ROUNDS = 5
    _MAX_CHUNK_MESSAGES = 60  # hard cap per consolidation round

    _SAFETY_BUFFER = 1024  # extra headroom for tokenizer estimation drift

    def __init__(
        self,
        store: MemoryStore,
        provider: LLMProvider,
        model: str,
        sessions: SessionManager,
        context_window_tokens: int,
        build_messages: Callable[..., list[dict[str, Any]]],
        get_tool_definitions: Callable[[], list[dict[str, Any]]],
        max_completion_tokens: int = 4096,
    ):
        self.store = store
        self.provider = provider
        self.model = model
        self.sessions = sessions
        self.context_window_tokens = context_window_tokens
        self.max_completion_tokens = max_completion_tokens
        self._build_messages = build_messages
        self._get_tool_definitions = get_tool_definitions
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )

    def get_lock(self, session_key: str) -> asyncio.Lock:
        """Return the shared consolidation lock for one session."""
        return self._locks.setdefault(session_key, asyncio.Lock())

    def pick_consolidation_boundary(
        self,
        session: Session,
        tokens_to_remove: int,
    ) -> tuple[int, int] | None:
        """Pick a user-turn boundary that removes enough old prompt tokens."""
        start = session.last_consolidated
        if start >= len(session.messages) or tokens_to_remove <= 0:
            return None

        removed_tokens = 0
        last_boundary: tuple[int, int] | None = None
        for idx in range(start, len(session.messages)):
            message = session.messages[idx]
            if idx > start and message.get("role") == "user":
                last_boundary = (idx, removed_tokens)
                if removed_tokens >= tokens_to_remove:
                    return last_boundary
            removed_tokens += estimate_message_tokens(message)

        return last_boundary

    def _cap_consolidation_boundary(
        self,
        session: Session,
        end_idx: int,
    ) -> int | None:
        """Clamp the chunk size without breaking the user-turn boundary."""
        start = session.last_consolidated
        if end_idx - start <= self._MAX_CHUNK_MESSAGES:
            return end_idx

        capped_end = start + self._MAX_CHUNK_MESSAGES
        for idx in range(capped_end, start, -1):
            if session.messages[idx].get("role") == "user":
                return idx
        return None

    def estimate_session_prompt_tokens(self, session: Session) -> tuple[int, str]:
        """Estimate current prompt size for the normal session history view."""
        recent_raw_turns = session.get_history(max_messages=0)
        channel, chat_id = (session.key.split(":", 1) if ":" in session.key else (None, None))
        working_set = self.sessions.load_latest_working_set(session.key)
        probe_messages = self._build_messages(
            working_set=working_set,
            recent_raw_turns=recent_raw_turns,
            selected_capsules=[],
            selected_artifacts=[],
            current_message="[token-probe]",
            channel=channel,
            chat_id=chat_id,
        )
        return estimate_prompt_tokens_chain(
            self.provider,
            self.model,
            probe_messages,
            self._get_tool_definitions(),
        )

    async def archive(self, messages: list[dict]) -> str | None:
        """Summarize messages via LLM and append to history.jsonl.

        Returns the summary text on success, None if nothing to archive.
        """
        if not messages:
            return None
        try:
            formatted = MemoryStore._format_messages(messages)
            response = await self.provider.chat_with_retry(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": render_template(
                            "agent/consolidator_archive.md",
                            strip=True,
                        ),
                    },
                    {"role": "user", "content": formatted},
                ],
                tools=None,
                tool_choice=None,
            )
            summary = response.content or "[no summary]"
            self.store.append_history(summary)
            return summary
        except Exception:
            logger.warning("Consolidation LLM call failed, raw-dumping to history")
            self.store.raw_archive(messages)
            return None

    async def maybe_consolidate_by_tokens(self, session: Session) -> None:
        """Loop: archive old messages until prompt fits within safe budget.

        The budget reserves space for completion tokens and a safety buffer
        so the LLM request never exceeds the context window.
        """
        if not session.messages or self.context_window_tokens <= 0:
            return

        lock = self.get_lock(session.key)
        async with lock:
            budget = self.context_window_tokens - self.max_completion_tokens - self._SAFETY_BUFFER
            target = budget // 2
            try:
                estimated, source = self.estimate_session_prompt_tokens(session)
            except Exception:
                logger.exception("Token estimation failed for {}", session.key)
                estimated, source = 0, "error"
            if estimated <= 0:
                return
            if estimated < budget:
                unconsolidated_count = len(session.messages) - session.last_consolidated
                logger.debug(
                    "Token consolidation idle {}: {}/{} via {}, msgs={}",
                    session.key,
                    estimated,
                    self.context_window_tokens,
                    source,
                    unconsolidated_count,
                )
                return

            for round_num in range(self._MAX_CONSOLIDATION_ROUNDS):
                if estimated <= target:
                    return

                boundary = self.pick_consolidation_boundary(session, max(1, estimated - target))
                if boundary is None:
                    logger.debug(
                        "Token consolidation: no safe boundary for {} (round {})",
                        session.key,
                        round_num,
                    )
                    return

                end_idx = boundary[0]
                end_idx = self._cap_consolidation_boundary(session, end_idx)
                if end_idx is None:
                    logger.debug(
                        "Token consolidation: no capped boundary for {} (round {})",
                        session.key,
                        round_num,
                    )
                    return

                chunk = session.messages[session.last_consolidated:end_idx]
                if not chunk:
                    return

                logger.info(
                    "Token consolidation round {} for {}: {}/{} via {}, chunk={} msgs",
                    round_num,
                    session.key,
                    estimated,
                    self.context_window_tokens,
                    source,
                    len(chunk),
                )
                if not await self.archive(chunk):
                    return
                session.last_consolidated = end_idx
                self.sessions.save(session)

                try:
                    estimated, source = self.estimate_session_prompt_tokens(session)
                except Exception:
                    logger.exception("Token estimation failed for {}", session.key)
                    estimated, source = 0, "error"
                if estimated <= 0:
                    return


# ---------------------------------------------------------------------------
# Dream — heavyweight cron-scheduled memory consolidation
# ---------------------------------------------------------------------------


class Dream:
    """Process committed turns into longer-term memory updates."""

    def __init__(
        self,
        store: MemoryStore,
        provider: LLMProvider,
        model: str,
        max_batch_size: int = 20,
        max_iterations: int = 10,
        max_tool_result_chars: int = 16_000,
    ):
        self.store = store
        self.provider = provider
        self.model = model
        self.max_batch_size = max_batch_size
        self.max_iterations = max_iterations
        self.max_tool_result_chars = max_tool_result_chars
        self._runner = AgentRunner(provider)
        self._tools = self._build_tools()
        from nanobot.session.manager import SessionManager

        self._sessions = SessionManager(store.workspace)

    # -- tool registry -------------------------------------------------------

    def _build_tools(self) -> ToolRegistry:
        """Build a minimal tool registry for the Dream agent."""
        from nanobot.agent.skills import BUILTIN_SKILLS_DIR
        from nanobot.agent.tools.filesystem import EditFileTool, ReadFileTool, WriteFileTool

        tools = ToolRegistry()
        workspace = self.store.workspace
        writable_targets = [
            self.store.current_file,
            self.store.reflections_file,
            self.store.observations_file,
        ]
        # Allow reading builtin skills for reference during skill creation
        extra_read = [BUILTIN_SKILLS_DIR] if BUILTIN_SKILLS_DIR.exists() else None
        tools.register(ReadFileTool(
            workspace=workspace,
            allowed_dir=workspace,
            extra_allowed_dirs=extra_read,
        ))
        tools.register(EditFileTool(
            workspace=workspace,
            allowed_dir=workspace,
            writable_targets=writable_targets,
        ))
        # write_file resolves relative paths from workspace root, but can only
        # write under skills/ so the prompt can safely use skills/<name>/SKILL.md.
        skills_dir = workspace / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        tools.register(WriteFileTool(workspace=workspace, allowed_dir=skills_dir))
        return tools

    # -- skill listing --------------------------------------------------------

    def _list_existing_skills(self) -> list[str]:
        """List existing skills as 'name — description' for dedup context."""
        import re as _re

        from nanobot.agent.skills import BUILTIN_SKILLS_DIR

        _DESC_RE = _re.compile(r"^description:\s*(.+)$", _re.MULTILINE | _re.IGNORECASE)
        entries: dict[str, str] = {}
        for base in (self.store.workspace / "skills", BUILTIN_SKILLS_DIR):
            if not base.exists():
                continue
            for d in base.iterdir():
                if not d.is_dir():
                    continue
                skill_md = d / "SKILL.md"
                if not skill_md.exists():
                    continue
                # Prefer workspace skills over builtin (same name)
                if d.name in entries and base == BUILTIN_SKILLS_DIR:
                    continue
                content = skill_md.read_text(encoding="utf-8")[:500]
                m = _DESC_RE.search(content)
                desc = m.group(1).strip() if m else "(no description)"
                entries[d.name] = desc
        return [f"{name} — {desc}" for name, desc in sorted(entries.items())]

    @staticmethod
    def _make_idempotency_key(turn_id: str, capsule_id: str, manifest_revision: int) -> str:
        return f"{turn_id}:{capsule_id}:{manifest_revision}"

    def _load_dream_cursor_state(self, session_key: str) -> dict[str, Any]:
        data = self._sessions.read_state_index(session_key, "dream-cursor")
        return data if isinstance(data, dict) else {}

    def _save_dream_cursor_state(self, session_key: str, data: dict[str, Any]) -> None:
        self._sessions.write_state_index(session_key, "dream-cursor", data)

    def _iter_state_session_dirs(self) -> list[Path]:
        root = self.store.state_dir / "sessions"
        if not root.exists():
            return []
        return sorted(path for path in root.iterdir() if path.is_dir())

    @staticmethod
    def _safe_read_json(path: Path) -> dict[str, Any] | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _dream_artifact_digest(artifact: dict[str, Any]) -> dict[str, Any]:
        render = artifact.get("dream_render")
        if isinstance(render, dict):
            return dict(render)
        safe_projection = {
            "artifact_id": artifact.get("artifact_id"),
            "source_type": artifact.get("source_type"),
            "source_input": artifact.get("source_input"),
            "digest": artifact.get("digest"),
            "content_version": artifact.get("content_version"),
            "invalidated_by": list(artifact.get("invalidated_by") or []),
        }
        return {key: value for key, value in safe_projection.items() if value not in (None, "", [])}

    @staticmethod
    def _candidate_signals(
        capsule: dict[str, Any],
        working_set_snapshot: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        signals: list[dict[str, Any]] = []
        if user_goal := capsule.get("user_goal"):
            signals.append({"type": "user_goal", "content": user_goal})
        for decision in capsule.get("decisions") or []:
            signals.append({"type": "decision", "content": decision})
        for outcome in capsule.get("outcomes") or []:
            signals.append({"type": "outcome", "content": outcome})
        for question in capsule.get("open_questions") or []:
            signals.append({"type": "open_question", "content": question})
        if isinstance(working_set_snapshot, dict):
            for goal in working_set_snapshot.get("active_goals") or []:
                signals.append({"type": "active_goal", "content": goal})
            for loop in working_set_snapshot.get("open_loops") or []:
                signals.append({"type": "open_loop", "content": loop})
            if focus := working_set_snapshot.get("last_user_focus"):
                signals.append({"type": "last_user_focus", "content": focus})
        return signals

    def _iter_pending_dream_inputs(self) -> list[dict[str, Any]]:
        pending: list[dict[str, Any]] = []
        for session_dir in self._iter_state_session_dirs():
            turns_dir = session_dir / "turns"
            if not turns_dir.exists():
                continue
            for turn_path in sorted(turns_dir.glob("*.json")):
                turn_state = self._safe_read_json(turn_path)
                if not isinstance(turn_state, dict):
                    continue
                session_key = turn_state.get("session_key")
                turn_id = turn_state.get("turn_id")
                if not isinstance(session_key, str) or not isinstance(turn_id, str):
                    continue
                if turn_state.get("commit_state") != "committed":
                    continue
                commit_ref = turn_state.get("commit_manifest_ref")
                if not isinstance(commit_ref, str) or not commit_ref:
                    continue
                manifest = self._sessions.resolve_ref(session_key, commit_ref)
                if not is_completed_manifest(manifest):
                    continue
                manifest_revision = manifest.get("turn_revision")
                if manifest_revision != turn_state.get("revision"):
                    continue
                capsule_ref = manifest.get("capsule_ref")
                if not isinstance(capsule_ref, str) or not capsule_ref:
                    continue
                capsule = self._sessions.resolve_ref(session_key, capsule_ref)
                if not isinstance(capsule, dict):
                    continue
                capsule_id = capsule.get("capsule_id")
                if not isinstance(capsule_id, str) or not capsule_id:
                    continue
                idempotency_key = self._make_idempotency_key(
                    turn_id,
                    capsule_id,
                    int(manifest_revision),
                )
                cursor_state = self._load_dream_cursor_state(session_key)
                processed_keys = {
                    key
                    for key in cursor_state.get("processed_keys") or []
                    if isinstance(key, str) and key
                }
                if idempotency_key in processed_keys:
                    continue
                working_set_snapshot = None
                working_set_version = manifest.get("working_set_version")
                if isinstance(working_set_version, int):
                    working_set_snapshot = self._sessions.load_working_set(session_key, working_set_version)
                artifact_digests: list[dict[str, Any]] = []
                for artifact_ref in manifest.get("artifact_refs") or []:
                    artifact = self._sessions.resolve_ref(session_key, artifact_ref)
                    if isinstance(artifact, dict):
                        artifact_digests.append(self._dream_artifact_digest(artifact))
                pending.append(
                    {
                        "session_key": session_key,
                        "turn_id": turn_id,
                        "capsule": dict(capsule),
                        "working_set_snapshot": (
                            dict(working_set_snapshot) if isinstance(working_set_snapshot, dict) else None
                        ),
                        "artifact_digests": artifact_digests,
                        "candidate_signals": self._candidate_signals(capsule, working_set_snapshot),
                        "idempotency_key": idempotency_key,
                        "_sort_key": (
                            str(manifest.get("created_at") or capsule.get("created_at") or ""),
                            session_key,
                            turn_id,
                        ),
                    }
                )
        pending.sort(key=lambda item: item["_sort_key"])
        return pending

    @staticmethod
    def _format_dream_inputs(inputs: list[dict[str, Any]]) -> str:
        blocks: list[str] = []
        for idx, item in enumerate(inputs, start=1):
            capsule = item["capsule"]
            working_set = item.get("working_set_snapshot")
            block_lines = [
                f"## Dream Input {idx}",
                f"- Session Key: {item['session_key']}",
                f"- Turn ID: {item['turn_id']}",
                f"- Idempotency Key: {item['idempotency_key']}",
                f"- Capsule ID: {capsule.get('capsule_id')}",
            ]
            if isinstance(working_set, dict):
                block_lines.append(f"- Working Set Version: {working_set.get('version')}")
            block_lines.extend(
                [
                    "",
                    "### Capsule",
                    json.dumps(capsule, ensure_ascii=False, indent=2),
                    "",
                    "### Artifact Digests",
                    json.dumps(item.get("artifact_digests") or [], ensure_ascii=False, indent=2),
                    "",
                    "### Candidate Signals",
                    json.dumps(item.get("candidate_signals") or [], ensure_ascii=False, indent=2),
                ]
            )
            if isinstance(working_set, dict):
                block_lines.extend(
                    [
                        "",
                        "### Working Set Snapshot",
                        json.dumps(working_set, ensure_ascii=False, indent=2),
                    ]
                )
            blocks.append("\n".join(block_lines))
        return "\n\n".join(blocks)

    def _build_file_context(self) -> str:
        current_date = datetime.now().strftime("%Y-%m-%d")
        current_soul = self.store.read_soul() or "(empty)"
        current_user_rules = self.store.read_user_rules() or "(empty)"
        current_user_profile = self.store.read_user_profile() or "(empty)"
        recent_reflections = self.store.get_recent_reflections_context(max_entries=10) or "(empty)"
        current_candidates = self.store.get_candidate_context(max_entries=10) or "(empty)"
        return (
            f"## Current Date\n{current_date}\n\n"
            f"## Current identity/SOUL.md ({len(current_soul)} chars)\n{current_soul}\n\n"
            f"## Current identity/USER_RULES.md ({len(current_user_rules)} chars)\n{current_user_rules}\n\n"
            f"## Current identity/USER_PROFILE.md ({len(current_user_profile)} chars)\n{current_user_profile}\n\n"
            "## working/CURRENT.md Policy\n"
            "Treat working/CURRENT.md as a mirror-only output. Do not use it as a source of truth.\n\n"
            f"## Recent archive/reflections.jsonl\n{recent_reflections}\n\n"
            f"## Recent candidate/observations.jsonl\n{current_candidates}"
        )

    def _mark_processed(self, inputs: list[dict[str, Any]]) -> None:
        by_session: dict[str, list[dict[str, Any]]] = {}
        for item in inputs:
            by_session.setdefault(item["session_key"], []).append(item)
        for session_key, entries in by_session.items():
            cursor_state = self._load_dream_cursor_state(session_key)
            processed_keys = [
                key
                for key in cursor_state.get("processed_keys") or []
                if isinstance(key, str) and key
            ]
            for entry in entries:
                processed_keys.append(entry["idempotency_key"])
            deduped = list(dict.fromkeys(processed_keys))
            last_entry = entries[-1]
            self._save_dream_cursor_state(
                session_key,
                {
                    "processed_keys": deduped,
                    "processed_count": len(deduped),
                    "last_turn_id": last_entry["turn_id"],
                    "last_idempotency_key": last_entry["idempotency_key"],
                    "updated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
                },
            )
        self.store.set_last_dream_cursor(self._count_processed_keys())

    def _count_processed_keys(self) -> int:
        total = 0
        for session_dir in self._iter_state_session_dirs():
            cursor = self._safe_read_json(session_dir / "indexes" / "dream-cursor.json")
            if not isinstance(cursor, dict):
                continue
            processed_keys = cursor.get("processed_keys") or []
            if isinstance(processed_keys, list):
                total += len([key for key in processed_keys if isinstance(key, str) and key])
        return total

    # -- main entry ----------------------------------------------------------

    async def run(self) -> bool:
        """Process committed turn capsules. Returns True if work was done."""
        from nanobot.agent.skills import BUILTIN_SKILLS_DIR

        pending_inputs = self._iter_pending_dream_inputs()
        if not pending_inputs:
            return False

        batch = pending_inputs[: self.max_batch_size]
        logger.info(
            "Dream: processing {} committed turn(s), batch={}",
            len(pending_inputs), len(batch),
        )

        dream_inputs_text = self._format_dream_inputs(batch)
        file_context = self._build_file_context()

        phase1_prompt = f"## Structured Dream Inputs\n{dream_inputs_text}\n\n{file_context}"

        try:
            phase1_response = await self.provider.chat_with_retry(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": render_template("agent/dream_phase1.md", strip=True),
                    },
                    {"role": "user", "content": phase1_prompt},
                ],
                tools=None,
                tool_choice=None,
            )
            analysis = phase1_response.content or ""
            logger.debug("Dream Phase 1 analysis ({} chars): {}", len(analysis), analysis[:500])
        except Exception:
            logger.exception("Dream Phase 1 failed")
            return False

        # Phase 2: Delegate to AgentRunner with read_file / edit_file
        existing_skills = self._list_existing_skills()
        skills_section = ""
        if existing_skills:
            skills_section = (
                "\n\n## Existing Skills\n"
                + "\n".join(f"- {s}" for s in existing_skills)
            )
        phase2_prompt = (
            f"## Analysis Result\n{analysis}\n\n"
            f"## Structured Dream Inputs\n{dream_inputs_text}\n\n"
            f"{file_context}{skills_section}"
        )

        tools = self._tools
        skill_creator_path = BUILTIN_SKILLS_DIR / "skill-creator" / "SKILL.md"
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": render_template(
                    "agent/dream_phase2.md",
                    strip=True,
                    skill_creator_path=str(skill_creator_path),
                ),
            },
            {"role": "user", "content": phase2_prompt},
        ]

        try:
            result = await self._runner.run(AgentRunSpec(
                initial_messages=messages,
                tools=tools,
                model=self.model,
                max_iterations=self.max_iterations,
                max_tool_result_chars=self.max_tool_result_chars,
                fail_on_tool_error=False,
            ))
            logger.debug(
                "Dream Phase 2 complete: stop_reason={}, tool_events={}",
                result.stop_reason, len(result.tool_events),
            )
            for ev in (result.tool_events or []):
                logger.info("Dream tool_event: name={}, status={}, detail={}", ev.get("name"), ev.get("status"), ev.get("detail", "")[:200])
        except Exception:
            logger.exception("Dream Phase 2 failed")
            result = None

        # Build changelog from tool events
        changelog: list[str] = []
        if result and result.tool_events:
            for event in result.tool_events:
                if event["status"] == "ok":
                    changelog.append(f"{event['name']}: {event['detail']}")

        self._mark_processed(batch)
        self.store.compact_history()

        if result and result.stop_reason == "completed":
            logger.info(
                "Dream done: {} change(s), processed_turns={}",
                len(changelog), len(batch),
            )
        else:
            reason = result.stop_reason if result else "exception"
            logger.warning(
                "Dream incomplete ({}): processed_turns={}",
                reason, len(batch),
            )

        # Git auto-commit (only when there are actual changes)
        if changelog and self.store.git.is_initialized():
            ts = datetime.now().strftime("%Y-%m-%d")
            sha = self.store.git.auto_commit(f"dream: {ts}, {len(changelog)} change(s)")
            if sha:
                logger.info("Dream commit: {}", sha)

        return True
