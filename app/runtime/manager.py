import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from app.errors import BusinessError
from app.response import ErrorCode
from app.runtime.agent import AgentTurnExecutor

logger = logging.getLogger(__name__)


@dataclass
class TurnEvent:
    id: int
    event: str
    data: dict[str, Any]


@dataclass
class TurnJob:
    app_id: int
    user_id: int
    code_gen_type: str
    message: str
    request_id: str
    events: list[TurnEvent] = field(default_factory=list)
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    completed: bool = False

    async def emit(self, event: str, payload: dict[str, Any]) -> None:
        async with self.condition:
            self.events.append(
                TurnEvent(
                    id=len(self.events) + 1,
                    event=event,
                    data={
                        'turnId': self.request_id,
                        'sequence': len(self.events) + 1,
                        **payload,
                    },
                )
            )
            self.condition.notify_all()

    async def finish(self) -> None:
        async with self.condition:
            self.completed = True
            self.condition.notify_all()


class GenerationManager:
    """同一 App 串行执行 Turn，并让任务独立于 HTTP 连接继续运行。"""

    def __init__(self, executor: AgentTurnExecutor) -> None:
        self._executor = executor
        self._jobs: dict[tuple[int, str], TurnJob] = {}
        self._app_locks: dict[int, asyncio.Lock] = {}
        self._registry_lock = asyncio.Lock()

    async def start_turn(
        self,
        *,
        app_id: int,
        user_id: int,
        code_gen_type: str,
        message: str,
        request_id: str,
    ) -> TurnJob:
        async with self._registry_lock:
            key = (app_id, request_id)
            existing = self._jobs.get(key)
            if existing is not None:
                if existing.message != message:
                    raise BusinessError(ErrorCode.PARAMS_ERROR, 'requestId 已用于其他消息')
                return existing

            job = TurnJob(
                app_id=app_id,
                user_id=user_id,
                code_gen_type=code_gen_type,
                message=message,
                request_id=request_id,
            )
            self._jobs[key] = job
            self._app_locks.setdefault(app_id, asyncio.Lock())
            asyncio.create_task(self._run(job))
            return job

    async def _run(self, job: TurnJob) -> None:
        async with self._app_locks[job.app_id]:
            await job.emit('turn_started', {'appId': job.app_id})
            try:
                await self._executor.execute(
                    app_id=job.app_id,
                    user_id=job.user_id,
                    code_gen_type=job.code_gen_type,
                    prompt=job.message,
                    turn_id=job.request_id,
                    emit=job.emit,
                )
            except Exception:
                logger.exception('Turn %s 执行失败', job.request_id)
                await job.emit(
                    'error',
                    {
                        'code': int(ErrorCode.SYSTEM_ERROR),
                        'message': '生成失败，请稍后重试',
                    },
                )
                await job.emit('turn_finished', {'status': 'failed'})
            else:
                await job.emit('turn_finished', {'status': 'completed'})
            finally:
                await job.finish()

    async def stream(
        self,
        job: TurnJob,
        last_event_id: int | None,
    ) -> AsyncIterator[TurnEvent]:
        cursor = last_event_id or 0
        if job.completed and cursor >= len(job.events):
            final_event = next(
                (event for event in reversed(job.events) if event.event == 'turn_finished'),
                None,
            )
            if final_event is not None:
                yield final_event
            return
        while True:
            async with job.condition:
                while len(job.events) <= cursor and not job.completed:
                    await job.condition.wait()
                pending = job.events[cursor:]
                completed = job.completed
            for event in pending:
                yield event
            cursor += len(pending)
            if completed and not pending:
                return

    @staticmethod
    def encode_sse(event: TurnEvent) -> str:
        payload = json.dumps(event.data, ensure_ascii=False)
        return f'id: {event.id}\nevent: {event.event}\ndata: {payload}\n\n'
