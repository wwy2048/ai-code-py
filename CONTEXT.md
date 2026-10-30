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

**代理运行时（Agent Runtime）**:
项目内自研的 agent 循环执行层：多轮工具循环、工具分发、会话持久化与上下文压缩。它是产品内核，不是框架依赖。
_Avoid_: LLM 服务、agent 框架

**回合（Turn）**:
一次用户请求及其引发的全部 assistant / tool 循环，直到产生最终回复或失败。一个回合内的消息共享同一个 `turn_id`，并以客户端提供的 `requestId` 去重。
_Avoid_: 请求、调用、轮次

**外部事件（External Event）**:
通过 SSE 推送给前端的结构化事件，只包含渲染所需信息；完整工具参数和结果保存在隐藏运行时消息中。
_Avoid_: SSE 文本块、日志

**工具（Tool）**:
模型可见的一个具名能力，由 schema（模型所见）与 handler（Python 所执行）成对描述。
_Avoid_: 函数、命令、接口

**工作目录（Workspace）**:
单个应用代码产物的落盘目录，约定为 `tmp/codeOutput/{type}_{appId}`；工具的文件操作以它为根，禁止越界。
_Avoid_: 沙箱、项目目录

**部署键（deployKey）**:
应用部署后生成的 6 位访问密钥，用于拼接预览地址。
_Avoid_: token、密钥

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
