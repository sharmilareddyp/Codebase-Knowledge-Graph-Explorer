"""Version 1 graph schema and the only Cypher the writer is allowed to run.

Data values are always passed as parameters. The one formatted statement is the
vector-index definition, and it accepts only an integer dimension that this
module range-checks. See ``docs/graph-schema.md`` for the node and relationship
model.
"""

from __future__ import annotations

SCHEMA_VERSION = 1
VECTOR_INDEX_NAME = "ckg_chunk_embedding"

CONSTRAINTS: tuple[str, ...] = (
    "CREATE CONSTRAINT ckg_repository_id IF NOT EXISTS FOR (n:Repository) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT ckg_file_id IF NOT EXISTS FOR (n:File) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT ckg_entity_id IF NOT EXISTS FOR (n:Entity) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT ckg_import_id IF NOT EXISTS FOR (n:Import) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT ckg_callsite_id IF NOT EXISTS FOR (n:CallSite) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT ckg_parse_error_id IF NOT EXISTS FOR (n:ParseError) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT ckg_skipped_id IF NOT EXISTS FOR (n:SkippedPath) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT ckg_chunk_id IF NOT EXISTS FOR (n:SearchChunk) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT ckg_schema_meta IF NOT EXISTS FOR (n:SchemaMeta) REQUIRE n.id IS UNIQUE",
)

INDEXES: tuple[str, ...] = (
    "CREATE INDEX ckg_entity_repo IF NOT EXISTS FOR (n:Entity) ON (n.repo_id)",
    "CREATE INDEX ckg_entity_repo_kind IF NOT EXISTS FOR (n:Entity) ON (n.repo_id, n.kind)",
    "CREATE INDEX ckg_entity_repo_name IF NOT EXISTS FOR (n:Entity) ON (n.repo_id, n.name)",
    "CREATE INDEX ckg_entity_file IF NOT EXISTS FOR (n:Entity) ON (n.repo_id, n.file_path)",
    "CREATE INDEX ckg_file_repo IF NOT EXISTS FOR (n:File) ON (n.repo_id)",
    "CREATE INDEX ckg_chunk_repo IF NOT EXISTS FOR (n:SearchChunk) ON (n.repo_id)",
    "CREATE INDEX ckg_callsite_repo IF NOT EXISTS FOR (n:CallSite) ON (n.repo_id)",
    "CREATE INDEX ckg_import_module IF NOT EXISTS FOR (n:Import) ON (n.module_id)",
)

MERGE_REPOSITORY = """
MERGE (n:Repository {id: $id})
SET n += $properties
"""

DELETE_REPO_GRAPH = """
MATCH (n {repo_id: $repo_id})
WHERE NOT n:Repository
DETACH DELETE n
"""

MERGE_SCHEMA_META = """
MERGE (s:SchemaMeta {id: $id})
SET s.version = $version, s.embedding_dimensions = $dimensions
"""

DROP_VECTOR_INDEX = "DROP INDEX ckg_chunk_embedding IF EXISTS"

_VECTOR_INDEX_TEMPLATE = """
CREATE VECTOR INDEX ckg_chunk_embedding IF NOT EXISTS
FOR (n:SearchChunk) ON n.embedding
OPTIONS {indexConfig: {`vector.dimensions`: __DIMENSIONS__, `vector.similarity_function`: 'cosine'}}
"""

LABEL_QUERIES: dict[tuple[str, ...], str] = {
    ("File",): """
        UNWIND $rows AS row
        MERGE (n:File {id: row.id})
        SET n += row.properties
    """,
    ("Entity", "Module"): """
        UNWIND $rows AS row
        MERGE (n:Entity:Module {id: row.id})
        SET n += row.properties
    """,
    ("Entity", "Class"): """
        UNWIND $rows AS row
        MERGE (n:Entity:Class {id: row.id})
        SET n += row.properties
    """,
    ("Entity", "Function"): """
        UNWIND $rows AS row
        MERGE (n:Entity:Function {id: row.id})
        SET n += row.properties
    """,
    ("Entity", "Method"): """
        UNWIND $rows AS row
        MERGE (n:Entity:Method {id: row.id})
        SET n += row.properties
    """,
    ("Import",): """
        UNWIND $rows AS row
        MERGE (n:Import {id: row.id})
        SET n += row.properties
    """,
    ("CallSite",): """
        UNWIND $rows AS row
        MERGE (n:CallSite {id: row.id})
        SET n += row.properties
    """,
    ("ParseError",): """
        UNWIND $rows AS row
        MERGE (n:ParseError {id: row.id})
        SET n += row.properties
    """,
    ("SkippedPath",): """
        UNWIND $rows AS row
        MERGE (n:SkippedPath {id: row.id})
        SET n += row.properties
    """,
}

