# 短期记忆改造方案架构文档

## 1. 背景

当前系统已经具备一定的上下文治理能力，核心链路主要分布在以下模块中：

- `nanobot/session/manager.py`：保存完整会话日志，并提供 `last_consolidated` 视图
- `nanobot/agent/context.py`：组装 system prompt、history 与 runtime context
- `nanobot/agent/runner.py`：执行 tool loop，并做 orphan repair、tool result budget、microcompact、history snip
- `nanobot/agent/loop.py`：负责多轮执行、checkpoint、pending user turn 恢复
- `nanobot/agent/autocompact.py`：对 idle session 做压缩与恢复摘要注入
- `nanobot/agent/memory.py`：负责 `Consolidator -> Dream -> Promoter` 的长期记忆链路

这些基础已经解决了“能跑”和“基本可恢复”的问题，但如果目标提升到：

1. 长期任务稳定运行
2. 多轮 tool calling 下上下文不快速膨胀
3. 不重复把无用内容反复发给模型

那么当前短期记忆的核心问题仍然存在：

- 短期上下文仍然过度依赖原始 `session.messages`
- tool 输出的在线压缩过于粗糙，容易丢语义
- prompt 裁剪仍偏“事后补救”，缺少显式工作集
- 恢复能力更多是“消息修补”，不是“回合状态恢复”

因此，本方案的目标不是继续增强“超预算后再截断”的机制，而是把短期记忆升级为一套显式工作集系统。

## 2. 改造目标

### 2.1 核心目标

- **稳定性**：支持长期任务、多轮工具调用、任务中断恢复，不轻易丢工作状态
- **上下文简洁性**：短期上下文增长速度低于原始消息增长速度
- **token 节省**：避免把大工具输出、已消费信息、低价值历史反复发送给模型

### 2.2 设计原则

- **日志层与工作层分离**：原始消息保真存储，模型只看工作集
- **先结构化，再压缩**：优先把回合整理成结构化 capsule，而不是直接截断原始消息
- **大结果句柄化**：tool 大输出进入 artifact store，prompt 仅保留 digest + 引用
- **恢复以状态为中心**：从“恢复消息链”升级到“恢复回合状态”
- **在线层与长期记忆层解耦**：短期记忆改造不依赖 Dream / Promoter 成功与否

## 3. 现状问题

### 3.1 原始历史仍是短期记忆主载体

当前 `Session.get_history()` 仍然以 `session.messages[last_consolidated:]` 为基础视图。
这意味着短期记忆的膨胀仍然主要受消息总量驱动，而不是受“当前任务所需信息量”驱动。

### 3.2 tool 结果压缩过于粗粒度

`runner._microcompact()` 当前会把旧工具结果替换成：

- `[read_file result omitted from context]`
- `[exec result omitted from context]`

这种方式虽然省 token，但问题是：

- 模型失去了仍可能必要的证据
- 容易触发重复读取、重复搜索、重复执行
- 长任务中会导致“记得调用过，但不记得结果是什么”

### 3.3 prompt 组装缺少显式预算层次

当前 `ContextBuilder.build_messages()` 更偏向于：

- system prompt
- history
- 当前 user message

而不是：

- 固定前缀
- 当前工作集
- 最近真实回合
- 少量历史胶囊
- 必要时再 hydrate 详细 artifact

这会导致 prompt 体积与 raw history 强耦合。

### 3.4 恢复机制仍偏消息修复

当前 `loop.py` 的 checkpoint / pending user turn 恢复能力已经不错，但主要解决的是：

- tool result 丢失
- assistant/tool 断链
- user message 提前落盘

它还不是显式的 turn-level 状态机，因此在复杂多轮任务中，恢复精度仍受限。

## 4. 总体方案

本方案将短期记忆拆成 5 层：

1. **Raw Session Log**
2. **Artifact Store**
3. **Turn Capsule**
4. **Working Set**
5. **Budgeted Prompt Assembly**

整体关系如下：

```mermaid
flowchart TD
    rawLog[RawSessionLog]
    artifactStore[ArtifactStore]
    turnCapsules[TurnCapsules]
    workingSet[WorkingSet]
    promptBuilder[PromptBuilder]
    llm[LLM]

    rawLog --> turnCapsules
    rawLog --> artifactStore
    artifactStore --> workingSet
    turnCapsules --> workingSet
    workingSet --> promptBuilder
    turnCapsules --> promptBuilder
    artifactStore --> promptBuilder
    promptBuilder --> llm
```

核心思想：

