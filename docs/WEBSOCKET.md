# WebSocket 服务端渠道

`nanobot` 可以直接作为一个 WebSocket 服务端运行，让网页前端、桌面客户端、命令行工具或自定义脚本通过长连接实时和 agent 交互。

如果你希望自己写一个聊天前端，而不是依赖现成 IM 平台，这个渠道通常是最直接的接入方式。

## 你能得到什么

WebSocket 渠道默认支持下面这些能力：

- 双向实时通信
- 流式输出，回复可以边生成边返回
- 基于 token 的鉴权
- 每个连接独立会话，每条连接都会分配独立的 `chat_id`
- 支持 TLS / SSL，也就是 `wss://`
- 用 `allowFrom` 对客户端做白名单控制
- 自动清理断开的连接

## 最快跑起来

### 1. 配置 `channels.websocket`

在 `config.json` 中添加：

```json
{
  "channels": {
    "websocket": {
      "enabled": true,
      "host": "127.0.0.1",
      "port": 8765,
      "path": "/",
      "websocketRequiresToken": false,
      "allowFrom": ["*"],
      "streaming": true
    }
  }
}
```

这是一个适合本机联调的最小配置：

- 监听 `127.0.0.1:8765`
- 不强制 token
- 允许任意 `client_id`
- 开启流式输出

### 2. 启动 nanobot

```bash
nanobot gateway
```

如果启动成功，日志里通常会看到类似输出：

```text
WebSocket server listening on ws://127.0.0.1:8765/
```

### 3. 连接客户端

你可以用 `websocat`：

```bash
websocat ws://127.0.0.1:8765/?client_id=alice
```

也可以用 Python：

```python
import asyncio
import json
import websockets


async def main():
    async with websockets.connect("ws://127.0.0.1:8765/?client_id=alice") as ws:
        ready = json.loads(await ws.recv())
        print(ready)
        # {"event": "ready", "chat_id": "...", "client_id": "alice"}

        await ws.send(json.dumps({"content": "Hello nanobot!"}))

        reply = json.loads(await ws.recv())
        print(reply["text"])


asyncio.run(main())
```

## 连接地址格式

```text
ws://{host}:{port}{path}?client_id={id}&token={token}
```

常用查询参数如下：

| 参数 | 是否必填 | 说明 |
|------|----------|------|
| `client_id` | 否 | 客户端标识，用于 `allowFrom` 校验。不传时会自动生成形如 `anon-xxxxxxxxxxxx` 的匿名 ID，最长 128 字符。 |
| `token` | 条件必填 | 当 `websocketRequiresToken=true` 或配置了静态 `token` 时，客户端必须提供它。 |

## 协议说明

WebSocket 渠道使用纯文本 JSON 帧通信。绝大多数情况下，每条消息都有一个 `event` 字段用于区分消息类型。

## 服务端发给客户端的消息

### `ready`

连接建立后，服务端会立即先发一条 `ready`：

```json
{
  "event": "ready",
  "chat_id": "uuid-v4",
  "client_id": "alice"
}
```

它的作用是告诉客户端：

- 当前连接已建立成功
- 当前连接被分配的 `chat_id`
- 服务端识别到的 `client_id`

### `message`

当 agent 使用非流式模式回复时，客户端会收到：

```json
{
  "event": "message",
  "text": "Hello! How can I help?",
  "media": ["/tmp/image.png"],
  "reply_to": "msg-id"
}
```

说明：

- `text`：回复正文
- `media`：附带文件路径，仅在存在媒体时出现
- `reply_to`：关联回复 ID，仅在需要线程化回复时出现

### `delta`

当开启 `streaming: true` 时，服务端会按增量输出文本：

```json
{
  "event": "delta",
  "text": "Hello",
  "stream_id": "s1"
}
```

你可以把它理解成“当前又多生成了一小段文本”。

### `stream_end`

一次流式输出结束时会发：

```json
{
  "event": "stream_end",
  "stream_id": "s1"
}
```

