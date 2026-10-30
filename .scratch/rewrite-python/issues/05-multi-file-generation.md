# 05: MULTI_FILE 真模型生成

**What to build:** 用户选择或被路由到 MULTI_FILE 后，Agent 能生成分离的 HTML、CSS 和 JavaScript 文件，实时预览完整页面，并在后续修改中保持文件结构一致。

**Blocked by:** 4

**Status:** ready-for-agent

- [ ] MULTI_FILE 使用独立提示词和文件约束，只允许约定范围内的静态文件与目录。
- [ ] 生成过程继续使用统一的结构化 SSE、Tool 事件和 Session 持久化，不为该类型复制一套运行时。
- [ ] 最终 Workspace 至少包含可启动的 HTML 入口和必要的 CSS/JS，浏览器预览无缺失资源。
- [ ] 修改需求会更新现有文件而不是无条件覆盖整个 Workspace。
- [ ] 脚本化测试和真实模型冒烟均覆盖 MULTI_FILE 完整生成与预览。
