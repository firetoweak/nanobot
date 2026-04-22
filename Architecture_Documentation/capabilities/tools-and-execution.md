# 能力域：Tools And Execution Sandbox

## 1. 责任范围

本能力域描述 agent 可用工具、工具注册方式、执行边界以及与子 agent / MCP 的关系。

当前主要实现位于：

- `nanobot/agent/tools/`
- `nanobot/agent/loop.py`
- `nanobot/agent/subagent.py`

## 2. 当前真实实现

`AgentLoop._register_default_tools()` 会按配置注册默认工具集合，当前覆盖：

- 文件读写与目录浏览
- glob / grep 类搜索
- notebook 编辑
- shell 执行
- web search / web fetch
- message tool
- spawn tool
- cron tool

其中 shell、web、cron 是否可用，取决于配置开关。

## 3. 执行边界

当前实现支持一定程度的边界控制，但不是完全隔离的沙箱系统：

- 文件工具可以限制到 workspace 或 allowed_dir。
- shell tool 可配置 `restrict_to_workspace`、超时、sandbox、附加 PATH、允许环境变量。
- web 工具会受 SSRF 白名单与网络安全配置影响。
- MCP 是延迟连接的，只有配置了 server 并在首次需要时才接入。

因此，当前真实状态应描述为“带边界约束的本地工具执行”，而不是“强安全隔离沙箱”。

## 4. 子 agent

当前项目已经有 `SpawnTool` 与 `SubagentManager`，说明“派生 agent”不是概念层面，而是已存在能力。

但当前它们仍属于同一进程和同一主系统的一部分，不是远程调度平台，也不是独立微服务。

## 5. 设计意图

工具层的设计意图是：

- 让 agent 在 workspace 内进行真实修改和检索。
- 通过 registry 把工具定义和执行统一管理。
- 对高风险能力施加配置化边界，而不是让每个入口自己决定执行规则。

## 6. 当前限制

- 工具权限控制主要靠运行时配置，不是强制型多租户安全模型。
- 子 agent 协作已经实现，但其治理、可视化和资源配额仍不是独立能力域。
- MCP 成功接入依赖外部 server 配置和可用性，文档中不能把它写成必然存在。

## 7. 相关测试

- `tests/tools/`
- `tests/agent/tools/test_subagent_tools.py`
- `tests/agent/test_mcp_connection.py`
