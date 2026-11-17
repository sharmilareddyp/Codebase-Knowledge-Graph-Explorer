"""Parse Python source with ``ast`` and collect definitions, imports, and calls.

Nothing in this module imports, executes, or compiles the indexed code. Syntax
errors are returned as parse issues so the caller can continue with other files.
"""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass, field

from codebase_kg.ids import call_id, entity_id, import_id
from codebase_kg.models import Entity, EntityKind, ImportRecord, ParseIssue

DOCSTRING_MAX = 4000
SIGNATURE_MAX = 500
DECORATOR_MAX = 25
BASE_MAX = 25
TEXT_MAX = 200


@dataclass
class RawCall:
    """A call expression before name resolution."""

    caller_qualified_name: str
    lineno: int
    ordinal: int
    raw_name: str
    parts: list[str] | None
    dynamic_reason: str | None


@dataclass
class FileExtract:
    """Extraction output for a single file."""

    source_hash: str
    line_count: int
    module: Entity | None = None
    entities: list[Entity] = field(default_factory=list)
    imports: list[ImportRecord] = field(default_factory=list)
    raw_calls: list[RawCall] = field(default_factory=list)
    parse_error: ParseIssue | None = None


def module_qualified_name(relative_posix: str) -> str:
    """Build a module name from a repository-relative POSIX path.

    ``pkg/mod.py`` becomes ``pkg.mod``. ``pkg/__init__.py`` becomes ``pkg``.
    Path components that are not Python identifiers are preserved so ids stay
    stable for repositories that contain unusual filenames.
    """

    path = relative_posix[:-3] if relative_posix.endswith(".py") else relative_posix
    parts = [part for part in path.split("/") if part not in ("", ".")]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts:
        return "__init__"
    return ".".join(parts)


def extract_file(*, repo_id: str, file_id_value: str, relative_path: str, source: str, source_hash: str) -> FileExtract:
    """Parse ``source`` and extract entities. Never executes it."""

    line_count = max(1, len(source.splitlines()))
    try:
        tree = ast.parse(source, filename=relative_path)
    except SyntaxError as exc:
        message = f"syntax error at line {exc.lineno or 1}: {exc.msg}"
        return FileExtract(
            source_hash=source_hash,
            line_count=line_count,
            parse_error=ParseIssue(path=relative_path, message=message),
        )

    module_qn = module_qualified_name(relative_path)
    is_package = relative_path.endswith("/__init__.py") or relative_path == "__init__.py"
    visitor = _Extractor(
        repo_id=repo_id,
        file_id_value=file_id_value,
        relative_path=relative_path,
        source=source,
        module_qn=module_qn,
        is_package=is_package,
    )
    module = visitor.build_module(tree, line_count)
    visitor.entities.append(module)
    for statement in tree.body:
        visitor.visit(statement)
    return FileExtract(
        source_hash=source_hash,
        line_count=line_count,
        module=module,
        entities=visitor.entities,
        imports=visitor.imports,
        raw_calls=visitor.raw_calls,
    )


