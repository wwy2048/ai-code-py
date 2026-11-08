from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from conftest import AppContext

from app.models import ModelCallLog, User


async def _register_and_login(
    app_context: AppContext,
    account: str,
    *,
    role: str = "user",
) -> int:
    response = await app_context.client.post(
        "/api/user/register",
        json={
            "userAccount": account,
            "userPassword": "password123",
            "checkPassword": "password123",
        },
    )
    user_id: int = response.json()["data"]
    if role != "user":
        async with app_context.session_factory() as session:
            user = await session.get(User, user_id)
            assert user is not None
            user.user_role = role
            await session.commit()
    login = await app_context.client.post(
        "/api/user/login",
        json={"userAccount": account, "userPassword": "password123"},
    )
    assert login.json()["code"] == 0
    return user_id


async def _seed_consumption_logs(
    app_context: AppContext,
    *,
    owner_id: int,
    first_app_id: int,
    second_app_id: int,
    other_user_id: int,
    other_app_id: int,
) -> None:
    now = datetime.now()
    async with app_context.session_factory() as session:
        session.add_all(
            [
                ModelCallLog(
                    app_id=first_app_id,
                    user_id=owner_id,
                    turn_id="consumption-1",
                    model_name="test-model",
                    call_kind="generation",
                    status="success",
                    duration_ms=100,
                    input_tokens=100,
                    cached_input_tokens=10,
                    output_tokens=20,
                    cost_cny=Decimal("0.00010000"),
                    create_time=now - timedelta(minutes=3),
                ),
                ModelCallLog(
                    app_id=first_app_id,
                    user_id=owner_id,
                    turn_id="consumption-2",
                    model_name="test-model",
                    call_kind="generation",
                    status="success",
                    duration_ms=120,
                    input_tokens=200,
                    cached_input_tokens=20,
                    output_tokens=30,
                    cost_cny=Decimal("0.00020000"),
                    create_time=now - timedelta(minutes=2),
                ),
                ModelCallLog(
                    app_id=second_app_id,
                    user_id=owner_id,
                    turn_id="consumption-3",
                    model_name="test-model",
                    call_kind="generation",
                    status="success",
                    duration_ms=140,
                    input_tokens=300,
                    cached_input_tokens=30,
                    output_tokens=40,
                    cost_cny=Decimal("0.00030000"),
                    create_time=now - timedelta(minutes=1),
                ),
                ModelCallLog(
                    app_id=other_app_id,
                    user_id=other_user_id,
                    turn_id="consumption-4",
                    model_name="test-model",
                    call_kind="compaction",
                    status="success",
                    duration_ms=160,
                    input_tokens=400,
                    cached_input_tokens=40,
                    output_tokens=50,
                    cost_cny=Decimal("0.00100000"),
                    create_time=now,
                ),
            ]
        )
        await session.commit()


async def test_admin_can_filter_and_aggregate_model_call_consumption(
    app_context: AppContext,
) -> None:
    owner_id = await _register_and_login(app_context, "consumption-owner")
    first_app = await app_context.client.post(
        "/api/app/create",
        json={"initPrompt": "消费应用一"},
    )
    second_app = await app_context.client.post(
        "/api/app/create",
        json={"initPrompt": "消费应用二"},
    )
    first_app_id = first_app.json()["data"]
    second_app_id = second_app.json()["data"]
    await app_context.client.post("/api/user/logout")

    other_user_id = await _register_and_login(app_context, "consumption-other")
    other_app = await app_context.client.post(
        "/api/app/create",
        json={"initPrompt": "其他用户应用"},
    )
    other_app_id = other_app.json()["data"]
    await app_context.client.post("/api/user/logout")

    await _register_and_login(app_context, "consumption-admin", role="admin")
    await _seed_consumption_logs(
        app_context,
        owner_id=owner_id,
        first_app_id=first_app_id,
        second_app_id=second_app_id,
        other_user_id=other_user_id,
        other_app_id=other_app_id,
    )

    response = await app_context.client.post(
        "/api/modelCall/admin/consumption/page",
        json={"pageNum": 1, "pageSize": 10, "userId": owner_id},
    )
    data = response.json()["data"]
    summary = data["summary"]

    assert response.json()["code"] == 0
    assert summary["totalCostCny"] == pytest.approx(0.0006)
    assert summary["totalCalls"] == 3
    assert summary["totalInputTokens"] == 600
    assert summary["totalCachedInputTokens"] == 60
    assert summary["totalOutputTokens"] == 90
    assert data["totalRow"] == 2

    records_by_app = {record["appId"]: record for record in data["records"]}
    first_record = records_by_app[first_app_id]
    assert first_record["userId"] == owner_id
    assert first_record["userAccount"] == "consumption-owner"
    assert first_record["callCount"] == 2
    assert first_record["inputTokens"] == 300
    assert first_record["costCny"] == pytest.approx(0.0003)
    assert first_record["lastCallTime"] is not None

    other_response = await app_context.client.post(
        "/api/modelCall/admin/consumption/page",
        json={"pageNum": 1, "pageSize": 10, "appId": other_app_id},
    )
    other_data = other_response.json()["data"]
    assert other_data["summary"]["totalCostCny"] == pytest.approx(0.001)
    assert other_data["summary"]["totalCalls"] == 1
    assert other_data["totalRow"] == 1
    assert other_data["records"][0]["appId"] == other_app_id
    assert other_data["records"][0]["userAccount"] == "consumption-other"


async def test_normal_user_cannot_read_admin_consumption(app_context: AppContext) -> None:
    await _register_and_login(app_context, "consumption-normal")

    response = await app_context.client.post(
        "/api/modelCall/admin/consumption/page",
        json={"pageNum": 1},
    )

    assert response.json()["code"] == 40101
