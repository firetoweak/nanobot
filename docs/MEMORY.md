# nanobot 的记忆系统

## 一句话总览

`nanobot` 当前采用的是一套分层、分权限、结构化状态优先的记忆系统：

- 运行时短期状态由 `.nanobot/state/` 下的结构化对象驱动
- `working/CURRENT.md` 保留为镜像/交接视图
- `archive/` 和 `candidate/` 负责归档、反思、候选晋升
- `identity/` 才是默认直接影响长期行为的高权限记忆层

如果只记一句话，可以记成：

- `TurnState + WorkingSetSnapshot + CommitManifest` 决定当前任务和恢复
- `CURRENT.md` 负责镜像展示，不再是运行时主真相源
- `Dream + Promoter` 负责把已提交的结构化产物沉淀到更长期的记忆层

## 1. 记忆分层

### 1.1 运行时短期层

当前短期运行时主链位于 `.nanobot/state/`：

```text
.nanobot/state/
  sessions/<session_key>/
    turns/
    messages/
    responses/
    working-set/
    capsules/
    artifacts/
    commits/
    indexes/
```

这层保存的是结构化执行状态，而不是给人读的自由文本。

关键对象包括：

- `TurnState`：当前 turn 在做什么、做到哪一步
- `WorkingSetSnapshot`：当前稳定工作集
- `TurnCapsule`：一轮完成后的结构化结论
- `ArtifactRecord`：工具结果及其 render/digest
- `CommitManifest`：稳定完成判据

### 1.2 镜像层

`working/CURRENT.md` 仍然存在，但现在属于镜像层：

- 方便人读
- 方便 handoff
- 可以被 Dream 更新
- 在部分 prompt 模板中保留兼容注入

但它不再承担：

- 当前运行时主状态
- 恢复判定
- Dream 的主输入真相源

### 1.3 审计与检索层

下面这些内容主要服务于审计、检索和兼容：

- `session.messages`
- `sessions/*.jsonl`
- `archive/history.jsonl`
- `archive/reflections.jsonl`

它们仍然重要，但不再是当前任务状态的唯一来源。

### 1.4 候选与长期层

- `candidate/observations.jsonl`：候选观察、待审核结论
- `identity/SOUL.md`：稳定助手原则
- `identity/USER_RULES.md`：稳定用户规则
- `identity/USER_PROFILE.md`：稳定用户画像

这层的关键目标是：让“记录下来”与“真正影响未来行为”之间有明确权限边界。

## 2. 运行时短期层

### 2.1 当前任务靠什么维持

当前一轮任务的执行与恢复主要依赖：

- `TurnState`
- `WorkingSetSnapshot`
- 最近合法 `Recent Raw Turns`
- `TurnCapsule` / `ArtifactRecord` 补充信息

其中：

- `TurnState` 管执行过程
- `WorkingSetSnapshot` 管稳定工作内存
- `CommitManifest` 管稳定完成

### 2.2 Prompt 看什么

当前 prompt 不是简单把 `session.messages` 全塞给模型，而是按分层组装：

1. `System Prefix`
2. `WorkingSetSnapshot`
3. `Recent Raw Turns`
4. `Selected TurnCapsules`
5. `Selected Artifact Render`
6. `Current User Message`

这意味着：

- 工作集优先于原始历史
- 原始历史只保留最近合法尾部
- 大工具结果默认以 render/digest 视图进入 prompt

### 2.3 原始消息还在做什么

`session.messages` 和 `last_consolidated` 没有消失，但它们当前主要承担：

- 原始会话审计
- `Recent Raw Turns` 的来源
- Consolidator 的归档输入

它们不再是短期运行时主工作内存。

## 3. 稳定提交层

### 3.1 什么叫“这轮真的完成了”

在当前架构里，一轮 turn 是否稳定完成，不是只看 `current_stage == completed`。

真正的完成判据是：

- `TurnState.commit_state == committed`
- `TurnState.commit_manifest_ref` 指向有效 manifest
- `CommitManifest.completed_marker == True`
- manifest 引用的核心对象可以解析

