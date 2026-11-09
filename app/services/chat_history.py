from datetime import datetime
from math import ceil
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import BusinessError
from app.models import ChatHistory, ModelCallLog
from app.redaction import sanitize_sensitive_text
from app.response import ErrorCode
from app.schemas import ChatHistoryQueryRequest
from app.services.apps import require_viewable_app


def serialize_chat_history(record: ChatHistory) -> dict:
    return {
        "id": record.id,
        "message": record.message,
        "messageType": record.message_type,
        "appId": record.app_id,
        "userId": record.user_id,
        "createTime": record.create_time.isoformat(),
        "updateTime": record.update_time.isoformat(),
        "isDelete": record.is_delete,
        "turnId": record.turn_id,
        "sequence": record.sequence,
        "messageKind": record.message_kind,
        "visible": record.visible,
        "active": record.active,
        "toolName": record.tool_name,
        "isError": record.is_error,
    }


def _preview(text: str, limit: int = 500) -> str:
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _sanitize_error_summary(value: str | None) -> str | None:
    return sanitize_sensitive_text(value)


def _admin_record(record: ChatHistory) -> dict:
    if record.message_kind == "tool_call":
        preview = f"工具调用 {record.tool_name or ''}".strip()
    elif not record.visible:
        preview = sanitize_sensitive_text(record.message, limit=500) or ""
    else:
        preview = _preview(record.message, 2000)
    return {
        "id": record.id,
        "messageType": record.message_type,
        "appId": record.app_id,
        "userId": record.user_id,
        "createTime": record.create_time.isoformat(),
        "isDelete": record.is_delete,
        "turnId": record.turn_id,
        "sequence": record.sequence,
        "messageKind": record.message_kind,
        "visible": record.visible,
        "active": record.active,
        "toolName": record.tool_name,
        "isError": record.is_error,
        "preview": preview,
    }


def _allowed_tool_input(payload: dict | None) -> dict[str, object]:
    if not isinstance(payload, dict):
        return {}
    source = payload.get("input")
    if not isinstance(source, dict):
        source = payload.get("arguments")
    if not isinstance(source, dict):
        return {}
    return {name: source[name] for name in ("path", "pattern") if name in source}


def _tool_activity(call: ChatHistory, result: ChatHistory | None) -> dict:
    payload = call.payload or {}
    result_payload = result.payload if result is not None else {}
    status = (
        "failed"
        if result is not None and result.is_error
        else "completed"
        if result is not None
        else "interrupted"
    )
    return {
        "toolCallId": call.tool_call_id,
        "toolName": call.tool_name,
        "batchId": payload.get("batchId"),
        "status": status,
        "input": _allowed_tool_input(result_payload) or _allowed_tool_input(payload),
        "outputPreview": (
            sanitize_sensitive_text(result.message, limit=500) if result is not None else ""
        ),
        "durationMs": (result_payload or {}).get("durationMs"),
        "sizeBytes": (result_payload or {}).get("sizeBytes"),
        "isError": bool(result.is_error) if result is not None else False,
        "sequence": call.sequence,
    }


