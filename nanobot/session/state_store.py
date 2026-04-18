"""Filesystem-backed structured state store."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from nanobot.session.state import (
    REF_ARTIFACT,
    REF_CAPSULE,
    REF_COMMIT,
    REF_MESSAGE,
    REF_RESPONSE,
    STATE_ROOT_DIR,
    StateStoreCASMismatch,
    is_completed_manifest,
    parse_ref,
)
from nanobot.utils.helpers import ensure_dir, safe_filename


class StateStore:
    """Structured state persistence rooted under `.nanobot/state`."""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.root = ensure_dir(workspace / STATE_ROOT_DIR)

    def session_dir(self, session_key: str) -> Path:
        return ensure_dir(self.root / "sessions" / safe_filename(session_key))

    def turns_dir(self, session_key: str) -> Path:
        return ensure_dir(self.session_dir(session_key) / "turns")

    def messages_dir(self, session_key: str) -> Path:
        return ensure_dir(self.session_dir(session_key) / "messages")

    def responses_dir(self, session_key: str) -> Path:
        return ensure_dir(self.session_dir(session_key) / "responses")

    def working_set_dir(self, session_key: str) -> Path:
        return ensure_dir(self.session_dir(session_key) / "working-set")

    def capsules_dir(self, session_key: str) -> Path:
        return ensure_dir(self.session_dir(session_key) / "capsules")

    def artifacts_dir(self, session_key: str) -> Path:
        return ensure_dir(self.session_dir(session_key) / "artifacts")

    def commits_dir(self, session_key: str) -> Path:
        return ensure_dir(self.session_dir(session_key) / "commits")

    def indexes_dir(self, session_key: str) -> Path:
        return ensure_dir(self.session_dir(session_key) / "indexes")

    def turn_path(self, session_key: str, turn_id: str) -> Path:
        return self.turns_dir(session_key) / f"{safe_filename(turn_id)}.json"

    def message_path(self, session_key: str, message_id: str) -> Path:
        return self.messages_dir(session_key) / f"{safe_filename(message_id)}.json"

    def response_path(self, session_key: str, response_id: str) -> Path:
        return self.responses_dir(session_key) / f"{safe_filename(response_id)}.json"

    def working_set_path(self, session_key: str, version: int) -> Path:
        return self.working_set_dir(session_key) / f"{int(version)}.json"

    def capsule_path(self, session_key: str, capsule_id: str) -> Path:
        return self.capsules_dir(session_key) / f"{safe_filename(capsule_id)}.json"

    def artifact_path(self, session_key: str, artifact_id: str) -> Path:
        return self.artifacts_dir(session_key) / f"{safe_filename(artifact_id)}.json"

    def commit_path(self, session_key: str, commit_id: str) -> Path:
        return self.commits_dir(session_key) / f"{safe_filename(commit_id)}.json"

    def index_path(self, session_key: str, name: str) -> Path:
        return self.indexes_dir(session_key) / f"{name}.json"

    @staticmethod
    def _write_text_atomic(path: Path, content: str) -> None:
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            tmp.write_text(content, encoding="utf-8")
            tmp.replace(path)
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)

    def _write_json_atomic(self, path: Path, data: dict[str, Any]) -> None:
        ensure_dir(path.parent)
        self._write_text_atomic(path, json.dumps(data, ensure_ascii=False, indent=2))

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any] | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None

    def load_turn_state(self, session_key: str, turn_id: str) -> dict[str, Any] | None:
        return self._read_json(self.turn_path(session_key, turn_id))

    def save_turn_state(
        self,
        session_key: str,
        turn_id: str,
        data: dict[str, Any],
        *,
        expected_revision: int | None = None,
    ) -> None:
        current = self.load_turn_state(session_key, turn_id)
        if expected_revision is not None:
            current_revision = current.get("revision") if isinstance(current, dict) else None
            if current_revision != expected_revision:
                raise StateStoreCASMismatch(
                    f"Turn {turn_id} expected revision {expected_revision}, got {current_revision}"
                )
        self._write_json_atomic(self.turn_path(session_key, turn_id), data)

    def save_message_object(self, session_key: str, message_id: str, data: dict[str, Any]) -> None:
        self._write_json_atomic(self.message_path(session_key, message_id), data)

    def load_message_object(self, session_key: str, message_id: str) -> dict[str, Any] | None:
        return self._read_json(self.message_path(session_key, message_id))

    def save_response_object(self, session_key: str, response_id: str, data: dict[str, Any]) -> None:
        self._write_json_atomic(self.response_path(session_key, response_id), data)

    def load_response_object(self, session_key: str, response_id: str) -> dict[str, Any] | None:
        return self._read_json(self.response_path(session_key, response_id))

    def save_capsule(self, session_key: str, capsule_id: str, data: dict[str, Any]) -> None:
        self._write_json_atomic(self.capsule_path(session_key, capsule_id), data)

    def load_capsule(self, session_key: str, capsule_id: str) -> dict[str, Any] | None:
        return self._read_json(self.capsule_path(session_key, capsule_id))

    def save_artifact(self, session_key: str, artifact_id: str, data: dict[str, Any]) -> None:
        self._write_json_atomic(self.artifact_path(session_key, artifact_id), data)

    def load_artifact(self, session_key: str, artifact_id: str) -> dict[str, Any] | None:
        return self._read_json(self.artifact_path(session_key, artifact_id))

    def save_commit_manifest(self, session_key: str, commit_id: str, data: dict[str, Any]) -> None:
        self._write_json_atomic(self.commit_path(session_key, commit_id), data)

    def load_commit_manifest(self, session_key: str, commit_id: str) -> dict[str, Any] | None:
        return self._read_json(self.commit_path(session_key, commit_id))

    def save_working_set(self, session_key: str, snapshot: dict[str, Any]) -> None:
        version = snapshot.get("version")
        if not isinstance(version, int):
            raise ValueError("Working set snapshot requires integer 'version'")
        self._write_json_atomic(self.working_set_path(session_key, version), snapshot)

    def load_working_set(self, session_key: str, version: int) -> dict[str, Any] | None:
        return self._read_json(self.working_set_path(session_key, version))

    def load_latest_working_set(self, session_key: str) -> dict[str, Any] | None:
        index = self._read_json(self.index_path(session_key, "latest-working-set"))
        if not isinstance(index, dict):
            return None
        version = index.get("version")
        if not isinstance(version, int):
            return None
        return self.load_working_set(session_key, version)

    def publish_latest_working_set(
        self,
        session_key: str,
        version: int,
        *,
        expected_version: int | None = None,
    ) -> None:
        path = self.index_path(session_key, "latest-working-set")
        current = self._read_json(path)
        current_version = current.get("version") if isinstance(current, dict) else None
        if expected_version is not None and current_version != expected_version:
            raise StateStoreCASMismatch(
                f"Working set expected version {expected_version}, got {current_version}"
            )
        snapshot = self.load_working_set(session_key, version)
        if not isinstance(snapshot, dict):
            raise FileNotFoundError(f"Working set version {version} not found for {session_key}")
        self._write_json_atomic(path, {"version": version})

    def write_index(self, session_key: str, name: str, data: dict[str, Any]) -> None:
        self._write_json_atomic(self.index_path(session_key, name), data)

    def read_index(self, session_key: str, name: str) -> dict[str, Any] | None:
        return self._read_json(self.index_path(session_key, name))

    def resolve_ref(self, session_key: str, ref: str) -> dict[str, Any] | None:
        parsed = parse_ref(ref)
        if parsed["kind"] == REF_MESSAGE:
            return self.load_message_object(session_key, parsed["id"])
        if parsed["kind"] == REF_RESPONSE:
            return self.load_response_object(session_key, parsed["id"])
        if parsed["kind"] == REF_ARTIFACT:
            return self.load_artifact(session_key, parsed["id"])
        if parsed["kind"] == REF_CAPSULE:
            return self.load_capsule(session_key, parsed["id"])
        if parsed["kind"] == REF_COMMIT:
            return self.load_commit_manifest(session_key, parsed["id"])
        raise ValueError(f"Unsupported ref kind: {parsed['kind']}")

    def is_committed_turn(self, session_key: str, commit_ref: str | None) -> bool:
        if not commit_ref:
            return False
        return is_completed_manifest(self.resolve_ref(session_key, commit_ref))
