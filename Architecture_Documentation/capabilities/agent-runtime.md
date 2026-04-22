# 能力域：Agent Runtime

## 1. 责任范围

本能力域描述 agent 的主执行内核，即一条输入如何经过上下文装配、模型调用、工具迭代和结果收敛。

当前主要实现位于：

- `nanobot/agent/loop.py`
- `nanobot/agent/runner.py`
- `nanobot/agent/hook.py`
- `nanobot/agent/subagent.py`
- `nanobot/bus/queue.py`

## 2. 当前真实实现

`AgentLoop` 是当前统一执行内核，负责：

- 持有 provider、workspace、tool registry、session manager、context builder。
- 注册默认工具，包括文件、搜索、shell、web、message、spawn、cron 等。
- 为不同 session 管理并发锁、活动任务和中途注入消息。
- 通过 `AgentRunner` 执行模型调用和工具循环。
- 协调 `Consolidator`、`AutoCompact`、`Dream`、`Promoter`。

`MessageBus` 负责把 channels 与 agent 解耦，但并不是复杂总线系统，当前只是两个 `asyncio.Queue`：

- inbound：渠道 -> agent
- outbound：agent -> 渠道

## 3. 运行语义

### 3.1 单次执行的大致阶段

1. 获取或创建 session。
2. 装配 system prompt 和 conversation payload。
3. 调用 provider 获取响应。
4. 如果有 tool call，执行工具并继续迭代。
5. 生成最终答复。
6. 写回 session 和结构化 state。
7. 视需要触发 consolidation / auto compact / dream / promotion。

### 3.2 并发与会话隔离

当前实现有以下机制：

- 每个 session 维护独立锁，避免同一会话并发写坏状态。
- 有全局并发闸门，默认通过环境变量 `NANOBOT_MAX_CONCURRENT_REQUESTS` 控制。
- 同一 session 在回合中收到新消息时，会进入 pending queue，而不是直接并发启动新任务。

这说明本项目不是“完全串行单线程 agent”，也不是“任意并发安全”的系统，而是会话内串行、跨会话受限并发。

## 4. Hook 与可观测性

`AgentLoop` 支持 hook 体系。当前 `_LoopHook` 负责：

- 流式输出整理。
- 工具执行前的进度回调。
- 统计 usage 和清理 thinking 内容。

这部分能力更多是运行期观测与展示，不是单独的业务逻辑层。

## 5. 设计意图

此能力域的设计意图是：所有入口共享同一个 agent runtime，而不是在 CLI、API、channel 里各自复制“半套 agent”。

当前代码基本符合这个方向，但不同入口仍保留各自输入协议与少量控制逻辑，例如：

- CLI 负责终端交互和流式显示。
- API 负责 multipart/json 解析与 session lock。
- channel 负责协议适配和 outbound retry。

## 6. 关键边界

- `AgentLoop` 很大，当前承担的职责偏多，是系统枢纽而不是轻量 orchestrator。
- 子 agent 已存在，但仍是 `AgentLoop` 的延伸机制，不是独立服务边界。
- bus 是进程内内存队列，不是持久化消息系统。

## 7. 相关测试

- `tests/agent/test_runner.py`
- `tests/agent/test_hook_composite.py`
- `tests/agent/test_task_cancel.py`
- `tests/agent/test_unified_session.py`
- `tests/test_nanobot_facade.py`
