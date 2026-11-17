import json
from pathlib import Path

from codebase_kg.graph.schema import LABEL_QUERIES, REL_QUERIES, SCHEMA_VERSION
from codebase_kg.graph.serializer import serialize_scan
from codebase_kg.indexer.pipeline import index_repository
from codebase_kg.models import ResolutionStatus


def test_sample_fixture_resolution(fixture_repo: Path) -> None:
    scan = index_repository(fixture_repo)
    names = {entity.name for entity in scan.entities}
    assert "secret_token" not in names
    assert "skip_me" not in names
    assert "generated_thing" not in names
    assert "vendored" not in names
    assert "slugify" in names
    assert len(scan.parse_errors) == 1
    assert scan.parse_errors[0].path == "broken_syntax.py"
    assert "SECRET_SHOULD_NOT_BE_STORED" not in scan.parse_errors[0].message
    assert any(item.path == "secret.py" and item.reason == "gitignore" for item in scan.skipped)
    assert any(item.path == "build" and item.is_directory for item in scan.skipped)
    assert any(item.path == "venv" and item.is_directory for item in scan.skipped)

    resolved = {
        (call.caller_qualified_name, call.raw_name): call.callee_qualified_name
        for call in scan.calls
        if call.status is ResolutionStatus.RESOLVED
    }
    assert resolved[("sample_pkg.package_slug", "slugify")] == "sample_pkg.utils.slugify"
    assert resolved[("sample_pkg.service.build_user", "User")] == "sample_pkg.models.User"
    assert resolved[("sample_pkg.service.build_user", "slugify")] == "sample_pkg.utils.slugify"
    assert resolved[("sample_pkg.models.User.rename", "self._touch")] == "sample_pkg.models.User._touch"
    assert resolved[("main.main", "build_user")] == "sample_pkg.service.build_user"
    assert resolved[("main.main", "Directory")] == "sample_pkg.service.Directory"
    assert resolved[("main", "main")] == "main.main"

    unresolved = {(call.caller_qualified_name, call.raw_name) for call in scan.calls if call.status is ResolutionStatus.UNRESOLVED}
    assert ("sample_pkg.service.build_user", "user.rename") in unresolved
    assert ("sample_pkg.service.build_user", "dynamic") in unresolved
    assert ("sample_pkg.service.Directory.add", "user._touch") in unresolved
    assert ("main.main", "directory.add") in unresolved
    builtins = {(call.caller_qualified_name, call.raw_name) for call in scan.calls if call.status is ResolutionStatus.BUILTIN}
    assert ("sample_pkg.service.build_user", "getattr") in builtins

    user_call = next(call for call in scan.calls if call.raw_name == "User" and call.caller_qualified_name.endswith("build_user"))
    callee = next(entity for entity in scan.entities if entity.id == user_call.callee_id)
    assert callee.kind.name == "CLASS"


def test_reindex_serialization_is_stable(fixture_repo: Path) -> None:
    first = serialize_scan(index_repository(fixture_repo))
    second = serialize_scan(index_repository(fixture_repo))
    first.repository["indexed_at"] = ""
    second.repository["indexed_at"] = ""
    assert first.schema_version == SCHEMA_VERSION == second.schema_version
    assert first.repository == second.repository
    assert first.nodes == second.nodes
    assert first.relationships == second.relationships


def test_serializer_omits_source_and_uses_known_labels(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text(
        'def foo():\n    sentinel = "UNIQUE_SOURCE_SENTINEL_123"\n    return sentinel\n',
        encoding="utf-8",
    )
    payload = serialize_scan(index_repository(tmp_path))
    encoded = json.dumps(
        {
            "repository": payload.repository,
            "nodes": [node.properties for node in payload.nodes],
            "relationships": [rel.properties for rel in payload.relationships],
        }
    )
    assert "UNIQUE_SOURCE_SENTINEL_123" not in encoded
    assert "sources" not in payload.repository
    for node in payload.nodes:
        assert node.labels in LABEL_QUERIES
    for relationship in payload.relationships:
        assert relationship.type in REL_QUERIES
    assert any(rel.type == "DEFINES" for rel in payload.relationships)
    assert any(rel.type == "CONTAINS" for rel in payload.relationships)
