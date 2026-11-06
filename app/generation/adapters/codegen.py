import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from app.generation.adapters.vue_project import (
    BuildResult,
    VueProjectBuilder,
    VueProjectTemplate,
    VueSourceSnapshot,
)
from app.generation.adapters.workspace import Workspace
from app.generation.ports.tools import ToolExtension

EventEmitter = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass(frozen=True)
class CompletionResult:
    ok: bool
    output: str
    size_bytes: int | None = None


class WorkspaceSnapshot(Protocol):
    def restore(self) -> None: ...

    def commit(self) -> None: ...


class CompletionPolicy(Protocol):
    """代码生成完成后的校验与修复策略。"""

    max_passes: int
    tool_name: str

    async def validate(self, workspace: Workspace) -> CompletionResult: ...

    def repair_message(self, result: CompletionResult) -> str: ...


class VueBuildCoordinator:
    """在同一 Turn 内复用相同源码指纹的构建结果，避免模型与完成策略重复构建。"""

    def __init__(self, builder: VueProjectBuilder) -> None:
        self._builder = builder
        self._last_digest: str | None = None
        self._last_result: BuildResult | None = None

    async def build(self, workspace: Workspace) -> BuildResult:
        digest = await asyncio.to_thread(self._builder.source_digest, workspace.root)
        if digest == self._last_digest and self._last_result is not None:
            return self._last_result
        result = await asyncio.to_thread(self._builder.build, workspace.root)
        self._last_digest = digest
        self._last_result = result
        return result


class VueCompletionPolicy:
    max_passes = 3
    tool_name = "build_project"

    def __init__(
        self,
        coordinator: VueBuildCoordinator,
        *,
        max_passes: int = 3,
    ) -> None:
        if max_passes < 1:
            raise ValueError("max_passes 必须大于 0")
        self._coordinator = coordinator
        self.max_passes = max_passes

    async def validate(self, workspace: Workspace) -> CompletionResult:
        result = await self._coordinator.build(workspace)
        return CompletionResult(
            ok=result.ok,
            output=result.output,
            size_bytes=result.dist_size_bytes,
        )

    def repair_message(self, result: CompletionResult) -> str:
        return (
            "构建失败（或浏览器校验失败），请先读取当前文件，再修复 Vue 页面、组件、路由或样式，"
            "完成后再次调用 build_project。错误日志（尾部）："
            f"{_tail(result.output, 3000)}"
        )


def _tail(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return f"(已省略前部 {omitted} 字符)\n{text[-limit:]}"


@dataclass(frozen=True)
class CodegenProfile:
    """一个 codeGenType 的组合式配置。"""

    code_gen_type: str
    allowed_write_paths: frozenset[str]
    prepare_workspace: Callable[[Workspace], None]
    build_tool_extension: Callable[[Workspace, str, EventEmitter], ToolExtension]
    completion_policy: CompletionPolicy | None = None
    snapshot_workspace: Callable[[Workspace], WorkspaceSnapshot | None] | None = None

    def prepare(self, workspace: Workspace) -> None:
        self.prepare_workspace(workspace)

    def snapshot(self, workspace: Workspace) -> WorkspaceSnapshot | None:
        if self.snapshot_workspace is None:
            return None
        return self.snapshot_workspace(workspace)

    def tools(
        self,
        workspace: Workspace,
        *,
        turn_id: str,
        emit: EventEmitter,
    ) -> ToolExtension:
        return self.build_tool_extension(workspace, turn_id, emit)


def _noop_prepare(workspace: Workspace) -> None:
    return None


def _no_tools(workspace: Workspace, turn_id: str, emit: EventEmitter) -> ToolExtension:
    return ToolExtension()


def build_codegen_profile(
    code_gen_type: str,
    *,
    vue_builder: VueProjectBuilder,
    max_validation_repairs: int = 3,
) -> CodegenProfile:
    """按 codeGenType 组合 Workspace、工具扩展和完成策略。"""
    if code_gen_type == "html":
        return CodegenProfile(
            code_gen_type=code_gen_type,
            allowed_write_paths=frozenset({"index.html"}),
            prepare_workspace=_noop_prepare,
            build_tool_extension=_no_tools,
        )
    if code_gen_type == "multi_file":
        return CodegenProfile(
            code_gen_type=code_gen_type,
            allowed_write_paths=frozenset({"index.html", "style.css", "script.js"}),
            prepare_workspace=_noop_prepare,
            build_tool_extension=_no_tools,
        )
    if code_gen_type == "vue_project":
        template = VueProjectTemplate()
        coordinator = VueBuildCoordinator(vue_builder)

        def build_vue_tools(
            workspace: Workspace,
            turn_id: str,
            emit: EventEmitter,
        ) -> ToolExtension:
            async def build_project(arguments: dict[str, Any]) -> tuple[str, int | None]:
                result = await coordinator.build(workspace)
                if not result.ok:
                    raise ValueError(result.output)
                return result.output, result.dist_size_bytes

            return ToolExtension(
                definitions=(
                    {
                        "name": "build_project",
                        "description": "执行受控 Vue 构建与浏览器校验，并返回错误日志。",
                        "input_schema": {"type": "object", "properties": {}},
                    },
                ),
                handlers={"build_project": build_project},
            )

        return CodegenProfile(
            code_gen_type=code_gen_type,
            allowed_write_paths=frozenset(
                {
                    "src/App.vue",
                    "src/style.css",
                    "src/router/routes.js",
                    "src/components/**",
                    "src/views/**",
                    "src/composables/**",
                    "src/data/**",
                    "src/utils/**",
                    "src/types/**",
                    "src/assets/**",
                    "public/**",
                }
            ),
            prepare_workspace=lambda workspace: template.prepare(workspace.root),
            build_tool_extension=build_vue_tools,
            completion_policy=VueCompletionPolicy(
                coordinator,
                max_passes=max_validation_repairs,
            ),
            snapshot_workspace=lambda workspace: VueSourceSnapshot(workspace.root),
        )
    raise ValueError(f"暂不支持的 codeGenType: {code_gen_type}")
