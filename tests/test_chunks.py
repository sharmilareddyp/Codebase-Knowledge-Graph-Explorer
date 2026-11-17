from pathlib import Path

from codebase_kg.indexer.pipeline import index_repository
from codebase_kg.retrieval.chunks import build_chunks
from codebase_kg.retrieval.prompt import STATIC_ANALYSIS_NOTE, SYSTEM_PROMPT, interpret_answer


def test_chunks_include_location_and_excerpt(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text(
        'def slugify(value):\n    """Make a slug."""\n    token = "UNIQUE_SOURCE_SENTINEL_123"\n    return token\n',
        encoding="utf-8",
    )
    scan = index_repository(tmp_path)
    chunks = build_chunks(scan)
    slug = next(chunk for chunk in chunks if chunk.properties["qualified_name"] == "mod.slugify")
    assert "file: mod.py" in slug.text
    assert "lines:" in slug.text
    assert "Make a slug." in slug.text
    assert "UNIQUE_SOURCE_SENTINEL_123" in slug.text
    assert slug.properties["start_line"] >= 1


def test_prompt_requires_citations_and_insufficient_evidence() -> None:
    assert "repository" in SYSTEM_PROMPT
    assert "file path" in SYSTEM_PROMPT
    assert "line range" in SYSTEM_PROMPT
    assert "INSUFFICIENT_EVIDENCE" in SYSTEM_PROMPT
    assert "getattr" in STATIC_ANALYSIS_NOTE
    text, sufficient = interpret_answer("INSUFFICIENT_EVIDENCE\nThe excerpt does not say.")
    assert sufficient is False
    assert "excerpt" in text
    assert interpret_answer("slugify returns its argument.")[1] is True
    assert interpret_answer("   ")[1] is False
