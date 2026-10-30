# 模型端点改用 DeepSeek（Anthropic 兼容协议）

原 ai-code 通过 LangChain4j + DashScope 调用 qwen3-max（OpenAI 兼容协议）。新项目的 agent 运行时按 Anthropic Messages API 设计，决定模型直连 DeepSeek 的 anthropic 兼容端点（`https://api.deepseek.com/anthropic`），不再使用 qwen3-max / DashScope。

原因：运行时协议原生、零中继组件、minicodex 已验证该端点实际可用。代价：模型从 qwen3-max 切换为 DeepSeek，生成效果需重新评估。否决的选项：qwen3-max + one-api/new-api 中继（多一层运维组件，保留为将来可选）。
