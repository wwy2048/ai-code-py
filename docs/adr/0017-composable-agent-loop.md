# Agent 循环采用薄内核、类型化端口与组合式策略

当前 `AgentTurnExecutor` 同时承担回合编排、模型循环、工具装配、持久化、代码生成差异和可观测性，导致新增工具或代码生成类型时必须修改同一个大对象。决定把 Agent Runtime 重构为：薄 `AgentLoop` 只操作内存中的 `TurnState`；模型通过 provider-neutral 的 `ModelRequest` / `ModelReply` 交互；持久化通过 `TurnJournal` 端口注入；工具继续使用“schema 列表 + handler 映射”，但由代码生成配置显式组合；横切行为通过异步、强类型的 Hook 接入。`AgentTurnExecutor` 保留为应用层装配门面，但不再直接实现循环细节。

## Status

Accepted（2026-10-31）。

## Consequences

- `AgentLoop` 不依赖 SQLAlchemy、Anthropic SDK 或具体代码生成类型，可单独测试和替换。
- 工具不采用全局装饰器注册；新增工具通过显式 handler 映射和 `ToolExtension` 组合，避免导入副作用。
- 不同 `codeGenType` 的 Workspace 准备、工具扩展和完成校验由 `CodegenProfile` / `CompletionPolicy` 表达，不通过执行器继承实现。
- Hook 使用显式事件上下文；`before_tool` 可以拒绝或重写调用，`after_tool` 可以替换结果，模型与回合生命周期事件只读。Hook 不使用全局可变注册表。
- 第一阶段保持数据库表结构、SSE 事件名/载荷和现有回合预算语义不变；`max_rounds_per_phase=12` 与 Vue 修复轮数 `3` 仅显式化并允许配置。
- 继续遵守 ADR-0002：不依赖、不复制 minicodex；只借鉴其消息分层、工具池和生命周期钩子的设计思想。
