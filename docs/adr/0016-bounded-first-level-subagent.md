# 子代理限制为一级、顺序和受控目录

主 Agent 可通过 `delegate_subagent` 工具声明任务目标与 Workspace 相对目录，把一个局部任务交给一级子代理。子代理只获得 read/write/edit/glob 文件工具，没有 shell、提问或再次委派能力；同一 App 使用独立锁保证同时最多一个子代理。执行受最大轮数和超时限制，主 Agent 只接收截断后的最终摘要。

## Status

Accepted（2026-10-31）。

## Consequences

- 子代理根目录必须位于 App Workspace 内，绝对路径、`..` 和解析后越界路径均拒绝。
- 开始、完成和失败通过 `subagent_started/finished/failed` External Event 展示。
- 子代理失败或超时作为工具失败返回主流程，不直接终止整个 Turn。
- 默认由 `SUBAGENT_ENABLED=false` 关闭；并发写入和嵌套委派不在当前能力范围内。
