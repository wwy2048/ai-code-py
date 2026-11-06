import asyncio

from app.generation.application.bus import EventBus, Subscription
from app.generation.application.runtime import GenerationRuntime
from app.generation.application.stream import TurnJob
from app.generation.domain.commands import StartTurnCommand
from app.generation.domain.events import EventEnvelope, EventType

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

    bus.subscribe(Subscription("broken", ("text_delta",), broken_handler))
    event = OutboundEvent(
        type=EventType.TEXT_DELTA,
        app_id=1,
        turn_id="turn-1",
        sequence=1,
        payload={"text": "hello"},
    )

    await bus.emit(event)

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


async def test_manager_text_and_lifecycle_events_share_one_sequence() -> None:
    class FakeExecutor:
        async def execute(
            self,
            command: StartTurnCommand,
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
        StartTurnCommand(
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
    assert [event.id for event in job.events] == list(range(1, len(job.events) + 1))
    await manager.shutdown()
