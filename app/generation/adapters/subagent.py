import asyncio
import posixpath
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from app.generation.adapters.tools import ToolRegistry
from app.generation.adapters.workspace import Workspace
from app.generation.application.hooks import HookBus
from app.generation.application.loop import AgentLoop, TurnState
from app.generation.domain.messages import UserMessage
from app.generation.ports.journal import NullTurnJournal
from app.generation.ports.model import AgentModel

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
        allowed_write_paths: frozenset[str] | None = None,
    ) -> None:
        self._workspace = workspace
        self._model_factory = model_factory
        self._code_gen_type = code_gen_type
        self._allowed_write_paths = allowed_write_paths
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
        scoped_write_paths = self._scoped_write_paths(workdir)
        async with self._lock:
            try:
                async with asyncio.timeout(self._timeout_seconds):
                    return await self._run(
                        task,
                        scope,
                        emit,
                        turn_id,
                        allowed_write_paths=scoped_write_paths,
                    )
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
        *,
        allowed_write_paths: frozenset[str] | None,
    ) -> str:
        await emit("subagent_started", {"turnId": turn_id, "workdir": scope.name})
        model = self._model_factory(self._code_gen_type)

        async def ignore_event(event: str, payload: dict[str, Any]) -> None:
            return None

        loop = AgentLoop(
            model=model,
            tools=ToolRegistry(
                Workspace(scope),
                allowed_write_paths=(
                    set(allowed_write_paths) if allowed_write_paths is not None else None
                ),
            ),
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
                        f"你是受控子代理。只处理指定目录内任务，不得提问或执行 shell。任务：{task}"
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

    def _scoped_write_paths(self, workdir: str) -> frozenset[str] | None:
        if self._allowed_write_paths is None:
            return None
        scope = posixpath.normpath(workdir.replace("\\", "/")).strip("/")
        scoped: set[str] = set()
        for pattern in self._allowed_write_paths:
            if pattern.startswith(scope + "/"):
                scoped.add(pattern[len(scope) + 1 :])
                continue
            if pattern.endswith("/**"):
                prefix = pattern[:-3]
                if scope == prefix or scope.startswith(prefix + "/"):
                    scoped.add("**")
        if not scoped:
            raise ValueError("子代理目录不在允许写入范围")
        return frozenset(scoped)

    def _scope(self, workdir: str) -> Path:
        if not workdir or Path(workdir).is_absolute() or ".." in Path(workdir).parts:
            raise ValueError("子代理目录必须是 Workspace 内的相对路径")
        scope = (self._workspace.root / workdir).resolve()
        if scope == self._workspace.root or not scope.is_relative_to(self._workspace.root):
            raise ValueError("子代理目录不得越出 Workspace")
        scope.mkdir(parents=True, exist_ok=True)
        return scope