- `session.messages` 继续保存事实
- tool 大输出进入 `ArtifactStore`
- 每个回合结束后生成结构化 `TurnCapsule`
- 当前任务依赖的信息收敛到 `WorkingSet`
- prompt 构建时优先加载 `WorkingSet`，不是优先加载全部历史

## 5. 核心组件设计

### 5.1 Raw Session Log

#### 目标

继续作为事实源，不承担“直接给模型看的上下文”职责。

#### 设计要求

- 继续沿用 `Session.messages`
- 保留完整 user / assistant / tool 事件
- 保留合法 tool chain 边界信息
- 不因为在线压缩而破坏原始事实

#### 结论

这一层基本沿用 `nanobot/session/manager.py` 现状，不做大改。

### 5.2 Artifact Store

#### 目标

把大工具输出从 prompt 中剥离出去，改成“句柄 + 摘要”的方式管理。

#### 适用对象

- `read_file`
- `grep`
- `glob`
- `exec`
- `web_fetch`
- `web_search`
- 未来可扩展到 MCP / browser / notebook 类工具

#### 存储内容

每个 artifact 至少包含：

- `artifact_id`
- `tool_name`
- `created_at`
- `source_args`
- `raw_ref`
- `digest`
- `size_chars`
- `reuse_hint`

#### 示例

```json
{
  "artifact_id": "art_read_file_001",
  "tool_name": "read_file",
  "created_at": "2026-04-19T12:00:00Z",
  "source_args": {
    "path": "nanobot/agent/runner.py"
  },
  "raw_ref": ".nanobot/tool-results/cli_test/call_123.txt",
  "digest": {
    "summary": "读取了 runner.py，关注点在 context governance、microcompact、history snip。",
    "key_entities": ["AgentRunner", "_microcompact", "_snip_history"],
    "range_hint": "full file"
  },
  "size_chars": 18420,
  "reuse_hint": "如果后续继续讨论 runner 上下文治理，优先复用该 artifact"
}
```

#### 关键策略

- 原始大输出只保存在磁盘
- prompt 中默认只注入 digest
- 只有明确需要时才重新 hydrate 原始内容

### 5.3 Turn Capsule

#### 目标

将一个完整 user turn 结构化为可复用、可裁剪的最小工作单元。

#### 为什么需要

当前系统裁的是“消息”，而不是“回合语义单元”。
Turn Capsule 的作用，是让系统今后裁掉的不是未经整理的原始消息，而是已经整理好的任务胶囊。

#### 结构建议

```json
{
  "turn_id": "turn_20260419_001",
  "timestamp": "2026-04-19T12:05:00Z",
  "user_goal": "分析短期记忆改造方向",
  "assistant_intent": "先调研现有实现，再输出架构方案",
  "tool_outcomes": [
    "读取了 context.py / runner.py / loop.py / autocompact.py",
    "确认 microcompact 过粗、working set 缺失"
  ],
  "decisions": [
    "短期记忆应改成 working set 模型",
    "tool 大输出要 artifact 化"
  ],
  "open_questions": [
    "working set 是否持久化为 sidecar 文件",
    "resume 时是否自动 hydrate 相关 artifacts"
  ],
  "artifact_refs": [
    "art_read_file_runner",
    "art_read_file_context"
  ],
  "next_expected_action": "输出 markdown 架构方案"
}
```

#### 特性

- 小而稳定
- 可排序、可选择性注入
- 比 raw summary 更适合作为在线短期记忆素材

### 5.4 Working Set

#### 目标

显式维护“模型当前真正需要知道什么”。

#### 定位

它不是长期记忆，也不是原始日志，而是**当前任务工作集**。

#### 建议内容

```json
{
  "session_key": "cli:test",
  "active_task": "设计短期记忆改造架构",
  "task_stage": "architecture_draft",
  "active_goals": [
    "提高长期任务稳定性",
    "减少上下文膨胀",
    "降低重复 token 消耗"
  ],
  "open_loops": [
    "确定短期记忆数据结构",
    "确定与现有 Session/Runner 的整合边界"
  ],
  "relevant_capsules": [
    "turn_20260419_001",
    "turn_20260419_002"
  ],
  "relevant_artifacts": [
    "art_read_file_runner",
    "art_read_file_loop"
  ],
  "last_user_focus": "先输出 markdown 文档",
  "budget_cache": {
    "working_set_tokens": 320,
    "capsule_tokens": 540,
    "recent_turn_tokens": 780
  }
}
```

#### 作用

