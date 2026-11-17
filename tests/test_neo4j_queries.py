import json
from pathlib import Path

from codebase_kg.graph.neo4j_store import Neo4jGraphStore
from codebase_kg.graph.schema import LABEL_QUERIES, REL_QUERIES, SCHEMA_VERSION, vector_index_statement
from codebase_kg.graph.serializer import serialize_scan
from codebase_kg.indexer.pipeline import index_repository


class _Result:
    def __init__(self, records: list[dict] | None = None) -> None:
        self._records = records or []

    def consume(self) -> None:
        return None

    def single(self) -> dict | None:
        return self._records[0] if self._records else None

    def __iter__(self):
        return iter(self._records)


class _Session:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def __enter__(self) -> "_Session":
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def execute_write(self, fn):
        return fn(self)

    def execute_read(self, fn):
        return fn(self)

    def run(self, query: str, **params: object) -> _Result:
        self.calls.append((query, params))
        if query.startswith("RETURN 1"):
            return _Result([{"ok": 1}])
        return _Result([])


class _Driver:
    def __init__(self) -> None:
        self.session_obj = _Session()
        self.closed = False

    def session(self, database: str | None = None) -> _Session:
        return self.session_obj

    def close(self) -> None:
        self.closed = True


def test_queries_are_static_and_parameterized(tmp_path: Path) -> None:
    store_source = Path("codebase_kg/graph/neo4j_store.py").read_text(encoding="utf-8")
    assert 'f"' not in store_source
    assert "f'" not in store_source
    assert ".format(" not in store_source
    for query in [*LABEL_QUERIES.values(), *REL_QUERIES.values()]:
        assert "$" in query
    statement = vector_index_statement(32)
    assert "32" in statement
    try:
        vector_index_statement(True)  # type: ignore[arg-type]
    except ValueError:
        pass
    else:
        raise AssertionError("boolean dimensions must be rejected")

    marker = tmp_path / "ckg_marker_repo"
    marker.mkdir()
    (marker / "mod.py").write_text("def marker():\n    return 1\n", encoding="utf-8")
    payload = serialize_scan(index_repository(marker))
    driver = _Driver()
    store = Neo4jGraphStore(driver, "neo4j", 32)
    assert store.ping()
    store.ensure_schema()
    store.replace_repository(payload)
    store.close()
    assert driver.closed
    assert store.vector_ready
    blob = json.dumps(driver.session_obj.calls, default=str)
    assert "ckg_marker_repo" in blob
    for query, _params in driver.session_obj.calls:
        assert "ckg_marker_repo" not in query
        assert "MATCH" in query or "CREATE" in query or "MERGE" in query or "DROP" in query or query.startswith("RETURN")
    assert any("DETACH DELETE" in query for query, _params in driver.session_obj.calls)
    assert SCHEMA_VERSION == 1


def test_vector_index_failure_falls_back(tmp_path: Path) -> None:
    class BoomSession(_Session):
        def run(self, query: str, **params: object) -> _Result:
            self.calls.append((query, params))
            if "CREATE VECTOR INDEX" in query:
                raise RuntimeError("vector indexes are disabled")
            if query.startswith("RETURN 1"):
                return _Result([{"ok": 1}])
            return _Result([])

    class BoomDriver(_Driver):
        def __init__(self) -> None:
            self.session_obj = BoomSession()
            self.closed = False

    store = Neo4jGraphStore(BoomDriver(), "neo4j", 8)
    store.ensure_schema()
    assert store.vector_ready is False
