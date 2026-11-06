import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from app.generation.adapters.vue_project import VueProjectBuilder, VueProjectTemplate
from app.generation.adapters.workspace import Workspace
from app.generation.ports.tools import ToolExtension

EventEmitter = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass(frozen=True)
class CompletionResult:
    ok: bool
    output: str
    size_bytes: int | None = None


class CompletionPolicy(Protocol):
    """代码生成完成后的校验与修复策略。"""

    max_passes: int
    tool_name: str

    async def validate(self, workspace: Workspace) -> CompletionResult: ...

    def repair_message(self, result: CompletionResult) -> str: ...


class VueCompletionPolicy:
    max_passes = 3
    tool_name = "build_project"

    def __init__(self, builder: VueProjectBuilder, *, max_passes: int = 3) -> None:
        if max_passes < 1:
            raise ValueError("max_passes 必须大于 0")
        self._builder = builder
        self.max_passes = max_passes

    async def validate(self, workspace: Workspace) -> CompletionResult:
        result = await asyncio.to_thread(self._builder.build, workspace.root)
        return CompletionResult(
            ok=result.ok,
            output=result.output,
            size_bytes=result.dist_size_bytes,
        )

    def repair_message(self, result: CompletionResult) -> str:
        return (
            "构建失败，请修复 src/App.vue 或 src/style.css 后再次"
            f"调用 build_project。错误日志：{result.output[:3000]}"
        )


@dataclass(frozen=True)
class CodegenProfile:
    """一个 codeGenType 的组合式配置。"""

    code_gen_type: str
    allowed_write_paths: frozenset[str]
    prepare_workspace: Callable[[Workspace], None]
    build_tool_extension: Callable[[Workspace, str, EventEmitter], ToolExtension]
    completion_policy: CompletionPolicy | None = None

    def prepare(self, workspace: Workspace) -> None:
        self.prepare_workspace(workspace)

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

        def build_vue_tools(
            workspace: Workspace,
            turn_id: str,
            emit: EventEmitter,
        ) -> ToolExtension:
            async def build_project(arguments: dict[str, Any]) -> tuple[str, int | None]:
                result = await asyncio.to_thread(vue_builder.build, workspace.root)
                if not result.ok:
                    raise ValueError(result.output)
                return result.output, result.dist_size_bytes

            return ToolExtension(
                definitions=(
                    {
                        "name": "build_project",
                        "description": "执行受控 Vue 构建并返回错误日志。",
                        "input_schema": {"type": "object", "properties": {}},
                    },
                ),
                handlers={"build_project": build_project},
            )

        return CodegenProfile(
            code_gen_type=code_gen_type,
            allowed_write_paths=frozenset({"src/App.vue", "src/style.css"}),
            prepare_workspace=lambda workspace: template.prepare(workspace.root),
            build_tool_extension=build_vue_tools,
            completion_policy=VueCompletionPolicy(
                vue_builder,
                max_passes=max_validation_repairs,
            ),
        )
    raise ValueError(f"暂不支持的 codeGenType: {code_gen_type}")
