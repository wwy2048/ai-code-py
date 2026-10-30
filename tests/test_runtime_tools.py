from pathlib import Path

import pytest

from app.runtime.model import ToolCall
from app.runtime.tools import ToolRegistry
from app.runtime.workspace import Workspace


def test_workspace_file_tools_write_read_edit_and_glob(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)

    workspace.write_file('index.html', '<h1>Hello</h1>')
    workspace.edit_file('index.html', 'Hello', 'World')

    assert workspace.read_file('index.html') == '<h1>World</h1>'
    assert workspace.glob('*.html') == ['index.html']


@pytest.mark.parametrize('path', ['../outside.txt', 'sub/../../outside.txt', '/absolute.txt'])
def test_workspace_rejects_paths_outside_root(tmp_path: Path, path: str) -> None:
    workspace = Workspace(tmp_path)

    with pytest.raises(ValueError):
        workspace.write_file(path, 'no')


def test_workspace_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / 'outside-runtime-root.txt'
    outside.write_text('outside', encoding='utf-8')
    workspace_root = tmp_path / 'workspace'
    workspace_root.mkdir()
    link = workspace_root / 'escape.txt'
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip('当前系统不允许创建符号链接')

    workspace = Workspace(workspace_root)

    with pytest.raises(ValueError):
        workspace.read_file('escape.txt')


def test_workspace_rejects_glob_escape(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)

    with pytest.raises(ValueError):
        workspace.glob('../*')


async def test_tool_registry_returns_readable_failure_for_missing_arguments(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry(Workspace(tmp_path))

    result = await registry.execute(
        ToolCall(id='bad-call', name='write_file', arguments={'path': 'index.html'})
    )

    assert result.ok is False
    assert '工具执行失败' in result.output
