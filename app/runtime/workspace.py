from pathlib import Path

_MAX_FILE_BYTES = 1_000_000


class Workspace:
    """把文件工具限制在单个 App 的 Workspace 内。"""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve_file(self, relative_path: str) -> Path:
        if not relative_path or Path(relative_path).is_absolute():
            raise ValueError("文件路径必须是非空相对路径")
        if ".." in Path(relative_path).parts:
            raise ValueError("文件路径不得越出 Workspace")
        target = (self.root / relative_path).resolve(strict=False)
        if target == self.root or not target.is_relative_to(self.root):
            raise ValueError("文件路径不得越出 Workspace")
        return target

    def _resolve_pattern(self, pattern: str) -> str:
        path = Path(pattern)
        if not pattern or path.is_absolute() or ".." in path.parts:
            raise ValueError("glob 模式不得越出 Workspace")
        return pattern

    def read_file(self, relative_path: str) -> str:
        target = self._resolve_file(relative_path)
        if not target.is_file():
            raise ValueError(f"文件不存在: {relative_path}")
        if target.stat().st_size > _MAX_FILE_BYTES:
            raise ValueError("文件过大，拒绝读取")
        return target.read_text(encoding="utf-8")

    def write_file(self, relative_path: str, content: str) -> None:
        if len(content.encode("utf-8")) > _MAX_FILE_BYTES:
            raise ValueError("文件过大，拒绝写入")
        target = self._resolve_file(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        resolved_parent = target.parent.resolve()
        if not resolved_parent.is_relative_to(self.root):
            raise ValueError("文件路径不得越出 Workspace")
        target.write_text(content, encoding="utf-8")

    def edit_file(self, relative_path: str, old_text: str, new_text: str) -> None:
        content = self.read_file(relative_path)
        if old_text not in content:
            raise ValueError("待替换文本不存在")
        self.write_file(relative_path, content.replace(old_text, new_text, 1))

    def glob(self, pattern: str) -> list[str]:
        safe_pattern = self._resolve_pattern(pattern)
        results: list[str] = []
        for candidate in self.root.glob(safe_pattern):
            resolved = candidate.resolve()
            if resolved.is_file() and resolved.is_relative_to(self.root):
                results.append(candidate.relative_to(self.root).as_posix())
        return sorted(results)
