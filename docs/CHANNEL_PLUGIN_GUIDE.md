# Channel 插件开发指南

这份文档面向想给 `nanobot` 接入新聊天渠道的开发者。你可以把一个渠道理解成“消息进来 + 回复发出去”的适配层，例如企业内部 IM、网页聊天窗口、客服系统、Webhook 网关等。

从零开发一个自定义 Channel，核心只需要三步：

1. 继承 `BaseChannel`
2. 打包为 Python 包
3. 通过 entry point 安装到 `nanobot`

> 建议直接基于 `nanobot` 源码开发，例如在源码目录执行 `pip install -e .`。这样你总能拿到最新的 `BaseChannel` 能力和接口定义，不容易和 PyPI 已发布版本脱节。

## 整体工作原理

`nanobot` 会通过 Python 的 [entry points](https://packaging.python.org/en/latest/specifications/entry-points/) 自动发现可用渠道。

当你执行 `nanobot gateway` 时，程序会扫描两类 Channel：

1. 内置渠道：`nanobot/channels/`
2. 外部插件：注册到 `nanobot.channels` entry point 分组的第三方包

如果配置文件里存在对应渠道配置，且该配置包含 `"enabled": true`，那么 `nanobot` 就会实例化这个 Channel 并启动它。

## 先建立正确心智模型

写一个 Channel 时，建议先记住下面这几点：

- `start()` 负责“接收外部消息”，通常要一直阻塞运行。
- `send()` 负责“把 agent 的回复发回外部平台”。
- 收到用户消息后，必须调用 `_handle_message(...)`，这样消息才会进入 `nanobot` 的总线。
- 渠道鉴权、`allowFrom` 检查、流式输出协商等共性能力，大多已经在 `BaseChannel` 里做好了。

一句话概括：你的插件主要做平台协议适配，不需要重新实现 agent 主循环。

## 快速上手示例

下面用一个最小的 `webhook` 渠道作为例子：外部系统通过 HTTP `POST` 发消息进来，`nanobot` 处理后再由 `send()` 把响应发出去。

### 项目结构

```text
nanobot-channel-webhook/
├── nanobot_channel_webhook/
│   ├── __init__.py
│   └── channel.py
└── pyproject.toml
```

### 第一步：实现一个最小 Channel

`nanobot_channel_webhook/__init__.py`

```python
from nanobot_channel_webhook.channel import WebhookChannel

__all__ = ["WebhookChannel"]
```

`nanobot_channel_webhook/channel.py`

```python
import asyncio
from typing import Any

from aiohttp import web
from loguru import logger
from pydantic import Field

from nanobot.channels.base import BaseChannel
from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import Base


class WebhookConfig(Base):
    """Webhook 渠道配置。"""

    enabled: bool = False
    port: int = 9000
    allow_from: list[str] = Field(default_factory=list)


class WebhookChannel(BaseChannel):
    name = "webhook"
    display_name = "Webhook"

    def __init__(self, config: Any, bus: MessageBus):
        if isinstance(config, dict):
            config = WebhookConfig(**config)
        super().__init__(config, bus)

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        return WebhookConfig().model_dump(by_alias=True)

    async def start(self) -> None:
        """启动 HTTP 服务并持续监听消息。

        关键点：start() 必须保持运行，直到 stop() 被调用。
        如果它提前返回，gateway 会认为该 channel 已经退出。
        """

        self._running = True

        app = web.Application()
        app.router.add_post("/message", self._on_request)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", self.config.port)
        await site.start()

        logger.info("Webhook listening on :{}", self.config.port)

        while self._running:
            await asyncio.sleep(1)

        await runner.cleanup()

    async def stop(self) -> None:
        self._running = False

    async def send(self, msg: OutboundMessage) -> None:
        """把 agent 结果发回外部系统。"""

        logger.info("[webhook] -> {}: {}", msg.chat_id, msg.content[:80])
        # 实际项目里可以在这里：
        # - 调用对方回调接口
        # - 通过平台 SDK 发消息
        # - 推送到 MQ / WebSocket / SSE 等

    async def _on_request(self, request: web.Request) -> web.Response:
        body = await request.json()

        sender = body.get("sender", "unknown")
        chat_id = body.get("chat_id", sender)
        text = body.get("text", "")
        media = body.get("media", [])

        # 这是最关键的一步：
        # 把外部消息交给 BaseChannel 做权限检查并投递到消息总线
        await self._handle_message(
            sender_id=sender,
            chat_id=chat_id,
            content=text,
            media=media,
        )

        return web.json_response({"ok": True})
```

## 第二步：注册 entry point

`pyproject.toml`

```toml
[project]
name = "nanobot-channel-webhook"
version = "0.1.0"
dependencies = ["nanobot", "aiohttp"]

[project.entry-points."nanobot.channels"]
webhook = "nanobot_channel_webhook:WebhookChannel"

[build-system]
requires = ["setuptools"]
build-backend = "setuptools.backends._legacy:_Backend"
```

这里最重要的是：

- entry point 的 key：`webhook`
- 对应类路径：`nanobot_channel_webhook:WebhookChannel`

其中 `webhook` 也会成为配置文件中的渠道名，也就是 `channels.websocket`、`channels.telegram` 这种层级里的那个名字。

## 第三步：安装并生成配置

```bash
pip install -e .
nanobot plugins list
nanobot onboard
```

这几个命令的作用分别是：

- `pip install -e .`：以开发模式安装插件
- `nanobot plugins list`：确认插件已被发现
- `nanobot onboard`：把插件默认配置写进 `config.json`

然后编辑 `~/.nanobot/config.json`：

```json
{
  "channels": {
    "webhook": {
      "enabled": true,
      "port": 9000,
      "allowFrom": ["*"]
    }
  }
}
```

## 第四步：运行和联调

启动 gateway：

```bash
nanobot gateway
```

另开一个终端模拟外部平台发消息：

```bash
curl -X POST http://localhost:9000/message \
  -H "Content-Type: application/json" \
  -d '{"sender": "user1", "chat_id": "user1", "text": "Hello!"}'
```

如果一切正常：

- 这条消息会被你的 Channel 收到
- `_handle_message()` 会把它交给 agent
- agent 处理后的回复会进入你的 `send()` 方法

## BaseChannel 接口速览

### 必须实现的方法

| 方法 | 作用 |
|------|------|
| `async start()` | 建立连接、监听消息，并持续运行。不能提前返回。 |
| `async stop()` | 停止监听、释放资源，通常设置 `self._running = False`。 |
| `async send(msg: OutboundMessage)` | 把 agent 产出的最终消息发回渠道平台。 |

### 可选实现：交互式登录

如果你的渠道接入需要交互认证，例如扫码登录、设备授权、网页登录确认，可以重写 `login(force=False)`：

```python
async def login(self, force: bool = False) -> bool:
    """
    执行渠道自己的登录流程。

    参数：
        force: 为 True 时忽略已有凭证并重新认证。

    返回：
        已登录或登录成功时返回 True。
    """
```

典型流程通常是：

1. 如果 `force=True`，清除旧凭证
2. 检查本地是否已登录
3. 未登录则展示二维码或发起认证流程
4. 登录成功后保存 token / cookie / session

用户可通过下面命令触发：

```bash
nanobot channels login <channel_name>
nanobot channels login <channel_name> --force
```

如果你的渠道不需要交互式登录，例如纯 token 模式，那么保持默认实现即可，默认 `login()` 会直接返回 `True`。

### BaseChannel 已经提供的能力

| 方法 / 属性 | 说明 |
|-------------|------|
| `_handle_message(sender_id, chat_id, content, media?, metadata?, session_key?)` | 收到用户消息时必须调用。它会做权限检查并投递到消息总线。 |
| `is_allowed(sender_id)` | 根据 `config.allow_from` 检查是否允许该发送者。 |
| `default_config()` | 返回默认配置，供 `nanobot onboard` 自动生成配置时使用。 |
| `transcribe_audio(file_path)` | 如果配置了相关能力，可对音频做转写。 |
| `supports_streaming` | 当配置开启 `streaming` 且子类实现了 `send_delta()` 时为 `True`。 |
| `is_running` | 返回当前运行状态。 |
| `login(force=False)` | 默认交互式登录入口，按需重写。 |

### 流式输出是可选项

如果你希望把 agent 的回复按 token 或小段文本实时推送给前端，而不是等完整结果生成后再一次性发送，就可以实现 `send_delta()`。

| 方法 | 作用 |
|------|------|
| `async send_delta(chat_id, delta, metadata?)` | 接收流式文本片段。默认是空实现。 |

## `OutboundMessage` 结构

`send()` 收到的是一个 `OutboundMessage`，你通常只需要关心以下字段：

```python
@dataclass
class OutboundMessage:
    channel: str
    chat_id: str
    content: str
    media: list[str]
    metadata: dict
```

字段含义：

- `channel`：当前渠道名
- `chat_id`：目标会话 ID，通常就是你最初传给 `_handle_message()` 的那个 `chat_id`
- `content`：最终回复文本，格式通常是 Markdown
- `media`：需要附带发送的本地文件路径
- `metadata`：附加信息，例如回复关联、流式标记等

## 流式输出支持

### 什么时候会启用流式输出

同时满足下面两个条件时，`nanobot` 才会进入流式发送模式：

1. 配置中设置 `"streaming": true`
2. 你的 Channel 子类实现了 `send_delta()`

只要缺任意一个条件，系统就会退回普通的 `send()` 一次性发送模式。

### `send_delta()` 的处理方式

通常你要处理两类调用：

```python
async def send_delta(
    self,
    chat_id: str,
    delta: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    meta = metadata or {}

    if meta.get("_stream_end"):
        # 一次流式输出结束，做收尾动作
        return

    # 普通增量文本，持续追加并刷新到前端
```

相关标记：

| 标记 | 含义 |
|------|------|
| `_stream_delta: True` | 当前调用携带一段增量文本 |
| `_stream_end: True` | 当前流式片段已经结束，`delta` 通常为空 |

### 示例：支持流式输出的 Webhook

```python
class WebhookChannel(BaseChannel):
    name = "webhook"
    display_name = "Webhook"

    def __init__(self, config: Any, bus: MessageBus):
        if isinstance(config, dict):
            config = WebhookConfig(**config)
        super().__init__(config, bus)
        self._buffers: dict[str, str] = {}

    async def send_delta(
        self,
        chat_id: str,
        delta: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        meta = metadata or {}

        if meta.get("_stream_end"):
            text = self._buffers.pop(chat_id, "")
            await self._deliver(chat_id, text, final=True)
            return

        self._buffers.setdefault(chat_id, "")
        self._buffers[chat_id] += delta
        await self._deliver(chat_id, self._buffers[chat_id], final=False)

    async def send(self, msg: OutboundMessage) -> None:
        await self._deliver(msg.chat_id, msg.content, final=True)
```

开启方式：

```json
{
  "channels": {
    "webhook": {
      "enabled": true,
      "streaming": true,
      "allowFrom": ["*"]
    }
  }
}
```

## 配置模型为什么必须用 Pydantic

这是插件开发里最容易踩坑的一点。

`BaseChannel.is_allowed()` 会通过下面这种方式读取白名单：

```python
getattr(self.config, "allow_from", [])
```

这意味着：

- 如果 `self.config` 是 Pydantic 模型，`allow_from` 是正常属性，读取没问题
- 如果 `self.config` 只是一个普通 `dict`，它没有 `allow_from` 属性，`getattr(...)` 会直接返回默认值 `[]`

结果就是：所有消息都被静默拒绝，看起来像“渠道没反应”，但其实是权限检查失败。

所以，插件 Channel 必须像内置 Channel 一样，使用继承自 `nanobot.config.schema.Base` 的 Pydantic 模型。

### 推荐写法

#### 1. 定义配置模型

```python
from pydantic import Field
from nanobot.config.schema import Base

class WebhookConfig(Base):
    enabled: bool = False
    port: int = 9000
    allow_from: list[str] = Field(default_factory=list)
```

`Base` 已经配置好了 camelCase / snake_case 兼容能力，所以配置文件里写 `"allowFrom"` 或 `"allow_from"` 都可以。

#### 2. 在 `__init__` 中把 `dict` 转成模型

```python
class WebhookChannel(BaseChannel):
    def __init__(self, config: Any, bus: MessageBus):
        if isinstance(config, dict):
            config = WebhookConfig(**config)
        super().__init__(config, bus)
```

#### 3. 后续统一按属性读取配置

```python
async def start(self) -> None:
    port = self.config.port
```

不要在 Channel 实现里继续把配置当成字典 `.get(...)` 来读，这样会让代码风格混乱，也更难发现问题。

### `default_config()` 的推荐实现

```python
@classmethod
def default_config(cls) -> dict[str, Any]:
    return WebhookConfig().model_dump(by_alias=True)
```

这样做的好处是：

- 默认值只维护一份，以配置模型为准
- 生成的配置键名自动使用 camelCase
- `nanobot onboard` 可以直接把这份结果写进 `config.json`

如果你不重写它，基类只会返回最简的 `{"enabled": false}`。

## 命名约定

建议统一使用下面的命名方式：

| 对象 | 格式 | 示例 |
|------|------|------|
| PyPI 包名 | `nanobot-channel-{name}` | `nanobot-channel-webhook` |
| entry point key | `{name}` | `webhook` |
| 配置项路径 | `channels.{name}` | `channels.webhook` |
| Python 包名 | `nanobot_channel_{name}` | `nanobot_channel_webhook` |

## 本地开发建议

典型本地联调流程如下：

```bash
git clone https://github.com/you/nanobot-channel-webhook
cd nanobot-channel-webhook
pip install -e .
nanobot plugins list
nanobot gateway
```

## 验证插件是否生效

执行：

```bash
nanobot plugins list
```

如果加载成功，你会看到类似结果：

```text
Name       Source   Enabled
telegram   builtin  yes
discord    builtin  no
webhook    plugin   yes
```

其中 `Source=plugin` 说明它是外部插件而不是内置渠道。

## 最后总结

如果你只记住三件事，请记这三条：

1. `start()` 必须常驻运行，收到消息后调用 `_handle_message()`
2. 配置必须用 Pydantic 模型，不要直接传裸 `dict`
3. 想支持流式输出，就同时开启 `streaming` 并实现 `send_delta()`

做到这三点，大多数自定义 Channel 都能顺利跑起来。
