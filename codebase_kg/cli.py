"""Command line for indexing, Neo4j checks, and the API server.

``ckg index`` reads the paths you pass and does not consult ``CKG_ALLOWED_ROOTS``.
The HTTP API uses that allow-list. Neither command executes the indexed source.
"""

from __future__ import annotations

import argparse
import sys

from codebase_kg.config import Settings, get_settings
from codebase_kg.errors import AppError
from codebase_kg.graph.neo4j_store import Neo4jGraphStore
from codebase_kg.logging_config import configure_logging
from codebase_kg.models import IndexOptions
from codebase_kg.service import index_repositories


def main(argv: list[str] | None = None) -> int:
    """Console-script entry point."""

    return run(argv)


def run(
    argv: list[str] | None = None,
    *,
    settings: Settings | None = None,
    store: object | None = None,
) -> int:
    """Run a CLI command. Tests may inject ``settings`` and ``store``."""

    if sys.version_info < (3, 11):
        print("Python 3.11 or newer is required.", file=sys.stderr)
        return 1

    parser = _parser()
    args = parser.parse_args(argv)
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    created: Neo4jGraphStore | None = None
    if store is None and args.command != "serve":
        store = Neo4jGraphStore.from_settings(settings)
        created = store
    try:
        if args.command == "check-neo4j":
            return _check(store)
        if args.command == "index":
            return _index(args, settings, store)
        if args.command == "serve":
            return _serve(args, settings)
    except AppError as exc:
        print(exc.detail, file=sys.stderr)
        return 1 if exc.status_code >= 500 else 2
    finally:
        if created is not None:
            created.close()
    return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ckg", description="Index Python repositories into Neo4j.")
    sub = parser.add_subparsers(dest="command", required=True)

    index = sub.add_parser("index", help="Index one or more local repositories")
    index.add_argument("paths", nargs="+", help="Repository directories")
    index.add_argument("--ignore-dir", action="append", default=[], help="Extra directory name to skip")
    index.add_argument("--no-gitignore", action="store_true", help="Do not apply .gitignore files")
    index.add_argument("--embed", action="store_true", help="Create OpenAI embeddings for entities")
    index.add_argument("--follow-symlinks", action="store_true", help="Follow symlinks that stay inside the repository")
    index.add_argument("--max-file-bytes", type=int, default=1_000_000)

    sub.add_parser("check-neo4j", help="Check that Neo4j accepts a query")

    serve = sub.add_parser("serve", help="Run the HTTP API")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    return parser


def _check(store: object) -> int:
    if store.ping():  # type: ignore[attr-defined]
        print("Neo4j connection succeeded.")
        return 0
    print("Neo4j connection failed.", file=sys.stderr)
    return 1


def _index(args: argparse.Namespace, settings: Settings, store: object) -> int:
    for name in args.ignore_dir:
        if name in {"", ".", ".."} or "/" in name or "\\" in name:
            print(f"ignore-dir must be a single directory name, got {name!r}.", file=sys.stderr)
            return 2
    if args.max_file_bytes < 1:
        print("--max-file-bytes must be positive.", file=sys.stderr)
        return 2
    if not store.ping():  # type: ignore[attr-defined]
        print("Neo4j connection failed.", file=sys.stderr)
        return 1
    store.ensure_schema()  # type: ignore[attr-defined]
    embedder = None
    if args.embed:
        key = settings.openai_key_value()
        if not key:
            print("Embeddings were requested, but OPENAI_API_KEY is not set.", file=sys.stderr)
            return 2
        from codebase_kg.retrieval.embeddings import OpenAIEmbeddingProvider

        embedder = OpenAIEmbeddingProvider(key, settings.openai_embedding_model, settings.openai_embedding_dimensions)
    options = IndexOptions(
        respect_gitignore=not args.no_gitignore,
        extra_ignore_dirs=tuple(args.ignore_dir),
        follow_symlinks=args.follow_symlinks,
        max_file_bytes=args.max_file_bytes,
    )
    results = index_repositories(
        args.paths,
        options,
        settings,
        store,
        enforce_allowed_roots=False,
        embed=args.embed,
        embedder=embedder,
    )
    for item in results:
        print(
            f"{item.repository_id} {item.root_path} files={item.indexed_file_count} "
            f"entities={item.entity_count} parse_errors={item.parse_error_count} "
            f"skipped={item.skipped_count} unresolved_calls={item.unresolved_call_count} "
            f"embeddings={item.embeddings_status}:{item.embeddings_count}"
        )
        if item.message:
            print(item.message, file=sys.stderr)
    return 0


def _serve(args: argparse.Namespace, settings: Settings) -> int:
    import uvicorn

    uvicorn.run(
        "codebase_kg.api.app:create_app",
        factory=True,
        host=args.host or settings.api_host,
        port=args.port or settings.api_port,
    )
    return 0
