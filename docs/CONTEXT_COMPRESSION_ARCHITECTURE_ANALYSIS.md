# nanobot 上下文压缩架构分析

本文基于当前仓库里的实现与现有设计文档，分析 nanobot 是如何处理“上下文压缩”的。这里的“压缩”并不只是把长对话做摘要，而是一个分层体系：

1. 在线阶段控制当前 prompt 的长度，避免上下文窗口被打爆。
2. 离线阶段把旧对话转成可检索、可审核、可晋升的记忆材料。
3. 通过权限边界区分“能影响当前行为的记忆”和“只是被保存起来的历史”。

换句话说，nanobot 的上下文压缩，本质上是“短期上下文减载 + 长期记忆治理”。

## 1. 一句话总览

当前实现可以概括成两条并行但衔接的链路：

```text
在线短期上下文
session.messages
  -> 基于 token 预算的 Consolidator
  -> archive/history.jsonl
  -> 当前 prompt 只保留未归档的新尾部

离线长期记忆
archive/history.jsonl
  -> Dream
  -> working/CURRENT.md | archive/reflections.jsonl | candidate/observations.jsonl
  -> Promoter
  -> identity/SOUL.md | identity/USER_RULES.md | identity/USER_PROFILE.md
```

因此，项目里的“上下文压缩”不是单级摘要，而是两段式：

- 第一段解决 LLM 当前请求的上下文窗口压力。
- 第二段解决长期连续性、知识沉淀和记忆污染风险。

## 2. 整体设计目标

从当前代码和文档看，这套架构主要解决 4 个问题：

### 2.1 控制 prompt 大小

`session.messages` 会持续增长，如果全部原样注入模型，很容易超过上下文窗口。系统需要在不破坏对话合法边界的前提下，把旧消息从在线 prompt 中移走。

### 2.2 保留连续性

旧消息被移出 prompt 后，不能彻底丢失；它们会先进入归档，再由 Dream 做更高层的整理，以便后续继续利用。

### 2.3 避免长期记忆污染

系统明确避免“摘要完直接写进长期身份记忆”。旧对话先进入 `archive/`，再进入 `candidate/`，最后才可能晋升到 `identity/`。这说明项目把长期记忆污染视为权限问题，而不是单纯的摘要质量问题。

### 2.4 保持可审计、可回退

`identity/*` 和 `working/CURRENT.md` 属于 prompt 关键记忆，配合 `GitStore` 做版本化，这让 Dream 产生的长期影响可查看、可恢复，而不是黑箱自动改写。

## 3. 当前上下文压缩策略是怎么做的

项目当前采用的是“前台按 token 压缩，后台按阶段沉淀”的策略。

### 3.1 在线压缩：按 token 压力触发

当一轮请求开始前，`AgentLoop` 会先调用 `Consolidator.maybe_consolidate_by_tokens(session)`。

它的核心流程是：

1. 估算当前 session 构造成 prompt 后的 token 数。
2. 计算安全预算：
   - `budget = context_window_tokens - max_completion_tokens - safety_buffer`
3. 如果当前估算值低于 `budget`，不压缩。
4. 如果超出 `budget`，从 `session.last_consolidated` 开始向前找一个“安全切点”。
5. 把这一段旧消息交给 LLM 摘要，并写入 `archive/history.jsonl`。
6. 推进 `session.last_consolidated`，让这段消息不再进入后续 prompt。
7. 重新估算 token，必要时继续压缩。

这里有两个很关键的实现细节：

- 触发阈值是“超过安全预算”。
- 一旦触发，不是刚降到预算以内就停，而是继续压缩到 `budget // 2` 左右的目标值，给后续工具调用、模型回答、注入消息留出更多余量。

### 3.2 在线压缩：按用户轮次边界切块

压缩不是随便从中间截断，而是尽量按“用户轮次边界”切。

当前逻辑会：

