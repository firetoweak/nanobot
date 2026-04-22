# ADR-0002：分层记忆与晋升闸门

## 状态

Accepted

## 背景

如果自动总结、Dream 或一次错误推断可以直接写入高权限长期记忆，那么 agent 的长期行为会被低质量结论污染。

## 决策

将记忆分为四层：

- `identity/`
- `working/`
- `archive/`
- `candidate/`

并要求候选观察先进入 `candidate/observations.jsonl`，再由 `Promoter` 按规则决定是否晋升到 `identity/*`。

## 结果

正面结果：

- 长期身份层不再被任意总结结果直接改写。
- `Dream` 与长期记忆之间建立了显式治理边界。
- 为后续引入更严格审核策略保留了扩展点。

代价与限制：

- 当前 `Promoter` 规则仍然较简单，不等于完备治理系统。
- `working/CURRENT.md` 仍存在并参与上下文，说明文本记忆路径尚未完全退场。
- 文本分层与结构化状态并行，心智模型仍有一定复杂度。
