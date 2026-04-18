"""Agent loop: the core processing engine."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import time
from contextlib import AsyncExitStack, nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable
from uuid import uuid4

from loguru import logger

from nanobot.agent.autocompact import AutoCompact
from nanobot.agent.context import ContextBuilder
from nanobot.agent.hook import AgentHook, AgentHookContext, CompositeHook
from nanobot.agent.memory import Consolidator, Dream
from nanobot.agent.promoter import Promoter
from nanobot.agent.runner import _MAX_INJECTIONS_PER_TURN, AgentRunner, AgentRunSpec
from nanobot.agent.skills import BUILTIN_SKILLS_DIR
from nanobot.agent.subagent import SubagentManager
from nanobot.agent.tools.cron import CronTool
from nanobot.agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from nanobot.agent.tools.message import MessageTool
from nanobot.agent.tools.notebook import NotebookEditTool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.search import GlobTool, GrepTool
from nanobot.agent.tools.shell import ExecTool
from nanobot.agent.tools.spawn import SpawnTool
from nanobot.agent.tools.web import WebFetchTool, WebSearchTool
from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.command import CommandContext, CommandRouter, register_builtin_commands
from nanobot.config.schema import AgentDefaults
from nanobot.providers.base import LLMProvider
from nanobot.session.manager import Session, SessionManager
from nanobot.session.state import (
    COMMIT_STATE_COMMITTED,
    COMMIT_STATE_OPEN,
    COMMIT_STATE_REPAIR_NEEDED,
    TURN_STAGE_AWAITING_MODEL,
    TURN_STAGE_AWAITING_TOOLS,
    TURN_STAGE_COLLECTING_USER,
    TURN_STAGE_COMPLETED,
    TURN_STAGE_FINALIZING,
    TURN_STAGE_INTERRUPTED,
    build_turn_state,
    make_ref,
    REF_ARTIFACT,
    REF_CAPSULE,
    REF_COMMIT,
    REF_RESPONSE,
    timestamp as state_timestamp,
)
from nanobot.utils.document import extract_documents
from nanobot.utils.helpers import image_placeholder_text, stringify_text_blocks
from nanobot.utils.helpers import truncate_text as truncate_text_fn
from nanobot.utils.runtime import EMPTY_FINAL_RESPONSE_MESSAGE

if TYPE_CHECKING:
    from nanobot.config.schema import ChannelsConfig, ExecToolConfig, WebToolsConfig
    from nanobot.cron.service import CronService


UNIFIED_SESSION_KEY = "unified:default"


class _LoopHook(AgentHook):
    """Core hook for the main loop."""

    def __init__(
        self,
        agent_loop: AgentLoop,
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
        *,
        channel: str = "cli",
        chat_id: str = "direct",
        message_id: str | None = None,
    ) -> None:
        super().__init__(reraise=True)
        self._loop = agent_loop
        self._on_progress = on_progress
        self._on_stream = on_stream
        self._on_stream_end = on_stream_end
        self._channel = channel
        self._chat_id = chat_id
        self._message_id = message_id
        self._stream_buf = ""

    def wants_streaming(self) -> bool:
        return self._on_stream is not None

    async def on_stream(self, context: AgentHookContext, delta: str) -> None:
        from nanobot.utils.helpers import strip_think

        prev_clean = strip_think(self._stream_buf)
        self._stream_buf += delta
        new_clean = strip_think(self._stream_buf)
        incremental = new_clean[len(prev_clean) :]
        if incremental and self._on_stream:
            await self._on_stream(incremental)

    async def on_stream_end(self, context: AgentHookContext, *, resuming: bool) -> None:
        if self._on_stream_end:
            await self._on_stream_end(resuming=resuming)
        self._stream_buf = ""

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        if self._on_progress:
            if not self._on_stream:
                thought = self._loop._strip_think(
                    context.response.content if context.response else None
                )
                if thought:
                    await self._on_progress(thought)
            tool_hint = self._loop._strip_think(self._loop._tool_hint(context.tool_calls))
            await self._on_progress(tool_hint, tool_hint=True)
        for tc in context.tool_calls:
            args_str = json.dumps(tc.arguments, ensure_ascii=False)
            logger.info("Tool call: {}({})", tc.name, args_str[:200])
        self._loop._set_tool_context(self._channel, self._chat_id, self._message_id)

    async def after_iteration(self, context: AgentHookContext) -> None:
        u = context.usage or {}
        logger.debug(
            "LLM usage: prompt={} completion={} cached={}",
            u.get("prompt_tokens", 0),
            u.get("completion_tokens", 0),
            u.get("cached_tokens", 0),
        )

    def finalize_content(self, context: AgentHookContext, content: str | None) -> str | None:
        return self._loop._strip_think(content)


class AgentLoop:
    """
    The agent loop is the core processing engine.

    It:
    1. Receives messages from the bus
    2. Builds context with history, memory, skills
    3. Calls the LLM
    4. Executes tool calls
    5. Sends responses back
    """

    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        max_iterations: int | None = None,
        context_window_tokens: int | None = None,
        context_block_limit: int | None = None,
        max_tool_result_chars: int | None = None,
        provider_retry_mode: str = "standard",
        web_config: WebToolsConfig | None = None,
        exec_config: ExecToolConfig | None = None,
        cron_service: CronService | None = None,
        restrict_to_workspace: bool = False,
        session_manager: SessionManager | None = None,
        mcp_servers: dict | None = None,
        channels_config: ChannelsConfig | None = None,
        timezone: str | None = None,
        session_ttl_minutes: int = 0,
        hooks: list[AgentHook] | None = None,
        unified_session: bool = False,
        disabled_skills: list[str] | None = None,
    ):
        from nanobot.config.schema import ExecToolConfig, WebToolsConfig

        defaults = AgentDefaults()
        self.bus = bus
        self.channels_config = channels_config
        self.provider = provider
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.max_iterations = (
            max_iterations if max_iterations is not None else defaults.max_tool_iterations
        )
        self.context_window_tokens = (
            context_window_tokens
            if context_window_tokens is not None
            else defaults.context_window_tokens
        )
        self.context_block_limit = context_block_limit
        self.max_tool_result_chars = (
            max_tool_result_chars
            if max_tool_result_chars is not None
            else defaults.max_tool_result_chars
        )
        self.provider_retry_mode = provider_retry_mode
        self.web_config = web_config or WebToolsConfig()
        self.exec_config = exec_config or ExecToolConfig()
        self.cron_service = cron_service
        self.restrict_to_workspace = restrict_to_workspace
        self._start_time = time.time()
        self._last_usage: dict[str, int] = {}
        self._extra_hooks: list[AgentHook] = hooks or []

        self.context = ContextBuilder(workspace, timezone=timezone, disabled_skills=disabled_skills)
        self.sessions = session_manager or SessionManager(workspace)
        self.tools = ToolRegistry()
        self.runner = AgentRunner(provider)
        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=self.model,
            web_config=self.web_config,
            max_tool_result_chars=self.max_tool_result_chars,
            exec_config=self.exec_config,
            restrict_to_workspace=restrict_to_workspace,
            disabled_skills=disabled_skills,
        )
        self._unified_session = unified_session
        self._running = False
        self._mcp_servers = mcp_servers or {}
        self._mcp_stacks: dict[str, AsyncExitStack] = {}
        self._mcp_connected = False
        self._mcp_connecting = False
        self._active_tasks: dict[str, list[asyncio.Task]] = {}  # session_key -> tasks
        self._background_tasks: list[asyncio.Task] = []
        self._session_locks: dict[str, asyncio.Lock] = {}
        # Per-session pending queues for mid-turn message injection.
        # When a session has an active task, new messages for that session
        # are routed here instead of creating a new task.
        self._pending_queues: dict[str, asyncio.Queue] = {}
        # NANOBOT_MAX_CONCURRENT_REQUESTS: <=0 means unlimited; default 3.
        _max = int(os.environ.get("NANOBOT_MAX_CONCURRENT_REQUESTS", "3"))
        self._concurrency_gate: asyncio.Semaphore | None = (
            asyncio.Semaphore(_max) if _max > 0 else None
        )
        self.consolidator = Consolidator(
            store=self.context.memory,
            provider=provider,
            model=self.model,
            sessions=self.sessions,
            context_window_tokens=context_window_tokens,
            build_messages=self.context.build_messages,
            get_tool_definitions=self.tools.get_definitions,
            max_completion_tokens=provider.generation.max_tokens,
        )
        self.auto_compact = AutoCompact(
            sessions=self.sessions,
            consolidator=self.consolidator,
            session_ttl_minutes=session_ttl_minutes,
        )
        self.dream = Dream(
            store=self.context.memory,
            provider=provider,
            model=self.model,
        )
        self.promoter = Promoter(store=self.context.memory)
        self._register_default_tools()
        self.commands = CommandRouter()
        register_builtin_commands(self.commands)

    def _register_default_tools(self) -> None:
        """Register the default set of tools."""
        allowed_dir = (
            self.workspace if (self.restrict_to_workspace or self.exec_config.sandbox) else None
        )
        extra_read = [BUILTIN_SKILLS_DIR] if allowed_dir else None
        self.tools.register(
            ReadFileTool(
                workspace=self.workspace, allowed_dir=allowed_dir, extra_allowed_dirs=extra_read
            )
        )
        for cls in (WriteFileTool, EditFileTool, ListDirTool):
            self.tools.register(cls(workspace=self.workspace, allowed_dir=allowed_dir))
        for cls in (GlobTool, GrepTool):
            self.tools.register(cls(workspace=self.workspace, allowed_dir=allowed_dir))
        self.tools.register(NotebookEditTool(workspace=self.workspace, allowed_dir=allowed_dir))
        if self.exec_config.enable:
            self.tools.register(
                ExecTool(
                    working_dir=str(self.workspace),
                    timeout=self.exec_config.timeout,
                    restrict_to_workspace=self.restrict_to_workspace,
                    sandbox=self.exec_config.sandbox,
                    path_append=self.exec_config.path_append,
                    allowed_env_keys=self.exec_config.allowed_env_keys,
                )
            )
        if self.web_config.enable:
            self.tools.register(
                WebSearchTool(config=self.web_config.search, proxy=self.web_config.proxy)
            )
            self.tools.register(WebFetchTool(proxy=self.web_config.proxy))
        self.tools.register(MessageTool(send_callback=self.bus.publish_outbound))
        self.tools.register(SpawnTool(manager=self.subagents))
        if self.cron_service:
            self.tools.register(
                CronTool(self.cron_service, default_timezone=self.context.timezone or "UTC")
            )

    async def _connect_mcp(self) -> None:
        """Connect to configured MCP servers (one-time, lazy)."""
        if self._mcp_connected or self._mcp_connecting or not self._mcp_servers:
            return
        self._mcp_connecting = True
        from nanobot.agent.tools.mcp import connect_mcp_servers

        try:
            self._mcp_stacks = await connect_mcp_servers(self._mcp_servers, self.tools)
            if self._mcp_stacks:
                self._mcp_connected = True
            else:
                logger.warning("No MCP servers connected successfully (will retry next message)")
        except asyncio.CancelledError:
            logger.warning("MCP connection cancelled (will retry next message)")
            self._mcp_stacks.clear()
        except BaseException as e:
            logger.error("Failed to connect MCP servers (will retry next message): {}", e)
            self._mcp_stacks.clear()
        finally:
            self._mcp_connecting = False

    def _set_tool_context(self, channel: str, chat_id: str, message_id: str | None = None) -> None:
        """Update context for all tools that need routing info."""
        for name in ("message", "spawn", "cron"):
            if tool := self.tools.get(name):
                if hasattr(tool, "set_context"):
                    tool.set_context(channel, chat_id, *([message_id] if name == "message" else []))

    @staticmethod
    def _strip_think(text: str | None) -> str | None:
        """Remove <think>…</think> blocks that some models embed in content."""
        if not text:
            return None
        from nanobot.utils.helpers import strip_think

        return strip_think(text) or None

    @staticmethod
    def _tool_hint(tool_calls: list) -> str:
        """Format tool calls as concise hints with smart abbreviation."""
        from nanobot.utils.tool_hints import format_tool_hints

        return format_tool_hints(tool_calls)

    def _effective_session_key(self, msg: InboundMessage) -> str:
        """Return the session key used for task routing and mid-turn injections."""
        if self._unified_session and not msg.session_key_override:
            return UNIFIED_SESSION_KEY
        return msg.session_key

    @staticmethod
    def _new_object_id(prefix: str) -> str:
        return f"{prefix}_{uuid4().hex}"

    def load_active_turn_state(self, session_key: str) -> dict[str, Any] | None:
        return self.sessions.load_active_turn_state(session_key)

    def create_turn_state(
        self,
        session_key: str,
        *,
        current_stage: str = TURN_STAGE_COLLECTING_USER,
    ) -> dict[str, Any]:
        turn_id = self._new_object_id("turn")
        turn_state = build_turn_state(
            session_key=session_key,
            turn_id=turn_id,
            current_stage=current_stage,
        )
        turn_state["resume_action"] = self.compute_resume_action(turn_state)
        self.persist_turn_state(
            turn_state,
            publish_active=True,
            publish_latest=True,
        )
        return turn_state

    def persist_turn_state(
        self,
        turn_state: dict[str, Any],
        *,
        expected_revision: int | None = None,
        publish_active: bool = True,
        publish_latest: bool = False,
    ) -> dict[str, Any]:
        persisted = dict(turn_state)
        persisted["updated_at"] = state_timestamp()
        self.sessions.save_turn_state(
            persisted["session_key"],
            persisted["turn_id"],
            persisted,
            expected_revision=expected_revision,
        )
        if publish_latest:
            self.sessions.publish_latest_turn(persisted["session_key"], persisted["turn_id"])
        if publish_active:
            if persisted.get("current_stage") == TURN_STAGE_COMPLETED:
                self.sessions.publish_active_turn(persisted["session_key"], None)
            else:
                self.sessions.publish_active_turn(persisted["session_key"], persisted["turn_id"])
        return persisted

    def compute_resume_action(self, turn_state: dict[str, Any]) -> str | None:
        stage = turn_state.get("current_stage")
        declared = turn_state.get("declared_tool_calls") or []
        completed = turn_state.get("completed_tool_results") or []
        declared_ids = {
            tc.get("id")
            for tc in declared
            if isinstance(tc, dict) and tc.get("id")
        }
        completed_ids = {
            result.get("tool_call_id")
            for result in completed
            if isinstance(result, dict) and result.get("tool_call_id")
        }
        pending_tools = bool(declared_ids - completed_ids)

        if stage == TURN_STAGE_AWAITING_TOOLS:
            return "await_tools" if pending_tools else "request_model_again"
        if stage == TURN_STAGE_AWAITING_MODEL:
            return "request_model_again" if (
                turn_state.get("user_message_ref")
                or turn_state.get("injected_messages")
                or completed
            ) else "replan"
        if stage == TURN_STAGE_FINALIZING:
            return "tail_finalize"
        if stage == TURN_STAGE_INTERRUPTED:
            if pending_tools:
                return "await_tools"
            if completed or turn_state.get("final_response_ref"):
                return "tail_finalize"
            if turn_state.get("injected_messages"):
                return "request_model_again"
            if turn_state.get("user_message_ref"):
                return "replan"
            return "replan"
        if stage == TURN_STAGE_COMPLETED:
            return None
        if stage == TURN_STAGE_COLLECTING_USER:
            return "tail_finalize" if turn_state.get("user_message_ref") else "replan"
        return None

    def _advance_turn_state(
        self,
        turn_state: dict[str, Any],
        *,
        expected_revision: int | None = None,
        publish_active: bool = True,
        publish_latest: bool = False,
        **changes: Any,
    ) -> dict[str, Any]:
        current_revision = int(turn_state.get("revision", 0))
        next_state = dict(turn_state)
        next_state.update(changes)
        next_state["revision"] = current_revision + 1
        next_state["resume_action"] = self.compute_resume_action(next_state)
        return self.persist_turn_state(
            next_state,
            expected_revision=current_revision if expected_revision is None else expected_revision,
            publish_active=publish_active,
            publish_latest=publish_latest,
        )

    def _save_message_object(
        self,
        *,
        session_key: str,
        turn_id: str,
        role: str,
        content: Any,
    ) -> str:
        message_id = self._new_object_id("msg")
        self.sessions.save_message_object(
            session_key,
            message_id,
            {
                "message_id": message_id,
                "session_key": session_key,
                "turn_id": turn_id,
                "role": role,
                "content": content,
                "created_at": state_timestamp(),
            },
        )
        return make_ref("message", message_id)

    def _build_message_object_content(self, text: str, media: list[str] | None) -> Any:
        return self.context._build_user_content(text, media if media else None)

    @staticmethod
    def _merge_records_by_key(
        existing: list[dict[str, Any]],
        incoming: list[dict[str, Any]],
        *,
        key: str,
    ) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        positions: dict[Any, int] = {}
        for record in list(existing) + list(incoming):
            if not isinstance(record, dict):
                continue
            value = record.get(key)
            item = dict(record)
            if value in (None, ""):
                merged.append(item)
                continue
            if value in positions:
                merged[positions[value]] = item
                continue
            positions[value] = len(merged)
            merged.append(item)
        return merged

    @staticmethod
    def _merge_refs(existing: list[str], incoming: list[str]) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()
        for ref in list(existing) + list(incoming):
            if not isinstance(ref, str) or not ref:
                continue
            if ref in seen:
                continue
            seen.add(ref)
            merged.append(ref)
        return merged

    @staticmethod
    def _artifact_refs_from_results(results: list[dict[str, Any]]) -> list[str]:
        refs: list[str] = []
        for result in results:
            if not isinstance(result, dict):
                continue
            if result.get("eligible_for_commit") is False or result.get("stale") is True:
                continue
            artifact_ref = result.get("artifact_ref")
            if isinstance(artifact_ref, str) and artifact_ref:
                refs.append(artifact_ref)
        return refs

    def _content_to_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text = stringify_text_blocks(content)
            if text is not None:
                return text
            return json.dumps(content, ensure_ascii=False)
        if content is None:
            return ""
        return str(content)

    def _resolve_ref_content_text(self, session_key: str, ref: str | None) -> str:
        if not isinstance(ref, str) or not ref:
            return ""
        resolved = self.sessions.resolve_ref(session_key, ref)
        if not isinstance(resolved, dict):
            return ""
        return self._content_to_text(resolved.get("content"))

    def _latest_working_set_version(self, session_key: str) -> int | None:
        latest = self.sessions.load_latest_working_set(session_key)
        if not isinstance(latest, dict):
            return None
        version = latest.get("version")
        return version if isinstance(version, int) else None

    def _build_turn_capsule(
        self,
        session: Session,
        turn_state: dict[str, Any],
        *,
        capsule_id: str,
        artifact_refs: list[str],
        final_content: str,
        source_revision: int,
    ) -> dict[str, Any]:
        user_goal = self._resolve_ref_content_text(session.key, turn_state.get("user_message_ref")).strip()
        if not user_goal:
            user_goal = "Continue the current task."
        return {
            "capsule_id": capsule_id,
            "turn_id": turn_state["turn_id"],
            "session_key": session.key,
            "source_revision": source_revision,
            "user_goal": user_goal,
            "assistant_intent": truncate_text_fn(final_content or user_goal, 400),
            "decisions": [],
            "outcomes": [truncate_text_fn(final_content, 800)] if final_content else [],
            "open_questions": [],
            "artifact_refs": list(artifact_refs),
            "next_expected_action": None,
            "capsule_version": 1,
            "created_at": state_timestamp(),
        }

    def _build_working_set_snapshot(
        self,
        session: Session,
        turn_state: dict[str, Any],
        *,
        version: int,
        capsule_ref: str | None,
        artifact_refs: list[str],
        source_revision: int,
    ) -> dict[str, Any]:
        user_goal = self._resolve_ref_content_text(session.key, turn_state.get("user_message_ref")).strip()
        return {
            "session_key": session.key,
            "version": version,
            "source_turn_id": turn_state["turn_id"],
            "source_revision": source_revision,
            "is_stable": True,
            "published_by": "agent_loop",
            "active_task": None,
            "task_stage": None,
            "active_goals": [user_goal] if user_goal else [],
            "open_loops": [],
            "last_user_focus": user_goal or None,
            "relevant_capsule_refs": [capsule_ref] if capsule_ref else [],
            "relevant_artifact_refs": list(artifact_refs),
            "budget_hints": {},
            "source_turn_ids": [turn_state["turn_id"]],
            "created_at": state_timestamp(),
        }

    def _build_commit_manifest(
        self,
        session: Session,
        turn_state: dict[str, Any],
        *,
        commit_id: str,
        turn_revision: int,
        artifact_refs: list[str],
        capsule_ref: str | None,
        working_set_version: int | None,
        final_response_ref: str | None,
    ) -> dict[str, Any]:
        return {
            "commit_id": commit_id,
            "turn_id": turn_state["turn_id"],
            "session_key": session.key,
            "turn_revision": turn_revision,
            "artifact_refs": list(artifact_refs),
            "capsule_ref": capsule_ref,
            "working_set_version": working_set_version,
            "final_response_ref": final_response_ref,
            "completed_marker": True,
            "created_at": state_timestamp(),
        }

    def _commit_validation_errors(self, session_key: str, turn_state: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        commit_ref = turn_state.get("commit_manifest_ref")
        if not isinstance(commit_ref, str) or not commit_ref:
            errors.append("missing_commit_manifest_ref")
            return errors
        if turn_state.get("commit_state") != COMMIT_STATE_COMMITTED:
            errors.append("commit_state_not_committed")
        manifest = self.sessions.resolve_ref(session_key, commit_ref)
        if not isinstance(manifest, dict):
            errors.append("missing_commit_manifest")
            return errors
        if manifest.get("turn_revision") != turn_state.get("revision"):
            errors.append("turn_revision_mismatch")
        final_response_ref = manifest.get("final_response_ref")
        if isinstance(final_response_ref, str) and final_response_ref:
            if self.sessions.resolve_ref(session_key, final_response_ref) is None:
                errors.append("missing_final_response")
        else:
            errors.append("missing_final_response_ref")
        capsule_ref = manifest.get("capsule_ref")
        if isinstance(capsule_ref, str) and capsule_ref:
            if self.sessions.resolve_ref(session_key, capsule_ref) is None:
                errors.append("missing_capsule")
        working_set_version = manifest.get("working_set_version")
        if isinstance(working_set_version, int):
            if self.sessions.load_working_set(session_key, working_set_version) is None:
                errors.append("missing_working_set")
        for artifact_ref in manifest.get("artifact_refs") or []:
            if self.sessions.resolve_ref(session_key, artifact_ref) is None:
                errors.append("missing_artifact")
                break
        return errors

    def _register_turn_message(
        self,
        session: Session,
        turn_state: dict[str, Any],
        *,
        role: str,
        content: Any,
        is_injection: bool,
    ) -> dict[str, Any]:
        message_ref = self._save_message_object(
            session_key=session.key,
            turn_id=turn_state["turn_id"],
            role=role,
            content=content,
        )
        if not is_injection and not turn_state.get("user_message_ref"):
            return self._advance_turn_state(
                turn_state,
                user_message_ref=message_ref,
                current_stage=TURN_STAGE_AWAITING_MODEL,
            )

        injected = list(turn_state.get("injected_messages") or [])
        injection_revision = int(turn_state.get("injection_revision", 0)) + 1
        injected.append(
            {
                "message_ref": message_ref,
                "role": role,
                "content": content,
                "injection_revision": injection_revision,
                "created_at": state_timestamp(),
            }
        )
        return self._advance_turn_state(
            turn_state,
            injected_messages=injected,
            injection_revision=injection_revision,
            current_stage=TURN_STAGE_AWAITING_MODEL,
        )

    def _checkpoint_stage(self, payload: dict[str, Any]) -> str:
        phase = payload.get("phase")
        pending_tool_calls = payload.get("pending_tool_calls") or []
        if phase == "awaiting_tools" or pending_tool_calls:
            return TURN_STAGE_AWAITING_TOOLS
        if phase == "tools_completed":
            return TURN_STAGE_AWAITING_MODEL
        if phase == "final_response":
            return TURN_STAGE_FINALIZING
        return TURN_STAGE_AWAITING_MODEL

    def _sync_turn_state_from_checkpoint(
        self,
        session: Session,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        turn_state = self.load_active_turn_state(session.key)
        if not isinstance(turn_state, dict):
            return None
        assistant_message = payload.get("assistant_message")
        error_state = dict(turn_state.get("error_state") or {})
        if isinstance(assistant_message, dict):
            error_state["checkpoint_assistant_message"] = assistant_message
        if phase := payload.get("phase"):
            error_state["checkpoint_phase"] = phase
        merged_declared = self._merge_records_by_key(
            list(turn_state.get("declared_tool_calls") or []),
            list(payload.get("pending_tool_calls") or []),
            key="id",
        )
        merged_completed = self._merge_records_by_key(
            list(turn_state.get("completed_tool_results") or []),
            list(payload.get("completed_tool_results") or []),
            key="tool_call_id",
        )
        merged_artifact_refs = self._merge_refs(
            list(turn_state.get("artifact_refs") or []),
            self._artifact_refs_from_results(list(payload.get("completed_tool_results") or [])),
        )
        return self._advance_turn_state(
            turn_state,
            current_stage=self._checkpoint_stage(payload),
            declared_tool_calls=merged_declared,
            completed_tool_results=merged_completed,
            artifact_refs=merged_artifact_refs,
            error_state=error_state or None,
        )

    @staticmethod
    def _pending_tool_calls_from_turn_state(turn_state: dict[str, Any]) -> list[dict[str, Any]]:
        completed_ids = {
            result.get("tool_call_id")
            for result in (turn_state.get("completed_tool_results") or [])
            if isinstance(result, dict) and result.get("tool_call_id")
        }
        pending: list[dict[str, Any]] = []
        for tool_call in turn_state.get("declared_tool_calls") or []:
            if not isinstance(tool_call, dict):
                continue
            tool_id = tool_call.get("id")
            if tool_id and tool_id not in completed_ids:
                pending.append(tool_call)
        return pending

    def _materialize_turn_state_messages(self, turn_state: dict[str, Any]) -> list[dict[str, Any]]:
        restored_messages: list[dict[str, Any]] = []
        error_state = turn_state.get("error_state") or {}
        assistant_message = error_state.get("checkpoint_assistant_message")
        if isinstance(assistant_message, dict):
            restored = dict(assistant_message)
            restored.setdefault("timestamp", state_timestamp())
            restored_messages.append(restored)
        for message in turn_state.get("completed_tool_results") or []:
            if isinstance(message, dict):
                restored = dict(message)
                restored.setdefault("timestamp", state_timestamp())
                restored_messages.append(restored)
        for tool_call in self._pending_tool_calls_from_turn_state(turn_state):
            tool_id = tool_call.get("id")
            name = ((tool_call.get("function") or {}).get("name")) or "tool"
            restored_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "name": name,
                    "content": "Error: Task interrupted before this tool finished.",
                    "timestamp": state_timestamp(),
                }
            )
        session_last_is_user = turn_state.get("user_message_ref")
        if (
            not restored_messages
            and turn_state.get("current_stage") == TURN_STAGE_INTERRUPTED
            and session_last_is_user
        ):
            restored_messages.append(
                {
                    "role": "assistant",
                    "content": "Error: Task interrupted before a response was generated.",
                    "timestamp": state_timestamp(),
                    "_source_user_message_ref": session_last_is_user,
                }
            )
        return restored_messages

    def _append_projected_messages(self, session: Session, restored_messages: list[dict[str, Any]]) -> bool:
        if not restored_messages:
            return False
        overlap = 0
        max_overlap = min(len(session.messages), len(restored_messages))
        for size in range(max_overlap, 0, -1):
            existing = session.messages[-size:]
            restored = restored_messages[:size]
            if all(
                self._checkpoint_message_key(left) == self._checkpoint_message_key(right)
                for left, right in zip(existing, restored)
            ):
                overlap = size
                break
        session.messages.extend(restored_messages[overlap:])
        return len(restored_messages[overlap:]) > 0

    def _restore_turn_state(self, session: Session) -> bool:
        turn_state = self.load_active_turn_state(session.key)
        if not isinstance(turn_state, dict):
            return False
        if turn_state.get("current_stage") == TURN_STAGE_COMPLETED or turn_state.get("commit_manifest_ref"):
            validation_errors = self._commit_validation_errors(session.key, turn_state)
            if validation_errors:
                turn_state = self.repair_partial_commit(session, turn_state) or turn_state
            if (
                isinstance(turn_state, dict)
                and turn_state.get("current_stage") == TURN_STAGE_COMPLETED
                and not self._commit_validation_errors(session.key, turn_state)
            ):
                self.sessions.publish_active_turn(session.key, None)
                self.sessions.publish_latest_turn(session.key, turn_state["turn_id"])
                return False
        restored = self._materialize_turn_state_messages(turn_state)
        return self._append_projected_messages(session, restored)

    def finalize_turn(
        self,
        session: Session,
        turn_state: dict[str, Any] | None,
        *,
        final_content: str | None = None,
    ) -> dict[str, Any] | None:
        if not isinstance(turn_state, dict):
            return None
        final_text = (final_content or "").strip()
        if not final_text:
            for message in reversed(session.messages):
                if message.get("role") == "assistant" and not message.get("tool_calls"):
                    final_text = self._content_to_text(message.get("content")).strip()
                    if final_text:
                        break
        if not final_text:
            final_text = EMPTY_FINAL_RESPONSE_MESSAGE

        artifact_refs = self._merge_refs(
            list(turn_state.get("artifact_refs") or []),
            self._artifact_refs_from_results(list(turn_state.get("completed_tool_results") or [])),
        )
        current_revision = int(turn_state.get("revision", 0))
        final_revision = current_revision + 1

        capsule_id = self._new_object_id("capsule")
        capsule = self._build_turn_capsule(
            session,
            turn_state,
            capsule_id=capsule_id,
            artifact_refs=artifact_refs,
            final_content=final_text,
            source_revision=final_revision,
        )
        self.sessions.save_capsule(session.key, capsule_id, capsule)
        capsule_ref = make_ref(REF_CAPSULE, capsule_id)

        previous_working_set_version = self._latest_working_set_version(session.key)
        working_set_version = (previous_working_set_version or 0) + 1
        working_set = self._build_working_set_snapshot(
            session,
            turn_state,
            version=working_set_version,
            capsule_ref=capsule_ref,
            artifact_refs=artifact_refs,
            source_revision=final_revision,
        )
        self.sessions.save_working_set(session.key, working_set)

        response_id = self._new_object_id("resp")
        response = {
            "response_id": response_id,
            "session_key": session.key,
            "turn_id": turn_state["turn_id"],
            "source_revision": final_revision,
            "content": final_text,
            "created_at": state_timestamp(),
        }
        self.sessions.save_response_object(session.key, response_id, response)
        final_response_ref = make_ref(REF_RESPONSE, response_id)

        commit_id = turn_state.get("commit_id") or self._new_object_id("commit")
        manifest = self._build_commit_manifest(
            session,
            turn_state,
            commit_id=commit_id,
            turn_revision=final_revision,
            artifact_refs=artifact_refs,
            capsule_ref=capsule_ref,
            working_set_version=working_set_version,
            final_response_ref=final_response_ref,
        )
        self.sessions.save_commit_manifest(session.key, commit_id, manifest)
        commit_ref = make_ref(REF_COMMIT, commit_id)

        finalized = self._advance_turn_state(
            turn_state,
            expected_revision=current_revision,
            commit_id=commit_id,
            commit_manifest_ref=commit_ref,
            final_response_ref=final_response_ref,
            working_set_version=working_set_version,
            capsule_ref=capsule_ref,
            artifact_refs=artifact_refs,
            commit_state=COMMIT_STATE_COMMITTED,
            current_stage=TURN_STAGE_COMPLETED,
            publish_active=True,
            publish_latest=False,
        )
        self.sessions.publish_latest_turn(session.key, finalized["turn_id"])
        if working_set.get("is_stable") is True:
            self.sessions.publish_latest_working_set(
                session.key,
                working_set_version,
                expected_version=previous_working_set_version,
            )
        return finalized

    def repair_partial_commit(
        self,
        session: Session,
        turn_state: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not isinstance(turn_state, dict):
            return None
        repairing = self._advance_turn_state(
            turn_state,
            current_stage=TURN_STAGE_FINALIZING,
            commit_state=COMMIT_STATE_REPAIR_NEEDED,
            publish_active=True,
            publish_latest=False,
        )
        artifact_refs = self._merge_refs(
            list(repairing.get("artifact_refs") or []),
            self._artifact_refs_from_results(list(repairing.get("completed_tool_results") or [])),
        )
        if not artifact_refs:
            return self._advance_turn_state(
                repairing,
                current_stage=TURN_STAGE_INTERRUPTED,
                commit_state=COMMIT_STATE_REPAIR_NEEDED,
                final_response_ref=None,
                capsule_ref=None,
                working_set_version=None,
                commit_id=None,
                commit_manifest_ref=None,
                publish_active=True,
                publish_latest=False,
            )

        missing_artifact = any(
            self.sessions.resolve_ref(session.key, artifact_ref) is None
            for artifact_ref in artifact_refs
        )
        capsule_ref = repairing.get("capsule_ref")
        final_response_ref = repairing.get("final_response_ref")
        working_set_version = repairing.get("working_set_version")
        repairable = (
            isinstance(capsule_ref, str)
            and self.sessions.resolve_ref(session.key, capsule_ref) is not None
            and isinstance(final_response_ref, str)
            and self.sessions.resolve_ref(session.key, final_response_ref) is not None
            and isinstance(working_set_version, int)
            and self.sessions.load_working_set(session.key, working_set_version) is not None
            and not missing_artifact
        )
        if not repairable:
            return self._advance_turn_state(
                repairing,
                current_stage=TURN_STAGE_INTERRUPTED,
                commit_state=COMMIT_STATE_REPAIR_NEEDED,
                final_response_ref=None,
                capsule_ref=None,
                working_set_version=None,
                commit_id=None,
                commit_manifest_ref=None,
                publish_active=True,
                publish_latest=False,
            )

        commit_id = repairing.get("commit_id") or self._new_object_id("commit")
        manifest = self._build_commit_manifest(
            session,
            repairing,
            commit_id=commit_id,
            turn_revision=int(repairing.get("revision", 0)) + 1,
            artifact_refs=artifact_refs,
            capsule_ref=capsule_ref,
            working_set_version=working_set_version,
            final_response_ref=final_response_ref,
        )
        self.sessions.save_commit_manifest(session.key, commit_id, manifest)
        repaired = self._advance_turn_state(
            repairing,
            commit_id=commit_id,
            commit_manifest_ref=make_ref(REF_COMMIT, commit_id),
            artifact_refs=artifact_refs,
            commit_state=COMMIT_STATE_COMMITTED,
            current_stage=TURN_STAGE_COMPLETED,
            publish_active=True,
            publish_latest=False,
        )
        self.sessions.publish_latest_turn(session.key, repaired["turn_id"])
        if isinstance(working_set_version, int):
            latest_version = self._latest_working_set_version(session.key)
            if latest_version is None or working_set_version > latest_version:
                self.sessions.publish_latest_working_set(
                    session.key,
                    working_set_version,
                    expected_version=latest_version,
                )
        return repaired

    def _interrupt_turn(
        self,
        session: Session,
        *,
        default_resume: str | None = None,
    ) -> dict[str, Any] | None:
        turn_state = self.load_active_turn_state(session.key)
        if not isinstance(turn_state, dict):
            return None
        interrupted = self._advance_turn_state(
            turn_state,
            current_stage=TURN_STAGE_INTERRUPTED,
        )
        if default_resume is not None and interrupted.get("resume_action") is None:
            interrupted = self._advance_turn_state(
                interrupted,
                resume_action=default_resume,
            )
        return interrupted

    async def _run_agent_loop(
        self,
        initial_messages: list[dict],
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
        *,
        session: Session | None = None,
        channel: str = "cli",
        chat_id: str = "direct",
        message_id: str | None = None,
        pending_queue: asyncio.Queue | None = None,
    ) -> tuple[str | None, list[str], list[dict], str, bool]:
        """Run the agent iteration loop.

        *on_stream*: called with each content delta during streaming.
        *on_stream_end(resuming)*: called when a streaming session finishes.
        ``resuming=True`` means tool calls follow (spinner should restart);
        ``resuming=False`` means this is the final response.

        Returns (final_content, tools_used, messages, stop_reason, had_injections).
        """
        loop_hook = _LoopHook(
            self,
            on_progress=on_progress,
            on_stream=on_stream,
            on_stream_end=on_stream_end,
            channel=channel,
            chat_id=chat_id,
            message_id=message_id,
        )
        hook: AgentHook = (
            CompositeHook([loop_hook] + self._extra_hooks) if self._extra_hooks else loop_hook
        )

        async def _checkpoint(payload: dict[str, Any]) -> None:
            if session is None:
                return
            self._sync_turn_state_from_checkpoint(session, payload)

        async def _drain_pending(*, limit: int = _MAX_INJECTIONS_PER_TURN) -> list[dict[str, Any]]:
            """Non-blocking drain of follow-up messages from the pending queue."""
            if pending_queue is None:
                return []
            items: list[dict[str, Any]] = []
            while len(items) < limit:
                try:
                    pending_msg = pending_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                content = pending_msg.content
                media = pending_msg.media if pending_msg.media else None
                if media:
                    content, media = extract_documents(content, media)
                    media = media or None
                user_content = self.context._build_user_content(content, media)
                runtime_ctx = self.context._build_runtime_context(
                    pending_msg.channel,
                    pending_msg.chat_id,
                    self.context.timezone,
                )
                if isinstance(user_content, str):
                    merged: str | list[dict[str, Any]] = f"{runtime_ctx}\n\n{user_content}"
                else:
                    merged = [{"type": "text", "text": runtime_ctx}] + user_content
                if session is not None and isinstance(content, str) and content.strip():
                    active_turn = self.load_active_turn_state(session.key)
                    if isinstance(active_turn, dict):
                        session.add_message("user", content)
                        self.sessions.save(session)
                        self._register_turn_message(
                            session,
                            active_turn,
                            role="user",
                            content=self._build_message_object_content(content, media),
                            is_injection=True,
                        )
                items.append({"role": "user", "content": merged})
            return items

        active_turn = self.load_active_turn_state(session.key) if session else None

        def _active_revision() -> int | None:
            if session is None:
                return None
            current = self.load_active_turn_state(session.key)
            if not isinstance(current, dict):
                return None
            revision = current.get("injection_revision")
            return revision if isinstance(revision, int) else None

        result = await self.runner.run(AgentRunSpec(
            initial_messages=initial_messages,
            tools=self.tools,
            model=self.model,
            max_iterations=self.max_iterations,
            max_tool_result_chars=self.max_tool_result_chars,
            hook=hook,
            error_message="Sorry, I encountered an error calling the AI model.",
            concurrent_tools=True,
            workspace=self.workspace,
            session_key=session.key if session else None,
            context_window_tokens=self.context_window_tokens,
            context_block_limit=self.context_block_limit,
            provider_retry_mode=self.provider_retry_mode,
            progress_callback=on_progress,
            checkpoint_callback=_checkpoint,
            injection_callback=_drain_pending,
            turn_id=active_turn.get("turn_id") if isinstance(active_turn, dict) else None,
            active_revision=(
                active_turn.get("injection_revision")
                if isinstance(active_turn, dict) and isinstance(active_turn.get("injection_revision"), int)
                else None
            ),
            revision_provider=_active_revision if session else None,
        ))
        self._last_usage = result.usage
        if result.stop_reason == "max_iterations":
            logger.warning("Max iterations ({}) reached", self.max_iterations)
        elif result.stop_reason == "error":
            logger.error("LLM returned error: {}", (result.final_content or "")[:200])
        return result.final_content, result.tools_used, result.messages, result.stop_reason, result.had_injections

    async def run(self) -> None:
        """Run the agent loop, dispatching messages as tasks to stay responsive to /stop."""
        self._running = True
        await self._connect_mcp()
        logger.info("Agent loop started")

        while self._running:
            try:
                msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
            except asyncio.TimeoutError:
                self.auto_compact.check_expired(
                    self._schedule_background,
                    active_session_keys=self._pending_queues.keys(),
                )
                continue
            except asyncio.CancelledError:
                # Preserve real task cancellation so shutdown can complete cleanly.
                # Only ignore non-task CancelledError signals that may leak from integrations.
                if not self._running or asyncio.current_task().cancelling():
                    raise
                continue
            except Exception as e:
                logger.warning("Error consuming inbound message: {}, continuing...", e)
                continue

            raw = msg.content.strip()
            if self.commands.is_priority(raw):
                ctx = CommandContext(msg=msg, session=None, key=msg.session_key, raw=raw, loop=self)
                result = await self.commands.dispatch_priority(ctx)
                if result:
                    await self.bus.publish_outbound(result)
                continue
            effective_key = self._effective_session_key(msg)
            # If this session already has an active pending queue (i.e. a task
            # is processing this session), route the message there for mid-turn
            # injection instead of creating a competing task.
            if effective_key in self._pending_queues:
                pending_msg = msg
                if effective_key != msg.session_key:
                    pending_msg = dataclasses.replace(
                        msg,
                        session_key_override=effective_key,
                    )
                try:
                    self._pending_queues[effective_key].put_nowait(pending_msg)
                except asyncio.QueueFull:
                    logger.warning(
                        "Pending queue full for session {}, falling back to queued task",
                        effective_key,
                    )
                else:
                    logger.info(
                        "Routed follow-up message to pending queue for session {}",
                        effective_key,
                    )
                    continue
            # Compute the effective session key before dispatching
            # This ensures /stop command can find tasks correctly when unified session is enabled
            task = asyncio.create_task(self._dispatch(msg))
            self._active_tasks.setdefault(effective_key, []).append(task)
            task.add_done_callback(
                lambda t, k=effective_key: self._active_tasks.get(k, [])
                and self._active_tasks[k].remove(t)
                if t in self._active_tasks.get(k, [])
                else None
            )

    async def _dispatch(self, msg: InboundMessage) -> None:
        """Process a message: per-session serial, cross-session concurrent."""
        session_key = self._effective_session_key(msg)
        if session_key != msg.session_key:
            msg = dataclasses.replace(msg, session_key_override=session_key)
        lock = self._session_locks.setdefault(session_key, asyncio.Lock())
        gate = self._concurrency_gate or nullcontext()

        # Register a pending queue so follow-up messages for this session are
        # routed here (mid-turn injection) instead of spawning a new task.
        pending = asyncio.Queue(maxsize=20)
        self._pending_queues[session_key] = pending

        try:
            async with lock, gate:
                try:
                    on_stream = on_stream_end = None
                    if msg.metadata.get("_wants_stream"):
                        # Split one answer into distinct stream segments.
                        stream_base_id = f"{msg.session_key}:{time.time_ns()}"
                        stream_segment = 0

                        def _current_stream_id() -> str:
                            return f"{stream_base_id}:{stream_segment}"

                        async def on_stream(delta: str) -> None:
                            meta = dict(msg.metadata or {})
                            meta["_stream_delta"] = True
                            meta["_stream_id"] = _current_stream_id()
                            await self.bus.publish_outbound(OutboundMessage(
                                channel=msg.channel, chat_id=msg.chat_id,
                                content=delta,
                                metadata=meta,
                            ))

                        async def on_stream_end(*, resuming: bool = False) -> None:
                            nonlocal stream_segment
                            meta = dict(msg.metadata or {})
                            meta["_stream_end"] = True
                            meta["_resuming"] = resuming
                            meta["_stream_id"] = _current_stream_id()
                            await self.bus.publish_outbound(OutboundMessage(
                                channel=msg.channel, chat_id=msg.chat_id,
                                content="",
                                metadata=meta,
                            ))
                            stream_segment += 1

                    response = await self._process_message(
                        msg, on_stream=on_stream, on_stream_end=on_stream_end,
                        pending_queue=pending,
                    )
                    if response is not None:
                        await self.bus.publish_outbound(response)
                    elif msg.channel == "cli":
                        await self.bus.publish_outbound(OutboundMessage(
                            channel=msg.channel, chat_id=msg.chat_id,
                            content="", metadata=msg.metadata or {},
                        ))
                except asyncio.CancelledError:
                    logger.info("Task cancelled for session {}", session_key)
                    raise
                except Exception:
                    logger.exception("Error processing message for session {}", session_key)
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel, chat_id=msg.chat_id,
                        content="Sorry, I encountered an error.",
                    ))
        finally:
            # Drain any messages still in the pending queue and re-publish
            # them to the bus so they are processed as fresh inbound messages
            # rather than silently lost.
            queue = self._pending_queues.pop(session_key, None)
            if queue is not None:
                leftover = 0
                while True:
                    try:
                        item = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    await self.bus.publish_inbound(item)
                    leftover += 1
                if leftover:
                    logger.info(
                        "Re-published {} leftover message(s) to bus for session {}",
                        leftover, session_key,
                    )

    async def close_mcp(self) -> None:
        """Drain pending background archives, then close MCP connections."""
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
            self._background_tasks.clear()
        for name, stack in self._mcp_stacks.items():
            try:
                await stack.aclose()
            except (RuntimeError, BaseExceptionGroup):
                logger.debug("MCP server '{}' cleanup error (can be ignored)", name)
        self._mcp_stacks.clear()

    def _schedule_background(self, coro) -> None:
        """Schedule a coroutine as a tracked background task (drained on shutdown)."""
        task = asyncio.create_task(coro)
        self._background_tasks.append(task)
        task.add_done_callback(self._background_tasks.remove)

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")

    async def _process_message(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
        pending_queue: asyncio.Queue | None = None,
    ) -> OutboundMessage | None:
        """Process a single inbound message and return the response."""
        # System messages: parse origin from chat_id ("channel:chat_id")
        if msg.channel == "system":
            channel, chat_id = (
                msg.chat_id.split(":", 1) if ":" in msg.chat_id else ("cli", msg.chat_id)
            )
            logger.info("Processing system message from {}", msg.sender_id)
            key = f"{channel}:{chat_id}"
            session = self.sessions.get_or_create(key)
            if self._restore_turn_state(session):
                self.sessions.save(session)

            session = self.auto_compact.prepare_session(session, key)

            await self.consolidator.maybe_consolidate_by_tokens(session)
            self._set_tool_context(channel, chat_id, msg.metadata.get("message_id"))
            history = session.get_history(max_messages=0)
            current_role = "assistant" if msg.sender_id == "subagent" else "user"

            working_set = self.sessions.load_latest_working_set(session.key)
            messages = self.context.build_messages(
                working_set=working_set,
                recent_raw_turns=history,
                selected_capsules=[],
                selected_artifacts=[],
                current_message=msg.content,
                channel=channel,
                chat_id=chat_id,
                current_role=current_role,
            )
            final_content, _, all_msgs, _, _ = await self._run_agent_loop(
                messages, session=session, channel=channel, chat_id=chat_id,
                message_id=msg.metadata.get("message_id"),
            )
            self._save_turn(session, all_msgs, 1 + len(history))
            self.finalize_turn(session, self.load_active_turn_state(session.key), final_content=final_content)
            self.sessions.save(session)
            self._schedule_background(self.consolidator.maybe_consolidate_by_tokens(session))
            return OutboundMessage(
                channel=channel,
                chat_id=chat_id,
                content=final_content or "Background task completed.",
            )

        # Extract document text from media at the processing boundary so all
        # channels benefit without format-specific logic in ContextBuilder.
        if msg.media:
            new_content, image_only = extract_documents(msg.content, msg.media)
            msg = dataclasses.replace(msg, content=new_content, media=image_only)

        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info("Processing message from {}:{}: {}", msg.channel, msg.sender_id, preview)

        key = session_key or msg.session_key
        session = self.sessions.get_or_create(key)
        if self._restore_turn_state(session):
            self.sessions.save(session)

        session = self.auto_compact.prepare_session(session, key)

        # Slash commands
        raw = msg.content.strip()
        ctx = CommandContext(msg=msg, session=session, key=key, raw=raw, loop=self)
        if result := await self.commands.dispatch(ctx):
            return result

        await self.consolidator.maybe_consolidate_by_tokens(session)

        self._set_tool_context(msg.channel, msg.chat_id, msg.metadata.get("message_id"))
        if message_tool := self.tools.get("message"):
            if isinstance(message_tool, MessageTool):
                message_tool.start_turn()

        history = session.get_history(max_messages=0)

        working_set = self.sessions.load_latest_working_set(session.key)
        initial_messages = self.context.build_messages(
            working_set=working_set,
            recent_raw_turns=history,
            selected_capsules=[],
            selected_artifacts=[],
            current_message=msg.content,
            media=msg.media if msg.media else None,
            channel=msg.channel,
            chat_id=msg.chat_id,
        )

        async def _bus_progress(content: str, *, tool_hint: bool = False) -> None:
            meta = dict(msg.metadata or {})
            meta["_progress"] = True
            meta["_tool_hint"] = tool_hint
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=content,
                    metadata=meta,
                )
            )

        # Persist the triggering user message immediately so TurnState retains
        # the active turn's user anchor even if the process dies mid-turn.
        user_persisted_early = False
        active_turn = self.load_active_turn_state(session.key)
        if not isinstance(active_turn, dict):
            active_turn = self.create_turn_state(session.key)
        if isinstance(msg.content, str) and msg.content.strip():
            session.add_message("user", msg.content)
            self.sessions.save(session)
            active_turn = self._register_turn_message(
                session,
                active_turn,
                role="user",
                content=self._build_message_object_content(msg.content, msg.media if msg.media else None),
                is_injection=bool(active_turn.get("user_message_ref")),
            )
            user_persisted_early = True

        try:
            final_content, _, all_msgs, stop_reason, had_injections = await self._run_agent_loop(
                initial_messages,
                on_progress=on_progress or _bus_progress,
                on_stream=on_stream,
                on_stream_end=on_stream_end,
                session=session,
                channel=msg.channel,
                chat_id=msg.chat_id,
                message_id=msg.metadata.get("message_id"),
                pending_queue=pending_queue,
            )
        except asyncio.CancelledError:
            self._interrupt_turn(session)
            self.sessions.save(session)
            raise
        except Exception:
            self._interrupt_turn(session)
            self.sessions.save(session)
            raise

        if final_content is None or not final_content.strip():
            final_content = EMPTY_FINAL_RESPONSE_MESSAGE

        # Skip the already-persisted user message when saving the turn
        save_skip = 1 + len(history) + (1 if user_persisted_early else 0)
        self._save_turn(session, all_msgs, save_skip)
        self.finalize_turn(
            session,
            self.load_active_turn_state(session.key),
            final_content=final_content,
        )
        self.sessions.save(session)
        self._schedule_background(self.consolidator.maybe_consolidate_by_tokens(session))

        # When follow-up messages were injected mid-turn, a later natural
        # language reply may address those follow-ups and should not be
        # suppressed just because MessageTool was used earlier in the turn.
        # However, if the turn falls back to the empty-final-response
        # placeholder, suppress it when the real user-visible output already
        # came from MessageTool.
        if (mt := self.tools.get("message")) and isinstance(mt, MessageTool) and mt._sent_in_turn:
            if not had_injections or stop_reason == "empty_final_response":
                return None

        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info("Response to {}:{}: {}", msg.channel, msg.sender_id, preview)

        meta = dict(msg.metadata or {})
        if on_stream is not None and stop_reason != "error":
            meta["_streamed"] = True
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=final_content,
            metadata=meta,
        )

    def _sanitize_persisted_blocks(
        self,
        content: list[dict[str, Any]],
        *,
        should_truncate_text: bool = False,
        drop_runtime: bool = False,
    ) -> list[dict[str, Any]]:
        """Strip volatile multimodal payloads before writing session history."""
        filtered: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                filtered.append(block)
                continue

            if (
                drop_runtime
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
                and block["text"].startswith(ContextBuilder._RUNTIME_CONTEXT_TAG)
            ):
                continue

            if block.get("type") == "image_url" and block.get("image_url", {}).get(
                "url", ""
            ).startswith("data:image/"):
                path = (block.get("_meta") or {}).get("path", "")
                filtered.append({"type": "text", "text": image_placeholder_text(path)})
                continue

            if block.get("type") == "text" and isinstance(block.get("text"), str):
                text = block["text"]
                if should_truncate_text and len(text) > self.max_tool_result_chars:
                    text = truncate_text_fn(text, self.max_tool_result_chars)
                filtered.append({**block, "text": text})
                continue

            filtered.append(block)

        return filtered

    def _save_turn(self, session: Session, messages: list[dict], skip: int) -> None:
        """Save new-turn messages into session, truncating large tool results."""
        from datetime import datetime

        for m in messages[skip:]:
            entry = dict(m)
            role, content = entry.get("role"), entry.get("content")
            if role == "assistant" and not content and not entry.get("tool_calls"):
                continue  # skip empty assistant messages — they poison session context
            if role == "tool":
                if isinstance(content, str) and len(content) > self.max_tool_result_chars:
                    entry["content"] = truncate_text_fn(content, self.max_tool_result_chars)
                elif isinstance(content, list):
                    filtered = self._sanitize_persisted_blocks(content, should_truncate_text=True)
                    if not filtered:
                        continue
                    entry["content"] = filtered
            elif role == "user":
                if isinstance(content, str) and content.startswith(ContextBuilder._RUNTIME_CONTEXT_TAG):
                    # Strip the entire runtime-context block (including any session summary).
                    # The block is bounded by _RUNTIME_CONTEXT_TAG and _RUNTIME_CONTEXT_END.
                    end_marker = ContextBuilder._RUNTIME_CONTEXT_END
                    end_pos = content.find(end_marker)
                    if end_pos >= 0:
                        after = content[end_pos + len(end_marker):].lstrip("\n")
                        if after:
                            entry["content"] = after
                        else:
                            continue
                    else:
                        # Fallback: no end marker found, strip the tag prefix
                        after_tag = content[len(ContextBuilder._RUNTIME_CONTEXT_TAG):].lstrip("\n")
                        if after_tag.strip():
                            entry["content"] = after_tag
                        else:
                            continue
                if isinstance(content, list):
                    filtered = self._sanitize_persisted_blocks(content, drop_runtime=True)
                    if not filtered:
                        continue
                    entry["content"] = filtered
            entry.setdefault("timestamp", datetime.now().isoformat())
            session.messages.append(entry)
        session.updated_at = datetime.now()

    @staticmethod
    def _checkpoint_message_key(message: dict[str, Any]) -> tuple[Any, ...]:
        return (
            message.get("role"),
            message.get("content"),
            message.get("tool_call_id"),
            message.get("name"),
            message.get("tool_calls"),
            message.get("reasoning_content"),
            message.get("thinking_blocks"),
        )

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        media: list[str] | None = None,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
    ) -> OutboundMessage | None:
        """Process a message directly and return the outbound payload."""
        await self._connect_mcp()
        msg = InboundMessage(
            channel=channel, sender_id="user", chat_id=chat_id,
            content=content, media=media or [],
        )
        return await self._process_message(
            msg,
            session_key=session_key,
            on_progress=on_progress,
            on_stream=on_stream,
            on_stream_end=on_stream_end,
        )
