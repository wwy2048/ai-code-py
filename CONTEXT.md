# ai-code-py

用 Python 从零重写 ai-code 的代码生成平台：用户通过对话生成网站，产物落盘后可实时预览与部署。前端（Vue3 + TypeScript）以原 ai-code 前端为基线复制进本仓库，后端全栈 Python。

## Language

**应用（App）**:
用户创建的生成目标，含生成类型、部署键与封面等属性，对应原系统的 App 实体。
_Avoid_: 项目、站点

**生成类型（codeGenType）**:
应用采用的生成方式，枚举 HTML / MULTI_FILE / VUE_PROJECT 三者之一。
_Avoid_: 模板、模式

**对话历史（ChatHistory）**:
应用会话中的消息记录，持久化于 `chat_history`；其中只有 visible 记录作为 UI 数据源，运行时消息也可保存在同一张表。
_Avoid_: 聊天记录、会话日志

**生成会话（Session）**:
一次可恢复的 agent 执行上下文，持久化于 MySQL；同一应用的连续对话共享一个生成会话。
_Avoid_: 请求、线程、对话

**生成上下文（Generation Context）**:
负责入站命令、Turn 调度、运行控制、出站事件与执行适配的限界上下文。
_Avoid_: GenerationManager、AgentTurnExecutor

**代理运行时（Agent Runtime）**:
生成上下文中的执行能力集合：应用层编排模型/工具循环，并通过端口与适配器协作完成持久化、压缩和流式输出。它是产品内核，不是框架依赖。
_Avoid_: LLM 服务、agent 框架

**生成代理（Generation Agent）**:
单个 App 的入站事件消费者，注册到事件总线并按 app_id 路由；持有 followup、steering、paused 收件箱、活动回合与常驻 worker。
_Avoid_: 全局 Agent、AppSession、worker

**队列投影（Queue Projection）**:
把收件箱状态与消息消费事实投影成前端可重放的队列快照和事件流；它不是队列事实本身。
_Avoid_: 排队队列、收件箱、前端数组

**流事件（Stream Event）**:
模型文本或思考等高频增量事件；只能由邮箱型消费者接收，由消费者自行合并刷帧。
_Avoid_: 普通生命周期事件、逐 token 回调

**回合（Turn）**:
一次用户请求及其引发的全部 assistant / tool 循环，直到产生最终回复或失败。一个回合内的消息共享同一个 `turn_id`，并以客户端提供的 `requestId` 去重。
_Avoid_: 请求、调用、轮次

**模型步（Step）**:
一个回合内的一次模型调用及其触发的一批工具执行；steering 只在模型步边界生效。
_Avoid_: 轮次、模型调用

**回合终态（Turn Outcome）**:
一个回合结束后的最终状态，取值为 completed、failed 或 interrupted。
_Avoid_: 完成标志、结束原因

**生命周期记录（Turn Lifecycle Record）**:
ChatHistory 中记录回合开始或终态的隐藏事实记录，不进入模型上下文。
_Avoid_: 事件日志、状态表

**中断占位（Interruption Placeholder）**:
回合被打断后为满足消息配对而写入的隐藏 assistant 或 tool 记录。
_Avoid_: 半截消息、伪回答

**入站事件（Inbound Event）**:
通过事件总线同步投递给生成会话 Agent 的用户意图，包括用户消息、插话和打断/转向；
投递只进入收件箱或控制旁路，不在发布调用中执行回合。
_Avoid_: 请求、直接方法调用、队列操作

**收件箱（Inbox）**:
生成会话接收用户消息事件的排队边界；同一条消息事件在 followup、steering、paused 状态间移动，
消费时直接使用原事件，不复制成第二套消息对象。
_Avoid_: 队列、缓冲区、Task

**队列操作（Queue Operation）**:
对尚未消费的收件箱消息执行 promote、delete、resume、clear 的同步状态转换；
它直接作用于收件箱并返回结果，不通过异步事件总线，也不进入 worker 收件箱。
_Avoid_: 队列事件、异步命令、入站消息

**邮箱型消费者（Mailbox Consumer）**:
通过非阻塞 `offer` 接收高频流事件、并自行决定合并与刷新节奏的消费者。
_Avoid_: 普通事件回调、逐 token 渲染器、同步监听器

**会话工作器（Session Worker）**:
消费生成会话收件箱并执行回合的常驻执行者；同一会话任一时刻最多处理一个回合。
_Avoid_: 线程、进程、Agent 实例

**出站事件（Outbound Event）**:
生成运行时向订阅者发布的结构化事实通知，用于 SSE、观测和其他消费者；只包含消费所需信息，完整工具参数和结果保存在隐藏运行时消息中。
_Avoid_: 外部事件、SSE 文本块、日志

**事件总线（Event Bus）**:
分发运行时出站事件并维护订阅关系的进程内通道；它不持有会话事实，也不是持久化队列。
_Avoid_: 消息队列、事件存储、HookBus

**事件订阅（Subscription）**:
消费者对一类或多类出站事件的声明，决定其接收范围与消费方式。
_Avoid_: 回调、监听器、插件

