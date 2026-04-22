# 能力域：Configuration And Workspace Boundaries

## 1. 责任范围

本能力域描述配置加载、环境变量解析、workspace 边界以及部分运行路径约定。

当前主要实现位于：

- `nanobot/config/loader.py`
- `nanobot/config/schema.py`
- `nanobot/config/paths.py`
- `nanobot/utils/helpers.py`

## 2. 当前真实实现

### 2.1 配置加载

`load_config(...)` 的行为是：

- 读取指定路径或默认 `~/.nanobot/config.json`
- 进行迁移修正
- 通过 Pydantic schema 校验
- 对网络安全白名单等进行后处理

### 2.2 环境变量插值

`resolve_config_env_vars(...)` 支持在字符串值中解析 `${VAR}` 模式。

这说明配置并非完全静态 JSON，也允许在部署环境中延迟注入密钥和地址。

### 2.3 Workspace 边界

多个能力都会引用 workspace 作为操作边界：

- 文件工具
- shell tool
- session / state / memory 存储
- template 同步

但要注意，当前工程并非所有内容都严格被 workspace 锁死。部分能力仍可根据配置放宽边界，因此应描述为“支持 workspace 约束”，而不是“天然强隔离”。

## 3. 设计意图

- 用 schema 保证配置结构可演进。
- 用 workspace 作为主要本地操作边界。
- 允许默认本地使用，同时支持更复杂部署方式。

## 4. 当前限制

- 仍以本地文件和单机运行假设为主。
- 配置迁移规则存在，但不是独立迁移框架。
- 不同工具和入口对 workspace 约束的依赖程度不完全相同。

## 5. 相关测试

- `tests/config/`
- `tests/tools/test_exec_security.py`
- `tests/tools/test_sandbox.py`
