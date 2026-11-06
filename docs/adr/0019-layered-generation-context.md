# 生成链路按领域、端口、应用层和适配器重新分层

生成链路不再以 `AgentTurnExecutor + GenerationManager + emit 回调` 为中心，而是建立独立的 `app/generation` 限界上下文。领域层只定义命令、事件和消息；端口层定义模型、Journal、工具与 Turn 执行契约；应用层负责收件箱、worker、控制状态、模型/工具循环和事件总线；适配器层实现 Anthropic、MySQL ChatHistory、Workspace、代码生成工具和 SSE。生产代码不再从旧 `app/runtime` 导入，旧目录已删除。

## Status

Accepted（2026-11-06）。细化并取代 ADR-0018 中的代码落点与模块边界，事件语义、单进程总线和 MySQL 事实层决策继续有效。

## Consequences

- 依赖方向固定为 `domain <- ports <- application <- adapters <- composition root`。
- `app/api` 只负责命令构造与 SSE 传输，不直接编排模型或工具。
- `AgentTurnExecutor` 变为适配器，只负责组装模型、工具、Workspace、Journal 和 `AgentLoop`。
- `GenerationRuntime` 成为唯一生成应用门面。
- 旧 `app/runtime` 已删除，生成链路只保留 `app/generation`。
- 新增模型、工具或持久化实现时，应通过端口和适配器扩展，而不是修改应用运行时。