REL_QUERIES: dict[str, str] = {
    "CONTAINS": """
        UNWIND $rows AS row
        MATCH (a {id: row.start_id})
        MATCH (b {id: row.end_id})
        MERGE (a)-[:CONTAINS]->(b)
    """,
    "DEFINES": """
        UNWIND $rows AS row
        MATCH (a {id: row.start_id})
        MATCH (b {id: row.end_id})
        MERGE (a)-[:DEFINES]->(b)
    """,
    "INHERITS": """
        UNWIND $rows AS row
        MATCH (a {id: row.start_id})
        MATCH (b {id: row.end_id})
        MERGE (a)-[:INHERITS]->(b)
    """,
    "HAS_IMPORT": """
        UNWIND $rows AS row
        MATCH (a {id: row.start_id})
        MATCH (b {id: row.end_id})
        MERGE (a)-[:HAS_IMPORT {import_id: row.import_id}]->(b)
    """,
    "IMPORTS": """
        UNWIND $rows AS row
        MATCH (a {id: row.start_id})
        MATCH (b {id: row.end_id})
        MERGE (a)-[r:IMPORTS {import_id: row.import_id}]->(b)
        SET r.lineno = row.lineno, r.statement = row.statement
    """,
    "CALLS": """
        UNWIND $rows AS row
        MATCH (a {id: row.start_id})
        MATCH (b {id: row.end_id})
        MERGE (a)-[r:CALLS {ordinal: row.ordinal, lineno: row.lineno, raw_name: row.raw_name}]->(b)
        SET r.call_site_id = row.call_site_id
    """,
    "CALL_SITE": """
        UNWIND $rows AS row
        MATCH (a {id: row.start_id})
        MATCH (b {id: row.end_id})
        MERGE (a)-[:CALL_SITE {call_site_id: row.call_site_id}]->(b)
    """,
    "HAS_PARSE_ERROR": """
        UNWIND $rows AS row
        MATCH (a {id: row.start_id})
        MATCH (b {id: row.end_id})
        MERGE (a)-[:HAS_PARSE_ERROR]->(b)
    """,
}

MERGE_CHUNKS = """
UNWIND $rows AS row
MERGE (n:SearchChunk {id: row.id})
SET n += row.properties
WITH n, row
MATCH (e:Entity {id: row.entity_id})
MERGE (n)-[:DESCRIBES]->(e)
"""

SET_EMBEDDING_COUNT = """
MATCH (r:Repository {id: $repo_id})
SET r.embedding_count = $count
"""

DELETE_CHUNKS = """
MATCH (c:SearchChunk {repo_id: $repo_id})
DETACH DELETE c
"""

LIST_REPOSITORIES = """
MATCH (r:Repository)
RETURN r {.*} AS node
ORDER BY r.name
"""

GET_REPOSITORY = """
MATCH (r:Repository {id: $id})
RETURN r {.*} AS node
"""

LIST_PARSE_ERRORS = """
MATCH (p:ParseError {repo_id: $repo_id})
RETURN p.path AS path, p.message AS message
ORDER BY p.path
LIMIT 200
"""

LIST_SKIPPED = """
MATCH (s:SkippedPath {repo_id: $repo_id})
RETURN s.path AS path, s.reason AS message, s.is_directory AS is_directory
ORDER BY s.path
LIMIT 500
"""

