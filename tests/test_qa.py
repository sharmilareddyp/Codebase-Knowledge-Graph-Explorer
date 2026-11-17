from pathlib import Path

import pytest

from codebase_kg.config import Settings
from codebase_kg.errors import AnswerError, EmbeddingsUnavailable
from codebase_kg.graph.memory import InMemoryGraphStore
from codebase_kg.models import Evidence, IndexOptions
from codebase_kg.retrieval.qa import QAService
from codebase_kg.service import index_repositories


class ScriptedEmbedder:
    dimensions = 2
    secrets = ["super-secret-key"]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            if "\nname: slugify\n" in f"\n{text}\n":
                vectors.append([1.0, 0.0])
            else:
                vectors.append([0.0, 0.0])
        return vectors

    def embed_query(self, text: str) -> list[float]:
        if "slugify" in text.lower():
            return [1.0, 0.0]
        return [0.0, 1.0]


class ScriptedAnswerer:
    def __init__(self) -> None:
        self.calls = 0

    def answer(self, question: str, evidence: list[Evidence]) -> str:
        self.calls += 1
        if "partial" in question:
            return "INSUFFICIENT_EVIDENCE\nThe excerpts do not fully answer this."
        names = ", ".join(item.qualified_name for item in evidence)
        return f"Based on the indexed code: {names}."


def _settings(root: Path) -> Settings:
    return Settings(allowed_roots=str(root), openai_api_key=None, vector_min_score=0.2, _env_file=None)


def test_query_cites_slugify_and_refuses_to_guess(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text(
        'def slugify(value):\n    """Return a slug."""\n    return value\n\ndef other():\n    """Something else."""\n    return 1\n',
        encoding="utf-8",
    )
    store = InMemoryGraphStore()
    embedder = ScriptedEmbedder()
    answerer = ScriptedAnswerer()
    summaries = index_repositories(
        [str(tmp_path)],
        IndexOptions(),
        _settings(tmp_path),
        store,
        enforce_allowed_roots=True,
        embed=True,
        embedder=embedder,
    )
    qa = QAService(store, embedder, answerer, min_score=0.2)
    result = qa.ask(summaries[0].repository_id, "What does slugify do?", top_k=3)
    assert result.sufficient_evidence is True
    assert result.evidence[0].qualified_name == "mod.slugify"
    assert result.evidence[0].file_path == "mod.py"
    assert result.evidence[0].start_line is not None
    assert "mod.slugify" in result.answer

    vague = qa.ask(summaries[0].repository_id, "partial slugify question", top_k=3)
    assert vague.sufficient_evidence is False
    assert vague.evidence

    unmatched = qa.ask(summaries[0].repository_id, "zzzz unmatched topic", top_k=3)
    assert unmatched.sufficient_evidence is False
    assert unmatched.evidence == []
    assert "insufficient" in unmatched.answer.lower()
    assert answerer.calls == 2


def test_query_requires_credentials(tmp_path: Path) -> None:
    store = InMemoryGraphStore()
    (tmp_path / "mod.py").write_text("x = 1\n", encoding="utf-8")
    summaries = index_repositories(
        [str(tmp_path)],
        IndexOptions(),
        _settings(tmp_path),
        store,
        enforce_allowed_roots=True,
        embed=False,
        embedder=None,
    )
    qa = QAService(store, None, None, 0.2)
    with pytest.raises(EmbeddingsUnavailable):
        qa.ask(summaries[0].repository_id, "What is x?", top_k=3)


def test_provider_failure_does_not_leak_the_key(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text("def slugify():\n    return 1\n", encoding="utf-8")
    store = InMemoryGraphStore()
    embedder = ScriptedEmbedder()
    index_repositories(
        [str(tmp_path)],
        IndexOptions(),
        _settings(tmp_path),
        store,
        enforce_allowed_roots=True,
        embed=True,
        embedder=embedder,
    )

    class Broken:
        secrets = ["super-secret-key"]

        def answer(self, question: str, evidence: list[Evidence]) -> str:
            raise RuntimeError("provider failed for key super-secret-key")

    qa = QAService(store, embedder, Broken(), 0.2)
    with pytest.raises(AnswerError) as caught:
        qa.ask(store.list_repositories()[0].id, "What does slugify do?", top_k=2)
    assert "super-secret-key" not in str(caught.value)
