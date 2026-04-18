"""Structured short-term state contracts and helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any, TypedDict

STATE_ROOT_DIR = ".nanobot/state"

REF_MESSAGE = "message"
REF_RESPONSE = "response"
REF_ARTIFACT = "artifact"
REF_CAPSULE = "capsule"
REF_COMMIT = "commit"

TURN_STAGE_COLLECTING_USER = "collecting_user"
TURN_STAGE_AWAITING_MODEL = "awaiting_model"
TURN_STAGE_AWAITING_TOOLS = "awaiting_tools"
TURN_STAGE_FINALIZING = "finalizing_turn"
TURN_STAGE_COMPLETED = "completed"
TURN_STAGE_INTERRUPTED = "interrupted"

TURN_STAGES = {
    TURN_STAGE_COLLECTING_USER,
    TURN_STAGE_AWAITING_MODEL,
    TURN_STAGE_AWAITING_TOOLS,
    TURN_STAGE_FINALIZING,
    TURN_STAGE_COMPLETED,
    TURN_STAGE_INTERRUPTED,
}

COMMIT_STATE_OPEN = "open"
COMMIT_STATE_SEALING = "sealing"
COMMIT_STATE_COMMITTED = "committed"
COMMIT_STATE_REPAIR_NEEDED = "repair_needed"

COMMIT_STATES = {
    COMMIT_STATE_OPEN,
    COMMIT_STATE_SEALING,
    COMMIT_STATE_COMMITTED,
    COMMIT_STATE_REPAIR_NEEDED,
}


class StateStoreCASMismatch(RuntimeError):
    """Raised when a CAS-style write sees an unexpected revision/version."""


class ParsedRef(TypedDict):
    kind: str
    id: str


class TurnState(TypedDict, total=False):
    turn_id: str
    session_key: str
    task_ref: str | None
    execution_ref: str | None
    revision: int
    current_stage: str
    resume_action: str | None
    user_message_ref: str | None
    declared_tool_calls: list[dict[str, Any]]
    completed_tool_results: list[dict[str, Any]]
    artifact_refs: list[str]
    capsule_ref: str | None
    capsule_status: str
    working_set_version: int | None
    injected_messages: list[dict[str, Any]]
    injection_revision: int
    final_response_ref: str | None
    commit_id: str | None
    commit_state: str
    commit_manifest_ref: str | None
    error_state: dict[str, Any] | None
    created_at: str
    updated_at: str


def timestamp() -> str:
    return datetime.now().isoformat()


def make_ref(kind: str, object_id: str) -> str:
    if not kind or not object_id:
        raise ValueError("kind and object_id are required")
    return f"{kind}:{object_id}"


def parse_ref(ref: str) -> ParsedRef:
    kind, sep, object_id = (ref or "").partition(":")
    if not sep or not kind or not object_id:
        raise ValueError(f"Invalid object ref: {ref!r}")
    return {"kind": kind, "id": object_id}


def build_turn_state(
    *,
    session_key: str,
    turn_id: str,
    current_stage: str = TURN_STAGE_COLLECTING_USER,
    revision: int = 0,
    injection_revision: int = 0,
) -> TurnState:
    if current_stage not in TURN_STAGES:
        raise ValueError(f"Unsupported turn stage: {current_stage}")
    now = timestamp()
    return {
        "turn_id": turn_id,
        "session_key": session_key,
        "task_ref": None,
        "execution_ref": None,
        "revision": revision,
        "current_stage": current_stage,
        "resume_action": None,
        "user_message_ref": None,
        "declared_tool_calls": [],
        "completed_tool_results": [],
        "artifact_refs": [],
        "capsule_ref": None,
        "capsule_status": "not_started",
        "working_set_version": None,
        "injected_messages": [],
        "injection_revision": injection_revision,
        "final_response_ref": None,
        "commit_id": None,
        "commit_state": COMMIT_STATE_OPEN,
        "commit_manifest_ref": None,
        "error_state": None,
        "created_at": now,
        "updated_at": now,
    }


def is_completed_manifest(data: dict[str, Any] | None) -> bool:
    if not isinstance(data, dict):
        return False
    return bool(data.get("commit_id") and data.get("completed_marker") is True)
