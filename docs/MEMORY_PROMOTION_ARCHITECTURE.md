# 当前记忆晋升架构说明

本文基于当前代码实现梳理 nanobot 的“记忆晋升”架构，重点解释 4 件事：

1. 记忆是如何从会话内容一步步进入长期记忆的
2. 各分区分别写什么、谁可以写、为什么这么分
3. 晋升发生在什么时机，由哪些状态控制
4. 当前实现的安全边界、回退能力与局限

## 1. 一句话总览

当前架构不是“总结完直接写进长期记忆”，而是一个分层、分权、带候选区的流水线：

```text
session.messages
  -> Consolidator
  -> archive/history.jsonl
  -> Dream
  -> working/CURRENT.md | archive/reflections.jsonl | candidate/observations.jsonl
  -> Promoter
  -> identity/SOUL.md | identity/USER_RULES.md | identity/USER_PROFILE.md
```

它的核心目标不是“尽量多记”，而是“控制哪些记忆有资格影响未来 prompt”。

## 2. 分层与分区

当前记忆系统可以理解为 5 个主要分区。

### 2.1 `session.messages`

- 这是在线会话的短期消息历史。
- 用户和助手的真实对话先进入这里。
- 它不是长期记忆文件，而是运行时会话状态。
- 当上下文过长时，老消息会被 `Consolidator` 摘要后移出主上下文压力区。

### 2.2 `identity/`

包含 3 个高权限文件：

- `identity/SOUL.md`：稳定的助手原则、边界、风格
- `identity/USER_RULES.md`：用户明确提出的长期规则
- `identity/USER_PROFILE.md`：稳定用户画像、长期偏好、长期背景

特点：

- 这是高权限记忆层。
- 默认会被注入 system prompt。
- 不能由 Dream 直接写入。
- 当前实现只能由 `Promoter` 把候选观察晋升进去。

### 2.3 `working/CURRENT.md`

- 保存当前活跃任务、短期 handoff、近期状态。
- 会进入 system prompt。
- 属于“可影响当前行为，但不应轻易永久化”的工作记忆层。
- Dream 和 Heartbeat 都允许写这个文件。

### 2.4 `archive/`

主要包含：

- `archive/history.jsonl`：由 `Consolidator` 追加写入的历史摘要
- `archive/reflections.jsonl`：由 Dream / Heartbeat 追加写入的反思与归档笔记
- `archive/.cursor`：`history.jsonl` 的游标
- `archive/.dream_cursor`：Dream 已消费到哪里

特点：

- 这是机器友好的归档层，不默认注入 prompt。
- 用于 Dream 后续消费、回顾、检索，而不是直接塑造身份。

### 2.5 `candidate/observations.jsonl`

- 这是候选记忆池。
- Dream 会把“可能值得长期保留，但还不该直接进 identity 的内容”写到这里。
- Promoter 只从这里读取并决定是否晋升或拒绝。

它是整个架构中的“权限缓冲层”。

## 3. 哪些记忆会进入 prompt

当前默认只注入以下内容：

- `identity/SOUL.md`
- `identity/USER_RULES.md`
- `identity/USER_PROFILE.md`
- `working/CURRENT.md`

以下内容默认不注入：

- `archive/history.jsonl`
- `archive/reflections.jsonl`
- `candidate/observations.jsonl`

这意味着：

- `identity/*` 和 `working/CURRENT.md` 是“行为影响层”
- `archive/*` 和 `candidate/*` 是“存储/审核层”

这是当前架构最重要的权限边界。

## 4. 记忆晋升的完整链路

## 4.1 第一段：会话压缩到归档

用户消息和助手消息先保存在 `session.messages` 中。

当上下文 token 超预算时，`Consolidator.maybe_consolidate_by_tokens()` 会触发压缩：

- 估算当前 prompt token
- 如果未超预算，不处理
- 如果超预算，按“用户轮次边界”切一段旧消息
- 调 LLM 生成摘要
- 追加写入 `archive/history.jsonl`
- 将会话的 `last_consolidated` 前移

这里的关键点：

