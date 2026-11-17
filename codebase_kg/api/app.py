"""FastAPI application.

The API reads and writes the graph through store methods. It does not accept
Cypher, and repository indexing is limited to ``CKG_ALLOWED_ROOTS``.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, Path, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from codebase_kg import __version__
from codebase_kg.api.schemas import (
    CallLinkResponse,
    CallersResponse,
    CalleesResponse,
    CitationResponse,
    EntityDetailResponse,
    EntityListResponse,
    EntitySummaryResponse,
    ErrorResponse,
    HealthResponse,
    ImportResponse,
    IndexRequest,
    IndexResponse,
    IndexSummaryResponse,
    IssueResponse,
    QueryRequest,
    QueryResponse,
    RepositoryDetailResponse,
    RepositoryListResponse,
    RepositorySummaryResponse,
    UnresolvedCallResponse,
)
from codebase_kg.config import Settings, get_settings
from codebase_kg.errors import AppError, NotFoundError
from codebase_kg.graph.neo4j_store import Neo4jGraphStore
from codebase_kg.graph.schema import SCHEMA_VERSION
from codebase_kg.logging_config import configure_logging
from codebase_kg.models import EntityKind, IndexOptions
from codebase_kg.retrieval.prompt import STATIC_ANALYSIS_NOTE
from codebase_kg.retrieval.qa import QAService
from codebase_kg.service import index_repositories

_ID = r"^[0-9a-f]{32}$"
logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    store: object | None = None,
    qa: QAService | None = None,
) -> FastAPI:
    """Build the API. Tests inject a store and QA service; the server uses Neo4j."""

    settings = settings or get_settings()
    configure_logging(settings.log_level)
    if store is None:
        store = Neo4jGraphStore.from_settings(settings)
    if qa is None:
        qa = _qa_from_settings(settings, store)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            app.state.store.ensure_schema()
        except AppError as exc:
            logger.warning("Graph schema setup failed: %s", exc.detail)
        except Exception as exc:
            logger.warning("Graph schema setup failed (%s)", type(exc).__name__)
        yield
        app.state.store.close()

    app = FastAPI(
        title="Codebase Knowledge Graph Explorer",
        version=__version__,
        summary="Index local Python repositories and query the resulting graph.",
        description=(
            "Indexed files are parsed with Python's ast module and are never executed. "
            "Call resolution is incomplete for dynamic Python. "
            "This service does not accept or execute Cypher. "
            + STATIC_ANALYSIS_NOTE
        ),
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.store = store
    app.state.qa = qa

    @app.exception_handler(AppError)
    async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"error": exc.error, "detail": exc.detail})

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": "validation_error",
                "detail": "Request validation failed",
                "issues": jsonable_encoder(exc.errors()),
            },
        )

    @app.get("/health", response_model=HealthResponse, tags=["health"])
    def health() -> HealthResponse:
        reachable = bool(app.state.store.ping())
        return HealthResponse(status="ok" if reachable else "degraded", neo4j=reachable, schema_version=SCHEMA_VERSION)

    @app.post("/repositories/index", response_model=IndexResponse, tags=["repositories"])
    def index_repos(body: IndexRequest) -> IndexResponse:
        options = IndexOptions(
            respect_gitignore=body.respect_gitignore,
            extra_ignore_dirs=tuple(body.ignore_dirs),
            follow_symlinks=body.follow_symlinks,
            max_file_bytes=body.max_file_bytes,
        )
        results = index_repositories(
            body.paths,
            options,
            app.state.settings,
            app.state.store,
            enforce_allowed_roots=True,
            embed=body.embed,
            embedder=app.state.qa.embedder,
        )
        return IndexResponse(results=[IndexSummaryResponse.model_validate(item) for item in results])

    @app.get("/repositories", response_model=RepositoryListResponse, tags=["repositories"])
    def list_repos() -> RepositoryListResponse:
        return RepositoryListResponse(
            repositories=[_repository_summary(item) for item in app.state.store.list_repositories()]
        )

    @app.get("/repositories/{repository_id}", response_model=RepositoryDetailResponse, tags=["repositories"])
    def get_repo(repository_id: Annotated[str, Path(pattern=_ID)]) -> RepositoryDetailResponse:
        detail = app.state.store.get_repository(repository_id)
        if detail is None:
            raise NotFoundError("Repository not found")
        return RepositoryDetailResponse(
            **_repository_summary(detail.summary).model_dump(),
            parse_errors=[IssueResponse(path=item.path, message=item.message) for item in detail.parse_errors],
            skipped=[
                IssueResponse(path=item.path, message=item.message, is_directory=item.is_directory)
                for item in detail.skipped
            ],
        )

    @app.get("/repositories/{repository_id}/entities", response_model=EntityListResponse, tags=["entities"])
    def list_entities(
        repository_id: Annotated[str, Path(pattern=_ID)],
        kind: EntityKind | None = None,
        name: str | None = None,
        file: str | None = None,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> EntityListResponse:
        if app.state.store.get_repository(repository_id) is None:
            raise NotFoundError("Repository not found")
        rows, total = app.state.store.list_entities(
            repository_id,
            kind=None if kind is None else kind.value,
            name=_blank(name),
            file=_blank(file),
            offset=offset,
            limit=limit,
        )
        return EntityListResponse(
            repository_id=repository_id,
            total=total,
            entities=[_entity_summary(item) for item in rows],
        )

    @app.get("/entities/{entity_id}", response_model=EntityDetailResponse, tags=["entities"])
    def get_entity(entity_id: Annotated[str, Path(pattern=_ID)]) -> EntityDetailResponse:
        detail = app.state.store.get_entity(entity_id)
        if detail is None:
            raise NotFoundError("Entity not found")
        return _entity_detail(detail)

    @app.get("/entities/{entity_id}/callers", response_model=CallersResponse, tags=["entities"])
    def callers(entity_id: Annotated[str, Path(pattern=_ID)]) -> CallersResponse:
        rows = app.state.store.callers(entity_id)
        if rows is None:
            raise NotFoundError("Entity not found")
        return CallersResponse(entity_id=entity_id, callers=[_call_link(item) for item in rows])

    @app.get("/entities/{entity_id}/callees", response_model=CalleesResponse, tags=["entities"])
    def callees(entity_id: Annotated[str, Path(pattern=_ID)]) -> CalleesResponse:
        rows = app.state.store.callees(entity_id)
        if rows is None:
            raise NotFoundError("Entity not found")
        resolved, unresolved = rows
        return CalleesResponse(
            entity_id=entity_id,
            resolved=[_call_link(item) for item in resolved],
            unresolved=[_unresolved(item) for item in unresolved],
        )

    @app.post("/query", response_model=QueryResponse, tags=["query"])
    def query(body: QueryRequest) -> QueryResponse:
        result = app.state.qa.ask(body.repository_id, body.question, body.top_k)
        return QueryResponse(
            answer=result.answer,
            sufficient_evidence=result.sufficient_evidence,
            limitations=STATIC_ANALYSIS_NOTE,
            citations=[
                CitationResponse(
                    repository_id=item.repository_id,
                    repository_name=item.repository_name,
                    file_path=item.file_path,
                    entity_id=item.entity_id,
                    entity_name=item.name,
                    qualified_name=item.qualified_name,
                    start_line=item.start_line,
                    end_line=item.end_line,
                    score=item.score,
                )
                for item in result.evidence
            ],
        )

    return app


def _qa_from_settings(settings: Settings, store: object) -> QAService:
    from codebase_kg.retrieval.embeddings import OpenAIEmbeddingProvider
    from codebase_kg.retrieval.qa import LangChainAnswerer

    key = settings.openai_key_value()
    if not key:
        return QAService(store, None, None, settings.vector_min_score)
    embedder = OpenAIEmbeddingProvider(key, settings.openai_embedding_model, settings.openai_embedding_dimensions)
    answerer = LangChainAnswerer(key, settings.openai_chat_model)
    return QAService(store, embedder, answerer, settings.vector_min_score)


def _blank(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return value


def _repository_summary(item: object) -> RepositorySummaryResponse:
    return RepositorySummaryResponse.model_validate(item, from_attributes=True)


def _entity_summary(item: object) -> EntitySummaryResponse:
    return EntitySummaryResponse.model_validate(item, from_attributes=True)


def _call_link(item: object) -> CallLinkResponse:
    return CallLinkResponse.model_validate(item, from_attributes=True)


def _unresolved(item: object) -> UnresolvedCallResponse:
    return UnresolvedCallResponse.model_validate(item, from_attributes=True)


def _entity_detail(detail: object) -> EntityDetailResponse:
    summary = detail.summary  # type: ignore[attr-defined]
    return EntityDetailResponse(
        **_entity_summary(summary).model_dump(),
        source_hash=detail.source_hash,  # type: ignore[attr-defined]
        bases=list(detail.bases),  # type: ignore[attr-defined]
        is_async=detail.is_async,  # type: ignore[attr-defined]
        callers=[_call_link(item) for item in detail.callers],  # type: ignore[attr-defined]
        callees=[_call_link(item) for item in detail.callees],  # type: ignore[attr-defined]
        unresolved_calls=[_unresolved(item) for item in detail.unresolved_calls],  # type: ignore[attr-defined]
        imports=[ImportResponse.model_validate(item, from_attributes=True) for item in detail.imports],  # type: ignore[attr-defined]
    )
