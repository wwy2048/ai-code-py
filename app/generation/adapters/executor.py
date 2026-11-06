from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.generation.adapters.codegen import CodegenProfile, build_codegen_profile
from app.generation.adapters.compaction import ContextCompactor
from app.generation.adapters.conversation import ConversationJournal
from app.generation.adapters.conversation_store import ConversationStore
from app.generation.adapters.debug_log import AgentDebugLogger, build_debug_hooks
from app.generation.adapters.memory import MemoryStore
from app.generation.adapters.subagent import SubagentRunner
from app.generation.adapters.tools import ToolRegistry
from app.generation.adapters.vue_project import VueProjectBuilder
from app.generation.adapters.workspace import Workspace
from app.generation.application.control import TurnControl
from app.generation.application.hooks import (
    HookBus,
    HookSet,
    TurnFailedContext,
    TurnFinishedContext,
    TurnStartedContext,
)
from app.generation.application.loop import AgentLoop, TurnState
from app.generation.domain.messages import AssistantMessage, UserMessage
from app.generation.domain.turns import TurnRequest
from app.generation.ports.model import AgentModel, ContextSummarizer
from app.generation.ports.tools import ToolExtension
from app.generation.ports.turn import EventEmitter, TurnController, TurnExecutionResult


class AgentTurnExecutor:
    """一个 Turn 的应用层装配门面；循环与持久化细节分别交给 AgentLoop 和 TurnJournal。"""

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
        max_rounds_per_phase: int = 12,
        max_validation_repairs: int = 3,
        hooks: HookSet | None = None,
    ) -> None:
        if max_rounds_per_phase < 1:
            raise ValueError("max_rounds_per_phase 必须大于 0")
        if max_validation_repairs < 1:
            raise ValueError("max_validation_repairs 必须大于 0")
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
        self._max_rounds_per_phase = max_rounds_per_phase
        self._max_validation_repairs = max_validation_repairs
        debug = AgentDebugLogger(
            enabled=agent_debug_log_enabled,
            max_chars=agent_debug_log_max_chars,
        )
        self._hooks = HookSet.combine(hooks, build_debug_hooks(debug))

    async def execute(
        self,
        command: TurnRequest,
        *,
        emit: EventEmitter,
        control: TurnController | None = None,
    ) -> TurnExecutionResult:
        app_id = command.app_id
        user_id = command.user_id
        code_gen_type = command.code_gen_type
        prompt = command.message
        turn_id = command.turn_id
        model = self._model_factory(code_gen_type)
        turn_control = control if isinstance(control, TurnControl) else TurnControl()
        async with self._session_factory() as session:
            conversation = ConversationStore(
                session,
                app_id=app_id,
                user_id=user_id,
                turn_id=turn_id,
            )
            await self._maybe_compact(
                conversation,
                model=model,
                prompt=prompt,
                emit=emit,
            )
            memory_store = MemoryStore(
                session,
                app_id=app_id,
                user_id=user_id,
                max_chars=self._memory_max_chars,
            )
            memory_context = await memory_store.load_for_context() if self._memory_enabled else []
            memory_message: str | None = None
            if memory_context:
                memory_message = ("[应用持久记忆]\n" + "\n\n".join(memory_context))[
                    : self._memory_max_chars
                ]
            history = await conversation.load_active_model_messages()
            await conversation.append_user(prompt)
            await conversation.append_turn_lifecycle("started")
            await conversation.commit()
            await emit("user_message_committed", {"text": prompt})

            workspace = Workspace(self._workspace_root_path / f"{code_gen_type}_{app_id}")
            profile = build_codegen_profile(
                code_gen_type,
                vue_builder=self._vue_builder,
                max_validation_repairs=self._max_validation_repairs,
            )
            profile.prepare(workspace)
            extension = self._combine_extensions(
                profile.tools(workspace, turn_id=turn_id, emit=emit),
                self._subagent_extension(
                    workspace=workspace,
                    code_gen_type=code_gen_type,
                    emit=emit,
                    turn_id=turn_id,
                ),
            )
            registry = ToolRegistry.with_extension(
                workspace,
                extension,
                allowed_write_paths=set(profile.allowed_write_paths),
            )
            state = TurnState(
                turn_id=turn_id,
                messages=[
                    *([UserMessage(content=memory_message)] if memory_message else []),
                    *history,
                    UserMessage(content=prompt),
                ],
            )
            journal = ConversationJournal(conversation)
            hook_bus = HookBus(self._hooks)
            hooks_started = False
            try:
                await hook_bus.turn_started(
                    TurnStartedContext(
                        turn_id=turn_id,
                        app_id=app_id,
                        user_id=user_id,
                        code_gen_type=code_gen_type,
                        prompt=prompt,
                    )
                )
                hooks_started = True
                loop = AgentLoop(
                    model=model,
                    tools=registry,
                    journal=journal,
                    emit=emit,
                    hooks=hook_bus,
                    control=turn_control,
                    max_rounds=self._max_rounds_per_phase,
                )
                status = await self._run_completion_passes(
                    loop=loop,
                    state=state,
                    profile=profile,
                    workspace=workspace,
                    journal=journal,
                    emit=emit,
                )
                if status == "interrupted":
                    await conversation.append_turn_lifecycle("finished", outcome="interrupted")
                    await conversation.commit()
                    await hook_bus.turn_finished(
                        TurnFinishedContext(
                            turn_id=turn_id,
                            status="interrupted",
                            assistant_text="",
                        )
                    )
                    return TurnExecutionResult(status="interrupted")

                # 这里只计算记忆摘要使用的最终文本，不把多轮文本拼成一条历史。
                assistant_text = state.final_assistant_text or state.last_round_text
                if not assistant_text and not state.persisted_assistant_text:
                    assistant_text = "页面已生成。"
                    await journal.append_assistant(
                        AssistantMessage(content=assistant_text),
                        batch_id=f"{turn_id}:final",
                    )
                if self._memory_enabled:
                    await memory_store.save_from_turn(turn_id, prompt, assistant_text)
                await conversation.append_turn_lifecycle("finished", outcome="completed")
                await conversation.commit()
                await hook_bus.turn_finished(
                    TurnFinishedContext(
                        turn_id=turn_id,
                        status="completed",
                        assistant_text=assistant_text,
                    )
                )
                return TurnExecutionResult(status="completed", assistant_text=assistant_text)
            except Exception as exc:
                try:
                    await conversation.append_turn_lifecycle("finished", outcome="failed")
                    await conversation.commit()
                except Exception:
                    await conversation.rollback()
                if hooks_started:
                    await hook_bus.turn_failed(TurnFailedContext(turn_id=turn_id, error=exc))
                raise

    async def _maybe_compact(
        self,
        conversation: ConversationStore,
        *,
        model: AgentModel,
        prompt: str,
        emit: EventEmitter,
    ) -> None:
        if self._context_compactor is None:
            return
        summarizer = model if isinstance(model, ContextSummarizer) else None
        await self._context_compactor.maybe_compact(
            conversation,
            summarizer=summarizer,
            pending_text=prompt,
            emit=emit,
        )

    async def _run_completion_passes(
        self,
        *,
        loop: AgentLoop,
        state: TurnState,
        profile: CodegenProfile,
        workspace: Workspace,
        journal: ConversationJournal,
        emit: EventEmitter,
    ) -> str:
        policy = profile.completion_policy
        max_passes = policy.max_passes if policy is not None else 1
        for attempt in range(max_passes):
            result = await loop.run_phase(state)
            if result.interrupted:
                return "interrupted"
            if policy is None:
                return "completed"

            validation = await policy.validate(workspace)
            status = "completed" if validation.ok else "failed"
            batch_id = f"{state.turn_id}:build:{attempt}"
            tool_call_id = f"final-build-{attempt}"
            await emit(
                "tool_started",
                {
                    "toolCallId": tool_call_id,
                    "name": policy.tool_name,
                    "batchId": batch_id,
                },
            )
            await emit(
                "tool_finished",
                {
                    "toolCallId": tool_call_id,
                    "name": policy.tool_name,
                    "batchId": batch_id,
                    "status": status,
                    "durationMs": 0,
                    "sizeBytes": validation.size_bytes,
                    "preview": validation.output[:300],
                },
            )
            if validation.ok:
                return "completed"
            if attempt == max_passes - 1:
                raise RuntimeError(f"Vue 构建失败: {validation.output[:1000]}")
            repair_message = policy.repair_message(validation)
            state.messages.append(UserMessage(content=repair_message))
            await journal.append_user(repair_message)
            await journal.commit()
        return "completed"

    def _subagent_extension(
        self,
        *,
        workspace: Workspace,
        code_gen_type: str,
        emit: EventEmitter,
        turn_id: str,
    ) -> ToolExtension:
        if not self._subagent_enabled:
            return ToolExtension()
        runner = SubagentRunner(
            workspace,
            self._model_factory,
            code_gen_type=code_gen_type,
            max_rounds=self._subagent_max_rounds,
            timeout_seconds=self._subagent_timeout_seconds,
        )

        async def delegate_subagent(arguments: dict[str, Any]) -> tuple[str, None]:
            summary = await runner.run(
                task=str(arguments["task"]),
                workdir=str(arguments["workdir"]),
                emit=emit,
                turn_id=turn_id,
            )
            return summary, None

        return ToolExtension(
            definitions=(
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
                },
            ),
            handlers={"delegate_subagent": delegate_subagent},
        )

    @staticmethod
    def _combine_extensions(*extensions: ToolExtension) -> ToolExtension:
        definitions: list[dict[str, Any]] = []
        handlers: dict[str, Any] = {}
        names: set[str] = set()
        for extension in extensions:
            for definition in extension.definitions:
                name = str(definition.get("name", ""))
                if not name:
                    raise ValueError("工具 schema 缺少 name")
                if name in names:
                    raise ValueError(f"工具重复注册: {name}")
                names.add(name)
                definitions.append(definition)
            for name, handler in extension.handlers.items():
                if name in handlers:
                    raise ValueError(f"工具 handler 重复注册: {name}")
                handlers[name] = handler
        return ToolExtension(definitions=tuple(definitions), handlers=handlers)
