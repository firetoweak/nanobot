# 能力域：Providers And Model Routing

## 1. 责任范围

本能力域描述模型提供方注册、后端选择、模型路由和 provider 差异处理。

当前主要实现位于：

- `nanobot/providers/registry.py`
- `nanobot/providers/base.py`
- `nanobot/providers/*_provider.py`
- `nanobot/nanobot.py`

## 2. 当前真实实现

当前项目不是只支持单一 LLM provider，而是通过 provider registry 做统一描述。

`ProviderSpec` 目前可表达：

- provider 名称和展示名
- 关键字匹配规则
- API key / api base 约定
- gateway / local / oauth / direct 等属性
- 后端类型
- 参数兼容差异

当前 registry 明确支持多种类型：

- 原生 Anthropic
- OpenAI-compatible 后端
- Azure OpenAI
- OpenAI Codex
- GitHub Copilot
- 多种 gateway / local / direct provider

## 3. 路由语义

从当前实现看，provider 选择不是单纯“写死一个类”，而是：

1. 根据 model 和配置判断 provider name。
2. 从 registry 取 `ProviderSpec`。
3. 根据 `backend` 实例化对应 provider 实现。
4. 再把 generation settings 注入 provider。

这使得“模型名、provider 名、后端实现、API base”四者并不总是一一对应。

## 4. 设计意图

此能力域的设计意图是：

- 把 provider 元数据集中维护，避免分散在配置、状态展示和实例化逻辑中。
- 允许 gateway、oauth provider、兼容端点和原生 SDK 共存。
- 在不改主 runtime 的前提下扩展新 provider。

## 5. 当前限制

- registry 很强，但也意味着理解路由时不能只靠模型名猜测。
- 不同 provider 对 prompt caching、max tokens、role alternation、reasoning content 的支持度不同。
- 文档中不能假设所有 provider 行为一致。

## 6. 相关测试

- `tests/providers/`
- `tests/test_build_status.py`
- `tests/test_package_version.py`