收到它后，客户端通常可以结束“正在生成中”的 UI 状态。

## 客户端发给服务端的消息

客户端有两种常见发送方式。

### 方式一：直接发纯文本

```json
"Hello nanobot!"
```

### 方式二：发 JSON 对象

```json
{"content": "Hello nanobot!"}
```

服务端会按顺序识别以下字段：

1. `content`
2. `text`
3. `message`

如果收到的不是合法 JSON，会被当成普通文本处理。

## 配置项参考

所有配置都写在 `config.json` 的 `channels.websocket` 下。

### 连接相关

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | `bool` | `false` | 是否启用 WebSocket 服务端。 |
| `host` | `string` | `"127.0.0.1"` | 监听地址。若需对外提供服务，通常改为 `"0.0.0.0"`。 |
| `port` | `int` | `8765` | 监听端口。 |
| `path` | `string` | `"/"` | WebSocket 升级路径。尾部斜杠会被规范化，根路径 `/` 会保留。 |
| `maxMessageBytes` | `int` | `1048576` | 单条入站消息最大字节数，范围约 1 KB 到 16 MB。 |

### 鉴权相关

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `token` | `string` | `""` | 静态共享密钥。设置后，客户端必须通过 `?token=<value>` 提供相同值。 |
| `websocketRequiresToken` | `bool` | `true` | 若为 `true`，且未配置静态 `token`，客户端也必须提供一个合法的已签发 token。 |
| `tokenIssuePath` | `string` | `""` | 短期 token 的签发接口路径，必须和 `path` 不同。 |
| `tokenIssueSecret` | `string` | `""` | 调用签发接口时必须携带的密钥。若为空，任何人都能申请 token，系统会打印警告。 |
| `tokenTtlS` | `int` | `300` | 已签发 token 的有效期，单位秒，范围约 30 到 86400。 |

### 访问控制

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `allowFrom` | `list[str]` | `["*"]` | 允许访问的 `client_id` 列表。`"*"` 代表全部允许，`[]` 代表全部拒绝。 |

### 流式输出

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `streaming` | `bool` | `true` | 是否开启流式输出。开启后通常会发送 `delta` 和 `stream_end`，而不是只发单条 `message`。 |

### 保活参数

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `pingIntervalS` | `float` | `20.0` | WebSocket ping 周期，单位秒，范围约 5 到 300。 |
| `pingTimeoutS` | `float` | `20.0` | 等待 pong 的超时时间，超时后连接会被关闭。 |

### TLS / SSL

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `sslCertfile` | `string` | `""` | TLS 证书文件路径（PEM）。 |
| `sslKeyfile` | `string` | `""` | TLS 私钥文件路径（PEM）。需和 `sslCertfile` 同时配置才能启用 `wss://`。 |

启用 SSL 后，最低 TLS 版本会被限制为 TLSv1.2。

## Token 签发机制

如果你要把 WebSocket 暴露给公网，推荐使用“短期 token”而不是把静态密钥直接写死在客户端里。

### 工作流程

1. 客户端向 `GET {tokenIssuePath}` 发请求
2. 请求头里带上 `Authorization: Bearer {tokenIssueSecret}`，或者使用 `X-Nanobot-Auth`
3. 服务端返回一个一次性 token
4. 客户端再拿着这个 token 发起 WebSocket 握手
5. token 使用一次后即作废

返回示例：

```json
{
  "token": "nbwt_aBcDeFg...",
  "expires_in": 300
}
```

### 配置示例

```json
{
  "channels": {
    "websocket": {
      "enabled": true,
      "port": 8765,
      "path": "/ws",
      "tokenIssuePath": "/auth/token",
      "tokenIssueSecret": "your-secret-here",
      "tokenTtlS": 300,
      "websocketRequiresToken": true,
      "allowFrom": ["*"],
      "streaming": true
    }
  }
}
```

客户端调用流程：