- 从 `last_consolidated` 之后开始扫描消息。
- 只在后续再次遇到 `role == "user"` 时，把那里视为安全边界。
- 尽量以完整的多轮交互为摘要单元，而不是把一组工具调用或 assistant 回复切成半段。

这能避免两类问题：

1. 对话语义被切断，摘要质量下降。
2. 工具调用链路被破坏，形成非法消息前缀。

### 3.3 在线压缩：单轮最多处理 60 条消息

`Consolidator` 还有一个硬上限 `_MAX_CHUNK_MESSAGES = 60`。

如果本次可归档分段太长，系统会尝试把边界往回收，直到找到 60 条消息之内、且仍然落在用户边界上的切点；如果找不到，就放弃这一轮压缩，而不是冒险切断回合。

这说明它的策略是：

- 优先合法边界；
- 其次控制单次摘要规模；
- 不为了压缩而破坏对话结构。

### 3.4 在线压缩：只“移出 prompt”，不物理删除消息

正常 token 压缩并不会直接从 `session.messages` 里删掉旧消息，而是通过 `last_consolidated` 标记“哪些消息已经归档过”。

也就是说：

- `session.messages` 是完整日志；
- `session.get_history()` 返回的是 `messages[last_consolidated:]` 这一段未归档尾部；
- 当前 prompt 只看到未归档部分；
- 旧消息虽然仍存于 session 文件，但已经不再参与正常 LLM 上下文构建。

这是一种“逻辑压缩”，而不是立刻做物理裁剪。

### 3.5 空闲压缩：针对长期 idle 会话的自动瘦身

除了 token 压缩，项目还有一个 `AutoCompact`，用于处理长时间无人继续的会话。

当配置了 `session_ttl_minutes` 且某个 session 超过这个空闲时间后，系统会在主循环空闲 tick 中触发 `_archive()`：

1. 只处理 `last_consolidated` 之后还没归档的新消息。
2. 把这些消息拆成：
   - 可归档前缀
   - 保留在 session 里的最近合法后缀
3. 默认只保留最近 8 条消息（`_RECENT_SUFFIX_MESSAGES = 8`）。
4. 可归档前缀通过 `Consolidator.archive()` 写入 `archive/history.jsonl`。
5. 归档摘要会暂存为 `_last_summary`，供用户下次回来时以运行时提示方式注入。

这个策略和在线 token 压缩不同：

- token 压缩：主要为了当前这轮 LLM 调用能放得下。
- idle 压缩：主要为了让长期静置的 session 自身也变轻，不把很久以前的全部尾部继续留在活跃上下文中。

### 3.6 恢复策略：摘要只做一次性恢复提示

当用户重新进入一个被 idle compact 过的 session 时，`prepare_session()` 会返回一个 session summary：

- 格式类似 “Inactive for X minutes. Previous conversation summary: ...”
- 这段内容被作为 runtime context 注入本轮用户消息之前
- 只用于这一次恢复，不会写回 `session.messages`
- 消费后会从 `_summaries` 或 metadata 中清掉

因此，idle compact 的恢复上下文是：

- 一次性的；
- 运行时的；
- 不污染长期 session 历史；
- 也不直接进入 `identity/*`。

## 4. 短期记忆设计架构

如果只看“当前对话如何被模型使用”，短期记忆架构主要由 4 层组成。

### 4.1 第一层：`session.messages`

这是最原始的短期记忆层，保存会话里的完整消息流，包括：

- user 消息
- assistant 消息
- tool 消息
- 相关附加字段，如 `tool_calls`、`tool_call_id`

它是系统最真实的会话源数据。

但这层并不等于“全部注入 prompt”，真正送给模型的是它经过过滤后的投影视图。

### 4.2 第二层：`last_consolidated` 驱动的在线工作视图

`Session.get_history()` 会从 `session.messages[last_consolidated:]` 取出未归档尾部，然后再做两次合法化处理：

1. 尽量从最近一次 `user` 开始，避免从半个回合中间起头。
2. 去掉前部孤立 tool 结果，保证消息序列合法。

