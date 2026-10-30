# 08: 上下文自动压缩

**What to build:** 超长会话在达到模型上下文预算时自动压缩，旧消息转为非 active，系统追加隐藏的 Compaction Summary；用户能看到压缩状态，并能继续完成当前目标。

**Blocked by:** 7

**Status:** ready-for-agent

- [ ] 使用配置的模型窗口预算和 provider usage 判断触发时机，保留最近的完整 Turn，不切断工具调用与结果。
- [ ] 压缩成功后旧消息只标记为非 active，不物理删除；追加一条隐藏且 active 的 Compaction Summary。
- [ ] 后续请求只投影摘要与剩余 active 消息，不重复总结已覆盖内容。
- [ ] SSE 能向用户展示压缩开始和完成事件，压缩失败时能安全回退或明确报错。
- [ ] 测试覆盖首次压缩、二次更新摘要、工具配对保护、恢复后继续生成和预算边界。
