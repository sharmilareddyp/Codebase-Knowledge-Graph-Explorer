from pathlib import Path

from fastapi.testclient import TestClient

from codebase_kg.api.app import create_app
from codebase_kg.config import Settings
from codebase_kg.graph.memory import InMemoryGraphStore
from codebase_kg.retrieval.qa import QAService
from tests.test_qa import ScriptedAnswerer, ScriptedEmbedder


def _client(tmp_path: Path) -> tuple[TestClient, InMemoryGraphStore]:
    store = InMemoryGraphStore()
    settings = Settings(allowed_roots=str(tmp_path), openai_api_key=None, vector_min_score=0.2, _env_file=None)
    qa = QAService(store, ScriptedEmbedder(), ScriptedAnswerer(), min_score=0.2)
    app = create_app(settings=settings, store=store, qa=qa)
    return TestClient(app), store


def test_health_index_and_navigation(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "pkg" / "util.py").write_text(
        'def slugify(value):\n    """Return a slug."""\n    return value\n',
        encoding="utf-8",
    )
    (tmp_path / "pkg" / "app.py").write_text(
        "from pkg.util import slugify\n\ndef run():\n    return slugify('x')\n",
        encoding="utf-8",
    )
    client, _store = _client(tmp_path)
    with client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        assert health.json()["schema_version"] == 1

        denied = client.post("/repositories/index", json={"paths": ["/etc"]})
        assert denied.status_code == 403
        assert denied.json()["error"] == "path_not_allowed"

        missing = client.post("/repositories/index", json={"paths": [str(tmp_path / "missing")]})
        assert missing.status_code == 403

        invalid = client.post("/repositories/index", json={"paths": []})
        assert invalid.status_code == 422
        assert invalid.json()["error"] == "validation_error"

        bad_ignore = client.post(
            "/repositories/index",
            json={"paths": [str(tmp_path)], "ignore_dirs": ["../escape"]},
        )
        assert bad_ignore.status_code == 422

        indexed = client.post("/repositories/index", json={"paths": [str(tmp_path)], "embed": True})
        assert indexed.status_code == 200
        body = indexed.json()["results"][0]
        assert body["embeddings_status"] == "completed"
        assert body["parse_error_count"] == 0
        repo_id = body["repository_id"]

        listing = client.get("/repositories")
        assert listing.status_code == 200
        assert listing.json()["repositories"][0]["id"] == repo_id

        detail = client.get(f"/repositories/{repo_id}")
        assert detail.status_code == 200

        entities = client.get(f"/repositories/{repo_id}/entities", params={"kind": "function", "name": "slugify"})
        assert entities.status_code == 200
        assert entities.json()["total"] == 1
        entity_id = entities.json()["entities"][0]["id"]
        assert entities.json()["entities"][0]["file_path"].endswith("util.py")

        entity = client.get(f"/entities/{entity_id}")
        assert entity.status_code == 200
        assert entity.json()["qualified_name"] == "pkg.util.slugify"
        assert any(item["statement"].startswith("from pkg.util import slugify") for item in entity.json()["imports"]) is False

        run_rows = client.get(f"/repositories/{repo_id}/entities", params={"name": "run"}).json()["entities"]
        run_id = next(item["id"] for item in run_rows if item["qualified_name"] == "pkg.app.run")
        callees = client.get(f"/entities/{run_id}/callees")
        assert callees.status_code == 200
        assert any(item["qualified_name"] == "pkg.util.slugify" for item in callees.json()["resolved"])
        callers = client.get(f"/entities/{entity_id}/callers")
        assert any(item["qualified_name"] == "pkg.app.run" for item in callers.json()["callers"])

        missing_entity = client.get("/entities/" + "a" * 32)
        assert missing_entity.status_code == 404
        assert missing_entity.json()["error"] == "not_found"

        bad_id = client.get("/entities/not-an-id")
        assert bad_id.status_code == 422

        answer = client.post("/query", json={"repository_id": repo_id, "question": "What does slugify do?"})
        assert answer.status_code == 200
        payload = answer.json()
        assert payload["sufficient_evidence"] is True
        citation = next(item for item in payload["citations"] if item["entity_name"] == "slugify")
        assert citation["file_path"].endswith("util.py")
        assert citation["qualified_name"] == "pkg.util.slugify"
        assert citation["start_line"] >= 1
        assert "Static analysis" in payload["limitations"]

        unknown = client.post("/query", json={"repository_id": repo_id, "question": "zzzz unmatched topic"})
        assert unknown.status_code == 200
        assert unknown.json()["sufficient_evidence"] is False
        assert unknown.json()["citations"] == []

        paths = set(client.get("/openapi.json").json()["paths"])
        assert not any("cypher" in path for path in paths)
        assert "/query" in paths
        assert "/health" in paths


def test_query_without_credentials_is_unavailable(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text("def slugify():\n    return 1\n", encoding="utf-8")
    store = InMemoryGraphStore()
    settings = Settings(allowed_roots=str(tmp_path), openai_api_key=None, _env_file=None)
    app = create_app(settings=settings, store=store, qa=QAService(store, None, None, 0.2))
    with TestClient(app) as client:
        indexed = client.post("/repositories/index", json={"paths": [str(tmp_path)]})
        repo_id = indexed.json()["results"][0]["repository_id"]
        response = client.post("/query", json={"repository_id": repo_id, "question": "What does slugify do?"})
        assert response.status_code == 503
        assert response.json()["error"] == "embeddings_unavailable"
        blocked = client.post("/repositories/index", json={"paths": [str(tmp_path)], "embed": True})
        assert blocked.status_code == 400
        assert blocked.json()["error"] == "bad_request"
