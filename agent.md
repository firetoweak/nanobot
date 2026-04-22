# agent.md

本文件定义 AI 在本仓库内进行 coding、重构和文档维护时必须遵守的项目级约束。

## 1. 总目标

在 `local_dev` 分支中，AI 的任务不是只改代码，而是同时维护“代码实现 + 架构文档”之间的一致性。

每次改动都应尽量保证：

- 代码反映当前设计意图。
- `Architecture_Documentation/` 反映当前真实实现。
- 用户能从文档变化中判断本次代码变更影响了哪块能力域。

## 2. 先读什么

开始改代码前，优先阅读：

1. `Architecture_Documentation/README.md`
2. `Architecture_Documentation/overview/project-overview.md`
3. `Architecture_Documentation/overview/design-principles.md`
4. `Architecture_Documentation/mapping/code-to-capability.md`
5. 与本次任务直接相关的 capability 文档

如发现文档与代码不一致，以代码真实实现为准，并在本次改动中修正文档。

关于 `docs/`：

- `docs/` 目录视为历史设计记录和持续更新的草稿区。
- 不要把 `docs/` 当作当前设计或当前实现的主依据。
- 只有在需要追溯历史思路时，才把它当背景材料参考，并且必须回到代码与 `Architecture_Documentation/` 交叉核对。

## 3. 文档维护规则

默认采用差量更新，而不是全量重写。

### 3.1 默认必须更新

当改动影响以下内容时，必须同步更新对应 capability 文档与 mapping：

- 外部行为
- 关键内部职责
- 重要限制条件
- 代码路径与能力域映射

### 3.2 仅在必要时更新

仅在以下情况更新 `overview/`：

- 职责边界变化
- 核心运行流变化
- 长期设计原则变化

仅在以下情况更新 `adr/`：

- 出现新的关键设计取舍
- 原有关键取舍被推翻或替代

仅在以下情况写入 `changes/`：

- 方案仍在实验期
- 真实实现尚未稳定
- 不适合直接提升为正式架构边界

## 4. 文档写作约束

必须写清楚：

- 当前真实实现
- 明确的设计意图
- 兼容层、过渡层、遗留路径
- 已实现边界和未实现边界

禁止写法：

- 把尚未实现或仅部分实现的能力写成已完整实现
- 用理想化描述掩盖当前限制
- 只复述 `docs/` 而不核对代码
- 直接依据 `docs/` 推断当前分支架构，而不检查正式架构文档与真实代码

## 5. 当前架构认知底线

在当前分支中，以下判断默认成立，除非代码已被正式改掉并同步更新文档：

- `AgentLoop` 是统一执行内核。
- `SessionManager` 和 `StateStore` 并存，分别承担历史持久化和结构化状态职责。
- `working/CURRENT.md` 仍存在，但不应被视为唯一主状态源。
- 长期记忆必须区分 `identity/`、`working/`、`archive/`、`candidate/`。
- 候选观察到身份层的晋升需要经过 `Promoter` 或等价治理机制。
- 多入口共享同一 agent runtime，不应各自复制独立业务内核。
- `local_dev` 不引入内置 WebUI；对话接入以各类 channel 为主，例如 QQ bot 插件。

## 6. 修改代码时的执行要求

- 改代码前，先通过 mapping 找到受影响的 capability 文档。
- 改代码后，同步更新受影响文档。
- 如果发现本次变更跨越多个能力域，要同时更新多个 capability 文档。
- 如果只是实验性改动，先记录到 `Architecture_Documentation/changes/`，不要过早写成正式定论。
- 除非用户明确改变分支方向，否则不要新增 `webui/`、浏览器对话页面或等价前端模块；优先通过 channel 方式扩展接入。

## 7. 提交说明建议

当一次改动同时更新代码和架构文档时，提交说明建议能体现：

- 改了哪个能力域
- 改了哪些关键实现
- 同步更新了哪些架构文档

这样用户可以从提交记录和文档更新一起判断系统演进路径。
