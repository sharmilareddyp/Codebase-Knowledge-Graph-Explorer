"""Index one repository directory into in-memory scan records."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path

from codebase_kg.ids import file_id, repository_id
from codebase_kg.indexer.discovery import discover_python_files
from codebase_kg.indexer.extractor import FileExtract, extract_file
from codebase_kg.indexer.resolver import render_import_statement, resolve_scan
from codebase_kg.models import FileRecord, IndexOptions, ScanResult, SkippedPath

logger = logging.getLogger(__name__)


def index_repository(root: Path, options: IndexOptions | None = None) -> ScanResult:
    """Parse every eligible Python file under ``root``.

    Unreadable files and syntax errors are recorded. The remaining files are
    still indexed. Source text is kept on the result only for optional
    embedding and is not persisted by the graph serializer.
    """

    options = options or IndexOptions()
    resolved = root.resolve()
    if not resolved.is_dir():
        raise NotADirectoryError(f"Repository path is not a directory: {resolved}")

    repo_id = repository_id(resolved)
    discovered, skipped = discover_python_files(resolved, options)
    extracts: list[FileExtract] = []
    files: list[FileRecord] = []
    parse_errors = []
    sources: dict[str, str] = {}

    for path in discovered:
        relative = path.relative_to(resolved).as_posix()
        current_id = file_id(repo_id, relative)
        try:
            raw = path.read_bytes()
        except OSError as exc:
            skipped.append(SkippedPath(path=relative, reason=f"unreadable file: {exc.strerror}", is_directory=False))
            continue
        if len(raw) > options.max_file_bytes:
            skipped.append(
                SkippedPath(
                    path=relative,
                    reason=f"file exceeds {options.max_file_bytes} bytes",
                    is_directory=False,
                )
            )
            continue
        try:
            source = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            skipped.append(SkippedPath(path=relative, reason="could not decode as utf-8", is_directory=False))
            continue

        source_hash = hashlib.sha256(raw).hexdigest()
        extract = extract_file(
            repo_id=repo_id,
            file_id_value=current_id,
            relative_path=relative,
            source=source,
            source_hash=source_hash,
        )
        files.append(
            FileRecord(
                id=current_id,
                repo_id=repo_id,
                path=relative,
                source_hash=source_hash,
                line_count=extract.line_count,
                parse_ok=extract.parse_error is None,
            )
        )
        if extract.parse_error is not None:
            parse_errors.append(extract.parse_error)
            continue
        sources[relative] = source
        extracts.append(extract)

    entities, imports, calls = resolve_scan(extracts, repo_id)
    for record in imports:
        record.statement = render_import_statement(record)

    scan = ScanResult(
        repo_id=repo_id,
        repo_name=resolved.name,
        root_path=str(resolved),
        indexed_at=datetime.now(timezone.utc).isoformat(),
        files=sorted(files, key=lambda item: item.path),
        entities=sorted(entities, key=lambda item: (item.file_path, item.start_line, item.qualified_name)),
        imports=sorted(imports, key=lambda item: (item.file_path, item.lineno, item.ordinal)),
        calls=calls,
        parse_errors=sorted(parse_errors, key=lambda item: item.path),
        skipped=sorted(skipped, key=lambda item: (item.path, item.reason)),
        sources=sources,
    )
    logger.info(
        "Indexed %s files=%s entities=%s parse_errors=%s skipped=%s unresolved_calls=%s",
        resolved,
        scan.indexed_file_count,
        len(scan.entities),
        len(scan.parse_errors),
        len(scan.skipped),
        scan.unresolved_call_count,
    )
    return scan
