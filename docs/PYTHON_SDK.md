# Python SDK

> 说明：这套接口目前仍偏实验性质，文档中的设计目标是在 `v0.1.5` 之后作为正式能力稳定提供。

如果你不想通过 CLI 或聊天渠道使用 `nanobot`，而是希望直接在 Python 代码里调用 agent，这份 SDK 就是最直接的入口。

常见使用场景包括：

- 在后端服务里嵌入 `nanobot`
- 写批处理脚本或自动化任务
- 为 Web API、桌面应用、内部工具封装智能能力
- 给一次运行加自定义 hook，观察 agent 执行过程

## 30 秒上手

```python
import asyncio
from nanobot import Nanobot


async def main():
    bot = Nanobot.from_config()
    result = await bot.run("东京现在几点？")
    print(result.content)


asyncio.run(main())
```

这段代码做了三件事：

1. 从配置文件创建一个 `Nanobot` 实例
2. 执行一次 agent 运行
3. 读取最终返回文本 `result.content`

## 最核心的两个 API

大多数场景里，你只需要理解两个入口：

- `Nanobot.from_config(...)`
- `await bot.run(...)`

## `Nanobot.from_config(config_path?, *, workspace?)`

这个方法用于从配置文件创建 `Nanobot` 实例。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `config_path` | `str \| Path \| None` | `None` | `config.json` 的路径。默认会读取 `~/.nanobot/config.json`。 |
| `workspace` | `str \| Path \| None` | `None` | 覆盖配置中的工作目录。 |

补充说明：

- 如果你显式传入了 `config_path`，但文件不存在，会抛出 `FileNotFoundError`
- 如果你希望把 agent 固定运行在某个项目目录，可以直接传 `workspace=...`

示例：

```python
from nanobot import Nanobot

bot = Nanobot.from_config(
    config_path="D:/configs/nanobot.json",
    workspace="D:/work/my-project",
)
```

## `await bot.run(message, *, session_key?, hooks?)`

这个方法会执行一次完整的 agent 处理流程，并返回 `RunResult`。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `message` | `str` | 必填 | 本次发给 agent 的用户输入。 |
| `session_key` | `str` | `"sdk:default"` | 会话隔离键。不同 key 会拥有独立上下文。 |
| `hooks` | `list[AgentHook] \| None` | `None` | 仅对本次运行生效的 hook 列表。 |

### 为什么要关心 `session_key`

如果你在服务端同时给多个用户提供能力，而所有请求都用同一个 `session_key`，会话上下文就会串掉。

推荐做法是：每个用户、每个会话、每个工单，至少使用独立的 `session_key`。

例如：

```python
await bot.run("hi", session_key="user-alice")
await bot.run("hi", session_key="user-bob")
```

这样 Alice 和 Bob 的历史上下文是完全隔离的。

## 返回值：`RunResult`

`bot.run()` 返回的是一个 `RunResult` 对象，常见字段如下：

| 字段 | 类型 | 说明 |
|------|------|------|
| `content` | `str` | agent 最终输出的文本结果。 |
| `tools_used` | `list[str]` | 本次运行里实际调用过的工具名。 |
| `messages` | `list[dict]` | 原始消息历史，主要用于调试或排查问题。 |

通常最常用的是 `content`，如果你在做审计、链路追踪或调试，则会更多查看 `tools_used` 和 `messages`。

## Hooks：在不改内核的前提下扩展行为

`hooks` 的作用是：让你在不修改 agent 内部实现的情况下，插入自己的观测、审计、统计或后处理逻辑。

典型用途包括：

- 记录用了哪些工具
- 统计每轮耗时
- 监控流式输出过程
- 在最终结果返回前做脱敏或格式处理

你只需要继承 `AgentHook`，然后按需重写对应方法。

### 可重写的 Hook 方法

| 方法 | 调用时机 |
|------|----------|
| `before_iteration(ctx)` | 每次 LLM 调用前 |
| `on_stream(ctx, delta)` | 流式输出每到一个增量 token / 文本片段时 |
| `on_stream_end(ctx)` | 流式输出结束时 |
| `before_execute_tools(ctx)` | 执行工具前，可查看 `ctx.tool_calls` |
| `after_iteration(ctx, response)` | 每次 LLM 响应完成后 |
| `finalize_content(ctx, content)` | 最终结果返回前，可做文本变换 |

## 示例：记录本次调用了哪些工具

```python
from nanobot.agent import AgentHook, AgentHookContext


class AuditHook(AgentHook):
    def __init__(self):
        self.calls = []

    async def before_execute_tools(self, ctx: AgentHookContext) -> None:
        for tc in ctx.tool_calls:
            self.calls.append(tc.name)
            print(f"[audit] {tc.name}({tc.arguments})")


hook = AuditHook()
result = await bot.run("List files in /tmp", hooks=[hook])
print(f"Tools used: {hook.calls}")
```

这个例子适合做：

- 调试 agent 为什么会调用某些工具
- 做安全审计
- 为日志系统打点

## 多个 Hook 可以同时组合

你可以一次传多个 hook：

```python
result = await bot.run("hi", hooks=[AuditHook(), MetricsHook()])
```

它们会按顺序执行。底层使用 `CompositeHook` 做分发，因此某个 hook 出错时，通常不会直接阻断其他 hook 的执行。

这让你可以把能力拆开：

- 一个 hook 做日志
- 一个 hook 做监控
- 一个 hook 做输出修饰

## `finalize_content` 是“管道”，不是广播

这点很容易忽略。

大多数异步 hook 方法更像“广播”模式：系统把事件通知给每个 hook。  
但 `finalize_content` 不一样，它是“流水线”模式：前一个 hook 的输出，会成为下一个 hook 的输入。

例如：

```python
class Censor(AgentHook):
    def finalize_content(self, ctx, content):
        return content.replace("secret", "***") if content else content
```

如果你连续挂多个 `finalize_content` hook，它们会按顺序逐步改写文本。

## 完整示例：统计每轮耗时

```python
import asyncio
from nanobot import Nanobot
from nanobot.agent import AgentHook, AgentHookContext


class TimingHook(AgentHook):
    async def before_iteration(self, ctx: AgentHookContext) -> None:
        import time

        ctx.metadata["_t0"] = time.time()

    async def after_iteration(self, ctx, response) -> None:
        import time

        elapsed = time.time() - ctx.metadata.get("_t0", 0)
        print(f"[timing] iteration took {elapsed:.2f}s")


async def main():
    bot = Nanobot.from_config(workspace="/my/project")
    result = await bot.run(
        "Explain the main function",
        hooks=[TimingHook()],
    )
    print(result.content)


asyncio.run(main())
```

## 推荐使用方式

如果你是第一次接 Python SDK，建议按下面思路使用：

1. 先只用 `Nanobot.from_config()` + `bot.run()` 跑通
2. 再用 `session_key` 做用户隔离
3. 最后按需加 `hooks` 做审计、监控、脱敏或埋点

## 适合嵌入到什么地方

Python SDK 很适合下面这类集成方式：

- FastAPI / Flask / Django 后端接口
- Celery / RQ 异步任务
- 定时脚本、批处理脚本
- Jupyter Notebook 或内部研发工具

你可以把它理解成：CLI 是“人手动调用 nanobot”，而 SDK 是“程序主动调用 nanobot”。

## 最后总结

如果只看最关键的几点：

- `Nanobot.from_config()` 负责创建实例
- `bot.run()` 负责执行一次请求
- `session_key` 负责会话隔离
- `hooks` 负责扩展运行过程

对大部分 Python 集成场景来说，这四个概念已经够用了。
