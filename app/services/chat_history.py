from datetime import datetime
from math import ceil

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import BusinessError
from app.models import ChatHistory, ModelCallLog
from app.redaction import sanitize_sensitive_text
from app.response import ErrorCode
from app.schemas import ChatHistoryQueryRequest
from app.services.apps import require_owned_app


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
                "callKind": log.call_kind,
                "modelName": log.model_name,
                "status": log.status,
                "durationMs": log.duration_ms,
                "inputTokens": log.input_tokens,
                "outputTokens": log.output_tokens,
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
    await require_owned_app(session, app_id, user_id)
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
    return {
        "records": [serialize_chat_history(record) for record in records],
        "pageNumber": 1,
        "pageSize": page_size,
        "totalPage": (total + page_size - 1) // page_size if total else 0,
        "totalRow": total,
        "optimizeCountQuery": False,
    }
