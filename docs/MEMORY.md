# nanobot 的记忆系统

`nanobot` 的记忆设计建立在一个很重要的前提上：

不是所有“记住的内容”，都应该拥有同样的权限。

有些信息会长期影响 agent 的行为和风格；有些只属于当前任务；有些适合归档检索，但不应该每次都被塞进 prompt；还有一些观察结论，暂时只能作为候选事实，不能直接升级成长期身份信息。

因此，`nanobot` 采用的是一种**分层、分权限的记忆系统**。

## 一句话理解这套设计

如果用最直白的话来讲：

- `identity` 是“真正长期生效的记忆”
- `working` 是“当前工作台”
- `archive` 是“可检索档案”
- `candidate` 是“待审核候选区”

这样设计的目标，不只是为了省上下文长度，更是为了防止错误总结被过早提升为长期事实。

## 记忆分层结构

`nanobot` 会把不同性质的记忆拆到不同层里：

- `session.messages`：当前会话中的实时短期消息
- `identity/`：允许直接影响 prompt 的长期身份与用户信息
- `working/CURRENT.md`：当前任务的工作上下文、交接状态和短期待办
- `archive/`：面向机器检索和回顾的历史摘要、反思记录
- `candidate/observations.jsonl`：尚未被确认的观察、候选结论和待晋升内容
- `GitStore`：对关键记忆文件做版本记录，便于审计和恢复

这套设计的核心收益是：

- 当前运行时保持轻量
- 长期信息可以积累
- 但不会让每次摘要都直接污染系统身份

## 目录结构

```text
workspace/
├── identity/
│   ├── SOUL.md
│   ├── USER_RULES.md
│   └── USER_PROFILE.md
├── working/
│   └── CURRENT.md
├── archive/
│   ├── history.jsonl
│   ├── reflections.jsonl
│   ├── .cursor
│   └── .dream_cursor
├── candidate/
│   └── observations.jsonl
```

各文件的职责可以这样理解：

| 路径 | 用途 |
|------|------|
| `identity/SOUL.md` | agent 的稳定原则、风格、边界 |
| `identity/USER_RULES.md` | 用户长期有效的明确规则和工作约束 |
| `identity/USER_PROFILE.md` | 用户背景、稳定偏好、长期事实 |
| `working/CURRENT.md` | 当前活跃任务的工作记忆和交接信息 |
| `archive/history.jsonl` | 会话摘要历史，追加写入 |
| `archive/reflections.jsonl` | Dream / Heartbeat 生成的反思记录 |
| `candidate/observations.jsonl` | 候选观察结论，待复核、待晋升 |

## 哪些记忆会直接进入 Prompt

默认情况下，只有下面这些内容会被注入核心系统 prompt：

- `identity/SOUL.md`
- `identity/USER_RULES.md`
- `identity/USER_PROFILE.md`
- `working/CURRENT.md`

而下面这些内容**默认不会**直接注入 prompt：

- `archive/history.jsonl`
- `archive/reflections.jsonl`
- `candidate/observations.jsonl`

这条边界非常关键。

它意味着：

- 档案可以很多，但不需要每次都带进上下文
- 候选观察可以被记录，但不会立刻影响 agent 的长期行为
- 真正会改变 agent 行为的，只能是被提升到 `identity/` 的内容

## 记忆是怎样流动的

与其把所有长期信息都扔进一个“大记忆桶”，`nanobot` 采用了一个分阶段流转模型。你可以把它理解成四个阶段。

## 阶段 1：Consolidator

当对话越来越长、开始挤压上下文窗口时，`Consolidator` 会把“足够老、可以安全摘要”的那部分会话压缩后写入 `archive/history.jsonl`。

这个文件有几个特征：

- 只追加，不回写历史
- 用 cursor 跟踪写入进度
- 优先服务于机器消费，其次才是人工查看

每一行都是一条 JSON 记录，例如：

```json
{"cursor": 42, "timestamp": "2026-04-03 00:02", "content": "- User prefers dark mode\n- Decided to use PostgreSQL"}
```

你可以把它理解成“对旧对话的结构化摘要流水账”。

## 阶段 2：Dream

`Dream` 是一个更慢、更偏反思性质的层。

它通常按计划定时运行，也可以手动触发。它会读取：

- `archive/history.jsonl` 里的新增摘要
- 当前已有的分层记忆状态

但要注意，`Dream` 默认**不会直接改写 `identity/`**。它的常规写入目标是：

- `working/CURRENT.md`
- `archive/reflections.jsonl`
- `candidate/observations.jsonl`

这样做的目的是把“反思”和“真正有权限的长期身份信息”分开。

换句话说，Dream 可以提出结论，但不能直接自封为真理。

## 阶段 3：Promoter

`Promoter` 是候选记忆和身份记忆之间的权限边界。

它会审查 `candidate/observations.jsonl` 中的条目，并决定某条观察应该：

