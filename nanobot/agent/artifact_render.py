"""Artifact persistence and invalidation helpers for tool execution."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from nanobot.session.state import REF_ARTIFACT, make_ref, timestamp as state_timestamp
from nanobot.session.state_store import StateStore
from nanobot.utils.helpers import (
    ensure_dir,
    safe_filename,
    sha256_text,
    stable_json_dumps,
    stringify_text_blocks,
    truncate_text,
    write_text_atomic,
)

_PROMPT_PREVIEW_CHARS = 1200


def _payload_to_text(payload: Any) -> tuple[str, str]:
    if isinstance(payload, str):
        return payload, "txt"
    if isinstance(payload, list):
        text_blocks = stringify_text_blocks(payload)
        if text_blocks is not None:
            return text_blocks, "txt"
        return stable_json_dumps(payload, indent=2), "json"
    if isinstance(payload, (dict, bool, int, float)) or payload is None:
        return stable_json_dumps(payload, indent=2), "json"
    return str(payload), "txt"


def _resolve_path(workspace: Path | None, raw_path: Any) -> Path | None:
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    path = Path(raw_path)
    if path.is_absolute() or workspace is None:
        return path
    return workspace / path


def _normalized_exec_signature(source_input: dict[str, Any], workspace: Path | None) -> dict[str, Any]:
    working_directory = (
        source_input.get("working_directory")
        or source_input.get("cwd")
        or source_input.get("working_dir")
        or source_input.get("path")
    )
    resolved = _resolve_path(workspace, working_directory)
    command = (
        source_input.get("command")
        or source_input.get("cmd")
        or source_input.get("args")
        or ""
    )
    return {
        "command": command,
        "working_directory": str(resolved) if resolved is not None else working_directory,
    }


def freshness_policy_for_source(source_type: str) -> str:
    if source_type == "read_file":
        return "file_bound"
    if source_type == "exec":
        return "command_bound"
    if source_type in {"web_fetch", "web_search"}:
        return "time_bound"
    return "immutable"


def compute_artifact_content_version(
    source_type: str,
    source_input: dict[str, Any],
    *,
    workspace: Path | None,
) -> str | None:
    if source_type == "read_file":
        path = _resolve_path(workspace, source_input.get("path"))
        if path is None:
            return None
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except FileNotFoundError:
            return f"missing:{path}"
        normalized = {"path": str(path), "digest": digest}
        return f"file:{sha256_text(stable_json_dumps(normalized))}"
    if source_type == "exec":
        signature = _normalized_exec_signature(source_input, workspace)
        return f"exec:{sha256_text(stable_json_dumps(signature))}"
    return None


def render_tool_artifact_for_prompt(
    artifact: dict[str, Any],
    *,
    budget_chars: int = _PROMPT_PREVIEW_CHARS,
) -> str:
    payload_text = str(artifact.get("_raw_preview") or artifact.get("digest") or "")
    if not payload_text:
        payload_text = stable_json_dumps(
            {
                "artifact_id": artifact.get("artifact_id"),
                "source_type": artifact.get("source_type"),
                "source_input": artifact.get("source_input"),
            }
        )
    raw_ref = artifact.get("raw_ref")
    if not isinstance(raw_ref, str):
        raw_ref = ""
    if len(payload_text) <= budget_chars:
        return payload_text
    preview = truncate_text(payload_text, budget_chars)
    return (
        "[tool artifact persisted]\n"
        f"Artifact: {artifact.get('artifact_id')}\n"
        f"Full payload saved to: {raw_ref or '(inline)'}\n"
        f"Original size: {artifact.get('size_chars', len(payload_text))} chars\n"
        f"Preview:\n{preview}"
    )


def _persist_raw_payload(
    *,
    workspace: Path,
    session_key: str,
    artifact_id: str,
    payload: Any,
) -> tuple[str, str]:
    state_store = StateStore(workspace)
    raw_dir = ensure_dir(state_store.artifacts_dir(session_key) / "raw")
    text_payload, suffix = _payload_to_text(payload)
    raw_path = raw_dir / f"{safe_filename(artifact_id)}.{suffix}"
    write_text_atomic(raw_path, text_payload)
    return str(raw_path), text_payload


def persist_tool_artifact(
    *,
    workspace: Path,
    session_key: str,
    turn_id: str,
    artifact_id: str,
    tool_call_id: str,
    source_type: str,
    source_input: dict[str, Any],
    payload: Any,
    declared_revision: int,
    invalidated_by: list[str] | None = None,
    eligible_for_commit: bool = True,
) -> dict[str, Any]:
    raw_ref, payload_text = _persist_raw_payload(
        workspace=workspace,
        session_key=session_key,
        artifact_id=artifact_id,
        payload=payload,
    )
    content_version = compute_artifact_content_version(
        source_type,
        source_input,
        workspace=workspace,
    )
    artifact = {
        "artifact_id": artifact_id,
        "artifact_ref": make_ref(REF_ARTIFACT, artifact_id),
        "session_key": session_key,
        "turn_id": turn_id,
        "tool_call_id": tool_call_id,
        "declared_revision": declared_revision,
        "source_type": source_type,
        "source_input": dict(source_input),
        "raw_ref": raw_ref,
        "digest": f"sha256:{sha256_text(payload_text)}",
        "size_chars": len(payload_text),
        "freshness_policy": freshness_policy_for_source(source_type),
        "content_version": content_version,
        "invalidated_by": list(invalidated_by or []),
        "eligible_for_commit": eligible_for_commit,
        "created_at": state_timestamp(),
        "_raw_preview": payload_text[:_PROMPT_PREVIEW_CHARS],
    }
    artifact["prompt_render"] = render_tool_artifact_for_prompt(artifact)
    artifact["capsule_render"] = {
        "artifact_id": artifact_id,
        "source_type": source_type,
        "digest": artifact["digest"],
        "content_version": content_version,
    }
    artifact["dream_render"] = {
        "artifact_id": artifact_id,
        "source_type": source_type,
        "source_input": dict(source_input),
        "digest": artifact["digest"],
        "content_version": content_version,
        "invalidated_by": list(artifact["invalidated_by"]),
    }
    StateStore(workspace).save_artifact(session_key, artifact_id, artifact)
    return artifact


def artifact_invalidation_reasons(
    artifact: dict[str, Any],
    *,
    workspace: Path | None = None,
    current_source_input: dict[str, Any] | None = None,
) -> list[str]:
    reasons = list(artifact.get("invalidated_by") or [])
    source_type = str(artifact.get("source_type") or "")
    source_input = current_source_input or dict(artifact.get("source_input") or {})
    current_version = compute_artifact_content_version(
        source_type,
        source_input,
        workspace=workspace,
    )
    stored_version = artifact.get("content_version")
    if source_type == "read_file" and current_version != stored_version:
        reasons.append("source_changed")
    if source_type == "exec" and current_version != stored_version:
        reasons.append("command_signature_changed")
    return list(dict.fromkeys(reasons))


def artifact_is_invalidated(
    artifact: dict[str, Any],
    *,
    workspace: Path | None = None,
    current_source_input: dict[str, Any] | None = None,
) -> bool:
    return bool(
        artifact_invalidation_reasons(
            artifact,
            workspace=workspace,
            current_source_input=current_source_input,
        )
    )
