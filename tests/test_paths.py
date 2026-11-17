from pathlib import Path

import pytest

from codebase_kg.paths import normalize_repository_path
from codebase_kg.errors import PathNotAllowed


def test_api_paths_must_stay_inside_allowed_roots(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    allowed = [tmp_path]
    assert normalize_repository_path(str(repo), allowed) == repo.resolve()
    with pytest.raises(PathNotAllowed):
        normalize_repository_path("/etc", allowed)
    with pytest.raises(PathNotAllowed):
        normalize_repository_path(str(tmp_path / "missing"), allowed)
    file_path = repo / "mod.py"
    file_path.write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(PathNotAllowed):
        normalize_repository_path(str(file_path), allowed)
    with pytest.raises(PathNotAllowed):
        normalize_repository_path(str(repo), [])


def test_cli_normalization_skips_the_allow_list(tmp_path: Path) -> None:
    assert normalize_repository_path(str(tmp_path), None) == tmp_path.resolve()
