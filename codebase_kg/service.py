"""Indexing operations shared by the CLI and the HTTP API."""

from __future__ import annotations

import logging

from codebase_kg.config import Settings
from codebase_kg.errors import BadRequestError
from codebase_kg.graph.serializer import serialize_scan
from codebase_kg.indexer.pipeline import index_repository
from codebase_kg.logging_config import redact
from codebase_kg.models import ChunkRecord, IndexOptions, IndexSummary
from codebase_kg.paths import normalize_repository_path
from codebase_kg.retrieval.chunks import build_chunks
from codebase_kg.retrieval.embeddings import EmbeddingProvider

logger = logging.getLogger(__name__)


def index_repositories(
    paths: list[str],
    options: IndexOptions,
    settings: Settings,
    store: object,
    *,
    enforce_allowed_roots: bool,
    embed: bool,
    embedder: EmbeddingProvider | None,
) -> list[IndexSummary]:
    """Validate paths, index each repository, and optionally embed entities.

    All paths are checked before any repository is written. The API passes
    ``enforce_allowed_roots=True``. The CLI passes ``False`` because the
    operator already chose the directory.
    """

    allowed = settings.parsed_allowed_roots() if enforce_allowed_roots else None
    roots = [normalize_repository_path(path, allowed) for path in paths]
    if embed and embedder is None:
        raise BadRequestError(
            "Embeddings were requested, but OpenAI credentials are not configured. Set OPENAI_API_KEY."
        )

    summaries: list[IndexSummary] = []
    for root in roots:
        scan = index_repository(root, options)
        store.replace_repository(serialize_scan(scan))  # type: ignore[attr-defined]
        status, count, message = _embed(scan, store, embedder if embed else None)
        summaries.append(
            IndexSummary(
                repository_id=scan.repo_id,
                name=scan.repo_name,
                root_path=scan.root_path,
                indexed_file_count=scan.indexed_file_count,
                skipped_count=len(scan.skipped),
                parse_error_count=len(scan.parse_errors),
                entity_count=len(scan.entities),
                resolved_call_count=scan.resolved_call_count,
                unresolved_call_count=scan.unresolved_call_count,
                builtin_call_count=scan.builtin_call_count,
                embeddings_status=status,
                embeddings_count=count,
                message=message,
            )
        )
    return summaries


def _embed(scan: object, store: object, embedder: EmbeddingProvider | None) -> tuple[str, int, str | None]:
    repo_id = scan.repo_id  # type: ignore[attr-defined]
    if embedder is None:
        store.set_embedding_count(repo_id, 0)  # type: ignore[attr-defined]
        return "skipped", 0, None

    drafts = build_chunks(scan)  # type: ignore[arg-type]
    if not drafts:
        store.set_embedding_count(repo_id, 0)  # type: ignore[attr-defined]
        return "completed", 0, None
    try:
        vectors = embedder.embed_documents([draft.text for draft in drafts])
    except Exception as exc:
        logger.warning("Embedding failed: %s", redact(str(exc), list(getattr(embedder, "secrets", []))))
        store.set_embedding_count(repo_id, 0)  # type: ignore[attr-defined]
        return "failed", 0, "Embedding provider request failed."
    if len(vectors) != len(drafts):
        store.set_embedding_count(repo_id, 0)  # type: ignore[attr-defined]
        return "failed", 0, "Embedding provider returned an unexpected number of vectors."

    records: list[ChunkRecord] = []
    for draft, vector in zip(drafts, vectors, strict=True):
        if len(vector) != embedder.dimensions:
            store.set_embedding_count(repo_id, 0)  # type: ignore[attr-defined]
            return "failed", 0, "Embedding dimensions do not match the configured model."
        records.append(
            ChunkRecord(
                id=draft.id,
                repo_id=draft.repo_id,
                entity_id=draft.entity_id,
                text=draft.text,
                embedding=[float(value) for value in vector],
                properties=draft.properties,
            )
        )
    store.upsert_chunks(records)  # type: ignore[attr-defined]
    return "completed", len(records), None
