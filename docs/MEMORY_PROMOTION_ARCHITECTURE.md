# 当前记忆晋升架构说明

## 1. 文档角色

本文说明 nanobot 当前“长期记忆晋升”这条链路如何工作，重点描述：

1. Dream 现在从哪里拿输入
2. 哪些文件可以写、哪些不能写
3. 候选观察如何进入长期身份记忆
4. 为什么 `working/CURRENT.md` 不再是这条链的主真相源

本文描述的是当前实现，不再沿用旧的 `session.messages -> history.jsonl -> CURRENT.md` 主通路叙述。

## 2. 一句话总览

当前记忆晋升主链可以概括成：

```text
committed turn
  -> CommitManifest
  -> TurnCapsule + WorkingSetSnapshot + Artifact digests
  -> Dream
  -> working/CURRENT.md | archive/reflections.jsonl | candidate/observations.jsonl
  -> Promoter
  -> identity/SOUL.md | identity/USER_RULES.md | identity/USER_PROFILE.md
```

它的核心目标不是“尽量多记”，而是“控制哪些结论有资格影响未来 prompt”。

## 3. 分层与分区

### 3.1 结构化状态层

记忆晋升链的上游不再是“当前短期状态靠 `CURRENT.md` 推断”，而是 committed turn 生成的结构化对象：

- `CommitManifest`
- `TurnCapsule`
- `WorkingSetSnapshot`
- `ArtifactRecord` 的 digest / render

这一层的职责是给 Dream 提供已提交、可复用、可去重的事实输入。

### 3.2 identity

高权限长期记忆层：

- `identity/SOUL.md`
- `identity/USER_RULES.md`
- `identity/USER_PROFILE.md`

特点：

- 默认进入系统 prompt
- 影响未来行为
- Dream 不能直接写
- 只有 Promoter 可以正式晋升进去

### 3.3 working/CURRENT.md

`working/CURRENT.md` 当前仍可被 Dream 更新，但它的角色已经降级为：

- 镜像输出
- handoff 视图
- 人类可读的短期摘要

它不是：

- Dream 的主输入真相源
- 当前运行时状态唯一来源
- 长期身份层

### 3.4 archive

主要包含：

- `archive/history.jsonl`
- `archive/reflections.jsonl`

当前职责：

- 保存历史归档与反思材料
- 提供可检索上下文
- 给长期记忆处理链补充背景

它不是当前短期状态判定层。

### 3.5 candidate/observations.jsonl

候选记忆池，用于缓存：

- 尚未验证的观察
- 晋升建议
- 暂时不适合直接进入 identity 的结论

它是权限缓冲层，而不是长期事实层。

### 3.6 heartbeat 与记忆晋升的关系

heartbeat 是否拥有普通 agent 的工具能力，和长期记忆晋升保护，不是同一个问题。

这里需要分开理解：

- identity 的保护目标，是避免自动流程直接把未经审核的结论写入高权限长期记忆
- heartbeat 的执行能力，属于“后台触发时能不能正常做事”的问题

因此当前推荐理解是：

- heartbeat 可以是完整 agent 的一种触发方式
- 但 heartbeat 不应绕过 candidate / Promoter 直接修改 `identity/*`
- 对 identity 的保护，应当通过 prompt 约束 + runtime 边界实现，而不是把 heartbeat 整体降成低权限执行器

## 4. Dream 现在如何拿输入

当前 Dream 会遍历结构化状态目录，挑出满足以下条件的 turn：

- `TurnState.commit_state == committed`
- `TurnState.commit_manifest_ref` 存在
- manifest 自洽且 `completed_marker=True`
- manifest revision 与 turn revision 一致
- capsule、working set、artifact digests 可加载

然后 Dream 为每个 turn 组装结构化输入：

```python
DreamInput = {
    "session_key": str,
    "turn_id": str,
    "capsule": dict,
    "working_set_snapshot": dict | None,
    "artifact_digests": list[dict],
    "candidate_signals": list[dict],
    "idempotency_key": str,
}
```

关键点：

- 输入来自 committed turn，而不是从 `CURRENT.md + history.jsonl` 反推
- `idempotency_key` 用于防止重复消费同一 turn
- Dream cursor 现在记录的是结构化 turn 消费进度

## 5. Dream 的输出边界

Dream 现在允许写：

- `working/CURRENT.md`
- `archive/reflections.jsonl`
- `candidate/observations.jsonl`
- `skills/<name>/SKILL.md`

Dream 明确禁止直写：

- `identity/SOUL.md`
- `identity/USER_RULES.md`
- `identity/USER_PROFILE.md`

这条边界非常重要：Dream 可以提出结论，但不能直接把结论提升成长期身份事实。

## 6. working/CURRENT.md 的当前定位

Dream 当前文件上下文里已经明确写出：

- `working/CURRENT.md` 是 mirror-only output
- 不应被当作 source of truth

因此 Dream 更新 `CURRENT.md` 的意义是：

- 生成给人读的当前摘要
- 提供兼容文本层
- 维护 handoff 体验

而不是：

- 驱动当前运行时主状态
- 作为长期晋升的原始事实基线

## 7. Promoter 如何做正式晋升

Promoter 负责从候选层读取 observation，并决定是否晋升到 identity。

它处理的基本流程是：

1. 读取 `candidate/observations.jsonl`
2. 过滤掉状态不允许或证据不足的 observation
3. 判断目标是 `SOUL`、`USER_RULES` 还是 `USER_PROFILE`
4. 满足条件时 append 到目标 identity 文件
5. 回写 observation 的处理状态

因此：

- Dream 负责提炼候选
- Promoter 负责权限升级

## 8. 为什么这样分层

这套架构的重点不是“摘要质量绝对正确”，而是“错误摘要也不能轻易获得过高权限”。

分层后的效果是：

- committed turn 给 Dream 提供更稳定、去噪的输入
- `CURRENT.md` 只承担镜像，不再绑架主状态
- archive 负责保留与检索，不直接变成长期身份
- candidate 负责缓冲与审核
- identity 只接受正式晋升结果
- heartbeat 是否具备完整工具能力，不改变这条晋升链；真正需要保护的是 `identity/*` 的正式入口

## 9. 与旧架构的区别

旧叙述更像：

```text
session.messages
  -> archive/history.jsonl
  -> Dream
  -> working/CURRENT.md
```

当前实现更接近：

```text
TurnState + CommitManifest
  -> TurnCapsule + WorkingSetSnapshot + Artifact digests
  -> Dream
  -> CURRENT mirror / reflections / candidate observations
  -> Promoter
  -> identity
```

变化的本质是：Dream 的输入从“旧文本推断”切到了“已提交结构化产物”。

## 10. 地图

把当前记忆晋升架构记成下面 6 条就够了：

- `/.nanobot/state/` 是短期运行时真相源
- committed turn 是 Dream 的主输入
- `working/CURRENT.md` 是 mirror / handoff，不是 source of truth
- `candidate/observations.jsonl` 是长期晋升前的缓冲层
- `identity/*` 是受保护的高权限长期层
- 正式进入 `identity/*` 的路径是 `candidate -> Promoter -> identity`

因此：

- 不要再把 `CURRENT.md`、`history.jsonl` 或原始消息尾部当成当前晋升链的核心输入层
- 不要把 heartbeat 的工具能力问题，和 identity 的保护边界混为一谈