这说明短期记忆不是“原始日志直接给模型”，而是一个经过合法边界修复的会话工作集。

### 4.3 第三层：运行时上下文注入

构造消息时，`ContextBuilder` 会把以下运行时信息拼到当前用户消息前面：

- 当前时间
- channel / chat_id
- 如果是 idle 恢复，还会带 session summary

它被明确标记为 runtime metadata，而不是可信指令。

这层属于“临时短期上下文”，特点是：

- 只影响当前轮；
- 不持久化到长期 memory；
- 保存 session 时会被剥离，防止元信息污染对话历史。

### 4.4 第四层：`working/CURRENT.md`

虽然 `working/CURRENT.md` 已经不属于纯会话日志，但从 prompt 注入角度，它仍然是短期记忆的一部分，因为它会直接进入 system prompt。

它承载的是：

- 当前活跃任务
- handoff 状态
- 近期仍有效但不应永久化的信息

这层比 `session.messages` 更稳定，但比 `identity/*` 更短命，因此可以理解为“跨轮工作的工作记忆层”。

### 4.5 短期记忆的核心特点

综合来看，项目的短期记忆设计有这些特点：

- 以 `session.messages` 作为真实事实源。
- 以 `last_consolidated` 把“日志全量”和“prompt 工作集”分开。
- 以 `runtime context` 承载一次性恢复提示。
- 以 `working/CURRENT.md` 承载跨轮但短寿命的任务上下文。
- 通过边界修复，确保短期记忆送入模型时始终是合法消息序列。

## 5. 长期记忆设计架构

长期记忆不是一个单文件，而是一个分区、分权、分阶段处理的层级系统。

### 5.1 分区结构

当前长期记忆主要由以下目录构成：

```text
identity/
  SOUL.md
  USER_RULES.md
  USER_PROFILE.md

working/
  CURRENT.md

archive/
  history.jsonl
  reflections.jsonl
  .cursor
  .dream_cursor

candidate/
  observations.jsonl
```

其中角色如下：

- `identity/`：高权限、稳定、默认注入 prompt 的身份记忆。
- `working/CURRENT.md`：当前工作态记忆，默认注入 prompt，但设计上应可过期。
- `archive/history.jsonl`：归档摘要层，供后续 Dream 消费，不默认注入。
- `archive/reflections.jsonl`：反思与归档注记，不默认注入。
- `candidate/observations.jsonl`：候选观察层，是进入 identity 之前的权限缓冲区。

### 5.2 第一段：Consolidator 把旧对话变成归档原料

短期上下文溢出的第一落点是 `archive/history.jsonl`。

`Consolidator.archive()` 的职责很单纯：

1. 把一段旧消息格式化为文本。
2. 调用专用模板 `agent/consolidator_archive.md` 提取关键事实。
3. 追加写入 `archive/history.jsonl`。
4. 为每条记录分配自增 cursor。

如果 LLM 摘要失败，它会降级为 raw archive，把原始消息按 `[RAW]` 格式落入 `history.jsonl`，保证归档链路不会断。

因此，长期记忆链路的第一层设计不是“精致记忆”，而是“先保证可落盘、可继续消费”。

### 5.3 第二段：Dream 做深层整理

Dream 是长期记忆的核心加工器，而且是一个两阶段处理器。

### Phase 1：分析

Dream 会读取：

- `.dream_cursor` 之后的新 `archive/history.jsonl`
- 当前 `identity/*`
- 当前 `working/CURRENT.md`
- 最近 `archive/reflections.jsonl`
- 最近 `candidate/observations.jsonl`

然后让模型输出分析结果，分类成：

- `[WORKING]`
- `[REFLECTION]`
- `[OBSERVATION]`
- `[PROMOTION]`
- `[SKILL]`

### Phase 2：受限落盘

第二阶段通过受限工具真正写文件，但只允许写：

