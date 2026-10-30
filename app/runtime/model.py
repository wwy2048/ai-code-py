from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ModelReply:
    text: str = ''
    tool_calls: list[ToolCall] = field(default_factory=list)


class AgentModel(Protocol):
    async def next_turn(self, messages: list[dict[str, Any]]) -> ModelReply: ...


class ScriptedHtmlModel:
    """Ticket 03 使用的确定性模型，产出单页 HTML。"""

    async def next_turn(self, messages: list[dict[str, Any]]) -> ModelReply:
        has_tool_result = any(message.get('role') == 'tool_result' for message in messages)
        if has_tool_result:
            return ModelReply(text='页面已生成，可以直接在右侧预览。')

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
        return ModelReply(
            text='我将创建 index.html。',
            tool_calls=[
                ToolCall(
                    id='tool-write-index',
                    name='write_file',
                    arguments={'path': 'index.html', 'content': html},
                )
            ],
        )
