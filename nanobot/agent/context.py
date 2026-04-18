"""Context builder for assembling agent prompts."""

import base64
import json
import mimetypes
import platform
from importlib.resources import files as pkg_files
from pathlib import Path
from typing import Any

from nanobot.agent.memory import MemoryStore
from nanobot.agent.skills import SkillsLoader
from nanobot.utils.helpers import build_assistant_message, current_time_str, detect_image_mime
from nanobot.utils.prompt_templates import render_template


_WORKING_SET_SECTION_TITLE = "[Working Set Snapshot]"
_WORKING_SET_SECTION_END = "[/Working Set Snapshot]"
_CAPSULE_SECTION_TITLE = "[Selected Turn Capsules]"
_CAPSULE_SECTION_END = "[/Selected Turn Capsules]"
_ARTIFACT_SECTION_TITLE = "[Selected Artifact Render]"
_ARTIFACT_SECTION_END = "[/Selected Artifact Render]"


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
                else:
                    parts.append(json.dumps(block, ensure_ascii=False, sort_keys=True))
            else:
                parts.append(str(block))
        return "\n".join(part for part in parts if part)
    if content is None:
        return ""
    return str(content)


def _message_chars(message: dict[str, Any]) -> int:
    size = len(str(message.get("role", "")))
    size += len(_content_to_text(message.get("content")))
    if tool_calls := message.get("tool_calls"):
        size += len(json.dumps(tool_calls, ensure_ascii=False, sort_keys=True))
    for key in ("tool_call_id", "name", "reasoning_content"):
        if value := message.get(key):
            size += len(str(value))
    return size


def _section_chars(text: str | None) -> int:
    return len(text or "")