- `working/CURRENT.md`
- `archive/reflections.jsonl`
- `candidate/observations.jsonl`
- `skills/<name>/SKILL.md`

明确不允许写：

- `identity/SOUL.md`
- `identity/USER_RULES.md`
- `identity/USER_PROFILE.md`

这就是长期记忆架构里最重要的权限隔离：Dream 能做整理和提案，但不能直接改高权限身份记忆。

### 5.4 第三段：Promoter 执行正式晋升

真正把候选内容写进长期身份记忆的是 `Promoter`。

它只从 `candidate/observations.jsonl` 读数据，并按硬规则决定：

- 跳过
- 拒绝
- 晋升

当前支持的晋升目标只有：

- `identity.USER_RULES`
- `identity.USER_PROFILE`
- `identity.SOUL`

晋升条件也很保守，主要只有两类：

1. `source == "explicit_user_statement"`
2. `evidence_count >= repeat_threshold`，默认阈值是 `2`

同时存在两个硬拒绝条件：

1. `contradicted_by` 不为空
2. `confidence < 0.25`

这说明长期记忆层当前偏保守治理：

- 宁可少晋升，也尽量避免污染 `identity/*`
- 优先接受明确用户陈述和重复出现的稳定证据

### 5.5 第四段：Prompt 注入边界

长期记忆里并不是所有内容都会进入 prompt。

当前默认注入的只有：

- `identity/SOUL.md`
- `identity/USER_RULES.md`
- `identity/USER_PROFILE.md`
- `working/CURRENT.md`

而这些不会默认注入：

- `archive/history.jsonl`
- `archive/reflections.jsonl`
- `candidate/observations.jsonl`

这个边界非常关键，因为它把长期记忆分成了两类：

- 行为影响层：会直接塑造后续 agent 行为。
- 存储审核层：只是保留、搜索、等待处理，不直接影响 prompt。

### 5.6 第五段：游标推进机制

长期记忆链路里有两个重要游标：

#### `archive/.cursor`

由 `append_history()` 维护，给 `history.jsonl` 的每条记录分配递增 cursor。

#### `archive/.dream_cursor`

记录 Dream 已处理到哪条归档摘要。Dream 每次只消费 cursor 更大的新内容，处理完成后再推进这个游标。

因此，长期记忆架构不是“反复扫全量”，而是标准的增量消费流水线。

### 5.7 第六段：版本化与回退

`GitStore` 会跟踪这些关键记忆文件：

- `identity/SOUL.md`
- `identity/USER_RULES.md`
- `identity/USER_PROFILE.md`
- `working/CURRENT.md`

Dream 产生真实改动时可以自动提交版本，因此支持：

- 查看最近 Dream 改了什么
- 对 memory 文件做回退

这让长期记忆系统具备两个工程属性：

- 可审计
- 可恢复

## 6. 短期记忆与长期记忆如何衔接

把两部分放在一起看，当前架构的衔接关系如下：

### 6.1 短期记忆负责“当下能用”

- `session.messages` 保留实时交互
- `last_consolidated` 控制 prompt 可见区
- `working/CURRENT.md` 保留近期工作态
- idle compact 的 session summary 只做一次性恢复

### 6.2 长期记忆负责“未来还要记住什么”

- `archive/history.jsonl` 收纳历史摘要
- Dream 提炼工作记忆、反思、候选观察
- Promoter 把少量高可信内容晋升到 `identity/*`

### 6.3 两者之间的桥是 `archive/history.jsonl`

短期上下文被压缩后，不会直接成为长期身份记忆，而是先变成归档层输入，再由 Dream 和 Promoter 层层处理。

因此，`archive/history.jsonl` 是整个系统的中间枢纽：

- 对上承接短期上下文减载
- 对下驱动长期记忆沉淀

## 7. 触发时机汇总

为了更清楚地理解“什么时候压缩、什么时候晋升”，可以把触发点整理如下。

### 7.1 `Consolidator`