`WorkingSet` 将成为 prompt 组装的第一优先级来源。
也就是说，今后“短期记忆”不再等价于“最后几百条消息”，而是等价于“当前工作集 + 少量最近真实回合”。

### 5.5 Budgeted Prompt Assembly

#### 目标

将 prompt 组装改造成有层次、有预算、有降级路径的流水线。

#### 推荐组装顺序

1. **System Prefix**
   - identity
   - bootstrap files
   - always skills

2. **Working Set**
   - active task
   - open loops
   - relevant capsules refs
   - relevant artifact refs

3. **Recent Raw Turns**
   - 最近 1~2 个完整真实回合
   - 保留真实工具调用链，利于稳定续跑

4. **Selected Turn Capsules**
   - 只注入和当前任务有关的 capsule
   - 按相关性与预算选择

5. **Artifact Digests**
   - 注入摘要而不是原始大输出

6. **Current User Message**
   - runtime context
   - 当前输入

#### 优先裁剪顺序

- 先裁剪低相关 capsule
- 再裁剪低相关 artifact digest
- 再裁剪较旧 raw turn
- 不轻易裁剪 working set
- 不裁剪稳定 system prefix

#### 结果

上下文膨胀将从“按消息条数增长”变成“按活跃任务复杂度增长”。

## 6. 稳定性设计

### 6.1 Turn State Machine

为每一轮新增显式状态机：

- `collecting_user`
- `awaiting_model`
- `awaiting_tools`
- `finalizing_turn`
- `completed`
- `interrupted`

#### 状态持久化内容

- `turn_id`
- 当前阶段
- 已声明 tool calls
- 已完成 tool results
- 已生成的 artifact refs
- 是否已生成 turn capsule
- 是否已刷新 working set

#### 目的

当进程中断时，恢复逻辑不再只是：

- 补 tool message
- 补 assistant placeholder

而是能知道：

- 这轮任务做到哪一步
- 哪些结果已经稳定落盘
- 是否应该重新请求模型
- 是否应该仅补尾部收口

### 6.2 断点恢复策略

```mermaid
flowchart TD
    interrupted[InterruptedTurn]
    loadState[LoadTurnState]
    checkArtifacts[CheckArtifacts]
    rebuildCapsule[CapsuleExists?]
    restoreWorkingSet[RestoreWorkingSet]
    resumeLoop[ResumeLLMLoop]

    interrupted --> loadState
    loadState --> checkArtifacts
    checkArtifacts --> rebuildCapsule
    rebuildCapsule --> restoreWorkingSet
    restoreWorkingSet --> resumeLoop
```

#### 恢复原则

- 优先恢复状态，而不是重放原始历史
- 已经有 artifact 的 tool 结果不重复执行
- 已经完成的回合不重复生成 capsule
- 已完成工作集刷新则直接复用

## 7. token 优化策略

### 7.1 从“内容重复发送”转成“句柄复用”

优化对象：

- 大 tool 输出
- 已经被消费过的旧工具结果
- 只用于历史解释的中间结果
- 当前任务不相关的历史回合

#### 原则

- 原始内容只存一次
- 在线上下文传 digest
- 必要时按引用回填

### 7.2 提高 prefix 稳定性

当前 `context.py` 中 system prompt 的组成已经较稳定，但 working memory 和 skills summary 仍可能波动较大。
改造后应尽量做到：

- identity / bootstrap / always skills 放前缀
- working set / capsules / artifact digests 放后缀
- 每轮变化部分尽量局部化

这样有利于 provider cache 命中，进一步节省 token。

### 7.3 工具级 digest 替换占位符

替代当前：

- `[read_file result omitted from context]`

改为：

- `read_file("runner.py") -> 关注点: AgentRunner, _microcompact, _snip_history, artifact=art_xxx`

这样既保留关键信号，又不需要反复重发全文。

## 8. 与现有模块的整合

### 8.1 `nanobot/session/manager.py`

职责保留：

- 原始消息持久化
- 合法边界处理

新增：

- sidecar state / working set 的加载与保存接口
- turn capsule 索引支持

### 8.2 `nanobot/agent/runner.py`

重点改造：

- 将 `_microcompact()` 升级为 tool-aware digest 机制
- 把大输出统一注册到 artifact store
- 保留现有 orphan repair / backfill / snip 的兜底作用

### 8.3 `nanobot/agent/context.py`

重点改造：

- 从“history 驱动”改成“working set 驱动”
- 引入分层预算 prompt assembly
- 支持 selected capsules / artifact digests 注入

### 8.4 `nanobot/agent/loop.py`

重点改造：

