from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from time import perf_counter

from app.generation.application.control import TurnControl
from app.generation.application.hooks import (
    AfterModelContext,
    AfterToolContext,
    BeforeModelContext,
    BeforeToolContext,
    HookBus,
    ModelFailedContext,
)
from app.generation.domain.messages import (
    AgentMessage,
    AssistantMessage,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from app.generation.ports.journal import TurnJournal
from app.generation.ports.model import AgentModel, ModelCallStatus, ModelRequest
from app.generation.ports.tools import ToolExecutionResult, ToolExecutor
from app.generation.ports.turn import EventEmitter

INTERRUPTION_PLACEHOLDER = "[本轮已被用户中断，不再基于不完整的内容作答]"
SKIPPED_TOOL_OUTPUT = "工具调用因用户中断而未执行。"
TRUNCATED_OUTPUT_CONTINUATION = (
    "[系统续写指令] 上一次模型输出因 max_tokens 被截断。不要重复已经输出的内容；"
    "如果代码尚未写入，请改用最小骨架和多次较小的 write_file/edit_file 工具调用；"
    "如果写入已经完成，请直接简洁总结当前结果并结束。"
)
FINALIZATION_FALLBACK_TEXT = (
    "本轮已达到工具调用上限，系统已停止继续修改。当前结果已保留，请检查预览；"
    "如需继续调整，请发送下一条指令。"
)


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
    interrupted: bool = False


class AgentLoop:
    """只负责模型与工具循环；持久化、策略和代码生成差异均从外部注入。"""

    def __init__(
        self,
        *,
        model: AgentModel,
        tools: ToolExecutor,
        journal: TurnJournal,
        emit: EventEmitter,
        hooks: HookBus | None = None,
        control: TurnControl | None = None,
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
        self._control = control or TurnControl()
        self._max_rounds = max_rounds
        self._fallback_text = fallback_text

    async def run_phase(self, state: TurnState) -> PhaseResult:
        """执行一段模型循环，直到模型不再调用工具、耗尽预算或被中断。"""
        for round_offset in range(self._max_rounds):
            state.model_round += 1
            round_index = state.model_round
            batch_id = f"{state.turn_id}:{round_index}"

            if await self._apply_pending_control(state, batch_id=batch_id, round_index=round_index):
                return PhaseResult(exhausted=False, interrupted=True)
            await self._drain_steering(state, batch_id=batch_id, round_index=round_index)

            streamed_parts: list[str] = []

            async def on_text(
                delta: str,
                parts: list[str] = streamed_parts,
                current_batch_id: str = batch_id,
            ) -> None:
                parts.append(delta)
                await self._emit("text_delta", {"text": delta, "batchId": current_batch_id})

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
            task = asyncio.create_task(self._model.next_turn(request, on_text))
            self._control.bind_step(task, step_id=f"{state.turn_id}:{round_index}")
            try:
                reply = await task
            except asyncio.CancelledError:
                duration_ms = int((perf_counter() - started) * 1000)
                if not self._control.has_interrupt():
                    if not task.done():
                        task.cancel()
                    raise
                await self._journal.append_model_call(
                    status=ModelCallStatus.CANCELLED,
                    model_name=self._model_name,
                    duration_ms=duration_ms,
                    error_type="Interrupted",
                    error_summary="用户中断当前模型步",
                )
                await self._journal.commit()
                if await self._apply_pending_control(
                    state, batch_id=batch_id, round_index=round_index
                ):
                    return PhaseResult(exhausted=False, interrupted=True)
                continue
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
            finally:
                self._control.clear_step(task)

            duration_ms = int((perf_counter() - started) * 1000)
            if self._control.has_interrupt():
                # 模型刚好返回但用户已抢先中断：该回复不进入轨迹。
                await self._journal.append_model_call(
                    status=ModelCallStatus.CANCELLED,
                    model_name=reply.model_name or self._model_name,
                    duration_ms=duration_ms,
                    input_tokens=reply.input_tokens,
                    cached_input_tokens=reply.cached_input_tokens,
                    output_tokens=reply.output_tokens,
                    error_type="Interrupted",
                    error_summary="用户中断当前模型步",
                )
                await self._journal.commit()
                if await self._apply_pending_control(
                    state, batch_id=batch_id, round_index=round_index
                ):
                    return PhaseResult(exhausted=False, interrupted=True)
                continue

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
                cached_input_tokens=reply.cached_input_tokens,
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
                if reply.tool_calls:
                    await self._reject_truncated_tools(
                        state,
                        reply.tool_calls,
                        batch_id=batch_id,
                        round_index=round_index,
                        assistant_text=reply.text,
                    )
                    continue
                if round_offset < self._max_rounds - 1:
                    await self._continue_truncated_output(
                        state,
                        assistant_text=round_text,
                    )
                    continue

            if not reply.tool_calls:
                if self._control.has_steering() and round_offset < self._max_rounds - 1:
                    await self._drain_steering(state, batch_id=batch_id, round_index=round_index)
                    continue
                self._control.close()
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

            effective_calls, results, interrupted = await self._execute_tools(
                state,
                reply.tool_calls,
                batch_id=batch_id,
                round_index=round_index,
            )
            state.messages.append(
                AssistantMessage(content=reply.text, tool_calls=tuple(effective_calls))
            )
            state.messages.extend(results)
            if interrupted:
                if await self._apply_pending_control(
                    state, batch_id=batch_id, round_index=round_index
                ):
                    return PhaseResult(exhausted=False, interrupted=True)
                continue

        self._control.close()
        return PhaseResult(exhausted=True, final_text=state.final_assistant_text)

    async def finalize(self, state: TurnState, *, reason: str) -> PhaseResult:
        """保持工具 schema 不变以复用前缀缓存，但只接受最终文字、不执行工具。"""
        state.model_round += 1
        round_index = state.model_round
        batch_id = f"{state.turn_id}:finalize"
        if await self._apply_pending_control(state, batch_id=batch_id, round_index=round_index):
            return PhaseResult(exhausted=False, interrupted=True)
        await self._drain_steering(state, batch_id=batch_id, round_index=round_index)

        state.messages.append(
            UserMessage(
                content=(
                    f"[系统收口指令] {reason}。不要再调用工具，也不要继续修改文件。"
                    "请基于当前已经完成的结果，用简洁中文总结：已完成内容、验证结果、"
                    "仍需注意的问题。"
                )
            )
        )
        streamed_parts: list[str] = []

        async def on_text(delta: str) -> None:
            streamed_parts.append(delta)
            await self._emit("text_delta", {"text": delta, "batchId": batch_id})

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
        task = asyncio.create_task(self._model.next_turn(request, on_text))
        self._control.bind_step(task, step_id=f"{state.turn_id}:finalize")
        reply = None
        try:
            reply = await task
        except asyncio.CancelledError:
            duration_ms = int((perf_counter() - started) * 1000)
            if not self._control.has_interrupt():
                if not task.done():
                    task.cancel()
                raise
            await self._journal.append_model_call(
                status=ModelCallStatus.CANCELLED,
                model_name=self._model_name,
                duration_ms=duration_ms,
                error_type="Interrupted",
                error_summary="用户中断最终收口",
            )
            await self._journal.commit()
            if await self._apply_pending_control(state, batch_id=batch_id, round_index=round_index):
                return PhaseResult(exhausted=False, interrupted=True)
            raise
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
        finally:
            self._control.clear_step(task)

        if reply is not None:
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
                cached_input_tokens=reply.cached_input_tokens,
                output_tokens=reply.output_tokens,
                retry_count=reply.retry_count,
            )
            await self._journal.commit()

        reply_text = reply.text if reply is not None else ""
        round_text = "".join(streamed_parts) if streamed_parts else reply_text
        final_text = round_text.strip()
        if not final_text:
            final_text = FINALIZATION_FALLBACK_TEXT
            await self._emit(
                "text_delta",
                {"text": final_text, "batchId": batch_id},
            )
        elif reply is not None and reply.text and not streamed_parts:
            await self._emit(
                "text_delta",
                {"text": reply.text, "batchId": batch_id},
            )

        state.last_round_text = final_text
        state.final_assistant_text = final_text
        state.persisted_assistant_text = True
        await self._journal.append_assistant(
            AssistantMessage(content=final_text),
            batch_id=batch_id,
        )
        await self._journal.commit()
        self._control.close()
        return PhaseResult(exhausted=False, final_text=final_text)

    async def _continue_truncated_output(
        self,
        state: TurnState,
        *,
        assistant_text: str,
    ) -> None:
        """保留截断文本，并用隐藏提示要求模型拆分剩余输出后继续。"""
        state.messages.append(
            AssistantMessage(content=assistant_text or "[上一轮输出因 max_tokens 截断]")
        )
        state.messages.append(UserMessage(content=TRUNCATED_OUTPUT_CONTINUATION))
        await self._journal.append_internal_user(TRUNCATED_OUTPUT_CONTINUATION)
        await self._journal.commit()

    async def _drain_steering(
        self,
        state: TurnState,
        *,
        batch_id: str,
        round_index: int,
    ) -> None:
        items = self._control.drain_steering()
        if not items:
            return
        texts: list[str] = []
        consumed_items: list[dict[str, str | None]] = []
        for text, command_id in items:
            texts.append(text)
            consumed_items.append({"commandId": command_id, "text": text})
            state.messages.append(UserMessage(content=text))
            await self._journal.append_user(text)
        await self._journal.commit()
        await self._emit(
            "steering_consumed",
            {
                "texts": texts,
                "items": consumed_items,
                "batchId": batch_id,
                "roundIndex": round_index,
            },
        )

    async def _apply_pending_control(
        self,
        state: TurnState,
        *,
        batch_id: str,
        round_index: int,
    ) -> bool:
        request = self._control.consume_interrupt()
        if request is None:
            return False
        reason = "stop" if request.stop else "redirect"
        await self._emit(
            "step_cancelled",
            {"reason": reason, "batchId": batch_id, "roundIndex": round_index},
        )
        state.messages.append(AssistantMessage(content=INTERRUPTION_PLACEHOLDER))
        await self._journal.append_assistant_placeholder(batch_id=batch_id)
        await self._journal.commit()
        if request.stop:
            self._control.close()
            await self._emit("turn_interrupted", {"reason": "user_stop"})
            return True
        for text in request.redirect_texts:
            state.messages.append(UserMessage(content=text))
            await self._journal.append_user(text)
        await self._journal.commit()
        await self._emit(
            "redirect_applied",
            {"texts": list(request.redirect_texts), "batchId": batch_id},
        )
        return False

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
    ) -> tuple[list[ToolCall], list[ToolResultMessage], bool]:
        effective_calls: list[ToolCall] = []
        results: list[ToolResultMessage] = []
        interrupted = False

        for index, original_call in enumerate(calls):
            if self._control.has_interrupt():
                await self._skip_remaining_tools(
                    state,
                    calls[index:],
                    effective_calls=effective_calls,
                    results=results,
                    batch_id=batch_id,
                )
                interrupted = True
                break

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
            if self._control.has_interrupt():
                await self._skip_remaining_tools(
                    state,
                    calls[index + 1 :],
                    effective_calls=effective_calls,
                    results=results,
                    batch_id=batch_id,
                )
                interrupted = True
                break
        return effective_calls, results, interrupted

    async def _skip_remaining_tools(
        self,
        state: TurnState,
        calls: list[ToolCall],
        *,
        effective_calls: list[ToolCall],
        results: list[ToolResultMessage],
        batch_id: str,
    ) -> None:
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
                output=SKIPPED_TOOL_OUTPUT,
                public_input=public_input,
                duration_ms=0,
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
                    is_error=True,
                )
            )
            await self._emit(
                "tool_finished",
                {
                    "toolCallId": call.id,
                    "name": call.name,
                    "batchId": batch_id,
                    "status": "failed",
                    "durationMs": 0,
                    "sizeBytes": None,
                    "preview": result.output,
                    **public_input,
                },
            )

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
        state.messages.append(AssistantMessage(content=assistant_text, tool_calls=tuple(calls)))
        state.messages.extend(results)