- `Consolidator` 只写 `archive/history.jsonl`
- 它不写 `identity/*`
- 它也不直接做“用户偏好晋升”

所以它只是把原始对话变成可供后续加工的“归档原料”。

## 4.2 第二段：Dream 从归档提炼候选记忆

Dream 是第二层处理器，分两阶段。

### Phase 1：分析

Dream 读取：

- `archive/history.jsonl` 中从 `.dream_cursor` 之后的新记录
- 当前 `identity/*`
- 当前 `working/CURRENT.md`
- 最近的 `archive/reflections.jsonl`
- 最近的 `candidate/observations.jsonl`

然后让模型输出若干分析结果，按以下类型分类：

- `[WORKING]`：应该进 `working/CURRENT.md`
- `[REFLECTION]`：应该进 `archive/reflections.jsonl`
- `[OBSERVATION]`：应该进 `candidate/observations.jsonl`
- `[PROMOTION]`：仍然写进 `candidate/observations.jsonl`，但标记为更接近晋升的候选
- `[SKILL]`：必要时生成技能文件

### Phase 2：受限写入

Dream 第二阶段通过受限工具落盘，但只允许写：

- `working/CURRENT.md`
- `archive/reflections.jsonl`
- `candidate/observations.jsonl`
- `skills/<name>/SKILL.md`

明确禁止直接写：

- `identity/SOUL.md`
- `identity/USER_RULES.md`
- `identity/USER_PROFILE.md`

这意味着 Dream 即使分析出“这条内容很像用户长期规则”，当前实现也只能先把它写成 candidate observation，不能越权直写 identity。

## 4.3 第三段：Promoter 执行真正的“晋升”

Promoter 是候选层到身份层的唯一正式晋升器。

它的处理流程是：

1. 读取 `candidate/observations.jsonl`
2. 遍历每条 observation
3. 先判断是否应拒绝
4. 再判断是否符合晋升条件
5. 如果符合，把内容 append 到目标 identity 文件
6. 回写 observation 的状态

## 5. 当前“晋升”是怎么判定的

当前代码中的晋升规则是硬规则，不是复杂评分器。

### 5.1 可晋升的前置条件

Promoter 只处理这些状态的 observation：

- `candidate`
- `observed`
- `promotion_proposal`

如果状态不在这个集合中，直接跳过。

同时还要求：

- `content` 不能为空
- `promotion_target` 必须能映射到目标文件

当前支持的目标只有：

- `identity.USER_RULES` -> `identity/USER_RULES.md`
- `identity.USER_PROFILE` -> `identity/USER_PROFILE.md`
- `identity.SOUL` -> `identity/SOUL.md`

### 5.2 晋升条件

当前只有两类晋升条件：

1. `source == "explicit_user_statement"`
2. `evidence_count >= repeat_threshold`

其中：

- `repeat_threshold` 默认是 `2`
- 也就是重复证据达到 2 次及以上时可以晋升

这代表当前系统更偏向两种可靠来源：

- 用户明确说过的话
- 跨轮次重复出现的稳定模式

### 5.3 拒绝条件

如果 observation 满足任一条件，会被拒绝：

1. 存在 `contradicted_by`
2. `confidence < 0.25`

也就是说，当前系统先做负向过滤，再做正向晋升。

## 6. 分区写入规则

可以把“谁能写哪里”整理成下面这张权限表。

| 写入者 | 可写目标 | 作用 |
|---|---|---|
| `Consolidator` | `archive/history.jsonl` | 把旧会话摘要归档 |
| `Dream` | `working/CURRENT.md` | 更新当前活跃工作状态 |
| `Dream` | `archive/reflections.jsonl` | 记录反思、归档笔记 |
| `Dream` | `candidate/observations.jsonl` | 写入候选观察与晋升提案 |
| `Promoter` | `identity/SOUL.md` | 晋升稳定助手原则 |
| `Promoter` | `identity/USER_RULES.md` | 晋升明确用户规则 |
| `Promoter` | `identity/USER_PROFILE.md` | 晋升稳定用户画像 |
| `Heartbeat` | `working/CURRENT.md`、`archive/reflections.jsonl` | 受限后台维护 |