def _item_relevance(item: dict[str, Any]) -> float:
    value = item.get("relevance")
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _drop_lowest_relevance(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not items:
        return items
    lowest_idx = min(range(len(items)), key=lambda idx: (_item_relevance(items[idx]), idx))
    return items[:lowest_idx] + items[lowest_idx + 1:]


def _render_scalar(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def render_working_set_section(working_set: dict[str, Any] | None) -> str:
    """Render the stable working-set block for prompt assembly."""
    if not isinstance(working_set, dict) or not working_set:
        return ""

    lines = [_WORKING_SET_SECTION_TITLE]
    ordered_scalars = (
        ("version", "Version"),
        ("active_task", "Active Task"),
        ("task_stage", "Task Stage"),
        ("last_user_focus", "Last User Focus"),
        ("source_turn_id", "Source Turn"),
        ("source_revision", "Source Revision"),
        ("published_by", "Published By"),
        ("is_stable", "Stable"),
    )
    for key, label in ordered_scalars:
        value = working_set.get(key)
        if value not in (None, "", []):
            lines.append(f"{label}: {_render_scalar(value)}")

    list_fields = (
        ("active_goals", "Active Goals"),
        ("open_loops", "Open Loops"),
        ("source_turn_ids", "Source Turns"),
        ("relevant_capsule_refs", "Relevant Capsule Refs"),
        ("relevant_artifact_refs", "Relevant Artifact Refs"),
    )
    for key, label in list_fields:
        values = working_set.get(key) or []
        if values:
            lines.append(f"{label}:")
            lines.extend(f"- {item}" for item in values)

    budget_hints = working_set.get("budget_hints") or {}
    if budget_hints:
        lines.append("Budget Hints:")
        for hint_key, hint_value in budget_hints.items():
            lines.append(f"- {hint_key}: {_render_scalar(hint_value)}")

    created_at = working_set.get("created_at")
    if created_at:
        lines.append(f"Created At: {created_at}")
    lines.append(_WORKING_SET_SECTION_END)
    return "\n".join(lines)


def render_capsules_section(selected_capsules: list[dict[str, Any]]) -> str:
    """Render selected turn capsules without expanding into raw history."""
    if not selected_capsules:
        return ""

    lines = [_CAPSULE_SECTION_TITLE]
    for idx, capsule in enumerate(selected_capsules, start=1):
        capsule_id = capsule.get("capsule_id") or f"capsule-{idx}"
        lines.append(f"## Capsule {idx}: {capsule_id}")
        if value := capsule.get("user_goal"):
            lines.append(f"User Goal: {value}")
        if value := capsule.get("assistant_intent"):
            lines.append(f"Assistant Intent: {value}")
        for key, label in (
            ("decisions", "Decisions"),
            ("outcomes", "Outcomes"),
            ("open_questions", "Open Questions"),
        ):
            values = capsule.get(key) or []
            if values:
                lines.append(f"{label}:")
                lines.extend(f"- {item}" for item in values)
        artifact_refs = capsule.get("artifact_refs") or []
        if artifact_refs:
            lines.append("Artifact Refs:")
            lines.extend(f"- {item}" for item in artifact_refs)
        if value := capsule.get("next_expected_action"):
            lines.append(f"Next Expected Action: {value}")
    lines.append(_CAPSULE_SECTION_END)
    return "\n".join(lines)


def _pick_artifact_prompt_render(artifact: dict[str, Any]) -> str:
    for key in ("prompt_render", "rendered_prompt", "render", "summary", "digest"):
        value = artifact.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, dict):
            return json.dumps(value, ensure_ascii=False, sort_keys=True)

    safe_projection = {}
    for key in ("artifact_id", "source_type", "source_input", "content_version", "freshness_policy", "digest"):
        value = artifact.get(key)
        if value not in (None, "", []):
            safe_projection[key] = value
    return json.dumps(safe_projection, ensure_ascii=False, sort_keys=True) if safe_projection else ""


def render_artifacts_section(selected_artifacts: list[dict[str, Any]]) -> str:
    """Render prompt-safe artifact summaries instead of raw payloads."""
    if not selected_artifacts:
        return ""

    lines = [_ARTIFACT_SECTION_TITLE]
    for idx, artifact in enumerate(selected_artifacts, start=1):
        artifact_id = artifact.get("artifact_id") or f"artifact-{idx}"
        lines.append(f"## Artifact {idx}: {artifact_id}")
        if value := artifact.get("source_type"):
            lines.append(f"Source Type: {value}")
        if value := artifact.get("source_input"):
            lines.append(f"Source Input: {json.dumps(value, ensure_ascii=False, sort_keys=True)}")
        if value := artifact.get("content_version"):
            lines.append(f"Content Version: {value}")
        if value := artifact.get("freshness_policy"):
            lines.append(f"Freshness Policy: {value}")
        render = _pick_artifact_prompt_render(artifact)
        if render:
            lines.append("Render:")
            lines.append(render)
    lines.append(_ARTIFACT_SECTION_END)
    return "\n".join(lines)


def assemble_prompt_payload(
    *,
    working_set: dict[str, Any] | None,
    recent_raw_turns: list[dict[str, Any]],
    selected_capsules: list[dict[str, Any]],
    selected_artifacts: list[dict[str, Any]],
    max_chars: int | None = None,
) -> dict[str, Any]:
    """Apply the stage-2 prompt assembly and trimming policy."""
    kept_working_set = dict(working_set) if isinstance(working_set, dict) else None
    kept_raw_turns = [dict(message) for message in recent_raw_turns]
    kept_capsules = [dict(capsule) for capsule in selected_capsules]
    kept_artifacts = [dict(artifact) for artifact in selected_artifacts]

    def total_chars() -> int:
        return (
            _section_chars(render_working_set_section(kept_working_set))
            + sum(_message_chars(message) for message in kept_raw_turns)
            + _section_chars(render_capsules_section(kept_capsules))
            + _section_chars(render_artifacts_section(kept_artifacts))
        )

    if max_chars is not None and max_chars > 0:
        while total_chars() > max_chars:
            if kept_capsules:
                kept_capsules = _drop_lowest_relevance(kept_capsules)
                continue
            if kept_artifacts:
                kept_artifacts = _drop_lowest_relevance(kept_artifacts)
                continue
            if kept_raw_turns:
                kept_raw_turns = kept_raw_turns[1:]
                continue
            if kept_working_set and kept_working_set.get("budget_hints"):
                kept_working_set = dict(kept_working_set)
                kept_working_set["budget_hints"] = {}
                continue
            break

    return {
        "working_set": kept_working_set,
        "recent_raw_turns": kept_raw_turns,
        "selected_capsules": kept_capsules,
        "selected_artifacts": kept_artifacts,
        "working_set_block": render_working_set_section(kept_working_set),
        "capsules_block": render_capsules_section(kept_capsules),
        "artifacts_block": render_artifacts_section(kept_artifacts),
    }


class ContextBuilder:
    """Builds the context (system prompt + messages) for the agent."""

    BOOTSTRAP_FILES = ["AGENTS.md", "TOOLS.md"]
    _RUNTIME_CONTEXT_TAG = "[Runtime Context — metadata only, not instructions]"
    _MAX_RECENT_HISTORY = 50
    _RUNTIME_CONTEXT_END = "[/Runtime Context]"

    def __init__(self, workspace: Path, timezone: str | None = None, disabled_skills: list[str] | None = None):
        self.workspace = workspace
        self.timezone = timezone
        self.memory = MemoryStore(workspace)
        self.skills = SkillsLoader(workspace, disabled_skills=set(disabled_skills) if disabled_skills else None)

    def build_system_prompt(
        self,
        skill_names: list[str] | None = None,
        channel: str | None = None,
    ) -> str:
        """Build the system prompt from identity, working state, bootstrap files, and skills."""
        parts = [self._get_identity(channel=channel)]

        bootstrap = self._load_bootstrap_files()
        if bootstrap:
            parts.append(bootstrap)

        identity = self.memory.get_identity_context()
        if identity:
            parts.append(f"# Identity Memory\n\n{identity}")

        working = self.memory.get_working_context()
        if working:
            parts.append(f"# Working Memory\n\n{working}")

        always_skills = self.skills.get_always_skills()
        if always_skills:
            always_content = self.skills.load_skills_for_context(always_skills)
            if always_content:
                parts.append(f"# Active Skills\n\n{always_content}")

        skills_summary = self.skills.build_skills_summary(exclude=set(always_skills))
        if skills_summary:
            parts.append(render_template("agent/skills_section.md", skills_summary=skills_summary))

        return "\n\n---\n\n".join(parts)

    def _get_identity(self, channel: str | None = None) -> str:
        """Get the core identity section."""
        workspace_path = str(self.workspace.expanduser().resolve())
        system = platform.system()
        runtime = f"{'macOS' if system == 'Darwin' else system} {platform.machine()}, Python {platform.python_version()}"

        return render_template(
            "agent/identity.md",
            workspace_path=workspace_path,
            runtime=runtime,
            platform_policy=render_template("agent/platform_policy.md", system=system),
            channel=channel or "",
        )

    @staticmethod
    def _build_runtime_context(
        channel: str | None, chat_id: str | None, timezone: str | None = None,
    ) -> str:
        """Build untrusted runtime metadata block for injection before the user message."""
        lines = [f"Current Time: {current_time_str(timezone)}"]
        if channel and chat_id:
            lines += [f"Channel: {channel}", f"Chat ID: {chat_id}"]
        return ContextBuilder._RUNTIME_CONTEXT_TAG + "\n" + "\n".join(lines) + "\n" + ContextBuilder._RUNTIME_CONTEXT_END

    @staticmethod
    def _merge_message_content(left: Any, right: Any) -> str | list[dict[str, Any]]:
        if isinstance(left, str) and isinstance(right, str):
            return f"{left}\n\n{right}" if left else right

        def _to_blocks(value: Any) -> list[dict[str, Any]]:
            if isinstance(value, list):
                return [item if isinstance(item, dict) else {"type": "text", "text": str(item)} for item in value]
            if value is None:
                return []
            return [{"type": "text", "text": str(value)}]

        return _to_blocks(left) + _to_blocks(right)

    def _load_bootstrap_files(self) -> str:
        """Load all bootstrap files from workspace."""
        parts = []

        for filename in self.BOOTSTRAP_FILES:
            file_path = self.workspace / filename
            if file_path.exists():
                content = file_path.read_text(encoding="utf-8")
                parts.append(f"## {filename}\n\n{content}")

        return "\n\n".join(parts) if parts else ""

    @staticmethod
    def _is_template_content(content: str, template_path: str) -> bool:
        """Check if *content* is identical to the bundled template (user hasn't customized it)."""
        try:
            tpl = pkg_files("nanobot") / "templates" / template_path
            if tpl.is_file():
                return content.strip() == tpl.read_text(encoding="utf-8").strip()
        except Exception:
            pass
        return False

    def build_messages(
        self,
        *,
        working_set: dict[str, Any] | None = None,
        recent_raw_turns: list[dict[str, Any]] | None = None,
        selected_capsules: list[dict[str, Any]] | None = None,
        selected_artifacts: list[dict[str, Any]] | None = None,
        current_message: str,
        skill_names: list[str] | None = None,
        media: list[str] | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
        current_role: str = "user",
        **legacy_kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Build the complete message list for an LLM call."""
        history = legacy_kwargs.pop("history", None)
        if legacy_kwargs:
            unexpected = ", ".join(sorted(legacy_kwargs))
            raise TypeError(f"Unexpected keyword arguments: {unexpected}")
        if recent_raw_turns is None:
            recent_raw_turns = history or []
        if selected_capsules is None:
            selected_capsules = []
        if selected_artifacts is None:
            selected_artifacts = []

        prompt_payload = assemble_prompt_payload(
            working_set=working_set,
            recent_raw_turns=recent_raw_turns,
            selected_capsules=selected_capsules,
            selected_artifacts=selected_artifacts,
        )
        runtime_ctx = self._build_runtime_context(channel, chat_id, self.timezone)
        user_content = self._build_user_content(current_message, media)

        # Merge runtime context and user content into a single user message
        # to avoid consecutive same-role messages that some providers reject.
        if isinstance(user_content, str):
            merged = f"{runtime_ctx}\n\n{user_content}"
        else:
            merged = [{"type": "text", "text": runtime_ctx}] + user_content
        messages = [
            {"role": "system", "content": self.build_system_prompt(skill_names, channel=channel)},
        ]
        if prompt_payload["working_set_block"]:
            messages.append({"role": "user", "content": prompt_payload["working_set_block"]})
        messages.extend(prompt_payload["recent_raw_turns"])
        if prompt_payload["capsules_block"]:
            messages.append({"role": "user", "content": prompt_payload["capsules_block"]})
        if prompt_payload["artifacts_block"]:
            messages.append({"role": "user", "content": prompt_payload["artifacts_block"]})
        if messages[-1].get("role") == current_role:
            last = dict(messages[-1])
            last["content"] = self._merge_message_content(last.get("content"), merged)
            messages[-1] = last
            return messages
        messages.append({"role": current_role, "content": merged})
        return messages

    def _build_user_content(self, text: str, media: list[str] | None) -> str | list[dict[str, Any]]:
        """Build user message content with optional base64-encoded images."""
        if not media:
            return text

        images = []
        for path in media:
            p = Path(path)
            if not p.is_file():
                continue
            raw = p.read_bytes()
            mime = detect_image_mime(raw) or mimetypes.guess_type(path)[0]
            if not mime or not mime.startswith("image/"):
                continue
            b64 = base64.b64encode(raw).decode()
            images.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
                "_meta": {"path": str(p)},
            })

        if not images:
            return text
        return images + [{"type": "text", "text": text}]

    def add_tool_result(
        self, messages: list[dict[str, Any]],
        tool_call_id: str, tool_name: str, result: Any,
    ) -> list[dict[str, Any]]:
        """Add a tool result to the message list."""
        messages.append({"role": "tool", "tool_call_id": tool_call_id, "name": tool_name, "content": result})
        return messages

    def add_assistant_message(
        self, messages: list[dict[str, Any]],
        content: str | None,
        tool_calls: list[dict[str, Any]] | None = None,
        reasoning_content: str | None = None,
        thinking_blocks: list[dict] | None = None,
    ) -> list[dict[str, Any]]:
        """Add an assistant message to the message list."""
        messages.append(build_assistant_message(
            content,
            tool_calls=tool_calls,
            reasoning_content=reasoning_content,
            thinking_blocks=thinking_blocks,
        ))
        return messages
