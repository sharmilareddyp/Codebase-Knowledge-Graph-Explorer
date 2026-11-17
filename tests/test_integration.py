"""Optional round trip against a real Neo4j instance.

Run with ``CKG_RUN_INTEGRATION=1 pytest -m integration`` after ``docker compose up``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from codebase_kg.config import get_settings
from codebase_kg.graph.neo4j_store import Neo4jGraphStore
from codebase_kg.models import IndexOptions
from codebase_kg.service import index_repositories

pytestmark = pytest.mark.integration

FIXTURE = Path(__file__).resolve().parents[1] / "examples" / "sample_repo"


@pytest.mark.skipif(os.environ.get("CKG_RUN_INTEGRATION") != "1", reason="Set CKG_RUN_INTEGRATION=1 to use Neo4j")
def test_neo4j_round_trip() -> None:
    settings = get_settings()
    store = Neo4jGraphStore.from_settings(settings)
    try:
        assert store.ping()
        store.ensure_schema()
        results = index_repositories(
            [str(FIXTURE)],
            IndexOptions(),
            settings,
            store,
            enforce_allowed_roots=False,
            embed=False,
            embedder=None,
        )
        repo_id = results[0].repository_id
        detail = store.get_repository(repo_id)
        assert detail is not None
        assert detail.summary.indexed_file_count == results[0].indexed_file_count
        rows, total = store.list_entities(repo_id, kind="function", name="slugify", file="utils.py", offset=0, limit=10)
        assert total == 1
        entity = store.get_entity(rows[0].id)
        assert entity is not None
        assert entity.summary.qualified_name == "sample_pkg.utils.slugify"
    finally:
        store.close()
