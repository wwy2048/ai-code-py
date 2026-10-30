# 07: 会话恢复与历史分页

**What to build:** 用户重新打开 App 时能看到按页加载的可见对话历史，继续提出修改需求，Agent 能恢复此前的活动上下文而不是从头开始。

**Blocked by:** 6

**Status:** ready-for-agent

- [ ] 普通用户只能读取自己 App 的 visible 消息；隐藏 Tool 消息与 Compaction Summary 不进入对话历史接口。
- [ ] 分页按稳定时间顺序返回可见消息，前端滚动加载不会重复或跳过记录。
- [ ] 新 Turn 的模型上下文只包含当前 App 的 active 消息，并保持 assistant tool_use 与 tool_result 的配对。
- [ ] 用户重新打开 App 后可以继续修改已有 HTML、MULTI_FILE 和 Vue 项目。
- [ ] 测试覆盖跨登录恢复、分页边界、所有权校验和隐藏消息隔离。