```bash
# 1. 先申请一个短期 token
curl -H "Authorization: Bearer your-secret-here" http://127.0.0.1:8765/auth/token

# 2. 再拿 token 发起 WebSocket 连接
websocat "ws://127.0.0.1:8765/ws?client_id=alice&token=nbwt_aBcDeFg..."
```

### 限制说明

- 已签发 token 只能使用一次
- 待消费 token 数量最多 10000 个，超过后会返回 HTTP 429
- 过期 token 会在签发或校验时顺带懒清理

## 安全注意事项

### 1. 默认是偏安全的

`websocketRequiresToken` 默认值是 `true`，也就是说如果你没有明确关闭它，系统默认希望客户端先完成鉴权。

### 2. 静态 token 使用了安全比较

静态 token 的校验使用 `hmac.compare_digest`，可以降低时序攻击风险。

### 3. `allowFrom` 不只是“表面校验”

`allowFrom` 会在握手阶段和消息处理阶段都参与检查，属于多一道防线，而不是只在某一层判断一次。

### 4. 每个连接都有独立会话

每条 WebSocket 连接都会分配自己的 `chat_id`，不同客户端之间不会共用会话上下文。

### 5. 公网暴露时应尽量启用 `wss://`

如果服务需要在公网或跨不可信网络暴露，建议同时配置证书和私钥，强制走 TLS。

## 关于媒体文件

当服务端发送 `message` 时，`media` 字段里可能出现本地文件路径。

这点很重要：远端客户端不能直接访问服务端本地文件系统，所以仅拿到路径通常还不够。常见做法有两种：

- 服务端和客户端共享文件系统挂载
- 另外起一个 HTTP 文件服务，把媒体目录暴露出去

## 常见部署方式

### 本地或可信内网调试：不鉴权

```json
{
  "channels": {
    "websocket": {
      "enabled": true,
      "host": "0.0.0.0",
      "port": 8765,
      "websocketRequiresToken": false,
      "allowFrom": ["*"],
      "streaming": true
    }
  }
}
```

适合开发阶段、局域网测试或单机桌面应用联调。

### 简单鉴权：静态 token

```json
{
  "channels": {
    "websocket": {
      "enabled": true,
      "token": "my-shared-secret",
      "allowFrom": ["alice", "bob"]
    }
  }
}
```

客户端连接示例：

```text
ws://127.0.0.1:8765/?client_id=alice&token=my-shared-secret
```

适合少量内部客户端，部署简单，但不适合把密钥长期硬编码到公开前端。

### 公网部署：签发短期 token

```json
{
  "channels": {
    "websocket": {
      "enabled": true,
      "host": "0.0.0.0",
      "port": 8765,
      "path": "/ws",
      "tokenIssuePath": "/auth/token",
      "tokenIssueSecret": "production-secret",
      "websocketRequiresToken": true,
      "sslCertfile": "/etc/ssl/certs/server.pem",
      "sslKeyfile": "/etc/ssl/private/server-key.pem",
      "allowFrom": ["*"]
    }
  }
}
```

这是更适合生产环境的方案。

### 自定义路径

```json
{
  "channels": {
    "websocket": {
      "enabled": true,
      "path": "/chat/ws",
      "allowFrom": ["*"]
    }
  }
}
```

此时客户端连接地址变为：

```text
ws://127.0.0.1:8765/chat/ws?client_id=...
```

尾部斜杠会自动归一化，所以 `/chat/ws` 和 `/chat/ws/` 通常效果一致。

## 建议你怎么选

如果你还不确定该怎么配，可以直接按下面思路选：

- 只是本地调试：`websocketRequiresToken=false`
- 小规模内部系统：静态 `token`
- 面向公网或浏览器前端：`tokenIssuePath + tokenIssueSecret + WSS`

## 最后总结

WebSocket 渠道适合用来做：

- Web 聊天前端
- 桌面应用嵌入式对话窗口
- 自定义 CLI / TUI 助手
- 内部服务之间的实时对话代理

它最大的优势是协议简单、交互实时、前后端都容易接入。对中文开发者来说，可以直接把它理解成“给 nanobot 开一个实时聊天接口”。
