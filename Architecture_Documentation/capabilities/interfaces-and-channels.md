# 能力域：Interfaces And Channels

## 1. 责任范围

本能力域描述用户如何进入系统，以及不同协议层如何映射到统一 agent 内核。

当前主要实现位于：

- `nanobot/cli/`
- `nanobot/api/server.py`
- `nanobot/channels/`
- `nanobot/channels/manager.py`
- `nanobot/nanobot.py`

## 1.1 当前分支约束

`local_dev` 当前明确不支持内置 WebUI。

这意味着：

- 本分支不维护浏览器前端对话模块。
- 面向外部的主要接入形态是 channel，而不是 Web 页面。
- 如果需要新增对话入口，应优先实现为新的 channel / plugin 适配，而不是新增 `webui/` 一类模块。

## 2. 当前入口类型

### 2.1 CLI

CLI 是当前最完整、最直接的本地交互入口之一。

当前特点：

- 基于 `typer` 和 `prompt_toolkit`
- 支持历史记录、终端恢复、交互式流式展示
- 对 Windows 终端编码和 prompt history 有兼容处理

### 2.2 SDK / 编程接口

`Nanobot.from_config()` 提供编程式 facade：

- 从配置构建 provider、bus、loop
- 通过 `run(...)` 发起一次直接 agent 调用

这层较轻，核心逻辑仍下沉到 `AgentLoop`。

### 2.3 HTTP API

`nanobot/api/server.py` 提供 OpenAI-compatible 风格接口，但当前是“兼容子集”而不是完整实现：

- 提供 `/v1/chat/completions`
- 提供 `/v1/models`
- 提供 `/health`
- 请求最终路由到固定 agent loop 和持久 session

当前限制明确包括：

- 只支持单条 user message 输入
- `stream=true` 暂不支持
- 模型选择受当前配置限制，不是任意模型代理

### 2.4 Channels

`channels/` 目录下当前支持多种外部聊天渠道，如 Telegram、Discord、Slack、Email、WebSocket 等。

`ChannelManager` 的职责是：

- 发现并初始化已启用渠道
- 启动/停止渠道
- 统一消费 outbound queue 并发送消息
- 对 streaming delta 做 coalescing
- 对发送失败做重试

对于本分支来说，channels 不只是“支持的一种入口”，而是主要对外对话接入面。

典型形态包括：

- QQ bot / QQ 插件接入
- Telegram / Discord / Slack 等消息渠道
- Email 或 WebSocket 这类协议型 channel

## 3. WebSocket 的真实状态

WebSocket 不是单纯客户端，而是本地 server channel：

- nanobot 作为 WebSocket server 对外监听
- 支持 token issue path、握手鉴权、allow_from 控制
- 每个连接映射为独立会话

这部分能力已经比较完整，但它仍属于 channels 体系，不等同于完整 Web UI。

## 4. 设计意图

这一层的设计意图是：

- 允许不同输入协议共享同一个 agent 内核。
- 保持入口适配逻辑与 agent 核心逻辑分离。
- 让渠道专注在协议转换、授权和消息发送，而不是自行维护 agent 状态机。

## 5. 当前限制

- 各入口仍各自维护一部分前置/后置逻辑，无法视为完全统一协议层。
- HTTP API 目前能力收敛，不能按“完整 OpenAI API 服务”理解。
- `local_dev` 当前没有内置 Web UI 目录，相关能力不能写进正式架构现状。
- 对本分支而言，新增浏览器前端会提高系统复杂度，不符合当前维护方向。

## 6. 相关测试

- `tests/cli/`
- `tests/channels/`
- `tests/test_openai_api.py`
- `tests/test_api_attachment.py`
