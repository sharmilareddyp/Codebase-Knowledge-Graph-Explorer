"""Deterministic identifiers for graph nodes.

Re-indexing the same repository path and the same qualified name yields the same
id, so writes can merge into existing nodes instead of inserting duplicates.
Moving a repository on disk changes its id because the resolved path is part of
the repository key.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_ID_LENGTH = 32


def stable_id(*parts: str) -> str:
    """Return a 128-bit hex id from the given stable parts.

    Parts are joined with a unit separator so ``("ab", "c")`` cannot collide
    with ``("a", "bc")``.
    """

    payload = "\x1f".join(parts)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return digest[:_ID_LENGTH]


def repository_id(root: Path) -> str:
    """Id for a repository rooted at a resolved absolute path."""

    return stable_id("repository", str(root.resolve()))


def file_id(repo_id: str, relative_path: str) -> str:
    """Id for a repository-relative POSIX path."""

    return stable_id("file", repo_id, relative_path)


def entity_id(repo_id: str, kind: str, qualified_name: str) -> str:
    """Id for a module, class, function, or method."""

    return stable_id("entity", repo_id, kind, qualified_name)


def import_id(
    repo_id: str,
    file_path: str,
    lineno: int,
    ordinal: int,
    level: int,
    source_module: str | None,
    imported_name: str | None,
    alias: str | None,
) -> str:
    """Id for one imported name at a source location."""

    return stable_id(
        "import",
        repo_id,
        file_path,
        str(lineno),
        str(ordinal),
        str(level),
        source_module or "",
        imported_name or "",
        alias or "",
    )


def call_id(
    repo_id: str,
    caller_qualified_name: str,
    lineno: int,
    ordinal: int,
    raw_name: str,
) -> str:
    """Id for one call expression inside a caller."""

    return stable_id(
        "call",
        repo_id,
        caller_qualified_name,
        str(lineno),
        str(ordinal),
        raw_name,
    )


def chunk_id(repo_id: str, entity_id_value: str) -> str:
    """Id for the single search chunk attached to an entity."""

    return stable_id("chunk", repo_id, entity_id_value, "v1")


def issue_id(kind: str, repo_id: str, path: str, detail: str) -> str:
    """Id for a parse error or skipped path recorded during a scan."""

    return stable_id(kind, repo_id, path, detail)
