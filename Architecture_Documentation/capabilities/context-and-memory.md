# 能力域：Context And Memory Governance

## 1. 责任范围

本能力域描述 prompt 上下文如何组装，以及长期/短期记忆如何分层治理。

当前主要实现位于：

- `nanobot/agent/context.py`
- `nanobot/agent/memory.py`
- `nanobot/agent/promoter.py`
- `nanobot/templates/`

## 2. 当前真实实现

### 2.1 Prompt 组装

`ContextBuilder` 当前不会只把“最近聊天记录”直接塞给模型，而是按结构化块组装：

- system prompt
- working set block
- recent raw turns
- selected capsules
- selected artifacts
- 当前用户消息

`assemble_prompt_payload(...)` 会在字符预算不足时，按优先级裁剪：

1. 先裁 capsule
2. 再裁 artifact
3. 再裁原始 turns
4. 最后才压缩 working set 的 budget hints

这说明当前实现明确偏向“工作集 + 结构化摘要优先”，而不是“历史尾部优先”。

### 2.2 分层记忆

`MemoryStore` 当前维护四层目录：

- `identity/`
- `working/`
- `archive/`
- `candidate/`

具体文件包括：

- `identity/SOUL.md`
- `identity/USER_RULES.md`
- `identity/USER_PROFILE.md`
- `working/CURRENT.md`
- `archive/history.jsonl`
- `archive/reflections.jsonl`
- `candidate/observations.jsonl`

### 2.3 `working/CURRENT.md` 的真实地位

当前实现里，`working/CURRENT.md` 依然会被 `ContextBuilder` 注入到 system prompt 中，也仍被 `MemoryStore` 读写。

但从整体设计和结构化状态引入的方向看，它不再是唯一主状态来源，而是：

- 兼容旧工作流的镜像层
- 给人阅读和 handoff 的文本视图
- 与结构化 working set 并存的过渡层

因此文档和代码改动中，不能再把它描述成“运行时唯一真相源”。

## 3. Dream 与 Promoter

### 3.1 Dream

`Dream` 位于 `nanobot/agent/memory.py` 中，负责从历史材料中生成：

- 归档型反思
- 候选观察

它的作用是“提出结论和候选”，不是直接改写高权限身份层。

### 3.2 Promoter

`Promoter` 当前是规则型晋升器，不是复杂学习系统。

它当前的晋升逻辑比较直接：

- 明确用户陈述可直接晋升
- 重复证据达到阈值可晋升
- 置信度低或被矛盾证据标记则拒绝

晋升目标只覆盖：

- `identity.USER_RULES`
- `identity.USER_PROFILE`
- `identity.SOUL`

这反映出当前长期记忆治理已经具备边界，但规则仍偏简单。

## 4. 设计意图

当前能力域的核心设计意图是：

- 让 prompt 更稳定地依赖结构化工作集。
- 让长期记忆通过候选层和晋升规则治理。
- 避免一次 Dream 总结直接污染高权限身份记忆。

## 5. 当前限制与未完成点

- `working/CURRENT.md` 仍在 system prompt 中占有位置，说明迁移尚未完全结束。
- Promoter 目前是硬规则，不具备复杂冲突消解或人工审核工作流。
- 文本记忆文件和结构化 state 仍是并行体系，而不是完全统一存储。

## 6. 相关测试

- `tests/agent/test_prompt_assembly.py`
- `tests/agent/test_context_prompt_cache.py`
- `tests/agent/test_memory_store.py`
- `tests/agent/test_consolidator.py`
- `tests/agent/test_dream.py`
- `tests/agent/test_promoter.py`
- `tests/agent/test_structured_context_upgrade_e2e.py`
