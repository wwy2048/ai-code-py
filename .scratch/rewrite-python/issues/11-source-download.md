# 11: 源码下载

**What to build:** 用户可以下载自己的 App 当前 Workspace 为 ZIP，在本地继续开发；三种 codeGenType 的下载内容都保留实际项目结构。

**Blocked by:** 6

**Status:** ready-for-agent

- [ ] 只有 App 所有者可以下载，越权请求被拒绝。
- [ ] ZIP 文件名安全、响应头正确，下载内容不包含 Workspace 外部文件。
- [ ] HTML、MULTI_FILE 与 VUE_PROJECT 均能下载并解压出可继续使用的项目。
- [ ] 空 Workspace、文件清理中和路径异常返回统一错误信封。
- [ ] API seam 测试覆盖权限、内容完整性和路径逃逸。
