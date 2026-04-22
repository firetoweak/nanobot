# 能力域：Session And Structured State

## 1. 责任范围

本能力域描述会话历史、回合状态、工作集快照、capsule、artifact 与 commit manifest 的持久化方式。

当前主要实现位于：

- `nanobot/session/manager.py`
- `nanobot/session/state.py`
- `nanobot/session/state_store.py`

## 2. 当前真实实现

当前项目存在两套并行但互相关联的会话持久化机制：

### 2.1 Session JSONL

`SessionManager` 会把消息历史保存到 `sessions/*.jsonl`。

用途主要是：

- 维护消息历史
- 支持 unconsolidated history 截取
- 保留 metadata 和 `last_consolidated`

这套机制仍是当前会话历史的基础层。

### 2.2 Structured State Store

`StateStore` 将结构化状态持久化到 `.nanobot/state/sessions/<session_key>/` 下，拆分为：

- `turns/`
- `messages/`
- `responses/`
- `working-set/`
- `capsules/`
- `artifacts/`
- `commits/`
- `indexes/`

写入采用原子替换文件方式，部分路径支持 CAS 语义，例如：

- turn state 可带 `expected_revision`
- latest working set 发布可带 `expected_version`

这说明当前状态层已经不是“随便覆盖 JSON 文件”的弱约束实现。

## 3. 关键对象含义

从当前代码与状态引用语义看，核心对象包括：

- turn state：描述一个回合当前所处阶段和引用关系
- message object / response object：消息与模型响应的结构化投影
- working set snapshot：当前稳定工作集快照
- capsule：压缩后的关键回合结论
- artifact：工具结果或外部内容的可复用投影
- commit manifest：回合是否正式完成的提交边界

当前 `StateStore.resolve_ref(...)` 支持解析这些结构化引用。

## 4. 设计意图

此能力域的设计意图很明确：

- 会话恢复不能只依赖聊天记录尾部。
- 结构化状态应当能表达“当前回合进行到哪里”“哪些工作集已经稳定”“哪些结论可复用”。
- 回合完成应有显式边界，而不是只靠经验推断。

## 5. 当前限制

- Session JSONL 与 structured state 仍并存，说明系统尚未完全收敛到单一状态源。
- 很多上层逻辑仍需在两套数据之间协调，而不是天然只读 structured state。
- 文档和代码改动时，要区分“聊天历史持久化”与“运行时结构化状态持久化”，不能混为一谈。

## 6. 相关测试

- `tests/agent/test_state_store.py`
- `tests/agent/test_turn_state_machine.py`
- `tests/agent/test_loop_save_turn.py`
- `tests/agent/test_commit_publish.py`
- `tests/agent/test_resume_repair.py`
- `tests/agent/test_session_manager_history.py`
