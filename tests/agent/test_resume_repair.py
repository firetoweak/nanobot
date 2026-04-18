from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from nanobot.agent.loop import AgentLoop
from nanobot.bus.queue import MessageBus
from nanobot.session.state import (
    COMMIT_STATE_COMMITTED,
    REF_ARTIFACT,
    REF_CAPSULE,
    REF_RESPONSE,
    TURN_STAGE_COMPLETED,
    TURN_STAGE_INTERRUPTED,
    make_ref,
)


def _make_full_loop(tmp_path: Path) -> AgentLoop:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    return AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path, model="test-model")


def _seed_artifact(loop: AgentLoop, session_key: str, turn_id: str) -> str:
    artifact = {
        "artifact_id": "art_1",
        "session_key": session_key,
        "turn_id": turn_id,
        "tool_call_id": "call_1",
        "declared_revision": 0,
        "source_type": "read_file",
        "source_input": {"path": "note.txt"},
        "raw_ref": "raw.txt",
        "digest": "sha256:abc",
        "size_chars": 3,
        "freshness_policy": "file_bound",
        "content_version": "file:v1",
        "invalidated_by": [],
        "created_at": "2026-01-01T00:00:00",
        "prompt_render": "artifact",
    }
    loop.sessions.save_artifact(session_key, "art_1", artifact)
    return make_ref(REF_ARTIFACT, "art_1")


def test_repair_partial_commit_recovers_from_missing_manifest(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)
    session = loop.sessions.get_or_create("cli:repair")
    turn_state = loop.create_turn_state(session.key)
    artifact_ref = _seed_artifact(loop, session.key, turn_state["turn_id"])

    capsule = {
        "capsule_id": "cap_1",
        "turn_id": turn_state["turn_id"],
        "session_key": session.key,
        "source_revision": turn_state["revision"],
        "user_goal": "goal",
        "assistant_intent": "intent",
        "decisions": [],
        "outcomes": [],
        "open_questions": [],
        "artifact_refs": [artifact_ref],
        "next_expected_action": None,
        "capsule_version": 1,
        "created_at": "2026-01-01T00:00:00",
    }
    response = {
        "response_id": "resp_1",
        "session_key": session.key,
        "turn_id": turn_state["turn_id"],
        "source_revision": turn_state["revision"],
        "content": "done",
        "created_at": "2026-01-01T00:00:00",
    }
    working_set = {
        "session_key": session.key,
        "version": 1,
        "source_turn_id": turn_state["turn_id"],
        "source_revision": turn_state["revision"],
        "is_stable": True,
        "published_by": "agent_loop",
        "active_task": None,
        "task_stage": None,
        "active_goals": [],
        "open_loops": [],
        "last_user_focus": None,
        "relevant_capsule_refs": [make_ref(REF_CAPSULE, "cap_1")],
        "relevant_artifact_refs": [artifact_ref],
        "budget_hints": {},
        "source_turn_ids": [turn_state["turn_id"]],
        "created_at": "2026-01-01T00:00:00",
    }
    loop.sessions.save_capsule(session.key, "cap_1", capsule)
    loop.sessions.save_response_object(session.key, "resp_1", response)
    loop.sessions.save_working_set(session.key, working_set)

    turn_state = loop._advance_turn_state(
        turn_state,
        current_stage="finalizing_turn",
        artifact_refs=[artifact_ref],
        capsule_ref=make_ref(REF_CAPSULE, "cap_1"),
        final_response_ref=make_ref(REF_RESPONSE, "resp_1"),
        working_set_version=1,
    )

    repaired = loop.repair_partial_commit(session, turn_state)

    assert repaired is not None
    assert repaired["current_stage"] == TURN_STAGE_COMPLETED
    assert repaired["commit_state"] == COMMIT_STATE_COMMITTED
    assert repaired["commit_manifest_ref"].startswith("commit:")
    assert loop.sessions.resolve_ref(session.key, repaired["commit_manifest_ref"]) is not None


