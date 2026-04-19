# 旧上下文压缩架构回顾

## 1. 文档状态

本文描述的是 nanobot 迁移前的上下文压缩架构，也就是以 `session.messages`、`last_consolidated`、`working/CURRENT.md` 和 idle summary 为核心的旧链路。

它不再代表当前主实现。

当前实现请改读：

- `docs/上下文工作集优化方案.md`
- `docs/回合状态恢复优化方案.md`
- `docs/MEMORY.md`

## 2. 旧架构回顾

旧架构的大致形态是：

```text
session.messages
  -> Consolidator
  -> archive/history.jsonl
  -> idle summary / runtime injection
  -> working/CURRENT.md
  -> Dream
```

它解决过几个真实问题：

- 在上下文窗口有限时移走旧消息
- 给长期记忆处理链提供归档材料
- 为长时间 idle 的会话补一段恢复摘要

但它也带来了明显局限：

- 当前任务状态仍然过度依赖原始消息尾部
- `CURRENT.md` 容易被误当作运行时主状态
- 恢复更像消息修补，而不是 turn 恢复
- 工具结果更像被“省略”，而不是被结构化保留

## 3. 为什么它已经不再是主实现

随着短期上下文整链升级落地，当前系统已经把主控制面切到结构化对象：

- `TurnState` 负责执行状态与恢复
- `WorkingSetSnapshot` 负责稳定工作内存
- `ArtifactRecord` 负责工具证据
- `TurnCapsule` 负责回合结论
- `CommitManifest` 负责稳定完成判据

因此，旧架构中的以下叙述已经失效：

- `session.messages` 是短期运行时主输入
- `last_consolidated` 决定在线工作记忆主边界
- `working/CURRENT.md` 是当前工作状态真相源
- idle summary 是恢复链的核心连续性机制

## 4. 旧路径现在的剩余职责

旧链路相关组件并没有全部消失，但职责已经变化：

- `session.messages`：原始日志与兼容会话存储
- `last_consolidated`：原始消息归档边界
- `working/CURRENT.md`：镜像/交接视图
- `archive/history.jsonl`：归档与检索材料

它们不再负责：

- 当前 turn 的主执行状态
- 短期主工作内存
- 稳定完成判据

## 5. 现行替代路径

如果你需要理解现行实现，应按下面的路径看：

```text
TurnState
  -> finalize_turn / repair_partial_commit
  -> TurnCapsule + WorkingSetSnapshot + ResponseObject
  -> CommitManifest
  -> latest-turn / latest-working-set indexes
  -> Dream / AutoCompact / Prompt consumers
```

对应文档：

- 短期上下文主链：`docs/上下文工作集优化方案.md`
- 恢复与提交：`docs/回合状态恢复优化方案.md`
- 总览：`docs/MEMORY.md`

## 6. 建议阅读方式

本文适合用于：

- 回顾旧设计为什么会被替换
- 理解仓库里为什么还保留 `last_consolidated`、`history.jsonl`、`CURRENT.md`
- 向后来维护者解释迁移动机

本文不适合用于：

- 描述当前运行时主链
- 解释当前恢复策略
- 指导新的代码实现