- 在 turn 完成时生成 capsule
- 更新 working set
- 维护 turn state machine
- 中断恢复时优先读取 turn state

### 8.5 `nanobot/agent/autocompact.py`

重点改造：

- idle 压缩不再只产出自由文本 summary
- 优先产出结构化 capsule / state snapshot
- 恢复时注入 working set 级摘要，而不是临时自然语言摘要

### 8.6 `nanobot/agent/memory.py`

短期内不大动。
本方案优先只改在线短期记忆层，不改变 `Consolidator -> Dream -> Promoter` 的长期记忆权限边界。

## 9. 分阶段实施

### Phase 1：引入 Artifact Store + Working Set

#### 目标

先解决重复大输出和上下文无序膨胀问题。

#### 范围

- `runner.py`
- `context.py`
- 新增 `short_memory.py` 或 `session_state.py`

#### 产出

- artifact digest
- working set sidecar
- prompt 分层预算初版

### Phase 2：引入 Turn Capsule

#### 目标

让在线短期记忆从“消息尾部”升级为“结构化回合胶囊”。

#### 范围

- `loop.py`
- `manager.py`
- `context.py`

#### 产出

- 每回合 capsule 生成
- capsule 选择性注入
- resume 与 capsule 协同

### Phase 3：引入 Turn State Machine

#### 目标

把恢复能力从消息修补升级到回合恢复。

#### 范围

- `loop.py`
- `runner.py`
- `manager.py`

#### 产出

- turn state 落盘
- 中断恢复 FSM
- 工具结果去重恢复

### Phase 4：整合 AutoCompact

#### 目标

让 idle compact 与 working set / capsule 统一。

#### 范围

- `autocompact.py`
- `context.py`
- `manager.py`

#### 产出

- 结构化 resume summary
- 更稳的跨 idle 恢复体验

## 10. 非目标

本方案暂不处理以下问题：

- 不把 `archive/history.jsonl` 直接变成 prompt 默认注入源
- 不改 Dream / Promoter 的权限模型
- 不在第一阶段引入向量数据库或语义检索系统
- 不把短期记忆直接做成长期人格记忆
- 不尝试用单个“全局摘要文件”替代 working set

## 11. 风险与对策

### 11.1 风险：working set 漂移

如果 working set 维护不当，可能出现“当前任务状态与真实对话不一致”。

#### 对策

- working set 只存可验证字段
- 关键内容来自 turn capsule，不直接自由生成
- turn 完成后统一刷新，避免中途频繁重写

### 11.2 风险：artifact digest 过于简化

如果 digest 太短，模型可能仍然重复执行工具。

#### 对策

- 先做规则化 digest
- 对重复 re-read 场景埋点
- 只在高价值工具上逐步增强 digest 粒度

### 11.3 风险：状态机带来复杂度

turn state machine 会提升恢复能力，但也增加实现复杂度。

#### 对策

- 第一阶段不引入完整 FSM
- 先做 artifact + working set
- 等在线收益验证后，再升级恢复层

## 12. 验收指标

### 12.1 稳定性指标

- 50+ tool calls 长任务成功率
- 中断恢复成功率
- orphan tool chain 恢复率
- 任务中途跟进消息注入后的完成率

### 12.2 上下文控制指标

- 平均 `prompt_tokens` 增长斜率
- 单任务最大上下文峰值
- raw history 与实际 prompt 大小的比值

### 12.3 token 成本指标

- 重复 `read_file` / `web_fetch` 次数
- 大工具输出重复发送率
- `cached_tokens` 占比提升情况
- artifact digest 命中率

## 13. 建议的首期实现结论

如果只做一版最小可用改造，建议优先落地以下三项：

1. **Artifact Store**
2. **Working Set**
3. **Prompt 分层预算组装**

这是最小成本、最大收益的一步，因为它能先解决：

- tool 输出重复发送
- 短期上下文无序增长
- 长任务中模型“知道做过什么，但不知道结果”的问题

而且不会破坏现有 `Consolidator / Dream / Promoter` 的整体设计边界。

## 14. 总结

本方案的核心不是“把更多历史塞进上下文”，而是：

- 把**原始日志**和**模型工作集**分开
- 把**大工具输出**变成**artifact + digest**
- 把**回合历史**变成**turn capsule**
- 把**恢复机制**升级为**turn-level state recovery**
- 把 prompt 从“按消息堆叠”改成“按工作集装配”

最终目标是让短期记忆从“超预算后被动压缩”升级为“面向长期任务的主动工作集系统”。
