from __future__ import annotations

from pathlib import Path

from nanobot.agent.context import (
    assemble_prompt_payload,
    render_artifacts_section,
    ContextBuilder,
)


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    return workspace


def _flatten_messages(messages: list[dict]) -> str:
    parts: list[str] = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
    return "\n\n".join(parts)


def test_prompt_assembly_order_is_structured_then_current_message(tmp_path) -> None:
    builder = ContextBuilder(_make_workspace(tmp_path))

    messages = builder.build_messages(
        working_set={
            "session_key": "cli:direct",
            "version": 1,
            "source_turn_id": "turn-1",
            "source_revision": 1,
            "is_stable": True,
            "published_by": "agent_loop",
            "active_task": "Upgrade short-term context",
            "task_stage": "phase-2",
            "active_goals": ["Cut prompt assembly to structured input"],
            "open_loops": [],
            "last_user_focus": "Phase 2 execution",
            "relevant_capsule_refs": [],
            "relevant_artifact_refs": [],
            "budget_hints": {"raw_turn_budget": 2},
            "source_turn_ids": ["turn-1"],
            "created_at": "2026-04-01T10:00:00",
        },
        recent_raw_turns=[
            {"role": "user", "content": "请检查当前 prompt 装配。"},
            {"role": "assistant", "content": "我会先读取相关代码和计划。"},
        ],
        selected_capsules=[
            {
                "capsule_id": "cap-1",
                "user_goal": "按阶段推进短期上下文升级",
                "assistant_intent": "从阶段 2 开始切换 build_messages",
                "decisions": ["保留 Session.get_history() 作为 raw turn 回退"],
                "outcomes": ["阶段 1 已通过验收"],
                "open_questions": [],
                "artifact_refs": [],
                "next_expected_action": "切换 prompt assembly",
            }
        ],
        selected_artifacts=[
            {
                "artifact_id": "art-1",
                "source_type": "read_file",
                "source_input": {"path": "nanobot/agent/context.py"},
                "content_version": "sha256:abc",
                "freshness_policy": "file_bound",
                "prompt_render": "ContextBuilder.build_messages() 仍以 history 为主输入。",
                "raw_payload": "THIS SHOULD NEVER APPEAR",
            }
        ],
        current_message="开始执行阶段 2。",
        channel="cli",
        chat_id="direct",
    )

    flattened = _flatten_messages(messages)
    working_idx = flattened.index("[Working Set Snapshot]")
    raw_idx = flattened.index("请检查当前 prompt 装配。")
    capsule_idx = flattened.index("[Selected Turn Capsules]")
    artifact_idx = flattened.index("[Selected Artifact Render]")
    current_idx = flattened.index("开始执行阶段 2。")

    assert working_idx < raw_idx < capsule_idx < artifact_idx < current_idx


def test_prompt_assembly_trim_order_prefers_capsules_then_artifacts_then_raw_turns() -> None:
    payload = assemble_prompt_payload(
        working_set={
            "session_key": "cli:direct",
            "version": 7,
            "source_turn_id": "turn-7",
            "source_revision": 3,
            "is_stable": True,
            "published_by": "agent_loop",
            "active_task": "Main task",
            "task_stage": "phase-2",
            "active_goals": ["Goal A"],
            "open_loops": ["Loop A"],
            "last_user_focus": "Focus A",
            "relevant_capsule_refs": [],
            "relevant_artifact_refs": [],
            "budget_hints": {"raw_turn_budget": 8, "artifact_budget": 4},
            "source_turn_ids": ["turn-6", "turn-7"],
            "created_at": "2026-04-01T10:00:00",
        },
        recent_raw_turns=[
            {"role": "user", "content": "old raw turn " * 10},
            {"role": "assistant", "content": "recent raw turn " * 8},
        ],
        selected_capsules=[
            {"capsule_id": "cap-low", "user_goal": "low", "relevance": 0.1, "decisions": ["x" * 80]},
            {"capsule_id": "cap-high", "user_goal": "high", "relevance": 0.9, "decisions": ["y" * 80]},
        ],
        selected_artifacts=[
            {"artifact_id": "art-low", "digest": "low " * 40, "relevance": 0.1},
            {"artifact_id": "art-high", "digest": "high " * 40, "relevance": 0.9},
        ],
        max_chars=320,
    )

    assert [item["capsule_id"] for item in payload["selected_capsules"]] == []
    assert [item["artifact_id"] for item in payload["selected_artifacts"]] == []
    assert len(payload["recent_raw_turns"]) < 2
    assert payload["working_set"] is not None
    assert payload["working_set"]["budget_hints"] == {}


def test_artifact_render_does_not_inject_raw_payload() -> None:
    rendered = render_artifacts_section(
        [
            {
                "artifact_id": "art-1",
                "source_type": "exec",
                "source_input": {"command": "pytest"},
                "digest": "pytest failed in tests/agent/test_prompt_assembly.py",
                "raw_payload": "FULL RAW TERMINAL OUTPUT SHOULD STAY OUT",
            }
        ]
    )

    assert "pytest failed in tests/agent/test_prompt_assembly.py" in rendered
    assert "FULL RAW TERMINAL OUTPUT SHOULD STAY OUT" not in rendered
