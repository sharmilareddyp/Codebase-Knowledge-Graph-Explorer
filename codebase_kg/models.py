"""Domain records produced by indexing and returned by graph reads."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class EntityKind(str, Enum):
    """Kinds of Python definitions stored as graph entities."""

    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"


class ResolutionStatus(str, Enum):
    """How a call expression was classified.

    ``RESOLVED`` means the callee is an entity in the indexed repository.
    ``BUILTIN`` means the name is a Python builtin and is not given a fake
    entity. ``UNRESOLVED`` means static analysis could not prove a target.
    """

    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    BUILTIN = "builtin"


@dataclass
class IndexOptions:
    """Knobs for a single indexing run."""

    respect_gitignore: bool = True
    extra_ignore_dirs: tuple[str, ...] = ()
    follow_symlinks: bool = False
    max_file_bytes: int = 1_000_000


@dataclass
class SkippedPath:
    """A path that was seen and deliberately not parsed."""

    path: str
    reason: str
    is_directory: bool


@dataclass
class ParseIssue:
    """A Python file that could not be parsed."""

    path: str
    message: str


@dataclass
class FileRecord:
    """One ``.py`` file that was opened, including files with syntax errors."""

    id: str
    repo_id: str
    path: str
    source_hash: str | None
    line_count: int
    parse_ok: bool


@dataclass
class Entity:
    """A module, class, function, or method definition."""

    id: str
    kind: EntityKind
    name: str
    qualified_name: str
    repo_id: str
    file_id: str
    file_path: str
    start_line: int
    end_line: int
    docstring: str | None
    source_hash: str | None
    parent_id: str | None
    parent_qualified_name: str | None
    module_id: str
    module_qualified_name: str
    signature: str | None = None
    bases: list[str] = field(default_factory=list)
    decorators: list[str] = field(default_factory=list)
    is_async: bool = False
    is_package: bool = False
    resolved_base_ids: list[str] = field(default_factory=list)
    resolved_base_qns: list[str] = field(default_factory=list)


@dataclass
class ImportRecord:
    """One imported name.

    Resolution fields are filled later by :mod:`codebase_kg.indexer.resolver`.
    Only module-level imports participate in name binding.
    """

    id: str
    repo_id: str
    file_id: str
    file_path: str
    module_id: str
    module_qualified_name: str
    lineno: int
    ordinal: int
    level: int
    is_from_import: bool
    is_module_level: bool
    source_module: str | None
    imported_name: str | None
    alias: str | None
    local_name: str
    resolved_module_id: str | None = None
    resolved_module_qn: str | None = None
    resolved_entity_id: str | None = None
    resolved_entity_qn: str | None = None
    statement: str = ""


@dataclass
class CallRecord:
    """One call expression and its resolution outcome."""

    id: str
    repo_id: str
    file_path: str
    caller_id: str
    caller_qualified_name: str
    lineno: int
    ordinal: int
    raw_name: str
    status: ResolutionStatus
    reason: str | None
    callee_id: str | None = None
    callee_qualified_name: str | None = None


@dataclass
class ScanResult:
    """Complete result of indexing one repository.

    ``sources`` is held only long enough to build embedding chunks. It is not
    written to the graph.
    """

    repo_id: str
    repo_name: str
    root_path: str
    indexed_at: str
    files: list[FileRecord] = field(default_factory=list)
    entities: list[Entity] = field(default_factory=list)
    imports: list[ImportRecord] = field(default_factory=list)
    calls: list[CallRecord] = field(default_factory=list)
    parse_errors: list[ParseIssue] = field(default_factory=list)
    skipped: list[SkippedPath] = field(default_factory=list)
    sources: dict[str, str] = field(default_factory=dict)

    @property
    def indexed_file_count(self) -> int:
        return sum(1 for item in self.files if item.parse_ok)

    @property
    def resolved_call_count(self) -> int:
        return sum(1 for item in self.calls if item.status is ResolutionStatus.RESOLVED)

    @property
    def unresolved_call_count(self) -> int:
        return sum(1 for item in self.calls if item.status is ResolutionStatus.UNRESOLVED)

    @property
    def builtin_call_count(self) -> int:
        return sum(1 for item in self.calls if item.status is ResolutionStatus.BUILTIN)


@dataclass(frozen=True)
class GraphNode:
    """A node ready to merge into the graph."""

    id: str
    labels: tuple[str, ...]
    properties: dict[str, object]


@dataclass(frozen=True)
class GraphRelationship:
    """A relationship ready to merge into the graph."""

    type: str
    start_id: str
    end_id: str
    properties: dict[str, object]


@dataclass
class GraphPayload:
    """Versioned, deterministic projection of a scan onto the graph schema."""

    schema_version: int
    repository: dict[str, object]
    nodes: list[GraphNode]
    relationships: list[GraphRelationship]


@dataclass(frozen=True)
class ChunkRecord:
    """Search text and its embedding for one entity."""

    id: str
    repo_id: str
    entity_id: str
    text: str
    embedding: list[float]
    properties: dict[str, object]


@dataclass(frozen=True)
class RepositorySummary:
    """Counts stored on a repository node."""

    id: str
    name: str
    root_path: str
    indexed_at: str
    schema_version: int
    file_count: int
    indexed_file_count: int
    entity_count: int
    import_count: int
    resolved_call_count: int
    unresolved_call_count: int
    builtin_call_count: int
    parse_error_count: int
    skipped_count: int
    embedding_count: int


@dataclass(frozen=True)
class IssueView:
    """A stored parse error or skipped path."""

    path: str
    message: str
    is_directory: bool = False


@dataclass(frozen=True)
class RepositoryDetail:
    """Repository summary plus truncated scan diagnostics."""

    summary: RepositorySummary
    parse_errors: list[IssueView]
    skipped: list[IssueView]


@dataclass(frozen=True)
class EntitySummary:
    """Entity fields returned by list endpoints."""

    id: str
    kind: str
    name: str
    qualified_name: str
    repository_id: str
    file_path: str
    start_line: int
    end_line: int
    signature: str | None
    docstring: str | None


@dataclass(frozen=True)
class CallLink:
    """A resolved call edge, from the caller or callee side."""

    entity_id: str
    name: str
    qualified_name: str
    kind: str
    file_path: str
    lineno: int
    raw_name: str


@dataclass(frozen=True)
class UnresolvedLink:
    """A call that was not given a callee entity."""

    lineno: int
    raw_name: str
    status: str
    reason: str | None


@dataclass(frozen=True)
class ImportLink:
    """An import recorded on the entity's module."""

    lineno: int
    statement: str
    target_qualified_name: str | None
    in_repository: bool


