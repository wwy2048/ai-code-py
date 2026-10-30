from dataclasses import dataclass
from time import perf_counter
from typing import Any

from app.runtime.model import ToolCall
from app.runtime.workspace import Workspace


@dataclass(frozen=True)
class ToolExecutionResult:
    ok: bool
    output: str
    public_input: dict[str, Any]
    duration_ms: int
    size_bytes: int | None = None


TOOL_DEFINITIONS = [
    {
        'name': 'read_file',
        'description': '读取 Workspace 中的 UTF-8 文本文件。',
        'input_schema': {
            'type': 'object',
            'properties': {'path': {'type': 'string'}},
            'required': ['path'],
        },
    },
    {
        'name': 'write_file',
        'description': '向 Workspace 写入一个 UTF-8 文本文件。',
        'input_schema': {
            'type': 'object',
            'properties': {
                'path': {'type': 'string'},
                'content': {'type': 'string'},
            },
            'required': ['path', 'content'],
        },
    },
    {
        'name': 'edit_file',
        'description': '替换 Workspace 文本文件中的一段文本。',
        'input_schema': {
            'type': 'object',
            'properties': {
                'path': {'type': 'string'},
                'old_text': {'type': 'string'},
                'new_text': {'type': 'string'},
            },
            'required': ['path', 'old_text', 'new_text'],
        },
    },
    {
        'name': 'glob',
        'description': '列出 Workspace 中匹配模式的文本文件。',
        'input_schema': {
            'type': 'object',
            'properties': {'pattern': {'type': 'string'}},
            'required': ['pattern'],
        },
    },
]


class ToolRegistry:
    def __init__(
        self,
        workspace: Workspace,
        allowed_write_paths: set[str] | None = None,
    ) -> None:
        self._workspace = workspace
        self._allowed_write_paths = allowed_write_paths
        self._handlers = {
            'read_file': self._read_file,
            'write_file': self._write_file,
            'edit_file': self._edit_file,
            'glob': self._glob,
        }

    @staticmethod
    def public_input(call: ToolCall) -> dict[str, Any]:
        allowed = ('path', 'pattern')
        return {key: call.arguments[key] for key in allowed if key in call.arguments}

    async def execute(self, call: ToolCall) -> ToolExecutionResult:
        started = perf_counter()
        handler = self._handlers.get(call.name)
        if handler is None:
            return ToolExecutionResult(
                ok=False,
                output=f'未知工具: {call.name}',
                public_input=self.public_input(call),
                duration_ms=0,
            )
        size_bytes: int | None = None
        try:
            output, size_bytes = handler(call.arguments)
            ok = True
        except (KeyError, TypeError, ValueError, OSError) as exc:
            output = f'工具执行失败: {exc}'
            ok = False
        duration_ms = int((perf_counter() - started) * 1000)
        return ToolExecutionResult(
            ok=ok,
            output=output,
            public_input=self.public_input(call),
            duration_ms=duration_ms,
            size_bytes=size_bytes,
        )

    def _read_file(self, arguments: dict[str, Any]) -> tuple[str, int]:
        content = self._workspace.read_file(str(arguments['path']))
        return content, len(content.encode('utf-8'))

    def _validate_write_path(self, path: str) -> None:
        if self._allowed_write_paths is not None and path not in self._allowed_write_paths:
            raise ValueError(f'当前 codeGenType 不允许写入文件: {path}')

    def _write_file(self, arguments: dict[str, Any]) -> tuple[str, int]:
        path = str(arguments['path'])
        self._validate_write_path(path)
        content = str(arguments['content'])
        self._workspace.write_file(path, content)
        size_bytes = len(content.encode('utf-8'))
        return f'已写入 {path}（{size_bytes} 字节）', size_bytes

    def _edit_file(self, arguments: dict[str, Any]) -> tuple[str, int]:
        path = str(arguments['path'])
        self._validate_write_path(path)
        self._workspace.edit_file(
            path,
            str(arguments['old_text']),
            str(arguments['new_text']),
        )
        content = self._workspace.read_file(path)
        return f'已更新 {path}', len(content.encode('utf-8'))

    def _glob(self, arguments: dict[str, Any]) -> tuple[str, None]:
        files = self._workspace.glob(str(arguments['pattern']))
        return ('\n'.join(files) if files else '没有匹配的文件'), None