class _Extractor(ast.NodeVisitor):
    def __init__(
        self,
        *,
        repo_id: str,
        file_id_value: str,
        relative_path: str,
        source: str,
        module_qn: str,
        is_package: bool,
    ) -> None:
        self.repo_id = repo_id
        self.file_id_value = file_id_value
        self.relative_path = relative_path
        self.source = source
        self.module_qn = module_qn
        self.is_package = is_package
        self.entities: list[Entity] = []
        self.imports: list[ImportRecord] = []
        self.raw_calls: list[RawCall] = []
        self.scope: list[tuple[str, str, str]] = []
        self._used_names: set[str] = set()
        self._call_ordinals: dict[tuple[str, int], int] = {}
        self._import_ordinals: dict[int, int] = {}
        self._module_id = ""

    def build_module(self, tree: ast.AST, line_count: int) -> Entity:
        qualified = self._allocate(self.module_qn, 1, 0)
        module = self._entity(
            kind=EntityKind.MODULE,
            name=qualified.split(".")[-1] if qualified else qualified,
            qualified_name=qualified,
            start_line=1,
            end_line=line_count,
            docstring=_docstring(tree),
            parent_id=None,
            parent_qualified_name=None,
            signature=None,
            is_package=self.is_package,
        )
        self._module_id = module.id
        module.module_id = module.id
        self.scope.append(("module", module.qualified_name, module.id))
        return module

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword.value)

        parent_kind, parent_qn, parent_id = self.scope[-1]
        if parent_kind == "function":
            base_name = f"{parent_qn}.<locals>.{node.name}"
        else:
            base_name = f"{parent_qn}.{node.name}"
        qualified = self._allocate(base_name, node.lineno, node.col_offset)
        entity = self._entity(
            kind=EntityKind.CLASS,
            name=node.name,
            qualified_name=qualified,
            start_line=node.lineno,
            end_line=node.end_lineno or node.lineno,
            docstring=_docstring(node),
            parent_id=parent_id,
            parent_qualified_name=parent_qn,
            signature=_clip(f"class {node.name}({', '.join(_safe_unparse(base) for base in node.bases)})", SIGNATURE_MAX),
            bases=[_clip(_safe_unparse(base), TEXT_MAX) for base in node.bases[:BASE_MAX]],
            decorators=_decorators(node),
        )
        self.entities.append(entity)
        self.scope.append(("class", entity.qualified_name, entity.id))
        for statement in node.body:
            self.visit(statement)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node, is_async=False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node, is_async=True)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._visit_arguments(node.args)
        _, parent_qn, parent_id = self.scope[-1]
        lambda_name = f"<lambda:L{node.lineno}C{node.col_offset}>"
        qualified = self._allocate(f"{parent_qn}.{lambda_name}", node.lineno, node.col_offset)
        entity = self._entity(
            kind=EntityKind.FUNCTION,
            name=lambda_name,
            qualified_name=qualified,
            start_line=node.lineno,
            end_line=node.end_lineno or node.lineno,
            docstring=None,
            parent_id=parent_id,
            parent_qualified_name=parent_qn,
            signature=_clip(_safe_unparse(node), SIGNATURE_MAX),
        )
        self.entities.append(entity)
        self.scope.append(("function", entity.qualified_name, entity.id))
        self.visit(node.body)
        self.scope.pop()

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._add_import(
                lineno=node.lineno or 1,
                level=0,
                is_from_import=False,
                source_module=alias.name,
                imported_name=None,
                alias=alias.asname,
                local_name=alias.asname or alias.name.split(".")[0],
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            star = alias.name == "*"
            self._add_import(
                lineno=node.lineno or 1,
                level=node.level or 0,
                is_from_import=True,
                source_module=node.module,
                imported_name=alias.name,
                alias=alias.asname,
                local_name="*" if star else (alias.asname or alias.name),
            )

    def visit_Call(self, node: ast.Call) -> None:
        _, caller_qn, _caller_id = self.scope[-1]
        parts, reason = _call_parts(node.func)
        raw_name = ".".join(parts) if parts else _clip(_safe_unparse(node.func), TEXT_MAX)
        key = (caller_qn, node.lineno or 1)
        ordinal = self._call_ordinals.get(key, 0)
        self._call_ordinals[key] = ordinal + 1
        self.raw_calls.append(
            RawCall(
                caller_qualified_name=caller_qn,
                lineno=node.lineno or 1,
                ordinal=ordinal,
                raw_name=raw_name,
                parts=parts,
                dynamic_reason=reason,
            )
        )
        self.generic_visit(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef, *, is_async: bool) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        if node.returns is not None:
            self.visit(node.returns)
        self._visit_arguments(node.args)

        parent_kind, parent_qn, parent_id = self.scope[-1]
        if parent_kind == "function":
            base = f"{parent_qn}.<locals>.{node.name}"
            kind = EntityKind.FUNCTION
        elif parent_kind == "class":
            base = f"{parent_qn}.{node.name}"
            kind = EntityKind.METHOD
        else:
            base = f"{parent_qn}.{node.name}"
            kind = EntityKind.FUNCTION
        qualified = self._allocate(base, node.lineno, node.col_offset)
        prefix = "async def" if is_async else "def"
        signature = _clip(f"{prefix} {node.name}({_safe_unparse(node.args)})", SIGNATURE_MAX)
        entity = self._entity(
            kind=kind,
            name=node.name,
            qualified_name=qualified,
            start_line=node.lineno,
            end_line=node.end_lineno or node.lineno,
            docstring=_docstring(node),
            parent_id=parent_id,
            parent_qualified_name=parent_qn,
            signature=signature,
            decorators=_decorators(node),
            is_async=is_async,
        )
        self.entities.append(entity)
        self.scope.append(("function", entity.qualified_name, entity.id))
        for statement in node.body:
            self.visit(statement)
        self.scope.pop()

    def _visit_arguments(self, args: ast.arguments) -> None:
        annotations: list[ast.expr] = []
        for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
            if arg.annotation is not None:
                annotations.append(arg.annotation)
        if args.vararg and args.vararg.annotation is not None:
            annotations.append(args.vararg.annotation)
        if args.kwarg and args.kwarg.annotation is not None:
            annotations.append(args.kwarg.annotation)
        for annotation in annotations:
            self.visit(annotation)
        for default in [*args.defaults, *args.kw_defaults]:
            if default is not None:
                self.visit(default)

    def _add_import(
        self,
        *,
        lineno: int,
        level: int,
        is_from_import: bool,
        source_module: str | None,
        imported_name: str | None,
        alias: str | None,
        local_name: str,
    ) -> None:
        ordinal = self._import_ordinals.get(lineno, 0)
        self._import_ordinals[lineno] = ordinal + 1
        _, _caller_qn, _caller_id = self.scope[-1]
        self.imports.append(
            ImportRecord(
                id=import_id(
                    self.repo_id,
                    self.relative_path,
                    lineno,
                    ordinal,
                    level,
                    source_module,
                    imported_name,
                    alias,
                ),
                repo_id=self.repo_id,
                file_id=self.file_id_value,
                file_path=self.relative_path,
                module_id=self._module_id,
                module_qualified_name=self.module_qn,
                lineno=lineno,
                ordinal=ordinal,
                level=level,
                is_from_import=is_from_import,
                is_module_level=self.scope[-1][0] == "module",
                source_module=source_module,
                imported_name=imported_name,
                alias=alias,
                local_name=local_name,
            )
        )

    def _allocate(self, base: str, lineno: int, column: int) -> str:
        candidate = base
        if candidate in self._used_names:
            candidate = f"{base}@L{lineno}"
        if candidate in self._used_names:
            candidate = f"{base}@L{lineno}C{column}"
        self._used_names.add(candidate)
        return candidate

    def _entity(
        self,
        *,
        kind: EntityKind,
        name: str,
        qualified_name: str,
        start_line: int,
        end_line: int,
        docstring: str | None,
        parent_id: str | None,
        parent_qualified_name: str | None,
        signature: str | None,
        bases: list[str] | None = None,
        decorators: list[str] | None = None,
        is_async: bool = False,
        is_package: bool = False,
    ) -> Entity:
        return Entity(
            id=entity_id(self.repo_id, kind.value, qualified_name),
            kind=kind,
            name=name,
            qualified_name=qualified_name,
            repo_id=self.repo_id,
            file_id=self.file_id_value,
            file_path=self.relative_path,
            start_line=start_line,
            end_line=end_line,
            docstring=docstring,
            source_hash=_excerpt_hash(self.source, start_line, end_line),
            parent_id=parent_id,
            parent_qualified_name=parent_qualified_name,
            module_id=self._module_id,
            module_qualified_name=self.module_qn,
            signature=signature,
            bases=bases or [],
            decorators=decorators or [],
            is_async=is_async,
            is_package=is_package,
        )


def _call_parts(func: ast.expr) -> tuple[list[str] | None, str | None]:
    if isinstance(func, ast.Name):
        return [func.id], None
    if isinstance(func, ast.Attribute):
        if (
            isinstance(func.value, ast.Call)
            and isinstance(func.value.func, ast.Name)
            and func.value.func.id == "super"
        ):
            return None, f"super().{func.attr} is not resolved"
        parts: list[str] = []
        current: ast.expr = func
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
            parts.reverse()
            return parts, None
        return None, "dynamic or complex call target"
    return None, "dynamic or complex call target"


def _docstring(node: ast.AST) -> str | None:
    docstring = ast.get_docstring(node, clean=True)
    if docstring is None:
        return None
    return _clip(docstring, DOCSTRING_MAX)


def _decorators(node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    return [_clip(_safe_unparse(item), TEXT_MAX) for item in node.decorator_list[:DECORATOR_MAX]]


def _safe_unparse(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except (ValueError, TypeError):
        return "<unparseable>"


def _clip(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def _excerpt_hash(source: str, start_line: int, end_line: int) -> str:
    lines = source.splitlines()
    excerpt = "\n".join(lines[max(0, start_line - 1) : max(0, end_line)])
    return hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
