import asyncio
import posixpath
import re
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

_BUILD_ERROR_LOG_PATH = ".ai-code-build-error.log"
_INLINE_ERROR_LOG_LIMIT = 4000
_MAX_BUILD_ERROR_LOG_CHARS = 900_000


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

    def repair_message(self, result: CompletionResult, workspace: Workspace) -> str: ...


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
        if result.ok:
            _clear_build_error_log(workspace)
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

    def repair_message(self, result: CompletionResult, workspace: Workspace) -> str:
        missing_imports = _missing_import_hints(result.output)
        hint_section = ""
        if missing_imports:
            hints = "\n".join(f"- {hint}" for hint in missing_imports)
            hint_section = (
                "检测到缺失模块引用：\n"
                f"{hints}\n"
                "必须创建对应视图文件或修正 import；"
                "不得通过删除路由、空视图或占位页面规避构建错误。\n\n"
            )
        return (
            "构建失败（或浏览器校验失败）。请先读取错误涉及的现有文件，再实际修复 Vue 页面、"
            "组件、路由或样式；只重复调用 build_project 不会解决问题。"
            "修复后再次调用 build_project。\n\n"
            f"{hint_section}"
            f"{_build_error_for_model(workspace, result.output)}"
        )


_MISSING_IMPORT_PATTERNS = (
    re.compile(r'Could not resolve "([^"]+)" from "([^"]+)"'),
    re.compile(r'Failed to resolve import "([^"]+)" from "([^"]+)"'),
)


def _missing_import_hints(output: str) -> list[str]:
    """从 Vite/Rollup 日志中提取缺失模块，避免关键错误被长堆栈淹没。"""
    hints: list[str] = []
    for pattern in _MISSING_IMPORT_PATTERNS:
        for specifier, importer in pattern.findall(output):
            target = specifier
            if specifier.startswith("."):
                target = posixpath.normpath(posixpath.join(posixpath.dirname(importer), specifier))
            detail = f"{importer} 引用的 {specifier} 未找到"
            if target != specifier:
                detail += f"（目标文件 {target}）"
            hints.append(detail)
    return list(dict.fromkeys(hints))


def _excerpt(text: str, limit: int) -> str:
    """保留日志首尾，优先让模型看到关键错误和最终堆栈。"""
    if len(text) <= limit:
        return text
    notice = "\n（已省略中部内容）\n"
    content_budget = limit - len(notice)
    head_size = content_budget // 2
    return f"{text[:head_size]}{notice}{text[-(content_budget - head_size) :]}"


def _persist_build_error_log(workspace: Workspace, output: str) -> str:
    content = output
    if len(content) > _MAX_BUILD_ERROR_LOG_CHARS:
        notice = "\n\n（日志超过安全长度，中部内容已省略）\n\n"
        budget = _MAX_BUILD_ERROR_LOG_CHARS - len(notice)
        head_size = budget // 2
        content = f"{content[:head_size]}{notice}{content[-(budget - head_size) :]}"
    workspace.write_file(_BUILD_ERROR_LOG_PATH, content)
    return _BUILD_ERROR_LOG_PATH


def _clear_build_error_log(workspace: Workspace) -> None:
    try:
        (workspace.root / _BUILD_ERROR_LOG_PATH).unlink(missing_ok=True)
    except OSError:
        return


def _build_error_for_model(workspace: Workspace, output: str) -> str:
    if len(output) <= _INLINE_ERROR_LOG_LIMIT:
        return f"错误日志：\n{output}"
    try:
        log_path = _persist_build_error_log(workspace, output)
    except (OSError, ValueError):
        return f"错误日志（摘要）：\n{_excerpt(output, _INLINE_ERROR_LOG_LIMIT)}"
    return (
        f"错误日志较长（{len(output)} 字符），完整日志已写入 {log_path}。"
        f"请立即调用 read_file 读取 {log_path}，根据完整日志定位并修复问题；"
        "不要反复调用 build_project，也不要依赖被截断的日志摘要。"
    )


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
    max_validation_repairs: int = 5,
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
                    raise ValueError(_build_error_for_model(workspace, result.output))
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
