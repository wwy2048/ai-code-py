# 生成运行时分层架构

## 1. 限界上下文

`app/generation` 是生成链路的限界上下文，负责：

- 通过双向事件总线接收入站用户消息与 interrupt；
- 为每个 App 维护 followup、steering、paused 收件箱和常驻 worker；
- 调度 Turn、维护 Turn 内部控制状态；
- 执行模型/工具循环并发布有序出站事件；
- 将收件箱状态投影为 App 级队列 SSE；
- 通过端口适配 MySQL、模型、文件工具和 SSE。

HTTP、数据库、Anthropic SDK、文件系统和 SSE 都不属于领域层。

## 2. 目录与职责

```text
app/generation/
├── domain/
│   ├── events.py           # 入站事件、出站事件、Mailbox、订阅约束
│   ├── turns.py            # TurnRequest 执行 DTO
│   └── messages.py         # 模型上下文消息
├── ports/
│   ├── model.py
│   ├── journal.py
│   ├── tools.py
│   └── turn.py
├── application/
│   ├── bus.py              # 双向 EventBus：publish / emit
│   ├── control.py          # Turn 控制状态
│   ├── hooks.py
│   ├── inbox.py            # per-App GenerationAgent 与同步队列操作
│   ├── loop.py
│   ├── projection.py       # QueueProjection 与 App 队列 replay
│   ├── runtime.py          # AgentRegistry、TurnJob 和全局生命周期
│   └── stream.py           # Turn replay、SSE 编码、流事件合并
└── adapters/
    ├── conversation.py
    ├── conversation_store.py
    ├── executor.py
    ├── anthropic_model.py
    ├── codegen.py
    ├── tools.py
    ├── workspace.py
    └── ...
```

## 3. 双向事件总线

```text
用户消息 / interrupt
        │
        └─ EventBus.publish(event, to=app_id)
               │  同步
               ▼
        GenerationAgent.enqueue
               │
        followup / steering / paused inbox

promote / delete / resume / clear
        │
        └─ 同步 Inbox 方法 ──> QueueProjection.apply_mutation
```

- 入站只做同步投递，不执行 Turn。
- 出站由 `EventBus.emit` 异步分派。
- `StreamEvent` 只允许 `Mailbox` 消费者订阅。
- 队列操作是同步结构操作，不经过事件总线。

## 4. Per-App Generation Agent

```text
GenerationRuntime
├── EventBus
└── AgentRegistry: app_id -> GenerationAgent

GenerationAgent
├── direct inbox
├── followup inbox
├── steering inbox
├── hidden inbox
├── paused inbox
├── active Turn
└── worker
```

- 同一 App 任意时刻最多执行一个 Turn。
- steering 优先于 followup；同一类内部按优先级和到达顺序。
- 收件箱直接持有原始 `UserMessage`，移动和消费过程中 ID 不变。

## 5. 一次 Turn 的数据流

```text
POST /chat/gen/code
  -> TurnRequest
  -> GenerationRuntime.start_turn
  -> EventBus.publish(UserMessage(direct))
  -> GenerationAgent.direct inbox
  -> worker
  -> TurnExecutor adapter
  -> AgentLoop
       ├─ model step
       ├─ tool execution
       ├─ steering drain
       └─ interrupt safe point
  -> EventBus.emit(TurnEvent)
       ├─ TurnJob mailbox   -> Turn SSE
       └─ QueueProjection   -> App Queue SSE
```

## 6. 控制与队列

```text
POST /chat/turn/control
  ├─ steer     -> EventBus.publish(UserMessage(intent=steering))
  └─ interrupt -> EventBus.publish(UserInterrupt)
```

`redirect` 已从新事件模型移除，旧端点明确返回参数错误。

```text
POST /chat/queue/control
  ├─ enqueue     -> EventBus.publish(UserMessage(intent=followup))
  └─ steer/delete/resume/clear_paused
                 -> GenerationAgent 同步方法
```

## 7. 事实与投影

- ChatHistory 是 Turn 事实层。
- `TurnJob` 只是进程内 SSE 重放缓冲，不是事实层。
- `QueueProjection` 是收件箱的读模型，不是队列事实层。
- `USER_MESSAGE_COMMITTED` 与 `STEERING_CONSUMED` 由投影消费，生成用户消息同步事件。
- 流式增量在 SSE 消费者侧按字符阈值或帧界合并，原始事件仍保存在 TurnJob replay buffer。
- 超出重放窗口时发送 `stream_gap`，前端重新读取 ChatHistory。
