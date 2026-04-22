# 项目总览

## 1. 项目定位

`local_dev` 是当前实际长期维护的主分支。它虽然起源于上游 `HKUDS/nanobot`，但当前实现已经不应再被视为“只做轻量同步的小改分支”。

当前项目更接近一个面向长期运行的个人 AI assistant / coding agent 框架，重点在于：

- 多入口接入同一个 agent 内核。
- 用结构化状态管理回合和恢复，而不是只靠聊天记录尾部。
- 将长期记忆、工作镜像、归档和候选观察分层治理。
- 为工具调用、调度、后台任务和多渠道消息提供统一的运行语义。

## 2. 当前真实系统边界

当前仓库已经实现的主要能力：

- CLI 交互入口。
- 编程式 SDK 入口（`Nanobot.from_config()`）。
- OpenAI-compatible HTTP API 入口，但能力仍比较收敛。
- 多聊天渠道入口，包含 Telegram、Discord、Slack、Email、WebSocket 等。
- 统一的 `AgentLoop` 作为核心执行内核。
- 会话历史 JSONL 存储与 `.nanobot/state` 结构化状态并存。
- 工具体系、MCP 接入、子 agent、cron、heartbeat、Dream/Promoter 流程。

当前仓库尚未形成或尚未纳入 `local_dev` 的能力：

- 完整的内置 Web UI 在当前 `local_dev` 不存在。
- HTTP API 不是通用 OpenAI API 替代品，只支持受限输入形式，且内部仍路由到单个固定 agent 会话模型。
- `working/CURRENT.md` 仍存在，但已被降级为镜像/兼容层，不是主状态真相源。

针对 `WebUI` 的明确分支约束：

- `local_dev` 不保留也不引入内置 WebUI 模块。
- 本分支的对话接入方向以各类 channel 为主，例如 QQ bot 插件、Telegram、Discord、Slack、Email、WebSocket channel。
- 如果未来需要新增接入方式，应优先按 channel 形态接入，而不是回到内置浏览器前端模块。

## 3. 系统主干

从运行时视角看，项目可以概括为四条主线：

1. 入口层：CLI / API / channels / SDK 接收用户输入。
2. 调度层：`MessageBus`、`ChannelManager`、`CronService`、`HeartbeatService` 协调消息与后台任务。
3. 执行层：`AgentLoop` 组装上下文、调用模型、执行工具、写回状态。
4. 持久化与治理层：`SessionManager`、`StateStore`、`MemoryStore`、`Dream`、`Promoter` 负责会话状态、结构化快照、归档与长期记忆治理。

## 4. 代码层面的核心判断

本项目的架构理解，不能只看 `docs/` 下的专题文档，也不能只靠目录名推断，需要以以下代码事实为准：

- `nanobot/agent/loop.py` 是主运行内核。
- `nanobot/agent/context.py` 决定 prompt 的装配方式和优先级。
- `nanobot/session/state_store.py` 与 `nanobot/session/state.py` 定义结构化状态存储语义。
- `nanobot/agent/memory.py`、`nanobot/agent/promoter.py` 定义分层记忆与晋升路径。
- `nanobot/channels/manager.py`、`nanobot/bus/queue.py` 决定多渠道消息分发方式。
- `nanobot/providers/registry.py` 与 provider 实现决定模型路由与后端差异。

补充约束：

- `docs/` 目录属于历史设计记录和持续更新的草稿区。
- 其中内容可作为背景材料，但不应再作为当前分支设计判断的主依据。
- 若 `docs/` 与代码或 `Architecture_Documentation/` 冲突，以代码和正式架构文档为准。

## 5. 典型运行流

### 5.1 交互主流程

1. 用户消息从 CLI / API / channel 进入。
2. 入口层将输入转成统一消息或直接调用 `AgentLoop.process_direct(...)`。
3. `AgentLoop` 读取 session、working set、capsule、artifact、memory 和 skills。
4. `ContextBuilder` 组装 system prompt 与 messages。
5. provider 执行模型调用。
6. 如有工具调用，`ToolRegistry` 执行工具并继续迭代。
7. 最终响应写回 session 与结构化状态，并通过总线或直接返回给调用方。
8. 在后续时机，consolidation / dream / promotion / cron / heartbeat 可能继续消费这些结果。

### 5.2 长期记忆治理流程

1. 已完成回合或历史消息被 consolidation / dream 消费。
2. `Dream` 写入 archive 反思和 candidate observations。
3. `Promoter` 决定是否把 observation 晋升到 `identity/*`。
4. 只有通过晋升规则的结论才会进入高权限长期记忆文件。

## 6. 文档使用约定

- 想理解当前项目“是什么”，优先看 `overview/`。
- 想修改某块代码，优先看对应的 `capabilities/` 文档和 `mapping/code-to-capability.md`。
- 想判断一次改动是否触发了架构边界变化，再看 `adr/` 和 `changes/`。
