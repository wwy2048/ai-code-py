import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from time import perf_counter
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.runtime.conversation import ConversationStore
from app.runtime.model import AgentModel, ModelCallStatus, ModelReply
from app.runtime.tools import ToolRegistry
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
    ) -> None:
        self._session_factory = session_factory
        self._workspace_root_path = workspace_root_path
        self._model_factory = model_factory
        self._vue_builder = vue_builder or VueProjectBuilder()
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
            registry = ToolRegistry(
                workspace,
                _ALLOWED_WRITE_PATHS.get(code_gen_type, set()),
                extra_tools,
                extra_handlers,
            )
            messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
            assistant_parts: list[str] = []

            async def run_model_rounds() -> None:
                for _ in range(12):
                    streamed_parts: list[str] = []

                    async def on_text(delta: str, parts: list[str] = streamed_parts) -> None:
                        parts.append(delta)
                        assistant_parts.append(delta)
                        await emit("text_delta", {"text": delta})

                    started = perf_counter()
                    try:
                        reply: ModelReply = await model.next_turn(
                            [{**message, "tools": registry.definitions} for message in messages],
                            on_text,
                        )
                    except Exception as exc:
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
                    await conversation.append_model_call(
                        status=ModelCallStatus.SUCCESS,
                        model_name=reply.model_name or getattr(model, "model_name", "unknown"),
                        duration_ms=int((perf_counter() - started) * 1000),
                        input_tokens=reply.input_tokens,
                        output_tokens=reply.output_tokens,
                        retry_count=reply.retry_count,
                    )
                    await conversation.commit()
                    if reply.text and not streamed_parts:
                        assistant_parts.append(reply.text)
                        await emit("text_delta", {"text": reply.text})
                    if not reply.tool_calls:
                        return
                    current_results: list[dict[str, Any]] = []
                    for call in reply.tool_calls:
                        await emit(
                            "tool_started",
                            {
                                "toolCallId": call.id,
                                "name": call.name,
                                **registry.public_input(call),
                            },
                        )
                        result = await registry.execute(call)
                        await conversation.append_tool_call(call)
                        await conversation.append_tool_result(call, result)
                        await conversation.commit()
                        current_results.append(
                            {
                                "role": "tool_result",
                                "tool_call_id": call.id,
                                "name": call.name,
                                "content": result.output,
                                "is_error": not result.ok,
                            }
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
                        {
                            "role": "assistant",
                            "content": reply.text,
                            "tool_calls": [
                                {"id": c.id, "name": c.name, "arguments": c.arguments}
                                for c in reply.tool_calls
                            ],
                        }
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

            assistant_text = "\n\n".join(assistant_parts).strip() or "页面已生成。"
            await conversation.append_assistant(assistant_text)
            await conversation.commit()
