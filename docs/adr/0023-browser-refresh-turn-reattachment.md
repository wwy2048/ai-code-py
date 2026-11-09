# 刷新页面通过活动 Turn 快照重挂载并重放 AI 回复

浏览器整页刷新会关闭既有 Turn SSE 连接，但服务端 Turn 继续执行。为了让刷新后的聊天、队列和预览恢复一致，决定把 Queue Stream 作为 App 级控制面：连接时返回 `activeTurn`，Turn 开始和结束时发送 `active_turn_changed`；客户端发现活动 Turn 后从游标 0 重新订阅 Turn Stream，并只清空该 `turnId` 的 AI 气泡后重建，保留用户消息。Turn 运行中保留完整事件缓冲，默认上限 16384；结束后立即释放完整事件，仅保留最终事件和轻量回执 10 分钟，用于短时幂等和最后一帧补给；数据库历史是 Turn 结束后恢复的事实源。空闲 30 分钟且没有活动 Turn、队列或 inflight 消息的 GenerationAgent 可以回收。预览状态由活动 Turn 驱动：有旧产物显示旧版本，无产物显示生成中，完成后切到新产物，失败或中断保留上一版，禁止把静态资源错误信封直接渲染为网页。

## Status

Accepted（2026-11-09）。

## Consequences

- Queue Stream 只负责 App 级状态和队列事实，不承载模型文本；Turn Stream 继续承载单个 Turn 的增量事件。
- `active_turn_changed` 由 GenerationAgent 生产，QueueProjection 发布，首次 `queue_snapshot` 也携带同一 `activeTurn` 描述。
- Turn Resume Snapshot 包含 `turnId`、`status`、`firstEventId`、`lastEventId` 和 `replayableFromZero`；前端据此判断能否完整重放。
- 超过事件缓冲范围时不得通过刷新页面形成循环；应停止自动重挂载并以持久化历史兜底。
- TurnJob 和空闲 GenerationAgent 都需要有界回收，避免扩大缓冲后造成进程内存持续增长。
- 放宽事件缓冲是当前进程内可用性措施，不是跨后端重启的持久化事件日志方案。