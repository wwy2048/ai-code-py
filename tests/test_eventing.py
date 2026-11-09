import asyncio

import pytest

from app.generation.application.bus import EventBus, Subscription
from app.generation.application.runtime import GenerationRuntime
from app.generation.application.stream import TurnEvent, TurnJob, coalesce_stream_events
from app.generation.domain.events import EventEnvelope, EventType
from app.generation.domain.turns import TurnRequest

OutboundEvent = EventEnvelope


class RecordingMailbox:
    def __init__(self) -> None:
        self.events: list[OutboundEvent] = []

    def offer(self, event: OutboundEvent) -> None:
        self.events.append(event)


async def test_event_bus_delivers_to_mailbox_and_isolates_failed_observer() -> None:
    bus = EventBus()
    mailbox = RecordingMailbox()
    bus.subscribe(Subscription("mailbox", ("text_delta",), mailbox))

    async def broken_handler(event: OutboundEvent) -> None:
        del event
        raise RuntimeError("observer failed")

    bus.subscribe(Subscription("broken", ("tool_started",), broken_handler))
    event = OutboundEvent(
        type=EventType.TEXT_DELTA,
        app_id=1,
        turn_id="turn-1",
        sequence=1,
        payload={"text": "hello"},
    )
    lifecycle = OutboundEvent(
        type=EventType.TOOL_STARTED,
        app_id=1,
        turn_id="turn-1",
        sequence=2,
        payload={"name": "write_file"},
    )

    await bus.emit(event)
    await bus.emit(lifecycle)

    assert mailbox.events == [event]


async def test_turn_job_reports_stream_gap_when_replay_window_is_exceeded() -> None:
    job = TurnJob(
        app_id=1,
        user_id=1,
        code_gen_type="html",
        message="prompt",
        request_id="turn-gap",
        max_events=2,
    )
    for sequence in range(1, 5):
        job.offer(
            EventEnvelope(
                type=EventType.TEXT_DELTA,
                app_id=1,
                turn_id="turn-gap",
                sequence=job.reserve_sequence(),
                payload={"text": str(sequence)},
            )
        )

    await job.finish("completed")

    events = [event async for event in job.stream(None)]

    assert events[0].event == "stream_gap"
    assert events[0].data["to"] == 2
    assert [event.id for event in events[1:]] == [3, 4]


async def test_runtime_prunes_finished_turn_and_idle_agent() -> None:
    class FakeExecutor:
        async def execute(
            self,
            command: TurnRequest,
            *,
            emit: object,
            control: object,
        ) -> object:
            del command, control
            assert callable(emit)
            await emit("user_message_committed", {"text": "prune"})
            await emit("text_delta", {"text": "ok", "batchId": "batch-prune"})

            class Result:
                status = "completed"

            return Result()

    runtime = GenerationRuntime(
        FakeExecutor(),  # type: ignore[arg-type]
        finished_turn_ttl_seconds=0,
        turn_receipt_limit=1,
        agent_idle_ttl_seconds=0,
    )
    job = await runtime.start_turn(
        TurnRequest(
            app_id=2,
            user_id=2,
            code_gen_type="html",
            message="prune",
            turn_id="prune-turn",
        )
    )
    async with asyncio.timeout(2):
        async with job.condition:
            while not job.completed:
                await job.condition.wait()
    await asyncio.sleep(0)

    assert await runtime.get_turn_job(2, "prune-turn") is None
    assert runtime._agents == {}
    assert runtime._jobs == {}


async def test_manager_text_and_lifecycle_events_share_one_sequence() -> None:
    class FakeExecutor:
        async def execute(
            self,
            command: TurnRequest,
            *,
            emit: object,
            control: object,
        ) -> object:
            del command, control
            assert callable(emit)
            await emit("text_delta", {"text": "ok", "batchId": "batch-1"})
            await asyncio.sleep(0)

            class Result:
                status = "completed"

            return Result()

    manager = GenerationRuntime(FakeExecutor())  # type: ignore[arg-type]
    job = await manager.start_turn(
        TurnRequest(
            app_id=1,
            user_id=1,
            code_gen_type="html",
            message="prompt",
            turn_id="sequence-turn",
        )
    )
    async with asyncio.timeout(2):
        async with job.condition:
            while not job.completed:
                await job.condition.wait()
    assert [event.id for event in job.events] == [3]
    assert job.events[0].event == "turn_finished"
    assert job.last_event_id == 3
    assert job.first_retained_id == 3
    assert job.replayable_from_zero is False
    await manager.shutdown()


def test_stream_event_rejects_await_handler() -> None:
    async def handler(event: OutboundEvent) -> None:
        del event

    bus = EventBus()
    with pytest.raises(ValueError, match="Mailbox"):
        bus.subscribe(Subscription("bad-stream", ("text_delta",), handler))


async def test_inbound_publish_routes_to_registered_sink() -> None:
    from app.generation.domain.events import UserMessage

    received: list[UserMessage] = []

    class SinkResult:
        status = "accepted"

    def sink(event: object) -> SinkResult:
        received.append(event)  # type: ignore[arg-type]
        return SinkResult()

    bus = EventBus()
    bus.register(7, sink)  # type: ignore[arg-type]
    event = UserMessage.create(
        event_id="message-1",
        app_id=7,
        user_id=1,
        code_gen_type="html",
        text="hello",
    )
    result = bus.publish(event, to=7)
    assert result.status == "accepted"
    assert received == [event]


async def test_stream_deltas_are_coalesced_without_losing_text() -> None:
    async def source():
        yield TurnEvent(id=1, event="text_delta", data={"text": "a", "batchId": "b"})
        yield TurnEvent(id=2, event="text_delta", data={"text": "b", "batchId": "b"})
        yield TurnEvent(id=3, event="tool_started", data={"name": "write_file"})
        yield TurnEvent(id=4, event="text_delta", data={"text": "c", "batchId": "b"})

    events = [event async for event in coalesce_stream_events(source())]
    assert [event.event for event in events] == ["text_delta", "tool_started", "text_delta"]
    assert events[0].data["text"] == "ab"
    assert events[0].id == 2
    assert events[2].data["text"] == "c"
