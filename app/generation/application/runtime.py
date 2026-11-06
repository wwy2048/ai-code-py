from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

from app.errors import BusinessError
from app.generation.application.bus import EventBus, Subscription
from app.generation.application.inbox import AppSession, CommandAck
from app.generation.application.stream import TurnEvent, TurnJob, encode_sse
from app.generation.domain.commands import (
    ControlCommand,
    InterruptTurnCommand,
    RedirectTurnCommand,
    StartTurnCommand,
    SteerTurnCommand,
)
from app.generation.domain.events import EventEnvelope, EventType
from app.generation.ports.turn import TurnExecutor
from app.response import ErrorCode

logger = logging.getLogger(__name__)


class GenerationRuntime:
    """生成上下文的应用层门面：收件箱、worker、总线和 Turn 生命周期。"""

    def __init__(
        self,
        executor: TurnExecutor,
        *,
        event_buffer_limit: int = 4096,
        bus: EventBus | None = None,
    ) -> None:
        self._executor = executor
        self._event_buffer_limit = event_buffer_limit
        self._bus = bus or EventBus()
        self._jobs: dict[tuple[int, str], TurnJob] = {}
        self._sessions: dict[int, AppSession] = {}
        self._command_acks: dict[
            tuple[int, str], tuple[tuple[str, str, str | None], CommandAck]
        ] = {}
        self._registry_lock = asyncio.Lock()
        self._stopping = False

    async def start_turn(self, command: StartTurnCommand) -> TurnJob:
        async with self._registry_lock:
            key = (command.app_id, command.turn_id)
            existing = self._jobs.get(key)
            if existing is not None:
                if existing.message != command.message:
                    raise BusinessError(ErrorCode.PARAMS_ERROR, "requestId 已用于其他消息")
                return existing
            job = self._create_job_locked(command)
            self._enqueue_start_locked(command)
            return job

    async def control(self, command: ControlCommand) -> CommandAck:
        async with self._registry_lock:
            fingerprint = self._fingerprint(command)
            request_key = (command.app_id, command.command_id)
            existing = self._command_acks.get(request_key)
            if existing is not None:
                old_fingerprint, old_ack = existing
                if old_fingerprint != fingerprint:
                    raise BusinessError(ErrorCode.PARAMS_ERROR, "commandId 已用于其他控制命令")
                return old_ack

            action = self._action(command)
            job = self._jobs.get((command.app_id, command.turn_id))
            session = self._sessions.get(command.app_id)
            if job is None:
                if session is not None and session.active_turn is not None:
                    ack = CommandAck(
                        "stale", action, command.app_id, command.turn_id, command.command_id
                    )
                elif isinstance(command, (SteerTurnCommand, RedirectTurnCommand)):
                    degraded = self._degrade_to_followup(command)
                    ack = CommandAck(
                        "degraded_to_followup",
                        action,
                        command.app_id,
                        degraded.turn_id,
                        command.command_id,
                        degraded.message,
                    )
                else:
                    ack = CommandAck(
                        "not_active", action, command.app_id, command.turn_id, command.command_id
                    )
            elif job.completed or not job.control.accepting_controls:
                if isinstance(command, (SteerTurnCommand, RedirectTurnCommand)):
                    degraded = self._degrade_to_followup(command)
                    ack = CommandAck(
                        "degraded_to_followup",
                        action,
                        command.app_id,
                        degraded.turn_id,
                        command.command_id,
                        degraded.message,
                    )
                else:
                    ack = CommandAck(
                        "not_active", action, command.app_id, command.turn_id, command.command_id
                    )
            elif isinstance(command, SteerTurnCommand):
                job.control.steer(command.text, command_id=command.command_id)
                ack = CommandAck(
                    "accepted", action, command.app_id, command.turn_id, command.command_id
                )
            elif isinstance(command, RedirectTurnCommand):
                job.control.redirect(command.text, command_id=command.command_id)
                ack = CommandAck(
                    "accepted", action, command.app_id, command.turn_id, command.command_id
                )
            elif isinstance(command, InterruptTurnCommand):
                job.control.stop(command_id=command.command_id)
                ack = CommandAck(
                    "accepted", action, command.app_id, command.turn_id, command.command_id
                )
            else:
                raise BusinessError(ErrorCode.PARAMS_ERROR, "未知控制命令")

            self._command_acks[request_key] = (fingerprint, ack)
            return ack

    async def stream(
        self,
        job: TurnJob,
        last_event_id: int | None,
    ) -> AsyncIterator[TurnEvent]:
        async for event in job.stream(last_event_id):
            yield event

    @staticmethod
    def encode_sse(event: TurnEvent) -> str:
        return encode_sse(event)

    async def shutdown(self) -> None:
        self._stopping = True
        workers = [
            session.worker for session in self._sessions.values() if session.worker is not None
        ]
        for worker in workers:
            worker.cancel()
        for worker in workers:
            try:
                await worker
            except asyncio.CancelledError:
                pass

    def _create_job_locked(self, command: StartTurnCommand) -> TurnJob:
        key = (command.app_id, command.turn_id)
        existing = self._jobs.get(key)
        if existing is not None:
            return existing
        job = TurnJob(
            app_id=command.app_id,
            user_id=command.user_id,
            code_gen_type=command.code_gen_type,
            message=command.message,
            request_id=command.turn_id,
            max_events=self._event_buffer_limit,
        )
        subscription = self._bus.subscribe(
            Subscription(name=f"turn:{command.turn_id}", event_types=("*",), handler=job)
        )
        job._subscription = subscription
        self._jobs[key] = job
        return job

    def _enqueue_start_locked(self, command: StartTurnCommand) -> None:
        session = self._sessions.setdefault(command.app_id, AppSession(app_id=command.app_id))
        session.enqueue(command)
        self._ensure_worker_locked(session)

    def _ensure_worker_locked(self, session: AppSession) -> None:
        if self._stopping:
            return
        if session.worker is None or session.worker.done():
            session.worker = asyncio.create_task(
                self._worker_loop(session), name=f"generation-worker:{session.app_id}"
            )

    async def _worker_loop(self, session: AppSession) -> None:
        while not self._stopping:
            command = await session.next_command()
            job = self._jobs.get((command.app_id, command.turn_id))
            if job is None:
                continue
            await self._run_turn(session, job, command)

    async def _run_turn(
        self,
        session: AppSession,
        job: TurnJob,
        command: StartTurnCommand,
    ) -> None:
        session.active_turn = job
        outcome = "failed"
        externally_cancelled = False
        await self._emit(job, EventType.TURN_STARTED, {"appId": job.app_id})
        try:
            result = await self._executor.execute(
                command,
                emit=lambda event, payload: self._emit(job, event, payload),
                control=job.control,
            )
            outcome = result.status
        except asyncio.CancelledError:
            outcome = "interrupted"
            externally_cancelled = True
            raise
        except Exception:
            logger.exception("Turn %s 执行失败", job.request_id)
            await self._emit(
                job,
                EventType.ERROR,
                {"code": int(ErrorCode.SYSTEM_ERROR), "message": "生成失败，请稍后重试"},
            )
        finally:
            if not externally_cancelled:
                await self._emit(job, EventType.TURN_FINISHED, {"status": outcome})
            job.control.close()
            await job.finish(outcome)
            session.active_turn = None
            subscription = job._subscription
            if isinstance(subscription, Subscription):
                self._bus.unsubscribe(subscription)
                job._subscription = None

    async def _emit(
        self,
        job: TurnJob,
        event_type: str | EventType,
        payload: dict[str, Any],
    ) -> None:
        event = EventEnvelope(
            type=EventType(event_type),
            app_id=job.app_id,
            turn_id=job.request_id,
            sequence=job.reserve_sequence(),
            payload=payload,
            step_id=job.control.step_id,
        )
        await self._bus.emit(event)

    def _degrade_to_followup(
        self,
        command: SteerTurnCommand | RedirectTurnCommand,
    ) -> StartTurnCommand:
        degraded = StartTurnCommand(
            app_id=command.app_id,
            user_id=command.user_id,
            code_gen_type=command.code_gen_type,
            message=command.text,
            turn_id=command.command_id,
        )
        if (degraded.app_id, degraded.turn_id) in self._jobs:
            return degraded
        self._create_job_locked(degraded)
        self._enqueue_start_locked(degraded)
        return degraded

    @staticmethod
    def _action(command: ControlCommand) -> str:
        if isinstance(command, SteerTurnCommand):
            return "steer"
        if isinstance(command, RedirectTurnCommand):
            return "redirect"
        return "interrupt"

    @staticmethod
    def _fingerprint(command: ControlCommand) -> tuple[str, str, str | None]:
        text = (
            command.text if isinstance(command, (SteerTurnCommand, RedirectTurnCommand)) else None
        )
        return GenerationRuntime._action(command), command.turn_id, text
