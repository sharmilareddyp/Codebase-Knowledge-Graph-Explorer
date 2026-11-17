"""Locate Python files without executing them."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pathspec

from codebase_kg.models import IndexOptions, SkippedPath

logger = logging.getLogger(__name__)

DEFAULT_IGNORE_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".idea",
        ".vscode",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        ".eggs",
        "__pycache__",
        "build",
        "dist",
        "htmlcov",
        "node_modules",
        "site-packages",
        "venv",
        ".venv",
        "env",
        "virtualenv",
    }
)


class GitIgnoreIndex:
    """Root and nested ``.gitignore`` rules, applied where practical.

    Patterns are evaluated relative to the ``.gitignore`` that declared them.
    A file is ignored when any ancestor rule set matches it. Negation inside a
    single ``.gitignore`` follows ``pathspec`` gitwildmatch semantics. A child
    ``.gitignore`` does not re-include a file whose parent directory was
    already pruned, which matches Git's "do not descend into an excluded
    directory" behavior.
    """

    def __init__(self) -> None:
        self._specs: dict[str, pathspec.PathSpec] = {}

    def add(self, directory_rel: str, text: str) -> None:
        lines = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            lines.append(line)
        if not lines:
            return
        self._specs[directory_rel] = pathspec.PathSpec.from_lines("gitwildmatch", lines)

    def ignored(self, relative_posix: str, *, is_dir: bool = False) -> bool:
        parts = relative_posix.split("/")
        directories = [""]
        for index in range(len(parts) - 1):
            directories.append("/".join(parts[: index + 1]))
        for directory in directories:
            spec = self._specs.get(directory)
            if spec is None:
                continue
            inner = relative_posix if directory == "" else relative_posix[len(directory) + 1 :]
            if _spec_matches(spec, inner, is_dir=is_dir):
                return True
        return False


def discover_python_files(
    root: Path,
    options: IndexOptions,
) -> tuple[list[Path], list[SkippedPath]]:
    """Walk ``root`` and return Python files plus skipped paths.

    Default ignore directories, optional extra names, and ``.gitignore`` files
    are pruned before descent. File symlinks are skipped unless
    ``follow_symlinks`` is set, and even then a symlink whose target leaves
    ``root`` is skipped.
    """

    ignore_dirs = set(DEFAULT_IGNORE_DIRS)
    ignore_dirs.update(options.extra_ignore_dirs)
    gitignore = GitIgnoreIndex() if options.respect_gitignore else None
    skipped: list[SkippedPath] = []
    files: list[Path] = []

    def onerror(error: OSError) -> None:
        filename = getattr(error, "filename", None) or str(root)
        try:
            rel = Path(filename).resolve().relative_to(root.resolve()).as_posix()
        except (OSError, ValueError):
            rel = str(filename)
        skipped.append(SkippedPath(path=rel, reason=f"unreadable directory: {error.strerror}", is_directory=True))

    visited: set[Path] = set()
    for dirpath, dirnames, filenames in os.walk(
        root,
        topdown=True,
        followlinks=options.follow_symlinks,
        onerror=onerror,
    ):
        current = Path(dirpath)
        try:
            resolved_current = current.resolve()
            rel_dir = current.relative_to(root).as_posix()
        except (OSError, ValueError):
            dirnames[:] = []
            continue
        if resolved_current in visited or not _symlink_stays_inside(current, root):
            dirnames[:] = []
            continue
        visited.add(resolved_current)
        if rel_dir == ".":
            rel_dir = ""

        if gitignore is not None:
            gitignore_path = current / ".gitignore"
            if gitignore_path.is_file() and not gitignore_path.is_symlink():
                try:
                    gitignore.add(rel_dir, gitignore_path.read_text(encoding="utf-8", errors="replace"))
                except OSError as exc:
                    logger.warning("Could not read gitignore at %s: %s", rel_dir or ".", exc.strerror)

        kept: list[str] = []
        for dirname in sorted(dirnames):
            child = current / dirname
            child_rel = f"{rel_dir}/{dirname}" if rel_dir else dirname
            if dirname in ignore_dirs or dirname == ".git":
                skipped.append(SkippedPath(path=child_rel, reason="ignored directory", is_directory=True))
                continue
            if child.is_symlink():
                if not options.follow_symlinks:
                    skipped.append(SkippedPath(path=child_rel, reason="symlink directory", is_directory=True))
                    continue
                if not _symlink_stays_inside(child, root):
                    skipped.append(
                        SkippedPath(path=child_rel, reason="symlink escapes repository", is_directory=True)
                    )
                    continue
            if gitignore is not None and gitignore.ignored(child_rel, is_dir=True):
                skipped.append(SkippedPath(path=child_rel, reason="gitignore", is_directory=True))
                continue
            kept.append(dirname)
        dirnames[:] = kept

        for filename in sorted(filenames):
            if not filename.endswith(".py"):
                continue
            path = current / filename
            rel = f"{rel_dir}/{filename}" if rel_dir else filename
            if path.is_symlink():
                if not options.follow_symlinks:
                    skipped.append(SkippedPath(path=rel, reason="symlink", is_directory=False))
                    continue
                if not _symlink_stays_inside(path, root):
                    skipped.append(SkippedPath(path=rel, reason="symlink escapes repository", is_directory=False))
                    continue
            if gitignore is not None and gitignore.ignored(rel, is_dir=False):
                skipped.append(SkippedPath(path=rel, reason="gitignore", is_directory=False))
                continue
            files.append(path)

    return files, skipped


def _spec_matches(spec: pathspec.PathSpec, relative_posix: str, *, is_dir: bool) -> bool:
    if spec.match_file(relative_posix):
        return True
    if is_dir and spec.match_file(relative_posix.rstrip("/") + "/"):
        return True
    return False


def _symlink_stays_inside(path: Path, root: Path) -> bool:
    try:
        resolved = path.resolve()
        resolved.relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True
