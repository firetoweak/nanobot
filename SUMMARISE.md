## 文档定位

- 这里记录实际发生过的重要改动事实、实现路径和验证证据。
- 它是开发记录，不是长期轻量偏好文档；长期偏好看 `设计总结.md`。
- 它也不是每次改动都必须更新的流水账；只有命中里程碑改动时，才建议追加记录。
- 这里按事实沉淀，不追求复盘腔，不删除已有历史记录。

## 何时记录

- 跨模块改动，后续可能需要回溯实现路径。
- 对外行为、接口语义或关键链路发生变化。
- 重要 bugfix、线上问题修复或高风险问题处置。
- 做了明确方案取舍，后续任务可能复用这次判断。
- 做过有效验证，值得作为后续排障或设计参考。

## 通常不必记录

- 单文件局部修复，且没有明显实现分叉或方案取舍。
- 纯注释、文案、命名整理。
- 一次性试验、临时排查、辅助脚本。
- 已经能在最终回复中清楚说明、且没有长期参考价值的小改动。

## 推荐记录结构

- 需求背景：这次为什么做。
- 影响范围：改了哪些模块、文件、函数或链路。
- 关键实现：核心实现逻辑和结构调整是什么。
- 方案原因：为什么这样做，而不是别的方式。
- 验证情况：运行了什么测试、脚本或人工验证。

## 2026-04-17 权限分级记忆改造

- 需求背景：需要在 NanoBot 现有 `session -> consolidation -> history -> Dream` 骨架上，把“统一长期仓库”改成“权限分级记忆”，核心目标是解决 Dream 离长期身份层过近、一次错误抽象会直接污染后续行为的问题。
- 影响范围：`nanobot/agent/memory.py`、`nanobot/agent/context.py`、`nanobot/agent/promoter.py`、`nanobot/agent/tools/filesystem.py`、`nanobot/agent/loop.py`、`nanobot/cli/commands.py`、`nanobot/utils/helpers.py`、`nanobot/templates/agent/identity.md`、`nanobot/templates/agent/dream_phase1.md`、`nanobot/templates/agent/dream_phase2.md`、`nanobot/skills/memory/SKILL.md`，以及配套测试与模板目录。
- 关键实现：`MemoryStore` 从 `SOUL.md / USER.md / memory/MEMORY.md / memory/history.jsonl` 扩展为 `identity/`、`working/`、`archive/`、`candidate/` 四层；新增 `identity/SOUL.md`、`identity/USER_RULES.md`、`identity/USER_PROFILE.md`、`working/CURRENT.md`、`archive/history.jsonl`、`archive/reflections.jsonl`、`candidate/observations.jsonl`，同时保留旧 `SOUL.md`、`USER.md`、`memory/MEMORY.md`、`memory/history.jsonl` 的兼容读和迁移入口。
- 关键实现：`ContextBuilder.build_system_prompt()` 改成只常驻注入 identity 模板、`identity/*` 和 `working/CURRENT.md`；`archive/*` 与 `candidate/*` 不再进入 system prompt，而是保留为检索底库。
- 关键实现：`Dream` 的 Phase 2 可写范围从 `SOUL.md / USER.md / memory/MEMORY.md` 收紧为 `working/CURRENT.md`、`archive/reflections.jsonl`、`candidate/observations.jsonl`；身份层只读，不再允许 Dream 直接升权。
- 关键实现：新增 `Promoter`，按硬规则处理 `candidate -> identity`，第一版只支持两类主晋升条件：`source == explicit_user_statement` 直接晋升，以及 `evidence_count >= 阈值` 的重复证据晋升；同时支持低置信度或冲突证据的 `rejected`。
- 关键实现：`gateway` 中新增 heartbeat 专用受限 `AgentLoop`，通过 `filesystem` 工具的 `writable_targets` 机制把 heartbeat 写权限限制到 `working/CURRENT.md` 和 `archive/reflections.jsonl`；同时把 `promoter` 注册为与 `dream` 并列的 system cron job。
- 方案原因：这次没有推翻 session/consolidation/history/dream 主链，而是通过“新结构主写、旧结构兼容读”的方式渐进迁移，尽量把行为变化集中在记忆治理边界而不是主对话骨架上，降低回归面。
- 方案原因：把晋升逻辑独立成 `Promoter`，而不是继续塞回 `Dream`，是为了避免“观察、归档、升权”职责重新混在一起；这样 Dream 默认只生成候选和反思，身份层修改必须经过单独闸门。
- 验证情况：安装开发依赖 `python -m pip install -e .[dev]`；随后运行 `python -m pytest tests/agent/test_memory_store.py tests/agent/test_context_prompt_cache.py tests/agent/test_dream.py tests/agent/test_promoter.py tests/tools/test_filesystem_tools.py tests/agent/test_git_store.py tests/command/test_builtin_dream.py tests/cli/test_commands.py`，结果 `178 passed`；另外用 `ReadLints` 检查本次改动文件，未发现新增 IDE 报错。

## 2026-04-17 旧版生产文档迁移落盘

- 需求背景：权限分级记忆已经切到 `identity / working / archive / candidate` 新结构，但实际工作目录 `C:\Users\19403\.nanobot\workspace` 里仍残留根目录 `SOUL.md`、`USER.md` 和 `memory/MEMORY.md`，而且其中部分内容已经和新地址分叉，继续并存会让人误把旧路径当主路径。
- 影响范围：实际工作目录 `C:\Users\19403\.nanobot\workspace` 下的 `identity/*`、根目录旧 `SOUL.md`/`USER.md`、`memory/MEMORY.md`；仓库说明文档 `README.md`、`docs/MEMORY.md`、`nanobot/templates/AGENTS.md`；版本日志 `SUMMARISE.md`、`设计总结日记.md`。
- 关键实现：先对旧路径与新路径文档做迁移前快照备份，再把根目录旧 `SOUL.md` 的有效原则并入 `identity/SOUL.md`，把旧 `USER.md` 中的用户背景、偏好、工作流要求拆分到 `identity/USER_PROFILE.md` 和 `identity/USER_RULES.md`。
- 关键实现：确认 `archive/history.jsonl`、`archive/.cursor`、`archive/.dream_cursor` 已承担真实归档职责，且未发现 `memory/history.jsonl`、`memory/HISTORY.md` 或 `memory` 下旧游标残留后，删除工作目录中的根目录旧 `SOUL.md`、`USER.md` 和 `memory/MEMORY.md`。
- 关键实现：把 README 和独立 memory 设计文档统一改写为新分层叙事，明确 `identity/*` 和 `working/CURRENT.md` 是 prompt 常驻主路径，`archive/*` 与 `candidate/*` 是检索/审核层，旧路径仅为兼容迁移入口。
- 方案原因：这次不再继续保留“新旧双主路径”，而是把兼容读留给代码，把主生产文档收口到新地址，避免后续人工维护时继续写错文件。
- 验证情况：人工核对 `C:\Users\19403\.nanobot\workspace` 下新旧文档内容与归档文件状态；确认 `archive/history.jsonl` 有效、`archive/reflections.jsonl` 与 `candidate/observations.jsonl` 为空但已预留；随后重新检查工作目录，旧根路径文档已移除，仅保留新分层文件和迁移备份。

