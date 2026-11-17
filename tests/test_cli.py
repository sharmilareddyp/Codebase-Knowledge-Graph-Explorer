from pathlib import Path

from codebase_kg.cli import run
from codebase_kg.config import Settings
from codebase_kg.graph.memory import InMemoryGraphStore


class _Down:
    def ping(self) -> bool:
        return False

    def close(self) -> None:
        return None


def test_cli_indexes_without_neo4j(tmp_path: Path, capsys) -> None:
    (tmp_path / "mod.py").write_text("def marker():\n    return 1\n", encoding="utf-8")
    store = InMemoryGraphStore()
    settings = Settings(allowed_roots="", openai_api_key=None, _env_file=None)
    code = run(["index", str(tmp_path)], settings=settings, store=store)
    captured = capsys.readouterr()
    assert code == 0
    assert "entities=" in captured.out
    assert len(store.list_repositories()) == 1
    assert run(["check-neo4j"], settings=settings, store=store) == 0
    assert run(["check-neo4j"], settings=settings, store=_Down()) == 1
    assert run(["index", str(tmp_path / "mod.py")], settings=settings, store=store) == 2
    assert run(["index", str(tmp_path), "--embed"], settings=settings, store=store) == 2
