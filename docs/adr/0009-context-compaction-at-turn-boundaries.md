# 在完整的 Turn 边界压缩上下文

上下文压缩在下一 Turn 开始前执行，使用最近一次成功模型调用的 provider usage 加上待发送用户文本判断是否达到配置阈值。压缩按 `turn_id` 整体选择旧 Turn，保留最近一个完整 Turn；旧摘要与更早 Turn 合并生成新摘要，所有被覆盖记录只标记为非 active，不删除。压缩摘要保持隐藏且 active，并在后续上下文投影中排在剩余活动消息之前。

## Status

Accepted（2026-10-31）。

## Consequences

- 不会在工具调用与结果之间切分 Turn，也不会压缩正在执行的当前 Turn。
- `compacted_by` 指向覆盖该记录的新摘要，二次压缩会更新并停用旧摘要。
- provider usage 缺失时使用活跃消息、待发送文本和固定提示词/工具开销估算；阈值与固定开销通过环境变量配置。
- 压缩摘要模型调用单独写入 `model_call_log`，通过 `call_kind=compaction` 与生成调用区分，并记录耗时、token、状态和脱敏错误类型。
- 摘要生成失败时发送明确的 compaction failed 事件，并让当前 Turn 继续使用原活动上下文。
- 隐藏摘要不进入普通用户的历史分页接口，前端只看到压缩状态事件。