如果从权限上理解：

- `archive/history.jsonl` 是归档入口
- `candidate/observations.jsonl` 是晋升缓冲区
- `identity/*` 是高权限终点
- `working/CURRENT.md` 是当前行为层，但不是长期身份层

## 7. 状态机：一条 observation 会经历什么状态

当前实现中，candidate observation 的关键状态如下。

### 7.1 初始状态

Dream 写入 observation 时，默认状态通常是：

- `candidate`

但 Promoter 也兼容读取：

- `observed`
- `promotion_proposal`

说明当前设计允许上游未来产生更细分状态，但真正实现的主干仍然是 `candidate`。

### 7.2 晋升后

如果满足晋升条件：

- `status = "promoted"`
- 增加 `promoted_at`
- 增加 `resolution_reason`

当前 `resolution_reason` 可能是：

- `explicit_user_statement`
- `repeated_evidence`

### 7.3 拒绝后

如果满足拒绝条件：

- `status = "rejected"`
- 增加 `rejected_at`
- 增加 `resolution_reason`

当前 `resolution_reason` 可能是：

- `contradicted`
- `low_confidence`

### 7.4 仍未处理

如果既不该拒绝，也不满足晋升条件：

- 保持原状态
- 继续留在 `candidate/observations.jsonl`

这就是“候选池”的意义：可以继续等待未来更多证据，而不是立刻进入 identity。

## 8. 时机：什么时候会发生这些动作

当前系统中，不同阶段的触发时机不同。

### 8.1 Consolidator 的触发时机

触发点在主消息处理链路中：

- 处理新消息前会检查一次
- 本轮响应结束后，会后台再检查一次

触发条件不是定时，而是：

- 当前会话 prompt token 超过安全预算

所以它是“按压力触发”。

### 8.2 Dream 的触发时机

Dream 有两种触发方式：

1. 手动触发：`/dream`
2. 定时触发：gateway 启动时注册系统 cron job

默认调度来自 `agents.defaults.dream.intervalH`：

- 默认值是 `2`
- 即默认每 2 小时执行一次

Dream 每次只处理 `.dream_cursor` 之后的新 `history.jsonl` 记录，并在运行后推进 `.dream_cursor`。

### 8.3 Promoter 的触发时机

Promoter 当前有一种主要自动触发方式：

- gateway 启动时注册系统 cron job
- 调度周期与 Dream 共用同一套 `dream` 配置

也就是默认：

- Promoter 也会每 2 小时跑一次

因此当前线上节奏通常是：

1. 对话变长 -> Consolidator 把旧消息归档
2. 到定时点 -> Dream 读取新归档，写入 candidate/working/reflection
3. 同样的定时系统任务 -> Promoter 扫 candidate，决定晋升或拒绝

注意：

- 当前代码里 Dream 和 Promoter 是两个独立系统 job
- 它们共享同一调度描述，但逻辑上并不是一个函数内串起来的
- 所以“候选生成”和“候选晋升”是解耦的

## 9. 游标与状态推进

当前架构里有两个非常关键的推进器。

### 9.1 `session.last_consolidated`

- 这是会话内的压缩边界
- 表示 `session.messages` 哪一部分已经被归档过
- 它防止同一段会话被重复摘要

### 9.2 `archive/.dream_cursor`

- 表示 Dream 已消费到哪条 `history.jsonl`
- Dream 每次只处理 cursor 更大的新归档记录
- 跑完后会推进到本批次最后一条记录

这两个游标分别负责：

- 会话 -> 归档 的去重推进
- 归档 -> Dream 的去重推进

## 10. 为什么必须经过 candidate 层

从设计上看，candidate 层解决的是“权限升级”问题，而不只是“记错了”的问题。

如果没有 candidate 层，会变成：

1. 对话被摘要
2. Dream 做出推断
3. 推断直接写入 identity

这会导致一个危险结果：

- 单次错误总结
- 一次误解
- 一次过度抽象

