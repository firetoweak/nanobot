from __future__ import annotations

import pytest

from nanobot.session.state import (
    REF_ARTIFACT,
    REF_CAPSULE,
    REF_COMMIT,
    REF_MESSAGE,
    REF_RESPONSE,
    StateStoreCASMismatch,
    build_turn_state,
    make_ref,
)
from nanobot.session.manager import SessionManager


@pytest.fixture
def sessions(tmp_path):
    return SessionManager(tmp_path)


def test_turn_state_round_trip(sessions: SessionManager) -> None:
    state = build_turn_state(session_key="cli:test", turn_id="turn-1")

    sessions.save_turn_state("cli:test", "turn-1", state)

    loaded = sessions.load_turn_state("cli:test", "turn-1")
    assert loaded is not None
    assert loaded["turn_id"] == "turn-1"
    assert loaded["session_key"] == "cli:test"
    assert loaded["current_stage"] == "collecting_user"


def test_turn_state_save_enforces_expected_revision(sessions: SessionManager) -> None:
    state = build_turn_state(session_key="cli:test", turn_id="turn-1", revision=1)
    sessions.save_turn_state("cli:test", "turn-1", state)

    next_state = dict(state)
    next_state["revision"] = 2
    sessions.save_turn_state("cli:test", "turn-1", next_state, expected_revision=1)

    broken = dict(next_state)
    broken["revision"] = 3
    with pytest.raises(StateStoreCASMismatch):
        sessions.save_turn_state("cli:test", "turn-1", broken, expected_revision=1)


def test_message_and_response_objects_resolve_by_ref(sessions: SessionManager) -> None:
    message = {
        "message_id": "msg-1",
        "session_key": "cli:test",
        "turn_id": "turn-1",
        "role": "user",
        "content": "hello",
        "created_at": "2026-01-01T00:00:00",
    }
    response = {
        "response_id": "resp-1",
        "session_key": "cli:test",
        "turn_id": "turn-1",
        "source_revision": 2,
        "content": "done",
        "created_at": "2026-01-01T00:00:01",
    }

    sessions.save_message_object("cli:test", "msg-1", message)
    sessions.save_response_object("cli:test", "resp-1", response)

    assert sessions.resolve_ref("cli:test", make_ref(REF_MESSAGE, "msg-1")) == message
    assert sessions.resolve_ref("cli:test", make_ref(REF_RESPONSE, "resp-1")) == response


def test_working_set_versions_append_and_publish_latest(sessions: SessionManager) -> None:
    snapshot1 = {
        "session_key": "cli:test",
        "version": 1,
        "source_turn_id": "turn-1",
        "source_revision": 1,
        "is_stable": True,
        "published_by": "agent_loop",
        "active_task": "task A",
        "task_stage": "stage A",
        "active_goals": ["goal A"],
        "open_loops": [],
        "last_user_focus": "focus A",
        "relevant_capsule_refs": [],
        "relevant_artifact_refs": [],
        "budget_hints": {},
        "source_turn_ids": ["turn-1"],
        "created_at": "2026-01-01T00:00:00",
    }
    snapshot2 = dict(snapshot1)
    snapshot2["version"] = 2
    snapshot2["active_task"] = "task B"

    sessions.save_working_set("cli:test", snapshot1)
    sessions.publish_latest_working_set("cli:test", 1)
    assert sessions.load_latest_working_set("cli:test") == snapshot1

    sessions.save_working_set("cli:test", snapshot2)
    sessions.publish_latest_working_set("cli:test", 2, expected_version=1)
    assert sessions.load_working_set("cli:test", 1) == snapshot1
    assert sessions.load_latest_working_set("cli:test") == snapshot2


def test_publish_latest_working_set_honors_expected_version(sessions: SessionManager) -> None:
    snapshot = {
        "session_key": "cli:test",
        "version": 1,
        "source_turn_id": None,
        "source_revision": None,
        "is_stable": True,
        "published_by": "agent_loop",
        "active_task": None,
        "task_stage": None,
        "active_goals": [],
        "open_loops": [],
        "last_user_focus": None,
        "relevant_capsule_refs": [],
        "relevant_artifact_refs": [],
        "budget_hints": {},
        "source_turn_ids": [],
        "created_at": "2026-01-01T00:00:00",
    }
    sessions.save_working_set("cli:test", snapshot)

    with pytest.raises(StateStoreCASMismatch):
        sessions.publish_latest_working_set("cli:test", 1, expected_version=99)


def test_artifact_capsule_and_commit_manifest_resolve(sessions: SessionManager) -> None:
    artifact = {
        "artifact_id": "art-1",
        "session_key": "cli:test",
        "turn_id": "turn-1",
        "tool_call_id": "call-1",
        "declared_revision": 1,
        "source_type": "read_file",
        "source_input": {"path": "README.md"},
        "raw_ref": "file:raw-1",
        "digest": "digest",
        "size_chars": 12,
        "freshness_policy": "file_bound",
        "content_version": "v1",
        "invalidated_by": [],
        "created_at": "2026-01-01T00:00:00",
    }
    capsule = {
        "capsule_id": "cap-1",
        "turn_id": "turn-1",
        "session_key": "cli:test",
        "source_revision": 1,
        "user_goal": "goal",
        "assistant_intent": "intent",
        "decisions": [],
        "outcomes": [],
        "open_questions": [],
        "artifact_refs": [make_ref(REF_ARTIFACT, "art-1")],
        "next_expected_action": None,
        "capsule_version": 1,
        "created_at": "2026-01-01T00:00:00",
    }
    commit = {
        "commit_id": "commit-1",
        "turn_id": "turn-1",
        "session_key": "cli:test",
        "turn_revision": 1,
        "artifact_refs": [make_ref(REF_ARTIFACT, "art-1")],
        "capsule_ref": make_ref(REF_CAPSULE, "cap-1"),
        "working_set_version": 1,
        "final_response_ref": make_ref(REF_RESPONSE, "resp-1"),
        "completed_marker": True,
        "created_at": "2026-01-01T00:00:00",
    }
    response = {
        "response_id": "resp-1",
        "session_key": "cli:test",
        "turn_id": "turn-1",
        "source_revision": 1,
        "content": "ok",
        "created_at": "2026-01-01T00:00:00",
    }

    sessions.save_artifact("cli:test", "art-1", artifact)
    sessions.save_capsule("cli:test", "cap-1", capsule)
    sessions.save_response_object("cli:test", "resp-1", response)
    sessions.save_commit_manifest("cli:test", "commit-1", commit)

    assert sessions.resolve_ref("cli:test", make_ref(REF_ARTIFACT, "art-1")) == artifact
    assert sessions.resolve_ref("cli:test", make_ref(REF_CAPSULE, "cap-1")) == capsule
    assert sessions.resolve_ref("cli:test", make_ref(REF_COMMIT, "commit-1")) == commit
