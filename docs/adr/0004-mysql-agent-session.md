# 使用 MySQL 持久化 Agent 会话

原设计借鉴 minicodex 的 Append-Only JSONL 会话文件，但本项目已经依赖 MySQL，且需要同时服务 UI 对话历史、运行时上下文、活动消息筛选和压缩检查点。决定把 `chat_history` 扩展为 agent 会话的持久化载体：新增前端可见性、活动状态及运行时消息所需字段；压缩时不删除历史，而是把已被摘要覆盖的消息标为非 active，并追加一条压缩摘要记录。MySQL 是会话的唯一持久化真相，不再维护 JSONL 会话文件。

## Status

Accepted（2026-10-30），修订 ADR-0002 的会话持久化部分。

## Consequences

- 会话读写与 UI 查询共用数据库，便于事务、分页和管理端观测，但数据库成为 agent 运行时的强依赖。
- `chat_history` 保留原 `message` / `messageType`，并增量增加 `turn_id`、`sequence`、`message_kind`、`payload`、`visible`、`active`、`tool_call_id`、`tool_name`、`is_error`、`compacted_by`。
- 旧 user / ai 行回填为 `visible=true, active=true`；工具调用和工具结果默认不可见，压缩摘要隐藏且保持 active。
- 压缩恢复必须稳定筛选 active 记录，并保证工具调用与对应工具结果不会被拆散。
- 需要按 `(appId, active, sequence)` 和 `(appId, visible, createTime, id)` 等查询模式建立索引，并通过 alembic 增量迁移和恢复契约测试；不允许据此要求重建现有表或强制全量数据迁移。
