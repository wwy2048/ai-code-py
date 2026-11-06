import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, replace
from typing import Any, Literal

from app.generation.domain.messages import ToolCall
from app.generation.ports.model import ModelReply, ModelRequest
from app.generation.ports.tools import ToolExecutionResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TurnStartedContext:
    turn_id: str
    app_id: int
    user_id: int
    code_gen_type: str
    prompt: str


@dataclass(frozen=True)
class BeforeModelContext:
    turn_id: str
    round_index: int
    request: ModelRequest


@dataclass(frozen=True)
class AfterModelContext:
    turn_id: str
    round_index: int
    request: ModelRequest
    reply: ModelReply
    duration_ms: int


@dataclass(frozen=True)
class ModelFailedContext:
    turn_id: str
    round_index: int
    request: ModelRequest
    error: Exception
    duration_ms: int


@dataclass(frozen=True)
class BeforeToolContext:
    turn_id: str
    round_index: int
    batch_id: str
    call: ToolCall


@dataclass(frozen=True)
class AfterToolContext:
    turn_id: str
    round_index: int
    batch_id: str
    call: ToolCall
    result: ToolExecutionResult


@dataclass(frozen=True)
class TurnFinishedContext:
    turn_id: str
    status: Literal["completed", "failed", "interrupted"]
    assistant_text: str = ""


@dataclass(frozen=True)
class TurnFailedContext:
    turn_id: str
    error: Exception


@dataclass(frozen=True)
class BeforeToolOutcome:
    """工具门禁结果；allow 可带改写后的 call，deny 必须说明原因。"""

    action: Literal["allow", "deny"] = "allow"
    call: ToolCall | None = None
    message: str = "工具调用被 Hook 拒绝"

    @classmethod
    def allow(cls, call: ToolCall | None = None) -> "BeforeToolOutcome":
        return cls(action="allow", call=call)

    @classmethod
    def deny(cls, message: str) -> "BeforeToolOutcome":
        return cls(action="deny", message=message)

    @classmethod
    def deny_with_call(cls, message: str, call: ToolCall) -> "BeforeToolOutcome":
        return cls(action="deny", call=call, message=message)


BeforeModelHook = Callable[[BeforeModelContext], Awaitable[None]]
AfterModelHook = Callable[[AfterModelContext], Awaitable[None]]
ModelFailedHook = Callable[[ModelFailedContext], Awaitable[None]]
BeforeToolHook = Callable[[BeforeToolContext], Awaitable[BeforeToolOutcome | None]]
AfterToolHook = Callable[[AfterToolContext], Awaitable[ToolExecutionResult | None]]
TurnStartedHook = Callable[[TurnStartedContext], Awaitable[None]]
TurnFinishedHook = Callable[[TurnFinishedContext], Awaitable[None]]
TurnFailedHook = Callable[[TurnFailedContext], Awaitable[None]]


@dataclass(frozen=True)
class HookSet:
    """一次装配得到的不可变 Hook 集合，不使用全局注册表。"""

    before_model: tuple[BeforeModelHook, ...] = ()
    after_model: tuple[AfterModelHook, ...] = ()
    model_failed: tuple[ModelFailedHook, ...] = ()
    before_tool: tuple[BeforeToolHook, ...] = ()
    after_tool: tuple[AfterToolHook, ...] = ()
    turn_started: tuple[TurnStartedHook, ...] = ()
    turn_finished: tuple[TurnFinishedHook, ...] = ()
    turn_failed: tuple[TurnFailedHook, ...] = ()

    @classmethod
    def combine(cls, *sets: "HookSet | None") -> "HookSet":
        available = [item for item in sets if item is not None]
        return cls(
            before_model=tuple(hook for item in available for hook in item.before_model),
            after_model=tuple(hook for item in available for hook in item.after_model),
            model_failed=tuple(hook for item in available for hook in item.model_failed),
            before_tool=tuple(hook for item in available for hook in item.before_tool),
            after_tool=tuple(hook for item in available for hook in item.after_tool),
            turn_started=tuple(hook for item in available for hook in item.turn_started),
            turn_finished=tuple(hook for item in available for hook in item.turn_finished),
            turn_failed=tuple(hook for item in available for hook in item.turn_failed),
        )


class HookBus:
    """按明确生命周期执行 Hook；门禁失败时采用 fail-closed。"""

    def __init__(self, hooks: HookSet | None = None) -> None:
        self._hooks = hooks or HookSet()

    async def turn_started(self, context: TurnStartedContext) -> None:
        await self._notify(self._hooks.turn_started, context)

    async def turn_finished(self, context: TurnFinishedContext) -> None:
        await self._notify(self._hooks.turn_finished, context)

    async def turn_failed(self, context: TurnFailedContext) -> None:
        await self._notify(self._hooks.turn_failed, context)

    async def before_model(self, context: BeforeModelContext) -> None:
        await self._notify(self._hooks.before_model, context)

    async def after_model(self, context: AfterModelContext) -> None:
        await self._notify(self._hooks.after_model, context)

    async def model_failed(self, context: ModelFailedContext) -> None:
        await self._notify(self._hooks.model_failed, context)

    async def before_tool(self, context: BeforeToolContext) -> BeforeToolOutcome:
        current_call = context.call
        for hook in self._hooks.before_tool:
            try:
                outcome = await hook(replace(context, call=current_call))
            except Exception as exc:
                logger.exception("before_tool Hook 执行失败")
                return BeforeToolOutcome.deny(f"工具调用被 Hook 拒绝: {type(exc).__name__}")
            if outcome is None:
                continue
            if outcome.action == "deny":
                return BeforeToolOutcome.deny_with_call(
                    outcome.message,
                    outcome.call or current_call,
                )
            if outcome.call is not None:
                current_call = outcome.call
        return BeforeToolOutcome.allow(current_call)

    async def after_tool(self, context: AfterToolContext) -> ToolExecutionResult:
        current_result = context.result
        for hook in self._hooks.after_tool:
            try:
                replacement = await hook(replace(context, result=current_result))
            except Exception as exc:
                logger.exception("after_tool Hook 执行失败")
                return ToolExecutionResult(
                    ok=False,
                    output=f"工具结果被 Hook 拒绝: {type(exc).__name__}",
                    public_input=current_result.public_input,
                    duration_ms=current_result.duration_ms,
                    size_bytes=current_result.size_bytes,
                )
            if replacement is not None:
                current_result = replacement
        return current_result

    @staticmethod
    async def _notify(
        hooks: Iterable[Callable[[Any], Awaitable[None]]],
        context: Any,
    ) -> None:
        for hook in hooks:
            try:
                await hook(context)
            except Exception:
                # 只读 Hook 不得改变执行结果；错误保留在日志中便于排查。
                logger.exception("只读 Hook 执行失败: %s", type(context).__name__)
