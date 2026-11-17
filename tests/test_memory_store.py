from pathlib import Path

from codebase_kg.graph.memory import InMemoryGraphStore, cosine
from codebase_kg.graph.serializer import serialize_scan
from codebase_kg.ids import chunk_id
from codebase_kg.indexer.pipeline import index_repository
from codebase_kg.models import ChunkRecord


def test_replace_is_idempotent_and_queries_relationships(fixture_repo: Path) -> None:
    store = InMemoryGraphStore()
    first = index_repository(fixture_repo)
    payload = serialize_scan(first)
    store.replace_repository(payload)
    store.replace_repository(serialize_scan(index_repository(fixture_repo)))

    summaries = store.list_repositories()
    assert len(summaries) == 1
    assert summaries[0].entity_count == len(first.entities)
    rows, total = store.list_entities(first.repo_id, kind="function", name="slugify", file="utils.py", offset=0, limit=10)
    assert total == 1
    assert rows[0].qualified_name == "sample_pkg.utils.slugify"

    detail = store.get_entity(rows[0].id)
    assert detail is not None
    rename = next(entity for entity in first.entities if entity.qualified_name == "sample_pkg.models.User.rename")
    callers = store.callers(rename.id)
    assert callers is not None
    assert any(item.qualified_name == "sample_pkg.models.User.rename" for item in store.get_entity(
        next(entity.id for entity in first.entities if entity.qualified_name == "sample_pkg.models.User._touch")
    ).callers)
    callees = store.callees(rename.id)
    assert callees is not None
    resolved, unresolved = callees
    assert any(item.qualified_name == "sample_pkg.models.User._touch" for item in resolved)
    build_user = next(entity for entity in first.entities if entity.qualified_name == "sample_pkg.service.build_user")
    resolved, unresolved = store.callees(build_user.id)
    assert any(item.raw_name == "user.rename" for item in unresolved)
    assert any(item.raw_name == "dynamic" for item in unresolved)
    assert store.get_entity("f" * 32) is None
    assert store.callers("f" * 32) is None


def test_similarity_uses_cosine(fixture_repo: Path) -> None:
    store = InMemoryGraphStore()
    scan = index_repository(fixture_repo)
    store.replace_repository(serialize_scan(scan))
    slug = next(entity for entity in scan.entities if entity.qualified_name == "sample_pkg.utils.slugify")
    other = next(entity for entity in scan.entities if entity.qualified_name == "main.main")
    store.upsert_chunks(
        [
            ChunkRecord(
                id=chunk_id(scan.repo_id, slug.id),
                repo_id=scan.repo_id,
                entity_id=slug.id,
                text="slugify",
                embedding=[1.0, 0.0],
                properties={
                    "id": chunk_id(scan.repo_id, slug.id),
                    "repo_id": scan.repo_id,
                    "entity_id": slug.id,
                    "text": "slugify",
                    "qualified_name": slug.qualified_name,
                    "name": slug.name,
                    "kind": slug.kind.value,
                    "file_path": slug.file_path,
                    "start_line": slug.start_line,
                    "end_line": slug.end_line,
                },
            ),
            ChunkRecord(
                id=chunk_id(scan.repo_id, other.id),
                repo_id=scan.repo_id,
                entity_id=other.id,
                text="main",
                embedding=[0.0, 1.0],
                properties={
                    "id": chunk_id(scan.repo_id, other.id),
                    "repo_id": scan.repo_id,
                    "entity_id": other.id,
                    "text": "main",
                    "qualified_name": other.qualified_name,
                    "name": other.name,
                    "kind": other.kind.value,
                    "file_path": other.file_path,
                    "start_line": other.start_line,
                    "end_line": other.end_line,
                },
            ),
        ]
    )
    hits = store.similarity_search(scan.repo_id, [1.0, 0.0], k=1, min_score=0.2)
    assert [hit.qualified_name for hit in hits] == ["sample_pkg.utils.slugify"]
    assert cosine([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine([1.0], [1.0, 0.0]) == 0.0
    assert store.get_repository(scan.repo_id).summary.embedding_count == 2
    store.set_embedding_count(scan.repo_id, 0)
    assert store.similarity_search(scan.repo_id, [1.0, 0.0], k=5, min_score=0.0) == []
