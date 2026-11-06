from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from app.errors import BusinessError
from app.generation.application.bus import EventBus, Subscription
from app.generation.application.inbox import (
    AppSession,
    CommandAck,
    QueueAck,
    QueuedFollowup,
)
from app.generation.application.stream import (
    QueueEvent,
    TurnEvent,
    TurnJob,
    encode_sse,
)
from app.generation.domain.commands import (
    ClearPausedFollowupsCommand,
    ControlCommand,
    DeleteQueuedCommand,
    EnqueueFollowupCommand,
    InterruptTurnCommand,
    QueueCommand,
    RedirectTurnCommand,
    ResumeFollowupsCommand,
    StartTurnCommand,
    SteerQueuedCommand,
    SteerTurnCommand,
)
from app.generation.domain.events import EventEnvelope, EventType
from app.generation.ports.turn import TurnExecutor
from app.response import ErrorCode

logger = logging.getLogger(__name__)


class GenerationRuntime:
    """生成上下文的应用层门面：收件箱、worker、队列、总线和 Turn 生命周期。"""

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
        self._queue_acks: dict[
            tuple[int, str], tuple[tuple[str, str, str], QueueAck]
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
            session = self._session_locked(command.app_id)
            session.enqueue_turn(command)
            self._ensure_worker_locked(session)
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
                if session is not None and session.pause_ready_followups():
                    self._publish_queue_changed(session)
            else:
                raise BusinessError(ErrorCode.PARAMS_ERROR, "未知控制命令")

            self._command_acks[request_key] = (fingerprint, ack)
            return ack

    async def queue_control(self, command: QueueCommand) -> QueueAck:
        """执行 App 级排队命令；所有队列变更都通过同一把锁串行化。"""
        async with self._registry_lock:
            action = self._queue_action(command)
            request_key = (command.app_id, command.command_id)
            fingerprint = self._queue_fingerprint(command)
            existing = self._queue_acks.get(request_key)
            if existing is not None:
                old_fingerprint, old_ack = existing
                if old_fingerprint != fingerprint:
                    raise BusinessError(ErrorCode.PARAMS_ERROR, "commandId 已用于其他队列命令")
                return old_ack

            session = self._session_locked(command.app_id)
            if isinstance(command, EnqueueFollowupCommand):
                ack = self._enqueue_followup_locked(session, command)
            elif isinstance(command, SteerQueuedCommand):
                ack = self._steer_queued_locked(session, command)
            elif isinstance(command, DeleteQueuedCommand):
                ack = self._delete_queued_locked(session, command)
            elif isinstance(command, ResumeFollowupsCommand):
                session.resume_paused()
                self._publish_queue_changed(session)
                self._ensure_worker_locked(session)
                ack = QueueAck("accepted", action, command.command_id, session.queue_snapshot())
            elif isinstance(command, ClearPausedFollowupsCommand):
                session.clear_paused()
                self._publish_queue_changed(session)
                ack = QueueAck("accepted", action, command.command_id, session.queue_snapshot())
            else:
                raise BusinessError(ErrorCode.PARAMS_ERROR, "未知队列命令")

            self._queue_acks[request_key] = (fingerprint, ack)
            return ack

    async def stream(
        self,
        job: TurnJob,
        last_event_id: int | None,
    ) -> AsyncIterator[TurnEvent]:
        async for event in job.stream(last_event_id):
            yield event

    async def stream_queue(
        self,
        app_id: int,
        last_event_id: int | None,
    ) -> AsyncIterator[QueueEvent]:
        async with self._registry_lock:
            session = self._session_locked(app_id)
            current = session.next_queue_sequence - 1
            snapshot = session.queue_snapshot()
        cursor = last_event_id if last_event_id is not None else current
        yield QueueEvent(id=cursor, event="queue_snapshot", data=snapshot)
        async for event in session.stream_queue_events(cursor):
            yield event

    @staticmethod
    def encode_sse(event: TurnEvent | QueueEvent) -> str:
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

    def _enqueue_followup_locked(
        self,
        session: AppSession,
        command: EnqueueFollowupCommand,
    ) -> QueueAck:
        if command.clear_paused:
            session.clear_paused()
        item = QueuedFollowup(
            app_id=command.app_id,
            user_id=command.user_id,
            code_gen_type=command.code_gen_type,
            message=command.message,
            command_id=command.command_id,
        )
        session.enqueue_followup(item)
        self._publish_queue_changed(session)
        self._ensure_worker_locked(session)
        return QueueAck("accepted", "enqueue", command.command_id, session.queue_snapshot())

    def _steer_queued_locked(
        self,
        session: AppSession,
        command: SteerQueuedCommand,
    ) -> QueueAck:
        removed = session.remove_followup(command.item_id)
        if removed is None:
            return QueueAck("not_found", "steer", command.command_id, session.queue_snapshot())
        item, _status = removed
        if session.active_turn is not None and session.active_turn.control.accepting_controls:
            session.active_turn.control.steer(item.message, command_id=item.command_id)
            status = "accepted"
        else:
            # 当前 Turn 已收口：消息仍会在下个 Turn 消费，但卡片已按用户
            # 点击 Steer 的意图移除，因此不重新投影到可见队列。
            session.enqueue_followup(item, visible=False, front=True)
            self._ensure_worker_locked(session)
            status = "degraded_to_followup"
        self._publish_queue_changed(session)
        return QueueAck(status, "steer", command.command_id, session.queue_snapshot())

    def _delete_queued_locked(
        self,
        session: AppSession,
        command: DeleteQueuedCommand,
    ) -> QueueAck:
        removed = session.remove_followup(command.item_id)
        if removed is None:
            return QueueAck("not_found", "delete", command.command_id, session.queue_snapshot())
        self._publish_queue_changed(session)
        return QueueAck("accepted", "delete", command.command_id, session.queue_snapshot())

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

    def _ensure_worker_locked(self, session: AppSession) -> None:
        if self._stopping:
            return
        if session.worker is None or session.worker.done():
            session.worker = asyncio.create_task(
                self._worker_loop(session), name=f"generation-worker:{session.app_id}"
            )

    async def _worker_loop(self, session: AppSession) -> None:
        while not self._stopping:
            ready = await session.next_turn()
            command = ready.command
            job = self._jobs.get((command.app_id, command.turn_id))
            if job is None:
                job = self._create_job_locked(command)
            await self._run_turn(session, job, command, ready.followup)

    async def _run_turn(
        self,
        session: AppSession,
        job: TurnJob,
        command: StartTurnCommand,
        followup: QueuedFollowup | None,
    ) -> None:
        session.active_turn = job
        outcome = "failed"
        externally_cancelled = False
        followup_consumed = False

        async def emit(event: str, payload: dict[str, Any]) -> None:
            nonlocal followup_consumed
            await self._emit(job, event, payload)
            event_type = EventType(event)
            if event_type is EventType.USER_MESSAGE_COMMITTED and followup is not None:
                followup_consumed = True
                self._publish_user_message_consumed(
                    session,
                    command_id=followup.command_id,
                    text=followup.message,
                    mode="followup",
                    turn_id=job.request_id,
                )
            elif event_type is EventType.STEERING_CONSUMED:
                items = payload.get("items", [])
                if isinstance(items, list):
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        item_command_id = item.get("commandId")
                        item_text = item.get("text")
                        if isinstance(item_command_id, str) and isinstance(item_text, str):
                            self._publish_user_message_consumed(
                                session,
                                command_id=item_command_id,
                                text=item_text,
                                mode="steer",
                                turn_id=job.request_id,
                            )

        await self._emit(job, EventType.TURN_STARTED, {"appId": job.app_id})
        try:
            result = await self._executor.execute(command, emit=emit, control=job.control)
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

            queue_changed = False
            if followup is not None and not followup_consumed:
                session.paused_followups.appendleft(followup)
                queue_changed = True

            unconsumed = job.control.take_unconsumed_steering()
            if unconsumed:
                degraded = [
                    QueuedFollowup(
                        app_id=job.app_id,
                        user_id=job.user_id,
                        code_gen_type=job.code_gen_type,
                        message=text,
                        command_id=command_id or str(uuid.uuid4()),
                    )
                    for text, command_id in unconsumed
                ]
                if outcome in {"failed", "interrupted"}:
                    for item in reversed(degraded):
                        session.paused_followups.appendleft(item)
                else:
                    for item in reversed(degraded):
                        session.enqueue_followup(item, front=True)
                queue_changed = True

            # 中断时已在控制命令到达时冻结当时已有的队列；中断收口后才新增的
            # Followup 不应被补刀暂停，否则用户无法在一次中断后继续正常对话。
            if outcome == "failed" and session.pause_ready_followups():
                queue_changed = True
            if queue_changed:
                self._publish_queue_changed(session)

            job.control.close()
            await job.finish(outcome)
            session.active_turn = None
            subscription = job._subscription
            if isinstance(subscription, Subscription):
                self._bus.unsubscribe(subscription)
                job._subscription = None
            if not self._stopping:
                self._ensure_worker_locked(session)

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

    def _publish_queue_changed(self, session: AppSession) -> QueueEvent:
        return session.publish_queue_event("queue_changed", session.queue_snapshot())

    def _publish_user_message_consumed(
        self,
        session: AppSession,
        *,
        command_id: str,
        text: str,
        mode: str,
        turn_id: str,
    ) -> QueueEvent:
        return session.publish_queue_event(
            "user_message_consumed",
            {
                "commandId": command_id,
                "turnId": turn_id,
                "text": text,
                "mode": mode,
                "queue": session.queue_snapshot(),
            },
        )

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
        session = self._session_locked(degraded.app_id)
        session.enqueue_turn(degraded)
        self._ensure_worker_locked(session)
        return degraded

    def _session_locked(self, app_id: int) -> AppSession:
        return self._sessions.setdefault(app_id, AppSession(app_id=app_id))

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

    @staticmethod
    def _queue_action(command: QueueCommand) -> str:
        if isinstance(command, EnqueueFollowupCommand):
            return "enqueue"
        if isinstance(command, SteerQueuedCommand):
            return "steer"
        if isinstance(command, DeleteQueuedCommand):
            return "delete"
        if isinstance(command, ResumeFollowupsCommand):
            return "resume"
        return "clear_paused"

    @staticmethod
    def _queue_fingerprint(command: QueueCommand) -> tuple[str, str, str]:
        action = GenerationRuntime._queue_action(command)
        target = ""
        text = ""
        if isinstance(command, EnqueueFollowupCommand):
            target = "clear" if command.clear_paused else "keep"
            text = command.message
        elif isinstance(command, (SteerQueuedCommand, DeleteQueuedCommand)):
            target = command.item_id
        return action, target, text
