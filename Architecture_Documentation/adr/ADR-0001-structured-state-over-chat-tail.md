# ADR-0001：结构化状态优先于聊天尾部恢复

## 状态

Accepted

## 背景

项目在早期更容易依赖“最近聊天记录 + 文本摘要”恢复上下文，但随着能力变复杂，这种方式会带来几个问题：

- 很难表达当前回合进行到哪一步。
- 很难准确判断哪些结论已稳定、哪些仍在中间态。
- 工具结果、压缩结果、归档结果和最终完成边界容易混在一起。

## 决策

在保留 `sessions/*.jsonl` 的同时，引入 `.nanobot/state` 结构化状态存储，并把以下对象显式化：

- turn state
- message / response object
- working set snapshot
- capsule
- artifact
- commit manifest

## 结果

正面结果：

- 恢复和回合推进可以依赖明确对象，而不是只依赖历史尾部。
- 工作集、可复用结论和完成边界更容易表达。
- 为后续 compact、dream、resume、repair 提供了更稳定的基础。

代价与限制：

- 现在同时维护 JSONL session 与 structured state，两套持久化需要协调。
- 实现复杂度明显上升。
- 部分旧路径仍依赖文本镜像，因此迁移尚未彻底完成。
