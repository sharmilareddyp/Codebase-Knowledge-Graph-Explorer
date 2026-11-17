"""Build one search chunk per indexed entity.

Chunks contain metadata, the docstring, and a truncated source excerpt. They are
the only place source text is retained, and only after an explicit embedding
request.
"""

from __future__ import annotations

from dataclasses import dataclass

from codebase_kg.ids import chunk_id
from codebase_kg.models import ScanResult

EXCERPT_MAX_CHARS = 1500


@dataclass(frozen=True)
class ChunkDraft:
    """Text ready to embed, plus the properties stored with the vector."""

    id: str
    repo_id: str
    entity_id: str
    text: str
    properties: dict[str, object]


def build_chunks(scan: ScanResult) -> list[ChunkDraft]:
    """Create searchable text for every entity in ``scan``."""

    drafts: list[ChunkDraft] = []
    for entity in scan.entities:
        source = scan.sources.get(entity.file_path, "")
        excerpt = _excerpt(source, entity.start_line, entity.end_line)
        lines = [
            f"kind: {entity.kind.value}",
            f"qualified_name: {entity.qualified_name}",
            f"name: {entity.name}",
            f"repository: {scan.repo_name}",
            f"file: {entity.file_path}",
            f"lines: {entity.start_line}-{entity.end_line}",
        ]
        if entity.signature:
            lines.append(f"signature: {entity.signature}")
        if entity.docstring:
            lines.append("docstring:")
            lines.append(entity.docstring)
        if excerpt:
            lines.append("source_excerpt:")
            lines.append(excerpt)
        text = "\n".join(lines)
        properties: dict[str, object] = {
            "id": chunk_id(scan.repo_id, entity.id),
            "repo_id": scan.repo_id,
            "entity_id": entity.id,
            "text": text,
            "qualified_name": entity.qualified_name,
            "name": entity.name,
            "kind": entity.kind.value,
            "file_path": entity.file_path,
            "start_line": entity.start_line,
            "end_line": entity.end_line,
        }
        drafts.append(
            ChunkDraft(
                id=str(properties["id"]),
                repo_id=scan.repo_id,
                entity_id=entity.id,
                text=text,
                properties=properties,
            )
        )
    return drafts


def _excerpt(source: str, start_line: int, end_line: int) -> str:
    if not source:
        return ""
    rows = source.splitlines()
    snippet = "\n".join(rows[max(0, start_line - 1) : max(0, end_line)])
    if len(snippet) <= EXCERPT_MAX_CHARS:
        return snippet
    return snippet[: EXCERPT_MAX_CHARS - 3] + "..."
