from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import Base
from app.db import create_engine as create_db_engine
from app.generation.adapters.conversation_store import ConversationStore
from app.generation.ports.model import ModelCallStatus
from app.models import ModelCallLog


async def test_model_call_persists_cached_tokens_and_computed_cost(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.model_billing.is_peak_time", lambda at=None: False)
    engine = create_db_engine(f"sqlite+aiosqlite:///{(tmp_path / 'cost.db').as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            store = ConversationStore(session, app_id=1, user_id=1, turn_id="cost-turn")
            await store.append_model_call(
                status=ModelCallStatus.SUCCESS,
                model_name="deepseek-flash",
                duration_ms=12,
                input_tokens=1_000_000,
                cached_input_tokens=200_000,
                output_tokens=100_000,
            )
            await store.commit()
        async with session_factory() as session:
            log = await session.scalar(
                select(ModelCallLog).where(ModelCallLog.turn_id == "cost-turn")
            )
    finally:
        await engine.dispose()

    assert log is not None
    assert log.user_id == 1
    assert log.cached_input_tokens == 200_000
    assert log.cost_cny == Decimal("1.20400000")
