# Followup 队列由后端事实与 App 级事件驱动

运行中的用户输入先进入 App 级 Followup 队列，前端只渲染排队项视图，不提前写入用户消息。Followup 被 worker 取出、或 Steering 在当前 Turn 的模型步边界被消费时，后端先提交唯一的可见用户消息，再通过 App 级队列 SSE 发布消费事件；前端依据事件移除排队项并同步消息。这样避免了 UI 与后端各写一份消息，也使 Turn SSE 在 `turn_finished` 关闭后仍能驱动跨 Turn 的队列状态。

用户中断时，中断命令到达瞬间已有的 Followup 转为暂停队列；中断收口后才新增的消息保持正常 Followup 语义。暂停队列由组级 Resume 恢复全部剩余项并按 FIFO 执行。暂停期间发送新消息时，前端提示用户在 Clear 与发送之间选择：Clear 清空暂停队列后发送，发送保留暂停队列并发送，右上角关闭则取消发送。Followup 执行失败时暂停剩余队列；正常完成则继续执行后续项。

## Status

Accepted（2026-11-06）。扩展 ADR-0018 的 Followup / Steering 语义，不改变单进程收件箱与 MySQL 事实层边界。

## Event Contract

- App 队列 SSE 独立于 Turn SSE，使用自己的游标；连接时先发送 `queue_snapshot`，随后重放游标之后的队列事件。
- `queue_changed` 携带当前普通队列与暂停队列快照。
- `user_message_consumed` 携带稳定的 `commandId`、`turnId`、文本和 `mode=followup|steer`，并附带消费后的队列快照。
- 每个排队项 `commandId` 在消费链路内幂等；前端另外按 `commandId` 去重，Turn SSE 不再负责插入 Steering 用户气泡。
- Followup 消费后，前端用同一 `turnId` 附加现有 Turn SSE，继续接收文本、工具与终态事件。

## Consequences

- 页面刷新可从 App 队列快照恢复尚在进程内的排队项；后端进程重启后未消费队列仍会丢失，符合 ADR-0018 的进程内收件箱约束。
- 队列事件与 Turn 事件形成两条通道：队列通道负责生命周期与用户消息事实，Turn 通道负责生成过程。
- 暂停队列不再使用逐条 Resume；第一条暂停项隐藏 `Steer`，后续项仍可 Steer 到当前新 Turn。
- 不再由前端在发送 Followup 时乐观插入用户消息；消费失败时队列项恢复为暂停状态，避免静默丢消息。
