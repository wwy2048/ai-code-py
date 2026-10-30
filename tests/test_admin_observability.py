from datetime import datetime, timedelta

from conftest import AppContext

from app.models import ChatHistory, ModelCallLog, User
from app.redaction import sanitize_sensitive_text


async def _register_login_and_create_app(
    app_context: AppContext,
    account: str,
) -> tuple[int, int]:
    register = await app_context.client.post(
        "/api/user/register",
        json={
            "userAccount": account,
            "userPassword": "password123",
            "checkPassword": "password123",
        },
    )
    user_id: int = register.json()["data"]
    await app_context.client.post(
        "/api/user/login",
        json={"userAccount": account, "userPassword": "password123"},
    )
    app_id: int = (
        await app_context.client.post(
            "/api/app/create",
            json={"initPrompt": "创建观测页面"},
        )
    ).json()["data"]
    return user_id, app_id


async def _promote_admin(app_context: AppContext, user_id: int) -> None:
    async with app_context.session_factory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        user.user_role = "admin"
        await session.commit()


async def _seed_debug_turn(app_context: AppContext, *, user_id: int, app_id: int) -> None:
    now = datetime.now()
    async with app_context.session_factory() as session:
        session.add_all(
            [
                ChatHistory(
                    message="用户需求",
                    message_type="user",
                    app_id=app_id,
                    user_id=user_id,
                    turn_id="turn-debug",
                    sequence=1,
                    message_kind="user",
                    visible=True,
                    active=True,
                    create_time=now - timedelta(seconds=5),
                ),
                ChatHistory(
                    message='{"content":"完整源码不得返回"}',
                    message_type="tool",
                    app_id=app_id,
                    user_id=user_id,
                    turn_id="turn-debug",
                    sequence=2,
                    message_kind="tool_call",
                    visible=False,
                    active=True,
                    tool_call_id="call-debug",
                    tool_name="write_file",
                    payload={
                        "arguments": {"path": "index.html", "content": "完整源码不得返回"},
                        "batchId": "turn-debug:1",
                        "assistantText": "开始写入",
                    },
                    create_time=now - timedelta(seconds=4),
                ),
                ChatHistory(
                    message="X" * 2000,
                    message_type="tool",
                    app_id=app_id,
                    user_id=user_id,
                    turn_id="turn-debug",
                    sequence=3,
                    message_kind="tool_result",
                    visible=False,
                    active=True,
                    tool_call_id="call-debug",
                    tool_name="write_file",
                    is_error=False,
                    payload={"sizeBytes": 2000, "durationMs": 12, "output": "X" * 2000},
                    create_time=now - timedelta(seconds=3),
                ),
                ChatHistory(
                    message="压缩摘要正文",
                    message_type="ai",
                    app_id=app_id,
                    user_id=user_id,
                    turn_id="turn-debug",
                    sequence=4,
                    message_kind="compaction",
                    visible=False,
                    active=True,
                    create_time=now - timedelta(seconds=2),
                ),
                ChatHistory(
                    message="已完成",
                    message_type="ai",
                    app_id=app_id,
                    user_id=user_id,
                    turn_id="turn-debug",
                    sequence=5,
                    message_kind="assistant",
                    visible=True,
                    active=True,
                    create_time=now - timedelta(seconds=1),
                ),
                ModelCallLog(
                    app_id=app_id,
                    turn_id="turn-debug",
                    model_name="test-model",
                    call_kind="generation",
                    status="error",
                    duration_ms=123,
                    input_tokens=100,
                    output_tokens=20,
                    error_type="ModelCallError",
                    error_summary="api_key=sk-live-secret Bearer abcdef",
                    retry_count=1,
                ),
            ]
        )
        await session.commit()


async def test_normal_user_cannot_read_admin_observability(app_context: AppContext) -> None:
    user_id, app_id = await _register_login_and_create_app(app_context, "observe-normal")
    await _seed_debug_turn(app_context, user_id=user_id, app_id=app_id)

    listing = await app_context.client.post(
        "/api/chatHistory/admin/list/page/vo",
        json={"pageNum": 1},
    )
    detail = await app_context.client.get(f"/api/chatHistory/admin/turn/{app_id}/turn-debug")

    assert listing.json()["code"] == 40101
    assert detail.json()["code"] == 40101


async def test_admin_history_filters_and_paginates_all_visibility(
    app_context: AppContext,
) -> None:
    user_id, app_id = await _register_login_and_create_app(app_context, "observe-admin")
    await _seed_debug_turn(app_context, user_id=user_id, app_id=app_id)
    await _promote_admin(app_context, user_id)

    filtered = await app_context.client.post(
        "/api/chatHistory/admin/list/page/vo",
        json={
            "pageNum": 1,
            "pageSize": 2,
            "appId": app_id,
            "userId": user_id,
            "messageType": "tool",
        },
    )
    second_page = await app_context.client.post(
        "/api/chatHistory/admin/list/page/vo",
        json={
            "pageNum": 2,
            "pageSize": 2,
            "appId": app_id,
            "userId": user_id,
            "messageType": "tool",
        },
    )

    assert filtered.json()["data"]["totalRow"] == 2
    assert len(filtered.json()["data"]["records"]) == 2
    assert second_page.json()["data"]["records"] == []
    assert all(record["messageType"] == "tool" for record in filtered.json()["data"]["records"])
    assert all(
        "完整源码不得返回" not in record["preview"] for record in filtered.json()["data"]["records"]
    )


async def test_admin_turn_debug_exposes_hidden_metadata_without_sensitive_bodies(
    app_context: AppContext,
) -> None:
    user_id, app_id = await _register_login_and_create_app(app_context, "observe-detail")
    await _seed_debug_turn(app_context, user_id=user_id, app_id=app_id)
    await _promote_admin(app_context, user_id)

    response = await app_context.client.get(f"/api/chatHistory/admin/turn/{app_id}/turn-debug")
    data = response.json()["data"]

    assert response.json()["code"] == 0
    assert data["turnId"] == "turn-debug"
    assert {message["messageKind"] for message in data["messages"]} >= {
        "user",
        "tool_call",
        "tool_result",
        "compaction",
        "assistant",
    }
    tool_call = next(
        message for message in data["messages"] if message["messageKind"] == "tool_call"
    )
    tool_result = next(
        message for message in data["messages"] if message["messageKind"] == "tool_result"
    )
    assert tool_call["visible"] is False
    assert tool_call["toolInput"] == {"path": "index.html"}
    assert "完整源码不得返回" not in response.text
    assert len(tool_result["preview"]) <= 500
    assert tool_result["preview"] != "X" * 2000
    assert data["modelCalls"][0]["status"] == "error"
    assert data["modelCalls"][0]["durationMs"] == 123
    assert "sk-live-secret" not in response.text
    assert "Bearer abcdef" not in response.text


def test_sensitive_values_are_removed_before_model_log_persistence() -> None:
    sanitized = sanitize_sensitive_text(
        'api_key="sk-live-secret" token: abc123 password=hello '
        "Bearer bearer-secret https://user:pass@example.com"
    )

    assert sanitized is not None
    for secret in ("sk-live-secret", "abc123", "hello", "bearer-secret", "user:pass"):
        assert secret not in sanitized
