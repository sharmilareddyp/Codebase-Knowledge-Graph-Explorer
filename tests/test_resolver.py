from pathlib import Path

from codebase_kg.indexer.pipeline import index_repository
from codebase_kg.models import EntityKind, ResolutionStatus


def _write(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_resolves_imports_inheritance_and_stops_at_dynamic_calls(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(
        tmp_path,
        "pkg/animals.py",
        '''
class Animal:
    def speak(self):
        """Speak."""
        return "..."

class Dog(Animal, object):
    def bark(self):
        self.speak()
        self.missing()

class ErrorCase(Exception):
    pass

def run(dog):
    dog.bark()
    print("hi")
    getattr(dog, "bark")()

def outer():
    def inner():
        return outer()
    def sibling():
        return inner()
    return sibling()
''',
    )
    _write(
        tmp_path,
        "pkg/caller.py",
        '''
from .animals import Dog, speak
import json

def make():
    dog = Dog()
    return dog.bark()

def loads():
    import json as local_json
    return local_json.loads("{}")
''',
    )
    scan = index_repository(tmp_path)
    by_name = {entity.qualified_name: entity for entity in scan.entities}
    dog = by_name["pkg.animals.Dog"]
    assert dog.kind is EntityKind.CLASS
    assert dog.resolved_base_qns == ["pkg.animals.Animal"]
    assert "object" in dog.bases
    assert by_name["pkg.animals.ErrorCase"].resolved_base_qns == []

    resolved = {
        (call.caller_qualified_name, call.raw_name): call.callee_qualified_name
        for call in scan.calls
        if call.status is ResolutionStatus.RESOLVED
    }
    unresolved = {
        (call.caller_qualified_name, call.raw_name): call.reason
        for call in scan.calls
        if call.status is ResolutionStatus.UNRESOLVED
    }
    builtins = {(call.caller_qualified_name, call.raw_name) for call in scan.calls if call.status is ResolutionStatus.BUILTIN}

    assert resolved[("pkg.animals.Dog.bark", "self.speak")] == "pkg.animals.Animal.speak"
    assert "method not found" in unresolved[("pkg.animals.Dog.bark", "self.missing")]
    assert "name not bound" in unresolved[("pkg.animals.run", "dog.bark")]
    assert any(
        call.caller_qualified_name == "pkg.animals.run" and call.reason and "dynamic" in call.reason
        for call in scan.calls
    )
    assert ("pkg.animals.run", "print") in builtins
    assert ("pkg.animals.run", "getattr") in builtins
    assert resolved[("pkg.animals.outer", "sibling")] == "pkg.animals.outer.<locals>.sibling"
    assert resolved[("pkg.animals.outer.<locals>.inner", "outer")] == "pkg.animals.outer"
    assert resolved[("pkg.animals.outer.<locals>.sibling", "inner")] == "pkg.animals.outer.<locals>.inner"
    assert resolved[("pkg.caller.make", "Dog")] == "pkg.animals.Dog"
    assert "name not bound" in unresolved[("pkg.caller.make", "dog.bark")]
    assert "name not bound" in unresolved[("pkg.caller.loads", "local_json.loads")]

    star = tmp_path / "pkg" / "star.py"
    star.write_text("from json import *\n\ndef use():\n    return dumps('x')\n", encoding="utf-8")
    scan = index_repository(tmp_path)
    star_calls = [call for call in scan.calls if call.caller_qualified_name == "pkg.star.use"]
    assert star_calls
    assert all(call.status is not ResolutionStatus.RESOLVED for call in star_calls)


def test_attribute_import_resolves_when_the_module_is_indexed(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(tmp_path, "pkg/util.py", "def helper():\n    return 1\n")
    _write(
        tmp_path,
        "pkg/app.py",
        "import pkg.util as util\n\ndef run():\n    return util.helper()\n",
    )
    scan = index_repository(tmp_path)
    resolved = [call for call in scan.calls if call.raw_name == "util.helper"]
    assert len(resolved) == 1
    assert resolved[0].callee_qualified_name == "pkg.util.helper"
    assert resolved[0].status is ResolutionStatus.RESOLVED
