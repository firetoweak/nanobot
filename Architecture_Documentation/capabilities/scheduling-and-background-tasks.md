# 能力域：Scheduling And Background Tasks

## 1. 责任范围

本能力域描述 cron、heartbeat、Dream 等非即时用户交互型后台能力。

当前主要实现位于：

- `nanobot/cron/service.py`
- `nanobot/heartbeat/service.py`
- `nanobot/agent/memory.py`
- `nanobot/agent/loop.py`

## 2. 当前真实实现

### 2.1 Cron

`CronService` 是当前正式存在的计划任务能力，特点包括：

- 基于本地文件存储 job store
- 支持 `at`、`every`、`cron expr` 三类 schedule
- 通过定时器计算下一次唤醒
- 维护 run history、last status、last error
- 支持多实例下通过 action log 合并变更

这说明 cron 不是“临时脚本”，而是已内置的运行时子系统。

### 2.2 Heartbeat

`HeartbeatService` 当前通过周期性读取 `HEARTBEAT.md` 来判断是否需要执行任务：

- Phase 1：先用虚拟 tool call 让模型判断 `skip` 或 `run`
- Phase 2：只有决定 `run` 时才执行真正任务

它是一个轻量、文件驱动的后台检查机制，而不是完整工作流引擎。

### 2.3 Dream

Dream 更偏“后台认知治理流程”，不是定时调度器，但在架构上属于后台异步处理能力的一部分。

## 3. 设计意图

- 让 agent 不只响应前台消息，也能执行定时和后台检查任务。
- 把“后台唤醒”和“真正执行任务”分成两步，减少无意义运行。
- 让总结、归档和长期记忆治理不阻塞主交互路径。

## 4. 当前限制

- Heartbeat 仍依赖 `HEARTBEAT.md` 文件，是一种轻量机制，不是可视化任务系统。
- Cron store 是本地文件模型，适合单机/轻量场景，不是分布式调度器。
- Dream 与 schedule 的耦合关系仍主要由主 runtime 协调，不是完全独立子系统。

## 5. 相关测试

- `tests/cron/test_cron_service.py`
- `tests/cron/test_cron_tool_list.py`
- `tests/agent/test_heartbeat_service.py`
- `tests/agent/test_dream.py`