触发时机：

- 每轮消息处理前先检查一次
- 每轮消息处理后后台再检查一次
- `/new` 时会把当前未归档部分异步归档

触发依据：

- 当前 prompt token 超预算

### 7.2 `AutoCompact`

触发时机：

- `AgentLoop.run()` 在空闲 tick 中检查所有 session

触发依据：

- 配置了 `session_ttl_minutes`
- session 长时间未活跃
- 当前没有进行中的 agent 任务占用该 session

### 7.3 `Dream`

触发时机：

- 手动命令 `/dream`
- gateway 启动后注册系统 cron job

默认调度：

- `agents.defaults.dream.intervalH = 2`
- 即默认每 2 小时跑一次

### 7.4 `Promoter`

触发时机：

- gateway 启动后注册系统 cron job

默认调度：

- 与 Dream 共用同一套调度配置
- 默认也是每 2 小时运行一次

## 8. 为什么这个架构可以视为“分层压缩”

从工程角度看，当前实现把“压缩”拆成了 3 个层次：

### 8.1 Prompt 层压缩

通过 `last_consolidated` 和 token 预算控制，让模型只看到必要的新尾部。

### 8.2 Session 层压缩

通过 `AutoCompact` 让长期 idle session 只保留最近合法后缀，并用一次性摘要帮助恢复上下文。

### 8.3 Memory 层压缩

通过 `Consolidator -> Dream -> Promoter`，把大量原始对话逐步压成：

- 归档摘要
- 反思记录
- 候选观察
- 高可信身份记忆

这比“做一份总摘要放进 memory”更稳健，因为它把不同寿命、不同权限、不同可信度的信息分开存储了。

## 9. 当前架构的优点

### 9.1 对 prompt 成本友好

旧消息会被主动移出在线 prompt，而不是无限累积。

### 9.2 对结构合法性友好

压缩时尊重用户轮次边界，并避免截断工具调用链。

### 9.3 对长期污染更保守

Dream 不能越权改 `identity/*`，必须经过 candidate 和 Promoter。

### 9.4 对恢复体验更友好

idle compact 后不是简单清空，而是给一次性的 resumed summary。

### 9.5 对工程治理更友好

有 cursor、有状态、有版本化、有回退路径。

## 10. 当前局限与注意点

### 10.1 `session.messages` 正常压缩后仍会持续增长

正常 token consolidation 只是推进 `last_consolidated`，不是立刻从 session 文件物理删除旧消息。真正做物理瘦身的是 idle compact。

### 10.2 identity 晋升规则还比较简单

当前主要靠显式用户陈述和重复证据，尚未形成更细粒度的冲突解决、语义去重、时间衰减机制。

### 10.3 去重仍是文本级

`Promoter` 写入 `identity/*` 时主要按简单文本包含去重，不是语义去重。

### 10.4 `working/CURRENT.md` 仍然是 prompt 注入层

虽然它不属于高权限身份记忆，但依然能直接影响当前行为，因此仍需要谨慎维护。

## 11. 结论

如果用一句话总结当前项目的上下文压缩架构：

> nanobot 不是把长对话“压成一个摘要”，而是把对话先从在线上下文中安全移出，再通过归档、反思、候选和晋升四层记忆流水线，逐步筛选出真正值得进入未来 prompt 的内容。

具体到两类记忆设计：

- 短期记忆架构：以 `session.messages` 为原始源，以 `last_consolidated` 为在线裁剪边界，以 runtime summary 和 `working/CURRENT.md` 维持近期连续性。
- 长期记忆架构：以 `archive/history.jsonl` 为归档入口，以 Dream 做分层提炼，以 `candidate/observations.jsonl` 做权限缓冲，以 Promoter 做高权限晋升。

因此，当前实现的核心不是“多记一点”，而是：

- 在当前轮里只保留必要上下文；
- 在长期里只让高可信信息进入 prompt；
- 用分层和限权来降低记忆污染风险。
