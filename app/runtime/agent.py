import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from time import perf_counter
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.runtime.compaction import ContextCompactor
from app.runtime.conversation import ConversationStore
from app.runtime.debug_log import AgentDebugLogger
from app.runtime.memory import MemoryStore
from app.runtime.messages import (
    assistant_tool_calls_message,
    tool_result_message,
    tool_use_block,
)
from app.runtime.model import AgentModel, ContextSummarizer, ModelCallStatus, ModelReply
from app.runtime.subagent import SubagentRunner
from app.runtime.tools import ToolExecutionResult, ToolRegistry
from app.runtime.vue_project import VueProjectBuilder, VueProjectTemplate
from app.runtime.workspace import Workspace

EventEmitter = Callable[[str, dict[str, Any]], Awaitable[None]]
_ALLOWED_WRITE_PATHS = {
    "html": {"index.html"},
    "multi_file": {"index.html", "style.css", "script.js"},
    "vue_project": {"src/App.vue", "src/style.css"},
}


class AgentTurnExecutor:
    """执行模型与工具循环，并把持久化职责交给 ConversationStore。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        workspace_root_path: Path,
        model_factory: Callable[[str], AgentModel],
        vue_builder: VueProjectBuilder | None = None,
        context_compactor: ContextCompactor | None = None,
        memory_enabled: bool = False,
        memory_max_chars: int = 4000,
        subagent_enabled: bool = False,
        subagent_max_rounds: int = 6,
        subagent_timeout_seconds: float = 60.0,
        agent_debug_log_enabled: bool = False,
        agent_debug_log_max_chars: int = 100000,
    ) -> None:
        self._session_factory = session_factory
        self._workspace_root_path = workspace_root_path
        self._model_factory = model_factory
        self._vue_builder = vue_builder or VueProjectBuilder()
        self._context_compactor = context_compactor
        self._memory_enabled = memory_enabled
        self._memory_max_chars = memory_max_chars
        self._subagent_enabled = subagent_enabled
        self._subagent_max_rounds = subagent_max_rounds
        self._subagent_timeout_seconds = subagent_timeout_seconds
        self._debug = AgentDebugLogger(
            enabled=agent_debug_log_enabled,
            max_chars=agent_debug_log_max_chars,
        )
        self._vue_template = VueProjectTemplate()

    async def execute(
        self,
        *,
        app_id: int,
        user_id: int,
        code_gen_type: str,
        prompt: str,
        turn_id: str,
        emit: EventEmitter,
    ) -> None:
        model = self._model_factory(code_gen_type)
        async with self._session_factory() as session:
            conversation = ConversationStore(
                session,
                app_id=app_id,
                user_id=user_id,
                turn_id=turn_id,
            )
            if self._context_compactor is not None:
                summarizer = model if isinstance(model, ContextSummarizer) else None
                await self._context_compactor.maybe_compact(
                    conversation,
                    summarizer=summarizer,
                    pending_text=prompt,
                    emit=emit,
                )
            memory_store = MemoryStore(
                session,
                app_id=app_id,
                user_id=user_id,
                max_chars=self._memory_max_chars,
            )
            memory_context = (
                await memory_store.load_for_context() if self._memory_enabled else []
            )
            memory_message: str | None = None
            if memory_context:
                memory_message = (
                    "[应用持久记忆]\n" + "\n\n".join(memory_context)
                )[: self._memory_max_chars]
            history = await conversation.load_active_model_messages()
            await conversation.append_user(prompt)
            await conversation.commit()

            workspace = Workspace(self._workspace_root_path / f"{code_gen_type}_{app_id}")
            if code_gen_type == "vue_project":
                self._vue_template.prepare(workspace.root)
            extra_tools: list[dict[str, Any]] = []
            extra_handlers: dict[str, Any] = {}
            if code_gen_type == "vue_project":
                extra_tools.append(
                    {
                        "name": "build_project",
                        "description": "执行受控 Vue 构建并返回错误日志。",
                        "input_schema": {"type": "object", "properties": {}},
                    }
                )

                async def build_project(arguments: dict[str, Any]) -> tuple[str, int | None]:
                    result = await asyncio.to_thread(self._vue_builder.build, workspace.root)
                    if not result.ok:
                        raise ValueError(result.output)
                    return result.output, result.dist_size_bytes

                extra_handlers["build_project"] = build_project
            if self._subagent_enabled:
                runner = SubagentRunner(
                    workspace,
                    self._model_factory,
                    code_gen_type=code_gen_type,
                    max_rounds=self._subagent_max_rounds,
                    timeout_seconds=self._subagent_timeout_seconds,
                )
                extra_tools.append(
                    {
                        "name": "delegate_subagent",
                        "description": "把明确范围的子任务委派给一级子代理，仅返回最终摘要。",
                        "input_schema": {
                            "type": "object",
                            "properties": {
                                "task": {"type": "string"},
                                "workdir": {"type": "string"},
                            },
                            "required": ["task", "workdir"],
                        },
                    }
                )

                async def delegate_subagent(
                    arguments: dict[str, Any],
                ) -> tuple[str, None]:
                    summary = await runner.run(
                        task=str(arguments["task"]),
                        workdir=str(arguments["workdir"]),
                        emit=emit,
                        turn_id=turn_id,
                    )
                    return summary, None

                extra_handlers["delegate_subagent"] = delegate_subagent
            registry = ToolRegistry(
                workspace,
                _ALLOWED_WRITE_PATHS.get(code_gen_type, set()),
                extra_tools,
                extra_handlers,
            )
            messages: list[dict[str, Any]] = [
                *(
                    [{"role": "user", "content": memory_message}]
                    if memory_message
                    else []
                ),
                *history,
                {"role": "user", "content": prompt},
            ]
            assistant_round_texts: list[str] = []
            model_round = 0

            async def run_model_rounds() -> None:
                nonlocal model_round
                for _ in range(12):
                    model_round += 1
                    batch_id = f"{turn_id}:{model_round}"
                    streamed_parts: list[str] = []

                    async def on_text(delta: str, parts: list[str] = streamed_parts) -> None:
                        parts.append(delta)
                        await emit("text_delta", {"text": delta})

                    started = perf_counter()
                    self._debug.llm_request(
                        turn_id=turn_id,
                        round_index=model_round,
                        model_name=getattr(model, "model_name", "unknown"),
                        messages=messages,
                        tools=registry.definitions,
                    )
                    try:
                        reply: ModelReply = await model.next_turn(
                            [{**message, "tools": registry.definitions} for message in messages],
                            on_text,
                        )
                    except Exception as exc:
                        self._debug.llm_error(
                            turn_id=turn_id,
                            round_index=model_round,
                            error=exc,
                            duration_ms=int((perf_counter() - started) * 1000),
                        )
                        await conversation.append_model_call(
                            status=ModelCallStatus.ERROR,
                            model_name=getattr(model, "model_name", "unknown"),
                            duration_ms=int((perf_counter() - started) * 1000),
                            error_type=type(exc).__name__,
                            error_summary=str(getattr(exc, "summary", type(exc).__name__))[:256],
                            retry_count=int(getattr(exc, "retry_count", 0)),
                        )
                        await conversation.commit()
                        raise
                    self._debug.llm_response(
                        turn_id=turn_id,
                        round_index=model_round,
                        reply=reply,
                        duration_ms=int((perf_counter() - started) * 1000),
                    )
                    await conversation.append_model_call(
                        status=ModelCallStatus.SUCCESS,
                        model_name=reply.model_name or getattr(model, "model_name", "unknown"),
                        duration_ms=int((perf_counter() - started) * 1000),
                        input_tokens=reply.input_tokens,
                        output_tokens=reply.output_tokens,
                        retry_count=reply.retry_count,
                    )
                    await conversation.commit()
                    round_text = "".join(streamed_parts) if streamed_parts else reply.text
                    if round_text.strip():
                        assistant_round_texts.append(round_text.strip())
                    if reply.text and not streamed_parts:
                        await emit("text_delta", {"text": reply.text})
                    if reply.stop_reason == "max_tokens":
                        if not reply.tool_calls:
                            raise RuntimeError(
                                "模型输出达到 token 上限，请缩短需求或拆分为多个回合"
                            )
                        truncated_results: list[dict[str, Any]] = []
                        for call in reply.tool_calls:
                            self._debug.tool_started(turn_id=turn_id, call=call)
                            result = ToolExecutionResult(
                                ok=False,
                                output=(
                                    "工具调用因模型输出达到 max_tokens 被截断，未执行。"
                                    "请把大文件拆成最小骨架和多次较小的 edit_file 调用。"
                                ),
                                public_input=registry.public_input(call),
                                duration_ms=0,
                            )
                            await conversation.append_tool_call(
                                call,
                                batch_id=batch_id,
                                assistant_text=reply.text,
                            )
                            await conversation.append_tool_result(
                                call,
                                result,
                                batch_id=batch_id,
                            )
                            await conversation.commit()
                            truncated_results.append(
                                tool_result_message(
                                    tool_call_id=call.id,
                                    name=call.name,
                                    content=result.output,
                                    is_error=True,
                                )
                            )
                            await emit(
                                "tool_finished",
                                {
                                    "toolCallId": call.id,
                                    "name": call.name,
                                    "status": "failed",
                                    "durationMs": 0,
                                    "sizeBytes": None,
                                    "preview": result.output,
                                    **result.public_input,
                                },
                            )
                            self._debug.tool_finished(
                                turn_id=turn_id,
                                call=call,
                                ok=False,
                                output=result.output,
                                duration_ms=0,
                                size_bytes=None,
                            )
                        messages.append(
                            assistant_tool_calls_message(
                                [
                                    tool_use_block(
                                        tool_call_id=call.id,
                                        name=call.name,
                                        arguments=call.arguments,
                                    )
                                    for call in reply.tool_calls
                                ],
                                content=reply.text,
                            )
                        )
                        messages.extend(truncated_results)
                        continue
                    if not reply.tool_calls:
                        return
                    current_results: list[dict[str, Any]] = []
                    for call in reply.tool_calls:
                        self._debug.tool_started(turn_id=turn_id, call=call)
                        await emit(
                            "tool_started",
                            {
                                "toolCallId": call.id,
                                "name": call.name,
                                **registry.public_input(call),
                            },
                        )
                        result = await registry.execute(call)
                        self._debug.tool_finished(
                            turn_id=turn_id,
                            call=call,
                            ok=result.ok,
                            output=result.output,
                            duration_ms=result.duration_ms,
                            size_bytes=result.size_bytes,
                        )
                        await conversation.append_tool_call(
                            call,
                            batch_id=batch_id,
                            assistant_text=reply.text,
                        )
                        await conversation.append_tool_result(
                            call,
                            result,
                            batch_id=batch_id,
                        )
                        await conversation.commit()
                        current_results.append(
                            tool_result_message(
                                tool_call_id=call.id,
                                name=call.name,
                                content=result.output,
                                is_error=not result.ok,
                            )
                        )
                        await emit(
                            "tool_finished",
                            {
                                "toolCallId": call.id,
                                "name": call.name,
                                "status": "completed" if result.ok else "failed",
                                "durationMs": result.duration_ms,
                                "sizeBytes": result.size_bytes,
                                "preview": result.output[:300],
                                **result.public_input,
                            },
                        )
                    messages.append(
                        assistant_tool_calls_message(
                            [
                                tool_use_block(
                                    tool_call_id=c.id,
                                    name=c.name,
                                    arguments=c.arguments,
                                )
                                for c in reply.tool_calls
                            ],
                            content=reply.text,
                        )
                    )
                    messages.extend(current_results)

            if code_gen_type == "vue_project":
                for attempt in range(3):
                    await run_model_rounds()
                    build_result = await asyncio.to_thread(self._vue_builder.build, workspace.root)
                    status = "completed" if build_result.ok else "failed"
                    await emit(
                        "tool_started",
                        {"toolCallId": f"final-build-{attempt}", "name": "build_project"},
                    )
                    await emit(
                        "tool_finished",
                        {
                            "toolCallId": f"final-build-{attempt}",
                            "name": "build_project",
                            "status": status,
                            "durationMs": 0,
                            "sizeBytes": build_result.dist_size_bytes,
                            "preview": build_result.output[:300],
                        },
                    )
                    if build_result.ok:
                        break
                    if attempt == 2:
                        raise RuntimeError(f"Vue 构建失败: {build_result.output[:1000]}")
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "构建失败，请修复 src/App.vue 或 src/style.css 后再次"
                                f"调用 build_project。错误日志：{build_result.output[:3000]}"
                            ),
                        }
                    )
            else:
                await run_model_rounds()

            assistant_text = "\n\n".join(assistant_round_texts).strip() or "页面已生成。"
            await conversation.append_assistant(assistant_text)
            if self._memory_enabled:
                await memory_store.save_from_turn(turn_id, prompt, assistant_text)
            await conversation.commit()
