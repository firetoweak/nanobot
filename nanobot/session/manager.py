"""Session management for conversation history."""

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.config.paths import get_legacy_sessions_dir
from nanobot.session.state_store import StateStore
from nanobot.utils.helpers import ensure_dir, find_legal_message_start, safe_filename


@dataclass
class Session:
    """A conversation session."""

    key: str  # channel:chat_id
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)
    last_consolidated: int = 0  # Number of messages already consolidated to files

    def add_message(self, role: str, content: str, **kwargs: Any) -> None:
        """Add a message to the session."""
        msg = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            **kwargs
        }
        self.messages.append(msg)
        self.updated_at = datetime.now()

    def get_history(self, max_messages: int = 500) -> list[dict[str, Any]]:
        """Return unconsolidated messages for LLM input, aligned to a legal tool-call boundary."""
        unconsolidated = self.messages[self.last_consolidated:]
        sliced = unconsolidated[-max_messages:]

        # Avoid starting mid-turn when possible.
        for i, message in enumerate(sliced):
            if message.get("role") == "user":
                sliced = sliced[i:]
                break

        # Drop orphan tool results at the front.
        start = find_legal_message_start(sliced)
        if start:
            sliced = sliced[start:]

        out: list[dict[str, Any]] = []
        for message in sliced:
            entry: dict[str, Any] = {"role": message["role"], "content": message.get("content", "")}
            for key in ("tool_calls", "tool_call_id", "name", "reasoning_content"):
                if key in message:
                    entry[key] = message[key]
            out.append(entry)
        return out

    def clear(self) -> None:
        """Clear all messages and reset session to initial state."""
        self.messages = []
        self.last_consolidated = 0
        self.updated_at = datetime.now()

    def retain_recent_legal_suffix(self, max_messages: int) -> None:
        """Keep a legal recent suffix, mirroring get_history boundary rules."""
        if max_messages <= 0:
            self.clear()
            return
        if len(self.messages) <= max_messages:
            return

        start_idx = max(0, len(self.messages) - max_messages)

        # If the cutoff lands mid-turn, extend backward to the nearest user turn.
        while start_idx > 0 and self.messages[start_idx].get("role") != "user":
            start_idx -= 1

        retained = self.messages[start_idx:]

        # Mirror get_history(): avoid persisting orphan tool results at the front.
        start = find_legal_message_start(retained)
        if start:
            retained = retained[start:]

        dropped = len(self.messages) - len(retained)
        self.messages = retained
        self.last_consolidated = max(0, self.last_consolidated - dropped)
        self.updated_at = datetime.now()