@dataclass(frozen=True)
class EntityDetail:
    """Entity definition plus the relationships the API exposes."""

    summary: EntitySummary
    source_hash: str | None
    bases: list[str]
    is_async: bool
    callers: list[CallLink]
    callees: list[CallLink]
    unresolved_calls: list[UnresolvedLink]
    imports: list[ImportLink]


@dataclass(frozen=True)
class ChunkHit:
    """One semantic-search hit before graph enrichment."""

    chunk_id: str
    entity_id: str
    score: float
    text: str
    qualified_name: str
    name: str
    kind: str
    file_path: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class Evidence:
    """Retrieved code context shown to the answering model and the API caller."""

    repository_id: str
    repository_name: str
    entity_id: str
    kind: str
    name: str
    qualified_name: str
    file_path: str
    start_line: int | None
    end_line: int | None
    docstring: str | None
    signature: str | None
    excerpt: str | None
    score: float
    callers: list[str]
    callees: list[str]
    unresolved_calls: list[str]
    imports: list[str]


@dataclass
class IndexSummary:
    """Outcome of indexing one path, including the embedding step."""

    repository_id: str
    name: str
    root_path: str
    indexed_file_count: int
    skipped_count: int
    parse_error_count: int
    entity_count: int
    resolved_call_count: int
    unresolved_call_count: int
    builtin_call_count: int
    embeddings_status: str
    embeddings_count: int
    message: str | None = None