**控制旁路（Control Bypass）**:
不进入普通收件箱尾部、需要优先于排队命令送达当前回合的控制通道，用于打断/转向。
_Avoid_: 高优先级消息、取消事件

**插话（Steering）**:
在当前回合的模型步边界插入后续输入，使其参与当前回合而不是等待下一回合。
后端在消费该输入时写入唯一的可见用户消息，并通知前端移除对应排队项。
_Avoid_: 追问、并发消息

**追问（Followup）**:
等待当前回合结束后再作为下一回合处理的用户意图。
后端在开始消费该意图时写入唯一的可见用户消息，并通知前端移除对应排队项。
_Avoid_: 排队消息、立即插话

**排队项（Queued Item）**:
运行期间已提交但尚未被消费的一条 Followup 或 Steering 用户意图，以稳定标识去重；
前端长条只是该排队项的视图，不单独构成消息事实。
_Avoid_: 排队长条、临时消息、前端事件

**排队队列（Followup Queue）**:
运行期间已提交、等待按顺序消费的 Followup 集合；用户中断后其中剩余项可整体转为暂停队列。
_Avoid_: 前端数组、临时消息列表

**暂停队列（Paused Followup Queue）**:
因用户中断或执行失败而停止自动消费的 Followup 队列；只能通过队列级恢复继续，不阻塞后续新消息。
_Avoid_: 已取消队列、丢弃队列

**组级恢复（Resume Queue）**:
恢复暂停队列全部剩余项，并按原顺序继续执行的用户动作。
_Avoid_: 单条 Resume、重试当前 Turn

**队列消费（Queue Consumption）**:
后端将一条 Followup 或 Steering 从排队状态取出时，恰好写入一条可见用户消息的过程；
前端只依据消费事件同步消息和排队项，不自行落第二份消息事实。
_Avoid_: 前端发送、重复插入消息

**转向（Redirect）**:
在打断当前回合的同时提交新的用户意图，使其成为当前回合后续执行的下一步输入。
_Avoid_: 修改、重试

**工具（Tool）**:
模型可见的一个具名能力，由 schema（模型所见）与 handler（Python 所执行）成对描述。
_Avoid_: 函数、命令、接口

**工作目录（Workspace）**:
单个应用代码产物的落盘目录，约定为 `tmp/codeOutput/{type}_{appId}`；工具的文件操作以它为根，禁止越界。
_Avoid_: 沙箱、项目目录

**部署键（deployKey）**:
应用部署后生成的 6 位访问密钥，用于拼接预览地址。
_Avoid_: token、密钥

**可视化编辑（Visual Editing）**:
用户在网站预览中直接选取页面元素并提交修改意图的交互方式。
_Avoid_: 页面编辑、元素编辑

**编辑模式（Visual Editing Mode）**:
预览 iframe 处于可拾取元素状态的模式；离开该模式后页面恢复原有交互。
_Avoid_: 修改模式、设计模式

**选中元素（Selected Element）**:
用户在编辑模式中明确选定并作为当前修改目标的预览页面 DOM 元素。
_Avoid_: 当前元素、目标元素

**元素来源定位（Element Source Location）**:
选中元素对应的生成项目源文件路径与行号，用于定位需要修改的代码。
_Avoid_: DOM 路径、选择器

**SSE 流（Stream）**:
后端向前端推送生成过程的事件通道。
_Avoid_: 推送、长连接

**活动消息（Active Message）**:
仍属于生成会话下一次模型上下文的 `chat_history` 记录；压缩后，被摘要覆盖的旧消息转为非活动，但不会被删除。
_Avoid_: 有效消息、未删除消息

**可见消息（Visible Message）**:
允许作为对话历史展示给前端的 `chat_history` 记录；工具调用、工具结果与压缩摘要默认不可见。
_Avoid_: UI 消息、聊天记录

**压缩摘要（Compaction Summary）**:
一条隐藏但仍 active 的会话消息，用于代替被标记为非 active 的旧消息进入后续模型上下文。
_Avoid_: 总结、检查点

**代理循环（Agent Loop）**:
一个回合内反复执行“模型调用→工具调用→结果回填”的薄内核，只依赖类型化消息、工具池和持久化端口，不拥有代码生成类型差异。
_Avoid_: AgentTurnExecutor、总编排器

**回合状态（Turn State）**:
一个回合在运行时的内存消息序列与循环进度；它是当前回合的工作状态，不是数据库记录本身。
_Avoid_: 会话、历史表、上下文快照

**回合日志（Turn Journal）**:
运行时向持久化层追加用户消息、助手消息、工具调用、工具结果和模型调用事实的端口。
_Avoid_: Repository、DAO、日志文件

**工具池（Tool Pool）**:
一个回合实际暴露给模型的工具 schema 列表及其 handler 映射，由基础工具与代码生成配置显式组合。
_Avoid_: 全局工具注册表、插件目录

**代码生成配置（Codegen Profile）**:
一个代码生成类型对应的 Workspace 准备、工具扩展与完成校验策略组合。
_Avoid_: 执行器子类、模式分支

**钩子（Hook）**:
在回合、模型或工具生命周期切点上执行的强类型扩展；只有明确的门禁切点可以阻断或改写执行。
_Avoid_: 事件监听器、全局回调、中间件