因此，`CommitManifest` 是稳定完成的唯一正式判据。

### 3.2 为什么要有稳定提交层

它解决了几个老问题：

- 不再只靠消息链猜测完成状态
- 恢复时能区分“已完成”“半提交”“仍在执行”
- Dream、AutoCompact、Prompt 可以消费同一套稳定对象

### 3.3 这层产出什么

一轮完成后，主链会产出：

- `TurnCapsule`
- `WorkingSetSnapshot`
- `ResponseObject`
- `CommitManifest`

这些对象共同构成后续恢复、压缩、长期沉淀的输入。

## 4. 长期记忆与晋升层

### 4.1 Consolidator

`Consolidator` 仍负责把旧原始消息沉淀进 `archive/history.jsonl`。

它的职责是：

- 减轻原始历史尾部压力
- 生成归档材料
- 为后续 Dream/检索提供辅助输入

它不是当前短期状态的唯一组织者。

### 4.2 Dream

`Dream` 当前处理的是 committed turn 的结构化产物，而不是主要靠 `CURRENT.md + history.jsonl` 反推当前状态。

Dream 的输入重点是：

- `TurnCapsule`
- `WorkingSetSnapshot`
- artifact digests / renders
- candidate signals

Dream 的输出目标包括：

- `working/CURRENT.md` 的镜像更新
- `archive/reflections.jsonl`
- `candidate/observations.jsonl`
- 必要时技能文件

Dream 不直接写 `identity/*`。

### 4.3 Promoter

`Promoter` 是候选层到身份层的权限边界。

它从 `candidate/observations.jsonl` 读取候选结论，并决定是否晋升到：

- `identity/SOUL.md`
- `identity/USER_RULES.md`
- `identity/USER_PROFILE.md`

因此，长期行为改变不是 Dream 直接决定的，而是经过候选层和晋升判断。

## 5. CURRENT.md 和 history.jsonl 现在还剩什么职责

### 5.1 working/CURRENT.md

现在的 `working/CURRENT.md` 更接近：

- 当前结构化状态的人类可读镜像
- handoff 视图
- 兼容文本层

它不再是：

- 当前任务的唯一工作记忆
- 恢复入口
- commit 完成判据

### 5.2 archive/history.jsonl

现在的 `archive/history.jsonl` 更接近：

- 归档历史
- 检索材料
- Dream/分析流程的辅助输入

它不再是：

- prompt 默认主输入
- 当前执行阶段的判定依据

## 6. 为什么要这样分层

这样做的好处不只是“省 token”，更重要的是把不同类型的信息分权：

- 当前执行状态交给结构化状态对象
- 人类可读摘要放进镜像层
- 归档和检索放进 archive
- 候选结论留在 candidate
- 真正长期生效的内容才进入 identity

这样可以避免两类常见问题：

1. 把临时状态误当成长期事实
2. 把历史材料误当成当前运行时主状态

## 7. 对外理解这套系统的方式

如果你要判断某类信息应该看哪里，可以按下面理解：

- 看“现在这轮做到哪了”：`TurnState`
- 看“当前稳定工作上下文”：`WorkingSetSnapshot`
- 看“这轮是否稳定完成”：`CommitManifest`
- 看“这轮沉淀了什么结论”：`TurnCapsule`
- 看“工具结果留下了什么可复用证据”：`ArtifactRecord`
- 看“给人读的当前交接摘要”：`working/CURRENT.md`
- 看“历史归档和检索材料”：`archive/history.jsonl`
- 看“待审核候选结论”：`candidate/observations.jsonl`
- 看“真正长期影响 agent 的内容”：`identity/*`

## 8. 相关文档

- `docs/上下文工作集优化方案.md`：短期上下文主链与 prompt 组装合同
- `docs/回合状态恢复优化方案.md`：恢复、提交、repair 合同
- `docs/MEMORY_PROMOTION_ARCHITECTURE.md`：记忆晋升与权限边界

如果需要理解“当前实现”，优先读上面三份；不要再把旧压缩分析文档当成现行权威说明。
