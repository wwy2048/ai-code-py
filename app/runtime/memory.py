from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppMemory


class MemoryStore:
    """按 App 隔离、按字符预算截断的持久记忆。"""

    def __init__(
        self,
        session: AsyncSession,
        *,
        app_id: int,
        user_id: int,
        max_chars: int,
    ) -> None:
        self._session = session
        self._app_id = app_id
        self._user_id = user_id
        self._max_chars = max(1, max_chars)

    async def save_from_turn(self, turn_id: str, user_message: str, assistant_message: str) -> None:
        total_limit = min(self._max_chars, 1000)
        prefix_chars = len("用户目标：") + len("\n处理结果：")
        part_limit = max(1, (total_limit - prefix_chars) // 2)
        content = (
            f"用户目标：{user_message.strip()[:part_limit]}"
            f"\n处理结果：{assistant_message.strip()[:part_limit]}"
        )[:total_limit]
        existing = await self._session.scalar(
            select(AppMemory.id).where(
                AppMemory.app_id == self._app_id,
                AppMemory.source_turn_id == turn_id,
            )
        )
        if existing is not None:
            return
        self._session.add(
            AppMemory(
                app_id=self._app_id,
                user_id=self._user_id,
                content=content,
                source_turn_id=turn_id,
            )
        )

    async def load_for_context(self) -> list[str]:
        records = (
            await self._session.scalars(
                select(AppMemory)
                .where(
                    AppMemory.app_id == self._app_id,
                    AppMemory.user_id == self._user_id,
                )
                .order_by(AppMemory.id.asc())
            )
        ).all()
        selected: list[str] = []
        used = 0
        for record in reversed(records):
            content = record.content[: self._max_chars]
            if used + len(content) > self._max_chars:
                continue
            selected.append(content)
            used += len(content)
        return list(reversed(selected))

    async def list_records(self) -> list[AppMemory]:
        return list(
            (
                await self._session.scalars(
                    select(AppMemory)
                    .where(
                        AppMemory.app_id == self._app_id,
                        AppMemory.user_id == self._user_id,
                    )
                    .order_by(AppMemory.id.desc())
                )
            ).all()
        )

    async def clear(self) -> None:
        await self._session.execute(
            delete(AppMemory).where(
                AppMemory.app_id == self._app_id,
                AppMemory.user_id == self._user_id,
            )
        )
        await self._session.commit()

    async def commit(self) -> None:
        await self._session.commit()

    @staticmethod
    def serialize(record: AppMemory) -> dict:
        return {
            "id": record.id,
            "content": record.content,
            "sourceTurnId": record.source_turn_id,
            "createTime": record.create_time.isoformat(),
            "updateTime": record.update_time.isoformat(),
        }
