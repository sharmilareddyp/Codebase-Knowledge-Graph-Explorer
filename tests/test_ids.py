from pathlib import Path

from codebase_kg.ids import entity_id, file_id, repository_id, stable_id


def test_stable_id_distinguishes_part_boundaries() -> None:
    assert stable_id("ab", "c") != stable_id("a", "bc")
    assert stable_id("entity", "r", "function", "pkg.foo") == stable_id("entity", "r", "function", "pkg.foo")
    assert len(stable_id("x")) == 32


def test_entity_id_includes_kind() -> None:
    function_id = entity_id("repo", "function", "pkg.mod.foo")
    method_id = entity_id("repo", "method", "pkg.mod.foo")
    assert function_id == entity_id("repo", "function", "pkg.mod.foo")
    assert function_id != method_id


def test_file_id_uses_the_relative_posix_path() -> None:
    assert file_id("repo", "a/b.py") == file_id("repo", "a/b.py")
    assert file_id("repo", "a/b.py") != file_id("repo", "a/c.py")


def test_repository_id_uses_the_resolved_path(tmp_path: Path) -> None:
    first = repository_id(tmp_path)
    second = repository_id(tmp_path / "..")
    assert first == repository_id(tmp_path.resolve())
    assert first != second
    other = tmp_path / "other"
    other.mkdir()
    assert repository_id(other) != first
