"""Normalize repository paths and enforce the API allow-list."""

from __future__ import annotations

from pathlib import Path

from codebase_kg.errors import PathNotAllowed


def normalize_repository_path(path: str, allowed_roots: list[Path] | None) -> Path:
    """Resolve ``path`` and optionally require it to sit inside ``allowed_roots``.

    ``allowed_roots is None`` skips the allow-list. That mode is reserved for the
    CLI, where the operator already named the directory. The API always passes
    the configured roots. An empty list refuses every path.

    The resolved path must be an existing directory. Symlinks are resolved
    before the allow-list check, so a link cannot point outside a root and
    still pass.
    """

    if "\x00" in path:
        raise PathNotAllowed("Repository path contains a NUL byte")

    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    try:
        resolved = candidate.resolve()
    except OSError as exc:
        raise PathNotAllowed(f"Repository path cannot be resolved: {exc.strerror}") from exc

    if not resolved.exists():
        raise PathNotAllowed(f"Repository path does not exist: {resolved}")
    if not resolved.is_dir():
        raise PathNotAllowed(f"Repository path is not a directory: {resolved}")

    if allowed_roots is not None:
        if not allowed_roots:
            raise PathNotAllowed(
                "No allowed roots are configured. Set CKG_ALLOWED_ROOTS to the directories the API may index."
            )
        if not any(_is_inside(resolved, root) for root in allowed_roots):
            raise PathNotAllowed(f"Repository path is outside the configured allowed roots: {resolved}")
    return resolved


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
