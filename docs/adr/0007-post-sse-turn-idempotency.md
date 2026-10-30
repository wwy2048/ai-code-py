# 生成请求使用 POST + requestId 的结构化 SSE

生成入口不再使用旧版 GET + EventSource，而改为 `POST /app/chat/gen/code`，请求体携带 `appId`、`message` 和 `requestId`，响应用结构化 SSE 事件。每个事件带 `turnId`、递增 `sequence` 与 SSE `id`，客户端可用 `Last-Event-ID` 续传。服务端在客户端断开后继续执行；同一 requestId 不重复创建回合，活跃回合从内存事件缓冲续传，已完成回合返回完成事件并让前端刷新持久化历史。

## Status

Accepted（2026-10-30）。

## Consequences

- 前端使用 `fetch` + ReadableStream 解析 SSE，不再依赖 EventSource，因此可以发送 POST 正文和自定义请求头。
- 消息不再出现在 URL 查询参数中，降低日志泄漏风险，也为幂等重试提供稳定键。
- 服务端需要维护活跃回合的事件缓冲和去重索引；进程重启后的未完成回合按失败处理，不做跨进程断点续传。
- 结构化事件使工具进度和文本增量可直接渲染，但前后端需要同步事件 schema 与版本。
