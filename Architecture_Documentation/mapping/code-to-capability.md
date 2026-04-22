# Code To Capability Mapping

本文件维护“代码路径 -> 能力域文档”的映射。AI 或人工改代码时，应优先用它判断需要同步更新哪些架构文档。

## 1. 主映射

| 代码路径 | 主要能力域文档 | 说明 |
| --- | --- | --- |
| `nanobot/agent/loop.py` | `capabilities/agent-runtime.md` | 主运行内核；如涉及上下文装配或状态对象，也可能联动其他文档 |
| `nanobot/agent/runner.py` | `capabilities/agent-runtime.md` | 模型调用与工具迭代执行 |
| `nanobot/agent/hook.py` | `capabilities/agent-runtime.md` | hook 生命周期与流式观测 |
| `nanobot/agent/subagent.py` | `capabilities/agent-runtime.md`, `capabilities/tools-and-execution.md` | 子 agent 协作 |
| `nanobot/agent/context.py` | `capabilities/context-and-memory.md` | prompt 结构与上下文裁剪 |
| `nanobot/agent/memory.py` | `capabilities/context-and-memory.md`, `capabilities/scheduling-and-background-tasks.md` | 分层记忆、Dream、Consolidator |
| `nanobot/agent/promoter.py` | `capabilities/context-and-memory.md` | 候选观察晋升到 identity |
| `nanobot/agent/tools/` | `capabilities/tools-and-execution.md` | 工具体系与执行边界 |
| `nanobot/session/manager.py` | `capabilities/session-and-state.md` | JSONL session 历史 |
| `nanobot/session/state.py` | `capabilities/session-and-state.md` | 状态常量、ref、阶段语义 |
| `nanobot/session/state_store.py` | `capabilities/session-and-state.md` | 结构化状态落盘 |
| `nanobot/cli/` | `capabilities/interfaces-and-channels.md` | CLI 入口 |
| `nanobot/api/server.py` | `capabilities/interfaces-and-channels.md` | HTTP API 子集 |
| `nanobot/channels/manager.py` | `capabilities/interfaces-and-channels.md` | 多渠道调度 |
| `nanobot/channels/` | `capabilities/interfaces-and-channels.md` | 各聊天渠道协议适配 |
| `nanobot/providers/registry.py` | `capabilities/providers-and-model-routing.md` | provider 元数据与路由规则 |
| `nanobot/providers/` | `capabilities/providers-and-model-routing.md` | provider 具体实现 |
| `nanobot/cron/` | `capabilities/scheduling-and-background-tasks.md` | 计划任务 |
| `nanobot/heartbeat/` | `capabilities/scheduling-and-background-tasks.md` | heartbeat 后台检查 |
| `nanobot/config/` | `capabilities/configuration-and-workspace.md` | 配置、路径与 schema |
| `nanobot/security/` | `capabilities/configuration-and-workspace.md`, `capabilities/tools-and-execution.md` | 网络安全与工具边界 |
| `nanobot/templates/` | `capabilities/context-and-memory.md` | prompt 模板和记忆模板 |
| `nanobot/skills/` | `capabilities/context-and-memory.md`, `capabilities/tools-and-execution.md` | skill 加载与 agent 可用能力 |

## 2. 测试映射

| 测试路径 | 对应能力域文档 |
| --- | --- |
| `tests/agent/` | `agent-runtime.md`, `context-and-memory.md`, `session-and-state.md`, `scheduling-and-background-tasks.md` |
| `tests/tools/` | `tools-and-execution.md` |
| `tests/channels/` | `interfaces-and-channels.md` |
| `tests/cli/` | `interfaces-and-channels.md` |
| `tests/providers/` | `providers-and-model-routing.md` |
| `tests/config/` | `configuration-and-workspace.md` |
| `tests/cron/` | `scheduling-and-background-tasks.md` |
| `tests/security/` | `configuration-and-workspace.md`, `tools-and-execution.md` |

## 3. 差量更新规则

默认情况下：

- 改代码时，至少同步更新本文件中的对应 capability 文档。
- 改测试但不改实现时，如果测试定义了新的行为边界，也要更新对应 capability 文档。
- 只在以下情况更新 `overview/`：
  - 职责边界变化
  - 核心运行流变化
  - 长期设计原则变化
- 只在以下情况更新 `adr/`：
  - 出现新的关键架构取舍
  - 旧取舍被正式推翻或替换
- 只在以下情况更新 `changes/`：
  - 新方案仍在实验期
  - 真实实现尚未稳定，不适合直接提升为正式架构描述

## 4. 高频变更提示

- 改 `AgentLoop` 时，通常还要检查 `context-and-memory.md` 与 `session-and-state.md` 是否被连带影响。
- 改 `ContextBuilder` 或 `MemoryStore` 时，通常要同时检查 prompt 结构、记忆分层和 `working/CURRENT.md` 的定位描述。
- 改 API / channels 时，不要忘记更新“当前支持范围与限制”，尤其是那些容易被误认为已完整实现的能力。