def test_repair_partial_commit_interrupts_when_final_response_missing(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)
    session = loop.sessions.get_or_create("cli:repair-missing-response")
    turn_state = loop.create_turn_state(session.key)
    artifact_ref = _seed_artifact(loop, session.key, turn_state["turn_id"])

    capsule = {
        "capsule_id": "cap_1",
        "turn_id": turn_state["turn_id"],
        "session_key": session.key,
        "source_revision": turn_state["revision"],
        "user_goal": "goal",
        "assistant_intent": "intent",
        "decisions": [],
        "outcomes": [],
        "open_questions": [],
        "artifact_refs": [artifact_ref],
        "next_expected_action": None,
        "capsule_version": 1,
        "created_at": "2026-01-01T00:00:00",
    }
    working_set = {
        "session_key": session.key,
        "version": 1,
        "source_turn_id": turn_state["turn_id"],
        "source_revision": turn_state["revision"],
        "is_stable": True,
        "published_by": "agent_loop",
        "active_task": None,
        "task_stage": None,
        "active_goals": [],
        "open_loops": [],
        "last_user_focus": None,
        "relevant_capsule_refs": [make_ref(REF_CAPSULE, "cap_1")],
        "relevant_artifact_refs": [artifact_ref],
        "budget_hints": {},
        "source_turn_ids": [turn_state["turn_id"]],
        "created_at": "2026-01-01T00:00:00",
    }
    loop.sessions.save_capsule(session.key, "cap_1", capsule)
    loop.sessions.save_working_set(session.key, working_set)

    turn_state = loop._advance_turn_state(
        turn_state,
        current_stage="finalizing_turn",
        artifact_refs=[artifact_ref],
        capsule_ref=make_ref(REF_CAPSULE, "cap_1"),
        final_response_ref=make_ref(REF_RESPONSE, "resp_1"),
        working_set_version=1,
    )

    repaired = loop.repair_partial_commit(session, turn_state)

    assert repaired is not None
    assert repaired["current_stage"] == TURN_STAGE_INTERRUPTED
    assert repaired["resume_action"] == "replan"


def test_restore_turn_state_requires_manifest_not_completed_stage_only(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)
    session = loop.sessions.get_or_create("cli:restore-missing-manifest")
    session.add_message("user", "hello")
    turn_state = loop.create_turn_state(session.key)
    turn_state = loop._advance_turn_state(
        turn_state,
        current_stage=TURN_STAGE_COMPLETED,
        commit_state=COMMIT_STATE_COMMITTED,
        user_message_ref=loop._save_message_object(
            session_key=session.key,
            turn_id=turn_state["turn_id"],
            role="user",
            content="hello",
        ),
    )
    loop.sessions.publish_active_turn(session.key, turn_state["turn_id"])

    restored = loop._restore_turn_state(session)

    assert restored is True
    reloaded = loop.sessions.load_active_turn_state(session.key)
    assert reloaded is not None
    assert reloaded["current_stage"] == TURN_STAGE_INTERRUPTED
    assert reloaded["resume_action"] == "replan"
    assert "interrupted before a response was generated" in session.messages[-1]["content"].lower()


def test_restore_turn_state_repairs_missing_working_set_as_interrupted(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)
    session = loop.sessions.get_or_create("cli:restore-missing-working-set")
    session.add_message("user", "hello")
    turn_state = loop.create_turn_state(session.key)
    artifact_ref = _seed_artifact(loop, session.key, turn_state["turn_id"])

    capsule = {
        "capsule_id": "cap_1",
        "turn_id": turn_state["turn_id"],
        "session_key": session.key,
        "source_revision": turn_state["revision"],
        "user_goal": "goal",
        "assistant_intent": "intent",
        "decisions": [],
        "outcomes": [],
        "open_questions": [],
        "artifact_refs": [artifact_ref],
        "next_expected_action": None,
        "capsule_version": 1,
        "created_at": "2026-01-01T00:00:00",
    }
    response = {
        "response_id": "resp_1",
        "session_key": session.key,
        "turn_id": turn_state["turn_id"],
        "source_revision": turn_state["revision"],
        "content": "done",
        "created_at": "2026-01-01T00:00:00",
    }
    loop.sessions.save_capsule(session.key, "cap_1", capsule)
    loop.sessions.save_response_object(session.key, "resp_1", response)

    turn_state = loop._advance_turn_state(
        turn_state,
        current_stage=TURN_STAGE_COMPLETED,
        commit_state=COMMIT_STATE_COMMITTED,
        artifact_refs=[artifact_ref],
        capsule_ref=make_ref(REF_CAPSULE, "cap_1"),
        final_response_ref=make_ref(REF_RESPONSE, "resp_1"),
        working_set_version=9,
        user_message_ref=loop._save_message_object(
            session_key=session.key,
            turn_id=turn_state["turn_id"],
            role="user",
            content="hello",
        ),
    )
    commit = {
        "commit_id": "commit_1",
        "turn_id": turn_state["turn_id"],
        "session_key": session.key,
        "turn_revision": turn_state["revision"],
        "artifact_refs": [artifact_ref],
        "capsule_ref": make_ref(REF_CAPSULE, "cap_1"),
        "working_set_version": 9,
        "final_response_ref": make_ref(REF_RESPONSE, "resp_1"),
        "completed_marker": True,
        "created_at": "2026-01-01T00:00:00",
    }
    loop.sessions.save_commit_manifest(session.key, "commit_1", commit)
    turn_state = loop._advance_turn_state(
        turn_state,
        commit_manifest_ref="commit:commit_1",
    )
    loop.sessions.publish_active_turn(session.key, turn_state["turn_id"])

    restored = loop._restore_turn_state(session)

    assert restored is True
    reloaded = loop.sessions.load_active_turn_state(session.key)
    assert reloaded is not None
    assert reloaded["current_stage"] == TURN_STAGE_INTERRUPTED
    assert reloaded["resume_action"] == "replan"
