from pathlib import Path

from codebase_kg.ids import file_id
from codebase_kg.indexer.extractor import extract_file, module_qualified_name
from codebase_kg.models import EntityKind


def test_module_qualified_name_from_paths() -> None:
    assert module_qualified_name("pkg/mod.py") == "pkg.mod"
    assert module_qualified_name("pkg/__init__.py") == "pkg"
    assert module_qualified_name("top.py") == "top"
    assert module_qualified_name("pkg/sub/__init__.py") == "pkg.sub"


def test_extracts_nested_qualified_names_without_executing() -> None:
    source = '''
class Outer:
    """Outer class."""

    def method(self):
        """Method doc."""
        def nested():
            return (lambda: 1)
        return nested

def outer():
    class Inner:
        pass
    return Inner
'''
    extracted = extract_file(
        repo_id="repo",
        file_id_value=file_id("repo", "pkg/mod.py"),
        relative_path="pkg/mod.py",
        source=source,
        source_hash="hash",
    )
    names = {entity.qualified_name: entity for entity in extracted.entities}
    assert "pkg.mod" in names
    assert names["pkg.mod"].kind is EntityKind.MODULE
    assert names["pkg.mod"].docstring is None or "Outer" not in (names["pkg.mod"].docstring or "")
    assert names["pkg.mod.Outer"].kind is EntityKind.CLASS
    assert names["pkg.mod.Outer"].docstring == "Outer class."
    assert names["pkg.mod.Outer.method"].kind is EntityKind.METHOD
    assert names["pkg.mod.Outer.method"].docstring == "Method doc."
    assert "pkg.mod.Outer.method.<locals>.nested" in names
    assert any(name.startswith("pkg.mod.Outer.method.<locals>.nested.<lambda:L") for name in names)
    assert "pkg.mod.outer.<locals>.Inner" in names
    assert names["pkg.mod.outer"].kind is EntityKind.FUNCTION
    method = names["pkg.mod.Outer.method"]
    assert method.start_line < method.end_line
    assert method.source_hash


def test_duplicate_function_names_are_disambiguated() -> None:
    source = """
if True:
    def foo():
        return 1
else:
    def foo():
        return 2
"""
    extracted = extract_file(
        repo_id="repo",
        file_id_value="file",
        relative_path="mod.py",
        source=source,
        source_hash="hash",
    )
    functions = [entity.qualified_name for entity in extracted.entities if entity.name == "foo"]
    assert len(functions) == 2
    assert any("@L" in name for name in functions)
    assert len({entity.id for entity in extracted.entities if entity.name == "foo"}) == 2


def test_syntax_error_does_not_include_the_following_source_line() -> None:
    source = "def broken(\nSECRET_SHOULD_NOT_BE_STORED = 1\n"
    extracted = extract_file(
        repo_id="repo",
        file_id_value="file",
        relative_path="bad.py",
        source=source,
        source_hash="hash",
    )
    assert extracted.parse_error is not None
    assert extracted.entities == []
    assert "SECRET_SHOULD_NOT_BE_STORED" not in extracted.parse_error.message
    assert "syntax error at line" in extracted.parse_error.message


def test_imports_and_raw_calls_are_recorded() -> None:
    source = """
import os.path
from pkg.mod import helper as renamed

def run():
    renamed()
    os.path.join("a", "b")
    value = helper()
    return value
"""
    extracted = extract_file(
        repo_id="repo",
        file_id_value="file",
        relative_path="app.py",
        source=source,
        source_hash="hash",
    )
    statements = {(item.local_name, item.is_module_level, item.imported_name) for item in extracted.imports}
    assert ("os", True, None) in statements
    assert ("renamed", True, "helper") in statements
    raw = {(call.caller_qualified_name, call.raw_name, tuple(call.parts) if call.parts else None) for call in extracted.raw_calls}
    assert ("app.run", "renamed", ("renamed",)) in raw
    assert ("app.run", "os.path.join", ("os", "path", "join")) in raw
    assert ("app.run", "helper", ("helper",)) in raw


def test_indexer_source_does_not_execute_code() -> None:
    text = "\n".join(path.read_text(encoding="utf-8") for path in Path("codebase_kg/indexer").glob("*.py"))
    assert "exec(" not in text
    assert "eval(" not in text
    assert "subprocess" not in text
    assert "importlib" not in text


def test_exec_statement_is_parsed_not_run(tmp_path: Path) -> None:
    from codebase_kg.indexer.pipeline import index_repository

    (tmp_path / "danger.py").write_text('exec("raise SystemExit(7)")\n', encoding="utf-8")
    scan = index_repository(tmp_path)
    assert scan.indexed_file_count == 1
    assert any(entity.name == "danger" or entity.qualified_name == "danger" for entity in scan.entities)