COUNT_ENTITIES = """
MATCH (n:Entity {repo_id: $repo_id})
WHERE ($kind IS NULL OR n.kind = $kind)
  AND ($name IS NULL OR n.name CONTAINS $name OR n.qualified_name CONTAINS $name)
  AND ($file IS NULL OR n.file_path CONTAINS $file)
RETURN count(n) AS total
"""

LIST_ENTITIES = """
MATCH (n:Entity {repo_id: $repo_id})
WHERE ($kind IS NULL OR n.kind = $kind)
  AND ($name IS NULL OR n.name CONTAINS $name OR n.qualified_name CONTAINS $name)
  AND ($file IS NULL OR n.file_path CONTAINS $file)
RETURN n {.*} AS node
ORDER BY n.file_path, n.start_line, n.qualified_name
SKIP $offset
LIMIT $limit
"""

GET_ENTITY = """
MATCH (n:Entity {id: $id})
RETURN n {.*} AS node
"""

LIST_CALLERS = """
MATCH (caller:Entity)-[r:CALLS]->(target:Entity {id: $id})
RETURN caller.id AS entity_id, caller.name AS name, caller.qualified_name AS qualified_name,
       caller.kind AS kind, caller.file_path AS file_path, r.lineno AS lineno, r.raw_name AS raw_name
ORDER BY caller.qualified_name, r.lineno
"""

LIST_CALLEES = """
MATCH (source:Entity {id: $id})-[r:CALLS]->(callee:Entity)
RETURN callee.id AS entity_id, callee.name AS name, callee.qualified_name AS qualified_name,
       callee.kind AS kind, callee.file_path AS file_path, r.lineno AS lineno, r.raw_name AS raw_name
ORDER BY callee.qualified_name, r.lineno
"""

LIST_UNRESOLVED = """
MATCH (site:CallSite {caller_id: $id})
WHERE site.status <> 'resolved'
RETURN site.lineno AS lineno, site.raw_name AS raw_name, site.status AS status, site.reason AS reason
ORDER BY site.lineno, site.ordinal
"""

LIST_IMPORTS = """
MATCH (imp:Import {module_id: $module_id})
RETURN imp {.*} AS node
ORDER BY imp.lineno, imp.ordinal
"""

VECTOR_QUERY = """
CALL db.index.vector.queryNodes('ckg_chunk_embedding', $candidate_limit, $embedding)
YIELD node, score
WHERE node.repo_id = $repo_id AND score >= $min_score
RETURN node.id AS id, node.entity_id AS entity_id, node.text AS text,
       node.qualified_name AS qualified_name, node.name AS name, node.kind AS kind,
       node.file_path AS file_path, node.start_line AS start_line, node.end_line AS end_line,
       score
ORDER BY score DESC
LIMIT $k
"""

FETCH_EMBEDDINGS = """
MATCH (c:SearchChunk {repo_id: $repo_id})
RETURN c.id AS id, c.entity_id AS entity_id, c.text AS text,
       c.qualified_name AS qualified_name, c.name AS name, c.kind AS kind,
       c.file_path AS file_path, c.start_line AS start_line, c.end_line AS end_line,
       c.embedding AS embedding
"""


def vector_index_statement(dimensions: int) -> str:
    """Build the vector-index statement for a validated integer dimension."""

    if isinstance(dimensions, bool) or not isinstance(dimensions, int):
        raise ValueError("embedding dimensions must be an integer")
    if not 1 <= dimensions <= 4096:
        raise ValueError("embedding dimensions must be between 1 and 4096")
    return _VECTOR_INDEX_TEMPLATE.replace("__DIMENSIONS__", str(dimensions))


def label_query(labels: tuple[str, ...]) -> str:
    """Return the constant merge statement for a known label set."""

    try:
        return LABEL_QUERIES[labels]
    except KeyError as exc:
        raise ValueError(f"Unsupported node labels: {labels!r}") from exc


def relationship_query(rel_type: str) -> str:
    """Return the constant merge statement for a known relationship type."""

    try:
        return REL_QUERIES[rel_type]
    except KeyError as exc:
        raise ValueError(f"Unsupported relationship type: {rel_type}") from exc
