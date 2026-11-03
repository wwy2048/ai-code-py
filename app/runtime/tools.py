import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

from app.runtime.messages import ToolCall
from app.runtime.tool_definitions import TOOL_DEFINITIONS
from app.runtime.workspace import Workspace

ToolHandlerResult = tuple[str, int | None]
ToolHandler = Callable[[dict[str, Any]], ToolHandlerResult | Awaitable[ToolHandlerResult]]


@dataclass(frozen=True)
class ToolExecutionResult:
    ok: bool
    output: str
    public_input: dict[str, Any]
    duration_ms: int
    size_bytes: int | None = None


@dataclass(frozen=True)
class ToolExtension:
    """代码生成配置追加的工具 schema 与 handler 映射。"""

    definitions: tuple[dict[str, Any], ...] = ()
    handlers: Mapping[str, ToolHandler] = field(default_factory=dict)


class ToolRegistry:
    """组合一个回合实际暴露的工具池，并负责统一参数校验与执行。"""

    def __init__(
        self,
        workspace: Workspace,
        allowed_write_paths: set[str] | None = None,
        extra_tools: Sequence[dict[str, Any]] | None = None,
        extra_handlers: Mapping[str, ToolHandler] | None = None,
        *,
        public_input_keys: tuple[str, ...] = ("path", "pattern"),
    ) -> None:
        self._workspace = workspace
        self._allowed_write_paths = allowed_write_paths
        self._public_input_keys = public_input_keys
        base_names = {str(item["name"]) for item in TOOL_DEFINITIONS}
        extras = list(extra_tools or [])
        extra_names = [str(item.get("name", "")) for item in extras]
        if any(not name for name in extra_names):
            raise ValueError("追加工具 schema 缺少 name")
        duplicates = (base_names & set(extra_names)) | (
            {name for name in extra_names if extra_names.count(name) > 1}
        )
        if duplicates:
            raise ValueError(f"工具 schema 重复注册: {', '.join(sorted(duplicates))}")
        extra_handler_map = dict(extra_handlers or {})
        handler_duplicates = base_names & set(extra_handler_map)
        if handler_duplicates:
            raise ValueError(f"工具 handler 重复注册: {', '.join(sorted(handler_duplicates))}")
        missing_handlers = set(extra_names) - set(extra_handler_map)
        orphan_handlers = set(extra_handler_map) - set(extra_names)
        if missing_handlers:
            raise ValueError(f"工具缺少 handler: {', '.join(sorted(missing_handlers))}")
        if orphan_handlers:
            raise ValueError(f"工具 handler 缺少 schema: {', '.join(sorted(orphan_handlers))}")
        self.definitions = [*TOOL_DEFINITIONS, *extras]
        self._handlers: dict[str, ToolHandler] = {
            "read_file": self._read_file,
            "write_file": self._write_file,
            "edit_file": self._edit_file,
            "glob": self._glob,
            **extra_handler_map,
        }

    @classmethod
    def with_extension(
        cls,
        workspace: Workspace,
        extension: ToolExtension,
        *,
        allowed_write_paths: set[str] | None = None,
        public_input_keys: tuple[str, ...] = ("path", "pattern"),
    ) -> "ToolRegistry":
        return cls(
            workspace,
            allowed_write_paths,
            extension.definitions,
            extension.handlers,
            public_input_keys=public_input_keys,
        )

    def public_input(self, call: ToolCall) -> dict[str, Any]:
        return {
            key: call.arguments[key]
            for key in self._public_input_keys
            if key in call.arguments
        }

    async def execute(self, call: ToolCall) -> ToolExecutionResult:
        started = perf_counter()
        handler = self._handlers.get(call.name)
        if handler is None:
            return ToolExecutionResult(
                ok=False,
                output=f"未知工具: {call.name}",
                public_input=self.public_input(call),
                duration_ms=0,
            )
        definition = next(
            (item for item in self.definitions if item.get("name") == call.name),
            None,
        )
        required = (
            definition.get("input_schema", {}).get("required", [])
            if definition is not None
            else []
        )
        missing = [key for key in required if key not in call.arguments]
        if missing:
            return ToolExecutionResult(
                ok=False,
                output=f"工具 {call.name} 缺少必需参数: {', '.join(missing)}",
                public_input=self.public_input(call),
                duration_ms=int((perf_counter() - started) * 1000),
            )
        size_bytes: int | None = None
        try:
            handled = handler(call.arguments)
            if inspect.isawaitable(handled):
                handled = await handled
            output, size_bytes = handled
            ok = True
        except (KeyError, TypeError, ValueError, OSError) as exc:
            output = f"工具执行失败: {exc}"
            ok = False
        duration_ms = int((perf_counter() - started) * 1000)
        return ToolExecutionResult(
            ok=ok,
            output=output,
            public_input=self.public_input(call),
            duration_ms=duration_ms,
            size_bytes=size_bytes,
        )

    def _read_file(self, arguments: dict[str, Any]) -> ToolHandlerResult:
        content = self._workspace.read_file(str(arguments["path"]))
        return content, len(content.encode("utf-8"))

    def _validate_write_path(self, path: str) -> None:
        if self._allowed_write_paths is not None and path not in self._allowed_write_paths:
            raise ValueError(f"当前 codeGenType 不允许写入文件: {path}")

    def _write_file(self, arguments: dict[str, Any]) -> ToolHandlerResult:
        path = str(arguments["path"])
        self._validate_write_path(path)
        content = str(arguments["content"])
        self._workspace.write_file(path, content)
        size_bytes = len(content.encode("utf-8"))
        return f"已写入 {path}（{size_bytes} 字节）", size_bytes

    def _edit_file(self, arguments: dict[str, Any]) -> ToolHandlerResult:
        path = str(arguments["path"])
        self._validate_write_path(path)
        self._workspace.edit_file(
            path,
            str(arguments["old_text"]),
            str(arguments["new_text"]),
        )
        content = self._workspace.read_file(path)
        return f"已更新 {path}", len(content.encode("utf-8"))

    def _glob(self, arguments: dict[str, Any]) -> ToolHandlerResult:
        files = self._workspace.glob(str(arguments["pattern"]))
        return ("\n".join(files) if files else "没有匹配的文件"), None
