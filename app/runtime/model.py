from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


TextEmitter = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class ModelReply:
    text: str = ''
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    model_name: str = ''
    retry_count: int = 0


class ModelCallStatus(StrEnum):
    SUCCESS = 'success'
    ERROR = 'error'


class ModelCallError(RuntimeError):
    def __init__(self, summary: str, retry_count: int) -> None:
        super().__init__(summary)
        self.summary = summary
        self.retry_count = retry_count


class AgentModel(Protocol):
    async def next_turn(
        self,
        messages: list[dict[str, Any]],
        on_text: TextEmitter,
    ) -> ModelReply: ...


class ScriptedHtmlModel:
    """Ticket 03 使用的确定性模型，产出单页 HTML。"""

    async def next_turn(
        self,
        messages: list[dict[str, Any]],
        on_text: TextEmitter,
    ) -> ModelReply:
        has_tool_result = any(message.get('role') == 'tool_result' for message in messages)
        if has_tool_result:
            text = '页面已生成，可以直接在右侧预览。'
            await on_text(text)
            return ModelReply(text=text)

        prompt = str(messages[0].get('content', ''))
        escaped_prompt = (
            prompt.replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;')
        )
        html = f'''<!doctype html>
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
</html>'''
        text = '我将创建 index.html。'
        await on_text(text)
        return ModelReply(
            text=text,
            tool_calls=[
                ToolCall(
                    id='tool-write-index',
                    name='write_file',
                    arguments={'path': 'index.html', 'content': html},
                )
            ],
        )


class UnavailableAgentModel:
    """未配置模型密钥时给出可读失败，而不是静默伪造生成结果。"""

    model_name = 'unavailable'

    async def next_turn(
        self,
        messages: list[dict[str, Any]],
        on_text: TextEmitter,
    ) -> ModelReply:
        raise RuntimeError('未配置 ANTHROPIC_API_KEY，无法调用生成模型')
