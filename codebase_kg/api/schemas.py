"""HTTP request and response models."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ErrorResponse(BaseModel):
    """Consistent error body. Validation errors also include ``issues``."""

    error: str
    detail: str
    issues: list[object] | None = None


class HealthResponse(BaseModel):
    status: str
    neo4j: bool
    schema_version: int


class IndexRequest(BaseModel):
    """Paths and options for one indexing request."""

    paths: list[str] = Field(min_length=1, max_length=20)
    ignore_dirs: list[str] = Field(default_factory=list, max_length=100)
    respect_gitignore: bool = True
    embed: bool = False
    follow_symlinks: bool = False
    max_file_bytes: int = Field(default=1_000_000, ge=1, le=20_000_000)

    @field_validator("ignore_dirs")
    @classmethod
    def _directory_names(cls, values: list[str]) -> list[str]:
        for value in values:
            if value in {"", ".", ".."} or "/" in value or "\\" in value:
                raise ValueError("ignore_dirs entries must be single directory names")
        return values


class IndexSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

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


class IndexResponse(BaseModel):
    results: list[IndexSummaryResponse]


class RepositorySummaryResponse(BaseModel):
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


class IssueResponse(BaseModel):
    path: str
    message: str
    is_directory: bool = False


class RepositoryDetailResponse(RepositorySummaryResponse):
    parse_errors: list[IssueResponse]
    skipped: list[IssueResponse]


class RepositoryListResponse(BaseModel):
    repositories: list[RepositorySummaryResponse]


class EntitySummaryResponse(BaseModel):
    id: str
    kind: str
    name: str
    qualified_name: str
    repository_id: str
    file_path: str
    start_line: int
    end_line: int
    signature: str | None = None
    docstring: str | None = None


class EntityListResponse(BaseModel):
    repository_id: str
    total: int
    entities: list[EntitySummaryResponse]


class CallLinkResponse(BaseModel):
    entity_id: str
    name: str
    qualified_name: str
    kind: str
    file_path: str
    lineno: int
    raw_name: str


class UnresolvedCallResponse(BaseModel):
    lineno: int
    raw_name: str
    status: str
    reason: str | None = None


class ImportResponse(BaseModel):
    lineno: int
    statement: str
    target_qualified_name: str | None = None
    in_repository: bool


class EntityDetailResponse(EntitySummaryResponse):
    source_hash: str | None = None
    bases: list[str]
    is_async: bool
    callers: list[CallLinkResponse]
    callees: list[CallLinkResponse]
    unresolved_calls: list[UnresolvedCallResponse]
    imports: list[ImportResponse]


class CallersResponse(BaseModel):
    entity_id: str
    callers: list[CallLinkResponse]


class CalleesResponse(BaseModel):
    """Resolved callees are graph edges. Unresolved calls are listed separately."""

    entity_id: str
    resolved: list[CallLinkResponse]
    unresolved: list[UnresolvedCallResponse]


class QueryRequest(BaseModel):
    repository_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    question: str = Field(min_length=3, max_length=4000)
    top_k: int = Field(default=5, ge=1, le=20)


class CitationResponse(BaseModel):
    repository_id: str
    repository_name: str
    file_path: str
    entity_id: str
    entity_name: str
    qualified_name: str
    start_line: int | None = None
    end_line: int | None = None
    score: float


class QueryResponse(BaseModel):
    answer: str
    sufficient_evidence: bool
    citations: list[CitationResponse]
    limitations: str
