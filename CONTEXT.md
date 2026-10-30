# ai-code-py

用 Python 从零重写 ai-code 的代码生成平台：用户通过对话生成网站，产物落盘后可实时预览与部署。前端（Vue3 + TypeScript）复用原 ai-code 前端，后端全栈 Python。

## Language

**应用（App）**:
用户创建的生成目标，含生成类型、部署键与封面等属性，对应原系统的 App 实体。
_Avoid_: 项目、站点

**生成类型（codeGenType）**:
应用采用的生成方式，枚举 HTML / MULTI_FILE / VUE_PROJECT 三者之一。
_Avoid_: 模板、模式

**对话历史（ChatHistory）**:
用户与应用之间的消息记录，作为 UI 数据源持久化于数据库。
_Avoid_: 聊天记录、会话日志

**生成会话（Session）**:
一次可恢复的 agent 执行上下文，以文件形式持久化（借鉴 minicodex 的 SessionLog 设计）；同一应用的连续对话共享一个生成会话。
_Avoid_: 请求、线程、对话

**代理运行时（Agent Runtime）**:
项目内自研的 agent 循环执行层：多轮工具循环、工具分发、会话持久化与上下文压缩。它是产品内核，不是框架依赖。
_Avoid_: LLM 服务、agent 框架

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
后端向前端推送生成过程的事件通道；HTML/MULTI_FILE 走文本流，VUE_PROJECT 走 JSON 事件流。
_Avoid_: 推送、长连接
