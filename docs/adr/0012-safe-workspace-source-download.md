# Workspace 源码安全打包下载

源码下载接口只允许 App 所有者访问，从 `workspace_root/{codeGenType}_{appId}` 递归打包为 ZIP。打包不跟随符号链接或 Windows junction，所有条目使用 Workspace 相对路径；`node_modules`、构建产物、编辑器目录、日志和临时文件不会进入压缩包。文件名固定为 ASCII 的 `app-{appId}.zip`。

## Status

Accepted（2026-10-31）。

## Consequences

- 三种生成类型下载后都保留可继续开发的相对项目结构。
- 空目录、缺失目录、链接、特殊文件、文件数量超限或总体积超限返回统一业务错误信封。
- 打包在工作线程中执行，避免大项目压缩阻塞事件循环；内存中最多允许 100 MiB 未压缩内容。
- 下载失败在响应前完成判定，不会返回半截 ZIP。
