from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class ToolCall:
    """模型请求执行的一次工具调用。"""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class UserMessage:
    """进入模型上下文的用户消息。"""

    content: str
    role: Literal["user"] = field(default="user", init=False)


@dataclass(frozen=True, slots=True)
class AssistantMessage:
    """带可选工具调用的助手消息。"""

    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    role: Literal["assistant"] = field(default="assistant", init=False)


@dataclass(frozen=True, slots=True)
class ToolResultMessage:
    """回填给模型的工具结果。"""

    tool_call_id: str
    name: str | None
    content: str
    is_error: bool = False
    role: Literal["tool_result"] = field(default="tool_result", init=False)


type AgentMessage = UserMessage | AssistantMessage | ToolResultMessage


def message_to_dict(message: AgentMessage) -> dict[str, Any]:
    """把类型化运行时消息转换成调试或压缩使用的普通字典。"""
    if isinstance(message, UserMessage):
        return {"role": "user", "content": message.content}
    if isinstance(message, AssistantMessage):
        return {
            "role": "assistant",
            "content": message.content,
            "tool_calls": [
                {"id": call.id, "name": call.name, "arguments": call.arguments}
                for call in message.tool_calls
            ],
        }
    return {
        "role": "tool_result",
        "tool_call_id": message.tool_call_id,
        "name": message.name,
        "content": message.content,
        "is_error": message.is_error,
    }


def messages_to_dicts(
    messages: tuple[AgentMessage, ...] | list[AgentMessage],
) -> list[dict[str, Any]]:
    """批量转换消息，供调试日志和摘要提示词使用。"""
    return [message_to_dict(message) for message in messages]