def build_turn_parts(records: list[ChatHistory]) -> dict[str, list[dict]]:
    """按可见文本边界投影展示 parts；连续工具调用合并为一个折叠组。"""
    results_by_key: dict[tuple[str, str], ChatHistory] = {}
    assistant_batches: set[tuple[str, str]] = set()

    for record in records:
        turn_id = record.turn_id
        if not turn_id:
            continue
        if record.message_kind == "tool_result" and record.tool_call_id:
            results_by_key[(turn_id, record.tool_call_id)] = record
        elif record.message_kind == "assistant" and record.visible and record.message.strip():
            batch_id = (record.payload or {}).get("batchId")
            if isinstance(batch_id, str) and batch_id:
                assistant_batches.add((turn_id, batch_id))

    parts_by_turn: dict[str, list[dict]] = {}
    current_group: dict[str, dict[str, Any]] = {}
    emitted_legacy_batches: set[tuple[str, str]] = set()

    for record in records:
        turn_id = record.turn_id
        if not turn_id:
            continue
        if record.message_kind == "assistant" and record.visible and record.message.strip():
            parts_by_turn.setdefault(turn_id, []).append(
                {
                    "type": "assistant_text",
                    "batchId": (record.payload or {}).get("batchId"),
                    "sequence": record.sequence,
                    "content": record.message,
                }
            )
            # 只有真正展示给用户的文本才会结束当前工具折叠组。
            current_group.pop(turn_id, None)
            continue
        if record.message_kind != "tool_call" or not record.tool_call_id:
            continue

        raw_batch_id = (record.payload or {}).get("batchId")
        batch_id = raw_batch_id if isinstance(raw_batch_id, str) else ""
        batch_key = (turn_id, batch_id or f"legacy:{record.tool_call_id}")

        # 兼容旧数据：旧版本把该轮 assistant 文本塞在 tool_call.payload 中。
        if batch_key not in assistant_batches and batch_key not in emitted_legacy_batches:
            emitted_legacy_batches.add(batch_key)
            legacy_text = str((record.payload or {}).get("assistantText") or "").strip()
            if legacy_text:
                parts_by_turn.setdefault(turn_id, []).append(
                    {
                        "type": "assistant_text",
                        "batchId": batch_id or None,
                        "sequence": record.sequence,
                        "content": legacy_text,
                    }
                )
                current_group.pop(turn_id, None)

        group = current_group.get(turn_id)
        if group is None:
            group_id = f"{turn_id}:tools:{record.sequence or record.tool_call_id}"
            group = {
                "type": "tool_group",
                "groupId": group_id,
                "sequence": record.sequence,
                "tools": [],
            }
            parts_by_turn.setdefault(turn_id, []).append(group)
            current_group[turn_id] = group
        group["tools"].append(
            _tool_activity(
                record,
                results_by_key.get((turn_id, record.tool_call_id)),
            )
        )

    for parts in parts_by_turn.values():
        parts.sort(
            key=lambda part: (
                part.get("sequence") or 0,
                0 if part.get("type") == "assistant_text" else 1,
            )
        )
    return parts_by_turn


