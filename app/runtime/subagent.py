import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from app.runtime.hooks import HookBus
from app.runtime.journal import NullTurnJournal
from app.runtime.loop import AgentLoop, TurnState
from app.runtime.messages import UserMessage
from app.runtime.model import AgentModel
from app.runtime.tools import ToolRegistry
from app.runtime.workspace import Workspace

SubagentEmitter = Callable[[str, dict[str, Any]], Awaitable[None]]
_APP_LOCKS: dict[Path, asyncio.Lock] = {}
_SUMMARY_MAX_CHARS = 4000
_EVENT_PREVIEW_CHARS = 300


class SubagentRunner:
    """一级、顺序、受控目录的一次性子代理。"""

    def __init__(
        self,
        workspace: Workspace,
        model_factory: Callable[[str], AgentModel],
        *,
        code_gen_type: str,
        max_rounds: int,
        timeout_seconds: float,
    ) -> None:
        self._workspace = workspace
        self._model_factory = model_factory
        self._code_gen_type = code_gen_type
        if max_rounds < 1 or timeout_seconds <= 0:
            raise ValueError("子代理轮数和超时配置必须大于 0")
        self._max_rounds = max_rounds
        self._timeout_seconds = timeout_seconds
        self._lock = _APP_LOCKS.setdefault(workspace.root, asyncio.Lock())

    async def run(
        self,
        *,
        task: str,
        workdir: str,
        emit: SubagentEmitter,
        turn_id: str,
    ) -> str:
        if not task.strip():
            raise ValueError("子代理任务目标不能为空")
        if self._lock.locked():
            raise ValueError("当前 App 已有子代理运行")
        scope = self._scope(workdir)
        async with self._lock:
            try:
                async with asyncio.timeout(self._timeout_seconds):
                    return await self._run(task, scope, emit, turn_id)
            except TimeoutError as exc:
                await emit("subagent_failed", {"turnId": turn_id, "reason": "timeout"})
                raise ValueError("子代理执行超时") from exc
            except Exception as exc:
                await emit("subagent_failed", {"turnId": turn_id, "reason": "error"})
                if isinstance(exc, ValueError):
                    raise
                raise ValueError("子代理执行失败") from exc

    async def _run(
        self,
        task: str,
        scope: Path,
        emit: SubagentEmitter,
        turn_id: str,
    ) -> str:
        await emit("subagent_started", {"turnId": turn_id, "workdir": scope.name})
        model = self._model_factory(self._code_gen_type)

        async def ignore_event(event: str, payload: dict[str, Any]) -> None:
            return None

        loop = AgentLoop(
            model=model,
            tools=ToolRegistry(Workspace(scope)),
            journal=NullTurnJournal(),
            emit=ignore_event,
            hooks=HookBus(),
            max_rounds=self._max_rounds,
            fallback_text="子代理已完成",
        )
        state = TurnState(
            turn_id=turn_id,
            messages=[
                UserMessage(
                    content=(
                        "你是受控子代理。只处理指定目录内任务，不得提问或执行 shell。"
                        f"任务：{task}"
                    )
                )
            ],
        )
        result = await loop.run_phase(state)
        if result.exhausted:
            raise ValueError("子代理超过最大执行轮数")
        summary = (result.final_text.strip() or "子代理已完成")[:_SUMMARY_MAX_CHARS]
        await emit(
            "subagent_finished",
            {"turnId": turn_id, "summary": summary[:_EVENT_PREVIEW_CHARS]},
        )
        return summary

    def _scope(self, workdir: str) -> Path:
        if not workdir or Path(workdir).is_absolute() or ".." in Path(workdir).parts:
            raise ValueError("子代理目录必须是 Workspace 内的相对路径")
        scope = (self._workspace.root / workdir).resolve()
        if scope == self._workspace.root or not scope.is_relative_to(self._workspace.root):
            raise ValueError("子代理目录不得越出 Workspace")
        scope.mkdir(parents=True, exist_ok=True)
        return scope
