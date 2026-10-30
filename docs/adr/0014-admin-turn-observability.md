# 管理员 Turn 观测只返回诊断所需数据

管理员历史列表可跨用户、App、消息类型和时间筛选全部 `chat_history` 记录。Turn 详情按 `sequence` 返回 visible 与隐藏消息、工具状态、耗时、大小和截断预览，并关联同 `appId + turnId` 的 `model_call_log`。普通用户无法访问管理员观测接口。

## Status

Accepted（2026-10-31）。

## Consequences

- 工具调用只返回 `path` / `pattern` 等允许输入，不返回完整代码、工具参数或大结果正文。
- 工具结果、压缩摘要和隐藏消息预览最多 500 字符；模型错误摘要会再次屏蔽 `sk-*`、Bearer 和 key/secret/token 赋值形式。
- 模型观测只包含模型名、调用类型、状态、耗时、token、重试和脱敏错误，不保存 API Key 或环境变量。
- 普通历史分页继续只返回 visible 消息，管理员能力与用户接口保持隔离。
