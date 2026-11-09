from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from time import monotonic
from typing import Any

from app.errors import BusinessError
from app.generation.application.bus import EventBus
from app.generation.application.inbox import (
    CommandAck,
    GenerationAgent,
    InboxAck,
    QueueAck,
    QueueOperation,
)
from app.generation.application.stream import (
    QueueEvent,
    TurnEvent,
    TurnJob,
    coalesce_stream_events,
    encode_sse,
)
from app.generation.domain.events import (
    EventEnvelope,
    EventType,
    Subscription,
    UserInterrupt,
    UserMessage,
)
from app.generation.domain.turns import TurnRequest
from app.generation.ports.turn import TurnExecutor
from app.response import ErrorCode

logger = logging.getLogger(__name__)


class GenerationRuntime:
    """生成上下文门面：全局总线/Agent 注册表，以及每 App 独立收件箱。"""

    def __init__(
        self,
        executor: TurnExecutor,
        *,
        event_buffer_limit: int = 16384,
        finished_turn_ttl_seconds: int = 600,
        turn_receipt_limit: int = 10,
        agent_idle_ttl_seconds: int = 1800,
        bus: EventBus | None = None,
    ) -> None:
        self._executor = executor
        self._event_buffer_limit = event_buffer_limit
        self._finished_turn_ttl_seconds = finished_turn_ttl_seconds
        self._turn_receipt_limit = turn_receipt_limit
        self._agent_idle_ttl_seconds = agent_idle_ttl_seconds
        self._bus = bus or EventBus()
        self._jobs: dict[tuple[int, str], TurnJob] = {}
        self._agents: dict[int, GenerationAgent] = {}
        self._subscriptions: dict[int, Subscription] = {}
        self._control_acks: dict[
            tuple[int, str], tuple[tuple[str, str, str | None], CommandAck]
        ] = {}
        self._queue_acks: dict[tuple[int, str], tuple[tuple[str, str, str], QueueAck]] = {}
        self._registry_lock = asyncio.Lock()
        self._stopping = False

    async def start_turn(self, request: TurnRequest) -> TurnJob:
        async with self._registry_lock:
            await self._prune_expired_locked()
            key = (request.app_id, request.turn_id)
            existing = self._jobs.get(key)
            if existing is not None:
                if existing.message != request.message:
                    raise BusinessError(ErrorCode.PARAMS_ERROR, "requestId 已用于其他消息")
                return existing
            job = self._create_job_locked(request, queued=False)
            agent = self._agent_locked(request.app_id)
            event = UserMessage.create(
                event_id=request.turn_id,
                app_id=request.app_id,
                user_id=request.user_id,
                code_gen_type=request.code_gen_type,
                text=request.message,
                direct=True,
            )
            self._bus.publish(event, to=request.app_id)
            agent.ensure_worker()
            return job

    async def publish_user_message(self, event: UserMessage) -> CommandAck:
        """把用户消息事件同步投递到 App Agent；不执行 Turn。"""
        async with self._registry_lock:
            fingerprint = ("user_message", event.intent, event.text)
            request_key = (event.app_id, event.event_id)
            existing = self._control_acks.get(request_key)
            if existing is not None:
                old_fingerprint, old_ack = existing
                if old_fingerprint != fingerprint:
                    raise BusinessError(ErrorCode.PARAMS_ERROR, "commandId 已用于其他消息")
                return old_ack

            agent = self._agent_locked(event.app_id)
            target_turn_id = event.payload.get("target_turn_id")
            if (
                isinstance(target_turn_id, str)
                and agent.active_turn is not None
                and agent.active_turn.request_id != target_turn_id
            ):
                result = CommandAck(
                    status="stale",
                    action="steer",
                    app_id=event.app_id,
                    turn_id=target_turn_id,
                    command_id=event.event_id,
                    message=event.text,
                )
                self._control_acks[request_key] = (fingerprint, result)
                return result
            ack = self._bus.publish(event, to=event.app_id)
            if not isinstance(ack, InboxAck):
                raise RuntimeError("App Agent 返回了无效 ACK")
            turn_id = event.event_id
            if event.intent == "steering" and agent.active_turn is not None:
                turn_id = agent.active_turn.request_id
            result = CommandAck(
                status=ack.status,
                action=ack.action,
                app_id=event.app_id,
                turn_id=turn_id,
                command_id=event.event_id,
                message=event.text,
            )
            self._control_acks[request_key] = (fingerprint, result)
            return result

    async def publish_interrupt(self, event: UserInterrupt) -> CommandAck:
        async with self._registry_lock:
            fingerprint = ("interrupt", str(event.payload.get("reason", "stop")), None)
            request_key = (event.app_id, event.event_id)
            existing = self._control_acks.get(request_key)
            if existing is not None:
                old_fingerprint, old_ack = existing
                if old_fingerprint != fingerprint:
                    raise BusinessError(ErrorCode.PARAMS_ERROR, "commandId 已用于其他控制命令")
                return old_ack
            agent = self._agent_locked(event.app_id)
            turn_id = agent.active_turn.request_id if agent.active_turn is not None else ""
            target_turn_id = event.payload.get("target_turn_id")
            if (
                isinstance(target_turn_id, str)
                and agent.active_turn is not None
                and agent.active_turn.request_id != target_turn_id
            ):
                result = CommandAck(
                    status="stale",
                    action="interrupt",
                    app_id=event.app_id,
                    turn_id=target_turn_id,
                    command_id=event.event_id,
                )
                self._control_acks[request_key] = (fingerprint, result)
                return result
            ack = self._bus.publish(event, to=event.app_id)
            if not isinstance(ack, InboxAck):
                raise RuntimeError("App Agent 返回了无效 ACK")
            result = CommandAck(
                status=ack.status,
                action=ack.action,
                app_id=event.app_id,
                turn_id=turn_id,
                command_id=event.event_id,
            )
            self._control_acks[request_key] = (fingerprint, result)
            return result

    async def queue_control(self, operation: QueueOperation) -> QueueAck:
        """同步队列操作适配层；操作本身不进入事件总线。"""
        async with self._registry_lock:
            fingerprint = self._queue_fingerprint(operation)
            request_key = (operation.app_id, operation.command_id)
            existing = self._queue_acks.get(request_key)
            if existing is not None:
                old_fingerprint, old_ack = existing
                if old_fingerprint != fingerprint:
                    raise BusinessError(ErrorCode.PARAMS_ERROR, "commandId 已用于其他队列命令")
                return old_ack

            agent = self._agent_locked(operation.app_id)
            if operation.action == "enqueue":
                if operation.clear_paused:
                    agent.clear_paused()
                event = UserMessage.create(
                    event_id=operation.command_id,
                    app_id=operation.app_id,
                    user_id=operation.user_id,
                    code_gen_type=operation.code_gen_type,
                    text=operation.message or "",
                )
                self._bus.publish(event, to=operation.app_id)
                ack = QueueAck("accepted", "enqueue", operation.command_id, agent.queue_snapshot())
            elif operation.action == "steer":
                ack = agent.promote(operation.item_id or "")
            elif operation.action == "delete":
                ack = agent.delete(operation.item_id or "")
            elif operation.action == "resume":
                result = agent.resume_paused()
                ack = QueueAck(result.status, "resume", operation.command_id, result.queue)
            elif operation.action == "clear_paused":
                result = agent.clear_paused()
                ack = QueueAck(result.status, "clear_paused", operation.command_id, result.queue)
            else:
                raise BusinessError(ErrorCode.PARAMS_ERROR, "未知队列操作")
            self._queue_acks[request_key] = (fingerprint, ack)
            return ack

    async def stream(
        self,
        job: TurnJob,
        last_event_id: int | None,
    ) -> AsyncIterator[TurnEvent]:
        async for event in coalesce_stream_events(job.stream(last_event_id)):
            yield event

    async def stream_queue(
        self,
        app_id: int,
        last_event_id: int | None,
    ) -> AsyncIterator[QueueEvent]:
        async with self._registry_lock:
            await self._prune_expired_locked()
            agent = self._agent_locked(app_id)
            agent.stream_subscribers += 1
            agent.touch()
        try:
            async for event in agent.projection.stream(last_event_id):
                yield event
        finally:
            async with self._registry_lock:
                agent.stream_subscribers = max(0, agent.stream_subscribers - 1)
                agent.touch()

    @staticmethod
    def encode_sse(event: TurnEvent | QueueEvent) -> str:
        return encode_sse(event)

    async def shutdown(self) -> None:
        self._stopping = True
        agents = list(self._agents.values())
        for agent in agents:
            await agent.stop()
            self._bus.unregister(agent.app_id)
            subscription = self._subscriptions.pop(agent.app_id, None)
            if subscription is not None:
                self._bus.unsubscribe(subscription)

    def _agent_locked(self, app_id: int) -> GenerationAgent:
        agent = self._agents.get(app_id)
        if agent is not None:
            return agent
        agent = GenerationAgent(app_id=app_id, runner=self._run_agent_message)
        self._agents[app_id] = agent
        self._bus.register(app_id, agent.enqueue)
        self._subscriptions[app_id] = self._bus.subscribe(
            Subscription(
                name=f"queue-projection:{app_id}",
                event_types=(
                    EventType.USER_MESSAGE_COMMITTED,
                    EventType.STEERING_CONSUMED,
                ),
                handler=agent.projection.on_event,
            )
        )
        return agent

    async def _run_agent_message(self, agent: GenerationAgent, event: UserMessage) -> None:
        request = TurnRequest(
            app_id=event.app_id,
            user_id=event.user_id,
            code_gen_type=event.code_gen_type,
            message=event.text,
            turn_id=event.event_id,
        )
        job = self._jobs.get((event.app_id, event.event_id))
        if job is None:
            job = self._create_job_locked(request, queued=True, command_id=event.event_id)
        await self._run_turn(agent, job, request, event)

    async def get_turn_job(self, app_id: int, turn_id: str) -> TurnJob | None:
        async with self._registry_lock:
            await self._prune_expired_locked()
            return self._jobs.get((app_id, turn_id))

    async def _prune_expired_locked(self) -> None:
        """惰性回收过期 Turn 回执和空闲 App Agent，避免内存无界增长。"""
        now = monotonic()
        for key, job in list(self._jobs.items()):
            if (
                job.completed
                and job.finished_at is not None
                and now - job.finished_at >= self._finished_turn_ttl_seconds
            ):
                self._jobs.pop(key, None)

        completed_by_app: dict[int, list[tuple[float, tuple[int, str]]]] = {}
        for key, job in self._jobs.items():
            if job.completed and job.finished_at is not None:
                completed_by_app.setdefault(key[0], []).append((job.finished_at, key))
        for receipts in completed_by_app.values():
            if len(receipts) <= self._turn_receipt_limit:
                continue
            receipts.sort(key=lambda item: item[0])
            for _, key in receipts[: -self._turn_receipt_limit]:
                self._jobs.pop(key, None)

        for app_id, agent in list(self._agents.items()):
            if agent.is_idle() and now - agent.last_active_at >= self._agent_idle_ttl_seconds:
                await agent.stop()
                self._bus.unregister(app_id)
                subscription = self._subscriptions.pop(app_id, None)
                if subscription is not None:
                    self._bus.unsubscribe(subscription)
                self._agents.pop(app_id, None)

    def _create_job_locked(
        self,
        request: TurnRequest,
        *,
        queued: bool,
        command_id: str | None = None,
    ) -> TurnJob:
        key = (request.app_id, request.turn_id)
        existing = self._jobs.get(key)
        if existing is not None:
            return existing
        job = TurnJob(
            app_id=request.app_id,
            user_id=request.user_id,
            code_gen_type=request.code_gen_type,
            message=request.message,
            request_id=request.turn_id,
            max_events=self._event_buffer_limit,
            command_id=command_id,
            queued=queued,
        )
        subscription = self._bus.subscribe(
            Subscription(name=f"turn:{request.turn_id}", event_types=("*",), handler=job)
        )
        job._subscription = subscription
        self._jobs[key] = job
        return job

    async def _run_turn(
        self,
        agent: GenerationAgent,
        job: TurnJob,
        request: TurnRequest,
        event: UserMessage,
    ) -> None:
        if not job.queued:
            agent.projection.clear_inflight(event.event_id)
        agent.activate_turn(job)
        logger.info(
            "生成任务开始 turn_id=%s app_id=%s user_id=%s code_gen_type=%s queued=%s",
            request.turn_id,
            request.app_id,
            request.user_id,
            request.code_gen_type,
            job.queued,
        )
        outcome = "failed"
        externally_cancelled = False
        user_message_committed = False

        async def emit(event_type: str, payload: dict[str, Any]) -> None:
            nonlocal user_message_committed
            await self._emit(job, event_type, payload)
            if EventType(event_type) is EventType.USER_MESSAGE_COMMITTED:
                user_message_committed = True

        await self._emit(job, EventType.TURN_STARTED, {"appId": job.app_id})
        try:
            result = await self._executor.execute(request, emit=emit, control=job.control)
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

            if not user_message_committed:
                agent.requeue_message(event, paused=True)

            unconsumed = job.control.take_unconsumed_steering()
            for text, command_id in unconsumed:
                steering_event = UserMessage.create(
                    event_id=command_id or str(uuid.uuid4()),
                    app_id=request.app_id,
                    user_id=request.user_id,
                    code_gen_type=request.code_gen_type,
                    text=text,
                    intent="steering",
                )
                agent.requeue_message(
                    steering_event,
                    paused=outcome in {"failed", "interrupted"},
                )

            if outcome == "failed":
                agent.pause_followups()

            job.control.close()
            await job.finish(outcome)
            job.retain_final_event_only()
            agent.finish_turn(job, outcome)
            subscription = job._subscription
            if isinstance(subscription, Subscription):
                self._bus.unsubscribe(subscription)
                job._subscription = None
            if not self._stopping:
                agent.ensure_worker()
            logger.info(
                "生成任务结束 turn_id=%s app_id=%s outcome=%s",
                request.turn_id,
                request.app_id,
                outcome,
            )

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
            command_id=job.command_id,
        )
        await self._bus.emit(event)

    @staticmethod
    def _queue_fingerprint(operation: QueueOperation) -> tuple[str, str, str]:
        text = operation.message or ""
        return operation.action, operation.item_id or "", text