都可能直接污染未来所有 prompt。

现在的设计把它拆成两步：

1. Dream 只能提出候选
2. Promoter 再按硬规则晋升

因此 candidate 层本质上是“记忆权限隔离层”。

## 11. 当前实现的安全与回退机制

### 11.1 Dream 不能直接改 identity

Dream 的写权限被工具层限制，只能改：

- `working/CURRENT.md`
- `archive/reflections.jsonl`
- `candidate/observations.jsonl`

这是第一道安全阀。

### 11.2 Identity/Working 的关键文件可被版本化

`GitStore` 当前跟踪这些文件：

- `identity/SOUL.md`
- `identity/USER_RULES.md`
- `identity/USER_PROFILE.md`
- `working/CURRENT.md`

Dream 在产生真实改动时会自动提交版本，因此可以用：

- `/dream-log`
- `/dream-restore`

查看和回退 memory 变更。

### 11.3 Promoter 写 identity 时会去重

Promoter 追加 bullet 前，会先检查内容是否已存在于目标文件。

因此：

- 不会重复无脑追加同一句话
- 但去重是基于简单文本包含，不是语义去重

## 12. 当前实现的几个关键特点

### 12.1 晋升逻辑偏保守

当前只认可：

- 用户明确表达
- 重复证据

这说明系统现阶段更重视“别污染 identity”，而不是“尽快长期记住更多东西”。

### 12.2 `working` 和 `identity` 明确分开

不是所有重要信息都应该直接晋升。

例如：

- 当前任务
- 最近决策上下文
- 短期 handoff

这些更适合放在 `working/CURRENT.md`，而不是长期写进 `USER_PROFILE`。

### 12.3 当前状态枚举比当前逻辑更宽

Promoter 兼容：

- `candidate`
- `observed`
- `promotion_proposal`

但上游 Dream 模板主要仍然以 `candidate` 为主。

这说明状态机接口已经为未来更细粒度的候选阶段预留了扩展空间，但当前主实现还比较简单。

## 13. 当前可以如何理解“晋升”

如果用一句更工程化的话来描述：

> 当前 nanobot 的记忆晋升，不是“把总结写进记忆”，而是“把候选观察在满足硬证据条件后，提升为可进入 prompt 的高权限身份记忆”。

所以“晋升”本质上包含两个动作：

1. 权限变化：从 candidate 层进入 identity 层
2. 注入变化：从默认不注入，变成默认注入 system prompt

这两点同时发生，才是真正意义上的 memory promotion。

## 14. 一条完整示例

假设用户多次说“默认用中文回复”。

完整链路会是：

1. 这几轮对话先进入 `session.messages`
2. 会话变长后，旧内容被 `Consolidator` 摘要进 `archive/history.jsonl`
3. Dream 读取这些归档，生成一条 observation，例如：
   - `content = "Default to Chinese responses"`
   - `promotion_target = "identity.USER_RULES"`
   - `source = "explicit_user_statement"` 或 `evidence_count >= 2`
   - `status = "candidate"`
4. 这条 observation 被写入 `candidate/observations.jsonl`
5. Promoter 扫描到它，发现满足晋升条件
6. 把这条内容 append 到 `identity/USER_RULES.md`
7. 把 observation 状态改为 `promoted`
8. 之后这条规则会默认进入 system prompt

这就是当前“记忆晋升”主链路的标准路径。

## 15. 总结

当前架构可以概括为：

- `Consolidator` 负责把会话压缩成可消费归档
- `Dream` 负责从归档中提炼工作记忆、反思和候选观察
- `Promoter` 负责把候选观察按硬规则晋升到高权限身份记忆
- `candidate/observations.jsonl` 是权限隔离层，也是晋升缓冲层
- `identity/*` 是最终会影响未来 prompt 的高权限记忆层

从实现成熟度看，当前架构已经把“长期记忆污染”这个风险拆成了独立治理问题，并且通过：

- 分区
- 限权
- 游标
- 状态
- 可回退版本

把记忆晋升做成了一条相对清晰、可审计、可保守演进的流水线。
