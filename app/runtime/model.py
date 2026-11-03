from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from app.runtime.messages import AgentMessage, ToolCall, ToolResultMessage, UserMessage

TextEmitter = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class ModelRequest:
    """provider-neutral 的一次模型调用请求。"""

    messages: tuple[AgentMessage, ...]
    tools: tuple[dict[str, Any], ...] = ()
    model_name: str = ""


@dataclass(frozen=True)
class ModelReply:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    model_name: str = ""
    retry_count: int = 0
    stop_reason: str | None = None


@dataclass(frozen=True)
class SummaryResult:
    text: str
    model_name: str
    input_tokens: int = 0
    output_tokens: int = 0
    retry_count: int = 0


class ModelCallStatus(StrEnum):
    SUCCESS = "success"
    ERROR = "error"


class ModelCallError(RuntimeError):
    def __init__(self, summary: str, retry_count: int) -> None:
        super().__init__(summary)
        self.summary = summary
        self.retry_count = retry_count


class AgentModel(Protocol):
    async def next_turn(
        self,
        request: ModelRequest,
        on_text: TextEmitter,
    ) -> ModelReply: ...


@runtime_checkable
class ContextSummarizer(Protocol):
    @property
    def model_name(self) -> str: ...

    async def summarize(
        self,
        *,
        previous_summary: str | None,
        transcript: str,
    ) -> SummaryResult: ...


class ScriptedHtmlModel:
    """确定性 HTML 模型，用于外部行为测试。"""

    async def next_turn(
        self,
        request: ModelRequest,
        on_text: TextEmitter,
    ) -> ModelReply:
        has_tool_result = bool(
            request.messages and isinstance(request.messages[-1], ToolResultMessage)
        )
        if has_tool_result:
            text = "页面已生成，可以直接在右侧预览。"
            await on_text(text)
            return ModelReply(text=text)

        prompt = next(
            message.content
            for message in reversed(request.messages)
            if isinstance(message, UserMessage)
        )
        escaped_prompt = (
            prompt.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )
        html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>AI 生成页面</title>
  <style>
    body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; }}
    body {{ font-family: sans-serif; color: #0f172a; }}
    body {{ background: linear-gradient(135deg, #e0f2fe, #dbeafe); }}
    main {{ max-width: 720px; padding: 48px; text-align: center; }}
    main {{ background: white; border-radius: 24px; }}
    main {{ box-shadow: 0 24px 80px rgba(15, 23, 42, .15); }}
  </style>
</head>
<body>
  <main>
    <h1>AI 生成页面</h1>
    <p>{escaped_prompt}</p>
  </main>
</body>
</html>"""
        text = "我将创建 index.html。"
        await on_text(text)
        return ModelReply(
            text=text,
            tool_calls=[
                ToolCall(
                    id="tool-write-index",
                    name="write_file",
                    arguments={"path": "index.html", "content": html},
                )
            ],
        )


class UnavailableAgentModel:
    """未配置模型密钥时给出可读失败，而不是静默伪造生成结果。"""

    model_name = "unavailable"

    async def next_turn(
        self,
        request: ModelRequest,
        on_text: TextEmitter,
    ) -> ModelReply:
        raise RuntimeError("未配置 ANTHROPIC_API_KEY，无法调用生成模型")


class ScriptedMultiFileModel:
    """确定性 MULTI_FILE 模型，用于外部行为测试。"""

    async def next_turn(
        self,
        request: ModelRequest,
        on_text: TextEmitter,
    ) -> ModelReply:
        has_tool_result = bool(
            request.messages and isinstance(request.messages[-1], ToolResultMessage)
        )
        if has_tool_result:
            text = "多文件页面已生成。"
            await on_text(text)
            return ModelReply(text=text)
        text = "我将创建 HTML、CSS 和 JavaScript 文件。"
        await on_text(text)
        return ModelReply(
            text=text,
            tool_calls=[
                ToolCall(
                    id="write-index",
                    name="write_file",
                    arguments={
                        "path": "index.html",
                        "content": (
                            "<!doctype html><html><head>"
                            '<link rel="stylesheet" href="style.css"></head>'
                            "<body><h1>Multi File</h1>"
                            '<script src="script.js"></script></body></html>'
                        ),
                    },
                ),
                ToolCall(
                    id="write-style",
                    name="write_file",
                    arguments={"path": "style.css", "content": "body { color: #1d4ed8; }"},
                ),
                ToolCall(
                    id="write-script",
                    name="write_file",
                    arguments={"path": "script.js", "content": 'console.log("ready");'},
                ),
            ],
        )


class ScriptedVueModel:
    async def next_turn(
        self,
        request: ModelRequest,
        on_text: TextEmitter,
    ) -> ModelReply:
        results = [
            message for message in request.messages if isinstance(message, ToolResultMessage)
        ]
        if any(
            isinstance(message, UserMessage) and "构建失败" in message.content
            for message in request.messages
        ):
            text = "根据构建错误修复组件。"
            await on_text(text)
            return ModelReply(
                text=text,
                tool_calls=[
                    ToolCall(
                        id="fix-vue",
                        name="write_file",
                        arguments={
                            "path": "src/App.vue",
                            "content": "<template><main><h1>Fixed Vue</h1></main></template>",
                        },
                    )
                ],
            )
        if any(message.name == "build_project" for message in results):
            text = "Vue 页面已构建。"
            await on_text(text)
            return ModelReply(text=text)
        text = "创建 Vue 页面。"
        await on_text(text)
        return ModelReply(
            text=text,
            tool_calls=[
                ToolCall(
                    id="write-vue",
                    name="write_file",
                    arguments={
                        "path": "src/App.vue",
                        "content": "<template><main><h1>Vue Generated</h1></main></template>",
                    },
                )
            ],
        )