def _tool_activities(parts_by_turn: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """保留旧接口的扁平工具活动字段，供兼容调用方使用。"""
    return {
        turn_id: [
            activity
            for part in parts
            if part.get("type") == "tool_group"
            for activity in part.get("tools", [])
        ]
        for turn_id, parts in parts_by_turn.items()
    }


async def list_all_history(
    session: AsyncSession,
    query: ChatHistoryQueryRequest,
) -> dict:
    conditions = [ChatHistory.is_delete == 0]
    if query.id is not None:
        conditions.append(ChatHistory.id == query.id)
    if query.app_id is not None:
        conditions.append(ChatHistory.app_id == query.app_id)
    if query.user_id is not None:
        conditions.append(ChatHistory.user_id == query.user_id)
    if query.message_type:
        conditions.append(ChatHistory.message_type == query.message_type)
    if query.message:
        conditions.append(ChatHistory.message.like(f"%{query.message}%"))
    if query.start_time is not None:
        conditions.append(ChatHistory.create_time >= query.start_time)
    if query.end_time is not None:
        conditions.append(ChatHistory.create_time <= query.end_time)
    total = await session.scalar(select(func.count(ChatHistory.id)).where(*conditions)) or 0
    records = (
        await session.scalars(
            select(ChatHistory)
            .where(*conditions)
            .order_by(ChatHistory.create_time.desc(), ChatHistory.id.desc())
            .offset((query.page_num - 1) * query.page_size)
            .limit(query.page_size)
        )
    ).all()
    return {
        "records": [_admin_record(record) for record in records],
        "pageNumber": query.page_num,
        "pageSize": query.page_size,
        "totalPage": ceil(total / query.page_size) if total else 0,
        "totalRow": total,
        "optimizeCountQuery": False,
    }


async def get_turn_debug(
    session: AsyncSession,
    *,
    app_id: int,
    turn_id: str,
) -> dict:
    records = (
        await session.scalars(
            select(ChatHistory)
            .where(
                ChatHistory.app_id == app_id,
                ChatHistory.turn_id == turn_id,
                ChatHistory.is_delete == 0,
            )
            .order_by(ChatHistory.sequence.asc(), ChatHistory.id.asc())
        )
    ).all()
    if not records:
        raise BusinessError(ErrorCode.NOT_FOUND, "Turn 不存在")
    logs = (
        await session.scalars(
            select(ModelCallLog)
            .where(ModelCallLog.app_id == app_id, ModelCallLog.turn_id == turn_id)
            .order_by(ModelCallLog.id.asc())
        )
    ).all()
    messages = []
    for record in records:
        payload = record.payload or {}
        allowed_input = {
            key: payload.get("input", {}).get(key)
            for key in ("path", "pattern")
            if isinstance(payload.get("input"), dict) and key in payload["input"]
        }
        arguments = payload.get("arguments")
        if isinstance(arguments, dict):
            allowed_input.update(
                {key: arguments[key] for key in ("path", "pattern") if key in arguments}
            )
        if record.message_kind == "tool_call":
            preview = f"工具调用 {record.tool_name or ''}".strip()
        elif record.message_kind == "tool_result":
            preview = sanitize_sensitive_text(record.message, limit=500) or ""
        elif record.message_kind == "compaction":
            preview = sanitize_sensitive_text(record.message, limit=500) or ""
        else:
            preview = (
                _preview(record.message, 2000)
                if record.visible
                else sanitize_sensitive_text(record.message, limit=500) or ""
            )
        messages.append(
            {
                "id": record.id,
                "sequence": record.sequence,
                "messageKind": record.message_kind,
                "messageType": record.message_type,
                "visible": record.visible,
                "active": record.active,
                "isError": record.is_error,
                "toolCallId": record.tool_call_id,
                "toolName": record.tool_name,
                "toolInput": allowed_input,
                "durationMs": payload.get("durationMs"),
                "sizeBytes": payload.get("sizeBytes"),
                "preview": preview,
                "createTime": record.create_time.isoformat(),
            }
        )
    return {
        "appId": app_id,
        "turnId": turn_id,
        "messages": messages,
        "modelCalls": [
            {
                "id": log.id,
                "userId": log.user_id,
                "callKind": log.call_kind,
                "modelName": log.model_name,
                "status": log.status,
                "durationMs": log.duration_ms,
                "inputTokens": log.input_tokens,
                "cachedInputTokens": log.cached_input_tokens,
                "outputTokens": log.output_tokens,
                "costCny": float(log.cost_cny),
                "errorType": log.error_type,
                "errorSummary": _sanitize_error_summary(log.error_summary),
                "retryCount": log.retry_count,
                "createTime": log.create_time.isoformat(),
            }
            for log in logs
        ],
    }


async def list_visible_history(
    session: AsyncSession,
    *,
    app_id: int,
    user_id: int,
    page_size: int,
    last_create_time: datetime | None,
    last_id: int | None,
) -> dict:
    await require_viewable_app(session, app_id, user_id)
    if page_size < 1 or page_size > 20:
        raise BusinessError(ErrorCode.PARAMS_ERROR, "pageSize 必须在 1 到 20 之间")

    conditions = [
        ChatHistory.app_id == app_id,
        ChatHistory.is_delete == 0,
        ChatHistory.visible.is_(True),
    ]
    if last_id is not None and last_create_time is None:
        raise BusinessError(ErrorCode.PARAMS_ERROR, "lastId 必须与 lastCreateTime 同时使用")
    if last_create_time is not None:
        if last_id is None:
            conditions.append(ChatHistory.create_time < last_create_time)
        else:
            conditions.append(
                or_(
                    ChatHistory.create_time < last_create_time,
                    and_(
                        ChatHistory.create_time == last_create_time,
                        ChatHistory.id < last_id,
                    ),
                )
            )
    total = await session.scalar(select(func.count(ChatHistory.id)).where(*conditions)) or 0
    records = (
        await session.scalars(
            select(ChatHistory)
            .where(*conditions)
            .order_by(ChatHistory.create_time.desc(), ChatHistory.id.desc())
            .limit(page_size)
        )
    ).all()
    turn_ids = {record.turn_id for record in records if record.turn_id}
    timeline_rows = (
        list(
            (
                await session.scalars(
                    select(ChatHistory)
                    .where(
                        ChatHistory.app_id == app_id,
                        ChatHistory.is_delete == 0,
                        ChatHistory.turn_id.in_(turn_ids),
                    )
                    .order_by(
                        ChatHistory.sequence.asc(),
                        ChatHistory.create_time.asc(),
                        ChatHistory.id.asc(),
                    )
                )
            ).all()
        )
        if turn_ids
        else []
    )
    parts_by_turn = build_turn_parts(timeline_rows)
    return {
        "records": [serialize_chat_history(record) for record in records],
        "partsByTurn": parts_by_turn,
        # 兼容旧前端/调试脚本，实际展示以 partsByTurn 的顺序为准。
        "activitiesByTurn": _tool_activities(parts_by_turn),
        "pageNumber": 1,
        "pageSize": page_size,
        "totalPage": (total + page_size - 1) // page_size if total else 0,
        "totalRow": total,
        "optimizeCountQuery": False,
    }
