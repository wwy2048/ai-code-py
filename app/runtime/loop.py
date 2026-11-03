from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

from app.runtime.hooks import (
    AfterModelContext,
    AfterToolContext,
    BeforeModelContext,
    BeforeToolContext,
    HookBus,
    ModelFailedContext,
)
from app.runtime.journal import TurnJournal
from app.runtime.messages import AgentMessage, AssistantMessage, ToolCall, ToolResultMessage
from app.runtime.model import AgentModel, ModelCallStatus, ModelRequest
from app.runtime.tools import ToolExecutionResult, ToolRegistry

EventEmitter = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass
class TurnState:
    """一个 Turn 在内存中的消息序列与循环进度。"""

    turn_id: str
    messages: list[AgentMessage] = field(default_factory=list)
    model_round: int = 0
    last_round_text: str = ""
    final_assistant_text: str = ""
    persisted_assistant_text: bool = False


@dataclass(frozen=True)
class PhaseResult:
    exhausted: bool
    final_text: str = ""


class AgentLoop:
    """只负责模型与工具循环；持久化、策略和代码生成差异均从外部注入。"""

    def __init__(
        self,
        *,
        model: AgentModel,
        tools: ToolRegistry,
        journal: TurnJournal,
        emit: EventEmitter,
        hooks: HookBus | None = None,
        max_rounds: int = 12,
        fallback_text: str = "页面已生成。",
    ) -> None:
        if max_rounds < 1:
            raise ValueError("max_rounds 必须大于 0")
        self._model = model
        self._tools = tools
        self._journal = journal
        self._emit = emit
        self._hooks = hooks or HookBus()
        self._max_rounds = max_rounds
        self._fallback_text = fallback_text

    async def run_phase(self, state: TurnState) -> PhaseResult:
        """执行一段模型循环，直到模型不再调用工具或耗尽本段预算。"""
        for _ in range(self._max_rounds):
            state.model_round += 1
            round_index = state.model_round
            batch_id = f"{state.turn_id}:{round_index}"
            streamed_parts: list[str] = []

            async def on_text(
                delta: str,
                parts: list[str] = streamed_parts,
                current_batch_id: str = batch_id,
            ) -> None:
                parts.append(delta)
                await self._emit(
                    "text_delta",
                    {"text": delta, "batchId": current_batch_id},
                )

            request = ModelRequest(
                messages=tuple(state.messages),
                tools=tuple(self._tools.definitions),
                model_name=self._model_name,
            )
            await self._hooks.before_model(
                BeforeModelContext(
                    turn_id=state.turn_id,
                    round_index=round_index,
                    request=request,
                )
            )
            started = perf_counter()
            try:
                reply = await self._model.next_turn(request, on_text)
            except Exception as exc:
                duration_ms = int((perf_counter() - started) * 1000)
                await self._hooks.model_failed(
                    ModelFailedContext(
                        turn_id=state.turn_id,
                        round_index=round_index,
                        request=request,
                        error=exc,
                        duration_ms=duration_ms,
                    )
                )
                await self._journal.append_model_call(
                    status=ModelCallStatus.ERROR,
                    model_name=self._model_name,
                    duration_ms=duration_ms,
                    error_type=type(exc).__name__,
                    error_summary=str(getattr(exc, "summary", type(exc).__name__))[:256],
                    retry_count=int(getattr(exc, "retry_count", 0)),
                )
                await self._journal.commit()
                raise

            duration_ms = int((perf_counter() - started) * 1000)
            await self._hooks.after_model(
                AfterModelContext(
                    turn_id=state.turn_id,
                    round_index=round_index,
                    request=request,
                    reply=reply,
                    duration_ms=duration_ms,
                )
            )
            await self._journal.append_model_call(
                status=ModelCallStatus.SUCCESS,
                model_name=reply.model_name or self._model_name,
                duration_ms=duration_ms,
                input_tokens=reply.input_tokens,
                output_tokens=reply.output_tokens,
                retry_count=reply.retry_count,
            )
            await self._journal.commit()

            round_text = "".join(streamed_parts) if streamed_parts else reply.text
            state.last_round_text = round_text.strip()
            if state.last_round_text:
                await self._journal.append_assistant(
                    AssistantMessage(content=round_text),
                    batch_id=batch_id,
                )
                state.persisted_assistant_text = True
                await self._journal.commit()
            if reply.text and not streamed_parts:
                await self._emit("text_delta", {"text": reply.text, "batchId": batch_id})

            if reply.stop_reason == "max_tokens":
                if not reply.tool_calls:
                    raise RuntimeError(
                        "模型输出达到 token 上限，请缩短需求或拆分为多个回合"
                    )
                await self._reject_truncated_tools(
                    state,
                    reply.tool_calls,
                    batch_id=batch_id,
                    round_index=round_index,
                    assistant_text=reply.text,
                )
                continue

            if not reply.tool_calls:
                if state.last_round_text:
                    state.final_assistant_text = round_text
                else:
                    state.final_assistant_text = self._fallback_text
                    await self._journal.append_assistant(
                        AssistantMessage(content=state.final_assistant_text),
                        batch_id=batch_id,
                    )
                    state.persisted_assistant_text = True
                    await self._journal.commit()
                    await self._emit(
                        "text_delta",
                        {"text": state.final_assistant_text, "batchId": batch_id},
                    )
                return PhaseResult(exhausted=False, final_text=state.final_assistant_text)

            effective_calls, results = await self._execute_tools(
                state,
                reply.tool_calls,
                batch_id=batch_id,
                round_index=round_index,
            )
            state.messages.append(
                AssistantMessage(content=reply.text, tool_calls=tuple(effective_calls))
            )
            state.messages.extend(results)

        return PhaseResult(exhausted=True, final_text=state.final_assistant_text)

    @property
    def _model_name(self) -> str:
        return str(getattr(self._model, "model_name", "unknown"))

    async def _execute_tools(
        self,
        state: TurnState,
        calls: list[ToolCall],
        *,
        batch_id: str,
        round_index: int,
    ) -> tuple[list[ToolCall], list[ToolResultMessage]]:
        effective_calls: list[ToolCall] = []
        results: list[ToolResultMessage] = []
        for original_call in calls:
            outcome = await self._hooks.before_tool(
                BeforeToolContext(
                    turn_id=state.turn_id,
                    round_index=round_index,
                    batch_id=batch_id,
                    call=original_call,
                )
            )
            call = outcome.call or original_call
            await self._emit(
                "tool_started",
                {
                    "toolCallId": call.id,
                    "name": call.name,
                    "batchId": batch_id,
                    **self._tools.public_input(call),
                },
            )
            if outcome.action == "deny":
                result = ToolExecutionResult(
                    ok=False,
                    output=outcome.message,
                    public_input=self._tools.public_input(call),
                    duration_ms=0,
                )
            else:
                result = await self._tools.execute(call)
            result = await self._hooks.after_tool(
                AfterToolContext(
                    turn_id=state.turn_id,
                    round_index=round_index,
                    batch_id=batch_id,
                    call=call,
                    result=result,
                )
            )

            effective_calls.append(call)
            await self._journal.append_tool_call(call, batch_id=batch_id)
            await self._journal.append_tool_result(call, result, batch_id=batch_id)
            await self._journal.commit()
            results.append(
                ToolResultMessage(
                    tool_call_id=call.id,
                    name=call.name,
                    content=result.output,
                    is_error=not result.ok,
                )
            )
            await self._emit(
                "tool_finished",
                {
                    "toolCallId": call.id,
                    "name": call.name,
                    "batchId": batch_id,
                    "status": "completed" if result.ok else "failed",
                    "durationMs": result.duration_ms,
                    "sizeBytes": result.size_bytes,
                    "preview": result.output[:300],
                    **result.public_input,
                },
            )
        return effective_calls, results

    async def _reject_truncated_tools(
        self,
        state: TurnState,
        calls: list[ToolCall],
        *,
        batch_id: str,
        round_index: int,
        assistant_text: str,
    ) -> None:
        results: list[ToolResultMessage] = []
        for call in calls:
            public_input = self._tools.public_input(call)
            await self._emit(
                "tool_started",
                {
                    "toolCallId": call.id,
                    "name": call.name,
                    "batchId": batch_id,
                    **public_input,
                },
            )
            result = ToolExecutionResult(
                ok=False,
                output=(
                    "工具调用因模型输出达到 max_tokens 被截断，未执行。"
                    "请把大文件拆成最小骨架和多次较小的 edit_file 调用。"
                ),
                public_input=public_input,
                duration_ms=0,
            )
            result = await self._hooks.after_tool(
                AfterToolContext(
                    turn_id=state.turn_id,
                    round_index=round_index,
                    batch_id=batch_id,
                    call=call,
                    result=result,
                )
            )
            await self._journal.append_tool_call(call, batch_id=batch_id)
            await self._journal.append_tool_result(call, result, batch_id=batch_id)
            await self._journal.commit()
            results.append(
                ToolResultMessage(
                    tool_call_id=call.id,
                    name=call.name,
                    content=result.output,
                    is_error=not result.ok,
                )
            )
            await self._emit(
                "tool_finished",
                {
                    "toolCallId": call.id,
                    "name": call.name,
                    "batchId": batch_id,
                    "status": "completed" if result.ok else "failed",
                    "durationMs": result.duration_ms,
                    "sizeBytes": result.size_bytes,
                    "preview": result.output,
                    **result.public_input,
                },
            )
        state.messages.append(
            AssistantMessage(content=assistant_text, tool_calls=tuple(calls))
        )
        state.messages.extend(results)