class SessionManager:
    """
    Manages conversation sessions.

    Sessions are stored as JSONL files in the sessions directory.
    """

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.sessions_dir = ensure_dir(self.workspace / "sessions")
        self.legacy_sessions_dir = get_legacy_sessions_dir()
        self.state_store = StateStore(workspace)
        self._cache: dict[str, Session] = {}

    def _get_session_path(self, key: str) -> Path:
        """Get the file path for a session."""
        safe_key = safe_filename(key.replace(":", "_"))
        return self.sessions_dir / f"{safe_key}.jsonl"

    def _get_legacy_session_path(self, key: str) -> Path:
        """Legacy global session path (~/.nanobot/sessions/)."""
        safe_key = safe_filename(key.replace(":", "_"))
        return self.legacy_sessions_dir / f"{safe_key}.jsonl"

    def get_or_create(self, key: str) -> Session:
        """
        Get an existing session or create a new one.

        Args:
            key: Session key (usually channel:chat_id).

        Returns:
            The session.
        """
        if key in self._cache:
            return self._cache[key]

        session = self._load(key)
        if session is None:
            session = Session(key=key)

        self._cache[key] = session
        return session

    def _load(self, key: str) -> Session | None:
        """Load a session from disk."""
        path = self._get_session_path(key)
        if not path.exists():
            legacy_path = self._get_legacy_session_path(key)
            if legacy_path.exists():
                try:
                    shutil.move(str(legacy_path), str(path))
                    logger.info("Migrated session {} from legacy path", key)
                except Exception:
                    logger.exception("Failed to migrate session {}", key)

        if not path.exists():
            return None

        try:
            messages = []
            metadata = {}
            created_at = None
            updated_at = None
            last_consolidated = 0

            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    data = json.loads(line)

                    if data.get("_type") == "metadata":
                        metadata = data.get("metadata", {})
                        created_at = datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None
                        updated_at = datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else None
                        last_consolidated = data.get("last_consolidated", 0)
                    else:
                        messages.append(data)

            return Session(
                key=key,
                messages=messages,
                created_at=created_at or datetime.now(),
                updated_at=updated_at or datetime.now(),
                metadata=metadata,
                last_consolidated=last_consolidated
            )
        except Exception as e:
            logger.warning("Failed to load session {}: {}", key, e)
            return None

    def save(self, session: Session) -> None:
        """Save a session to disk."""
        path = self._get_session_path(session.key)

        with open(path, "w", encoding="utf-8") as f:
            metadata_line = {
                "_type": "metadata",
                "key": session.key,
                "created_at": session.created_at.isoformat(),
                "updated_at": session.updated_at.isoformat(),
                "metadata": session.metadata,
                "last_consolidated": session.last_consolidated
            }
            f.write(json.dumps(metadata_line, ensure_ascii=False) + "\n")
            for msg in session.messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")

        self._cache[session.key] = session

    def invalidate(self, key: str) -> None:
        """Remove a session from the in-memory cache."""
        self._cache.pop(key, None)

    def list_sessions(self) -> list[dict[str, Any]]:
        """
        List all sessions.

        Returns:
            List of session info dicts.
        """
        sessions = []

        for path in self.sessions_dir.glob("*.jsonl"):
            try:
                # Read just the metadata line
                with open(path, encoding="utf-8") as f:
                    first_line = f.readline().strip()
                    if first_line:
                        data = json.loads(first_line)
                        if data.get("_type") == "metadata":
                            key = data.get("key") or path.stem.replace("_", ":", 1)
                            sessions.append({
                                "key": key,
                                "created_at": data.get("created_at"),
                                "updated_at": data.get("updated_at"),
                                "path": str(path)
                            })
            except Exception:
                continue

        return sorted(sessions, key=lambda x: x.get("updated_at", ""), reverse=True)

    def load_turn_state(self, session_key: str, turn_id: str) -> dict[str, Any] | None:
        return self.state_store.load_turn_state(session_key, turn_id)

    def save_turn_state(
        self,
        session_key: str,
        turn_id: str,
        data: dict[str, Any],
        *,
        expected_revision: int | None = None,
    ) -> None:
        self.state_store.save_turn_state(
            session_key,
            turn_id,
            data,
            expected_revision=expected_revision,
        )

    def save_message_object(self, session_key: str, message_id: str, data: dict[str, Any]) -> None:
        self.state_store.save_message_object(session_key, message_id, data)

    def load_message_object(self, session_key: str, message_id: str) -> dict[str, Any] | None:
        return self.state_store.load_message_object(session_key, message_id)

    def save_response_object(self, session_key: str, response_id: str, data: dict[str, Any]) -> None:
        self.state_store.save_response_object(session_key, response_id, data)

    def load_response_object(self, session_key: str, response_id: str) -> dict[str, Any] | None:
        return self.state_store.load_response_object(session_key, response_id)

    def save_working_set(self, session_key: str, snapshot: dict[str, Any]) -> None:
        self.state_store.save_working_set(session_key, snapshot)

    def load_working_set(self, session_key: str, version: int) -> dict[str, Any] | None:
        return self.state_store.load_working_set(session_key, version)

    def load_latest_working_set(self, session_key: str) -> dict[str, Any] | None:
        return self.state_store.load_latest_working_set(session_key)

    def publish_latest_working_set(
        self,
        session_key: str,
        version: int,
        *,
        expected_version: int | None = None,
    ) -> None:
        self.state_store.publish_latest_working_set(
            session_key,
            version,
            expected_version=expected_version,
        )

    def save_capsule(self, session_key: str, capsule_id: str, data: dict[str, Any]) -> None:
        self.state_store.save_capsule(session_key, capsule_id, data)

    def load_capsule(self, session_key: str, capsule_id: str) -> dict[str, Any] | None:
        return self.state_store.load_capsule(session_key, capsule_id)

    def save_artifact(self, session_key: str, artifact_id: str, data: dict[str, Any]) -> None:
        self.state_store.save_artifact(session_key, artifact_id, data)

    def load_artifact(self, session_key: str, artifact_id: str) -> dict[str, Any] | None:
        return self.state_store.load_artifact(session_key, artifact_id)

    def save_commit_manifest(self, session_key: str, commit_id: str, data: dict[str, Any]) -> None:
        self.state_store.save_commit_manifest(session_key, commit_id, data)

    def load_commit_manifest(self, session_key: str, commit_id: str) -> dict[str, Any] | None:
        return self.state_store.load_commit_manifest(session_key, commit_id)

    def read_state_index(self, session_key: str, name: str) -> dict[str, Any] | None:
        return self.state_store.read_index(session_key, name)

    def write_state_index(self, session_key: str, name: str, data: dict[str, Any]) -> None:
        self.state_store.write_index(session_key, name, data)

    def resolve_ref(self, session_key: str, ref: str) -> dict[str, Any] | None:
        return self.state_store.resolve_ref(session_key, ref)

    def publish_active_turn(self, session_key: str, turn_id: str | None) -> None:
        if turn_id is None:
            self.write_state_index(session_key, "active-turn", {"turn_id": None})
            return
        self.write_state_index(session_key, "active-turn", {"turn_id": turn_id})

    def get_active_turn_id(self, session_key: str) -> str | None:
        index = self.read_state_index(session_key, "active-turn")
        if not isinstance(index, dict):
            return None
        turn_id = index.get("turn_id")
        return turn_id if isinstance(turn_id, str) and turn_id else None

    def load_active_turn_state(self, session_key: str) -> dict[str, Any] | None:
        turn_id = self.get_active_turn_id(session_key)
        if not turn_id:
            return None
        return self.load_turn_state(session_key, turn_id)

    def publish_latest_turn(self, session_key: str, turn_id: str | None) -> None:
        if turn_id is None:
            self.write_state_index(session_key, "latest-turn", {"turn_id": None})
            return
        self.write_state_index(session_key, "latest-turn", {"turn_id": turn_id})

    def get_latest_turn_id(self, session_key: str) -> str | None:
        index = self.read_state_index(session_key, "latest-turn")
        if not isinstance(index, dict):
            return None
        turn_id = index.get("turn_id")
        return turn_id if isinstance(turn_id, str) and turn_id else None

    def load_latest_turn_state(self, session_key: str) -> dict[str, Any] | None:
        turn_id = self.get_latest_turn_id(session_key)
        if not turn_id:
            return None
        return self.load_turn_state(session_key, turn_id)
