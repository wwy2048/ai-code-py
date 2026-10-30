from typing import Any


def tool_use_block(
    *,
    tool_call_id: str,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """构造模型协议中的单个 tool_use 块。"""
    return {"id": tool_call_id, "name": name, "arguments": arguments}


def assistant_tool_calls_message(
    tool_calls: list[dict[str, Any]],
    *,
    content: str,
) -> dict[str, Any]:
    """构造模型协议中的 assistant 工具调用消息。"""
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": tool_calls,
    }


def tool_result_message(
    *,
    tool_call_id: str,
    name: str | None,
    content: str,
    is_error: bool,
) -> dict[str, Any]:
    """构造模型协议中的工具结果消息。"""
    return {
        "role": "tool_result",
        "tool_call_id": tool_call_id,
        "name": name,
        "content": content,
        "is_error": is_error,
    }