- 继续留在候选区
- 被拒绝
- 或晋升到以下长期记忆中：
  - `identity/SOUL.md`
  - `identity/USER_RULES.md`
  - `identity/USER_PROFILE.md`

初版实现更偏向保守，通常会优先采纳以下这类证据：

- 用户明确直接表达过的事实
- 跨多个会话重复出现、证据稳定的结论

这能避免某次 Dream 的抽象过度，直接污染长期身份层。

## 阶段 4：Heartbeat 与后台任务

Heartbeat 作业和其他后台任务也可以写记忆，但权限会被严格限制。

通常它们只能写入这类目标：

- `working/CURRENT.md`
- `archive/reflections.jsonl`

它们不会被授予整个记忆树的广泛写权限。

## 为什么一定要做分层记忆

旧式“单桶记忆”的优点是简单，但它有一个很危险的问题：

从历史里提炼出来的一次摘要，太容易被误当成长期事实。

而分层记忆通过权限拆分，把下面几类东西明确分开了：

- 可以长期塑造未来行为的身份信息
- 应该随着任务结束而过期的工作状态
- 适合检索但不该总是注入 prompt 的归档内容
- 需要观察、审核、晋升流程的候选结论

所以它要解决的不只是“记忆错误”问题，更是“权限升级”问题。

一句话概括：很多记忆污染，本质上不是摘要错了，而是摘要被给了不该有的权限。

## 如何搜索过去发生过的事

如果你想查历史，不应该优先去看 prompt 层，而应该查 JSONL 档案：

- `archive/history.jsonl`
- `archive/reflections.jsonl`
- `candidate/observations.jsonl`

典型搜索方式：

```bash
# 搜索摘要历史
rg -i "keyword" archive/history.jsonl

# 搜索候选观察
rg -i "prefers concise" candidate/observations.jsonl

# 统计 archive 下 JSONL 里的命中项
rg -i --glob "*.jsonl" "keyword" archive
```

这说明 `archive/` 和 `candidate/` 的主要职责是“检索、审阅、晋升”，而不是“默认注入提示词”。

## 用户可直接使用的记忆命令

记忆系统不是黑盒，用户可以直接查看和干预。

| 命令 | 作用 |
|------|------|
| `/dream` | 立即执行一次 Dream |
| `/dream-log` | 查看最近一次 Dream 对记忆的修改 |
| `/dream-log <sha>` | 查看指定版本的 Dream 变更 |
| `/dream-restore` | 列出最近可恢复的 Dream 版本 |
| `/dream-restore <sha>` | 把记忆恢复到某次修改之前 |

之所以提供这些命令，是因为自动记忆虽然强大，但用户仍应保有：

- 查看权
- 理解权
- 恢复权

## 关键记忆文件是可版本化的

`GitStore` 会跟踪以下关键 prompt 记忆文件：

- `identity/SOUL.md`
- `identity/USER_RULES.md`
- `identity/USER_PROFILE.md`
- `working/CURRENT.md`

这样做的意义是：

- 你可以知道哪里被改了
- 可以比较不同版本之间的差异
- 也可以把系统恢复到更早的状态

对于真正会影响 agent 行为的记忆，这是非常重要的可审计能力。

## Dream 的配置方式

`Dream` 的配置位于 `agents.defaults.dream`：

```json
{
  "agents": {
    "defaults": {
      "dream": {
        "intervalH": 2,
        "modelOverride": null,
        "maxBatchSize": 20,
        "maxIterations": 10
      }
    }
  }
}
```

各字段含义如下：

| 字段 | 含义 |
|------|------|
| `intervalH` | Dream 运行间隔，单位小时 |
| `modelOverride` | 是否为 Dream 单独指定模型 |
| `maxBatchSize` | 每次 Dream 最多处理多少条历史摘要 |
| `maxIterations` | Dream 编辑阶段允许使用的最大步骤数 |

更实用一点的理解方式：

- `modelOverride: null` 表示 Dream 默认沿用主 agent 的模型
- `maxBatchSize` 决定 Dream 每轮吃掉多少条新的 `archive/history.jsonl` 记录
- `maxIterations` 控制 Dream 在更新工作区、档案区、候选区时最多能进行多少轮读写操作
- `intervalH` 是配置 Dream 周期运行的主要方式，内部按 `every` 调度，而不是传统 cron 表达式

## 在日常使用里，它带来了什么

分层记忆真正带来的不是“多几个目录”，而是更可控的长期连续性：

- 对话不必一直背着无限长历史运行
- 长期事实可以逐渐沉淀
- 但不会因为一条可疑结论就立刻改变 agent 身份
- 用户可以检查、回滚关键记忆

## 最后总结

这套记忆模型想达到的效果，不是把一切都存起来，而是把“该如何记住”这件事做成有权限边界的系统。

你可以把它理解成一句话：

**记忆不是一个垃圾堆，而是一套带审批流程的连续性系统。**
