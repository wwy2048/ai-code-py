# 03: HTML 生成确定性闭环

**What to build:** 用户能够走完一次完整的 HTML 生成：提交需求后看到 Agent 文本增量与工具进度，文件写入 Workspace，完成后页面立即在预览区显示。此切片使用脚本化模型替身，先证明 Session、Agent Runtime、SSE 和前端之间的完整路径可靠。

**Blocked by:** 2

**Status:** ready-for-agent

- [ ] `chat_history` 增量迁移完成后可保存 user、assistant、tool_call、tool_result 等运行时消息，并通过 visible / active 区分展示与上下文用途。
- [ ] 用户提交带 requestId 的生成请求，服务端返回结构化 SSE，至少覆盖回合开始、文本增量、工具开始、工具结束、回合完成和错误。
- [ ] Agent Runtime 能完成多轮工具循环，受限文件工具只能读写当前 App 的 Workspace，越界与符号链接逃逸被拒绝。
- [ ] 脚本化模型能够生成可加载的 HTML 页面，前端显示工具进度并自动刷新预览。
- [ ] 同一 requestId 不会重复创建 Turn；客户端断开后生成继续，重连可按事件序号续传。
- [ ] 外部行为测试覆盖文件落盘、SSE 事件顺序、Session 持久化和路径越界。
