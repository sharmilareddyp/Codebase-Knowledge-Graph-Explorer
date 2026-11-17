import os
from pathlib import Path

from codebase_kg.indexer.discovery import discover_python_files
from codebase_kg.models import IndexOptions


def test_gitignore_and_default_directories(tmp_path: Path) -> None:
    (tmp_path / "keep.py").write_text("def keep():\n    return 1\n", encoding="utf-8")
    (tmp_path / "secret.py").write_text("def secret():\n    return 1\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("secret.py\n*.log\n!keep.py\n", encoding="utf-8")
    nested = tmp_path / "pkg"
    nested.mkdir()
    (nested / ".gitignore").write_text("skip_me.py\n", encoding="utf-8")
    (nested / "skip_me.py").write_text("def skip_me():\n    return 1\n", encoding="utf-8")
    (nested / "visible.py").write_text("def visible():\n    return 1\n", encoding="utf-8")
    vendored = tmp_path / "venv" / "lib"
    vendored.mkdir(parents=True)
    (vendored / "noise.py").write_text("def noise():\n    return 1\n", encoding="utf-8")
    custom = tmp_path / "scratch"
    custom.mkdir()
    (custom / "extra.py").write_text("def extra():\n    return 1\n", encoding="utf-8")

    files, skipped = discover_python_files(tmp_path, IndexOptions(extra_ignore_dirs=("scratch",)))
    relative = {path.relative_to(tmp_path).as_posix() for path in files}
    assert relative == {"keep.py", "pkg/visible.py"}
    reasons = {item.path: item.reason for item in skipped}
    assert reasons["secret.py"] == "gitignore"
    assert reasons["pkg/skip_me.py"] == "gitignore"
    assert reasons["venv"] == "ignored directory"
    assert reasons["scratch"] == "ignored directory"


def test_negation_in_one_gitignore(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("secret.py\n!secret.py\n", encoding="utf-8")
    (tmp_path / "secret.py").write_text("x = 1\n", encoding="utf-8")
    files, skipped = discover_python_files(tmp_path, IndexOptions())
    assert [path.name for path in files] == ["secret.py"]
    assert skipped == []


def test_symlinks_do_not_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside.py"
    outside.write_text("def outside():\n    return 1\n", encoding="utf-8")
    link = tmp_path / "escape.py"
    link.symlink_to(outside)
    inside = tmp_path / "inside.py"
    inside.write_text("def inside():\n    return 1\n", encoding="utf-8")
    internal_link = tmp_path / "alias.py"
    internal_link.symlink_to(inside)

    files, skipped = discover_python_files(tmp_path, IndexOptions())
    assert {path.name for path in files} == {"inside.py"}
    assert {item.path for item in skipped} >= {"escape.py", "alias.py"}

    files, skipped = discover_python_files(tmp_path, IndexOptions(follow_symlinks=True))
    names = {path.name for path in files}
    assert "inside.py" in names
    assert "alias.py" in names
    assert "escape.py" not in names
    assert any(item.path == "escape.py" and "escapes" in item.reason for item in skipped)
    outside.unlink()


def test_unreadable_and_oversized_files_are_skipped(tmp_path: Path) -> None:
    big = tmp_path / "big.py"
    big.write_text("x = 1\n", encoding="utf-8")
    blocked = tmp_path / "blocked.py"
    blocked.write_text("def blocked():\n    return 1\n", encoding="utf-8")
    os.chmod(blocked, 0)
    try:
        from codebase_kg.indexer.pipeline import index_repository

        scan = index_repository(tmp_path, IndexOptions(max_file_bytes=4))
    finally:
        os.chmod(blocked, 0o644)
    paths = {item.path: item.reason for item in scan.skipped}
    assert "exceeds" in paths["big.py"]
    assert "unreadable" in paths["blocked.py"]
    assert scan.indexed_file_count == 0
