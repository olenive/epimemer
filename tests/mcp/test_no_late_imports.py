"""A running server never loads Epimemer code from disk after it has started.

A tool body that imports an Epimemer module the server has not loaded yet
reads that module from disk on its first call. When the package was upgraded
underneath a long-running server, that file is the new version, and it can
import a name the old, already-loaded modules do not have. That is how
`reflect` and `apply_reflection` came to fail with an `ImportError` on
`DeferralSettings` while every other tool kept working.

So the invariant is: once `epimemer.mcp.server` has been imported, every
Epimemer module that any function the server can run imports is already in
`sys.modules`. A fresh interpreter imports the server and reports what it
loaded; this test reads every loaded module's source for imports inside
function bodies and checks each against that report.
"""

import ast
import importlib.util
import json
import subprocess
import sys
from collections.abc import Iterator

# What the child interpreter reports: every loaded Epimemer module, with its
# source file and the names it defines. The names answer whether
# `from package import name` finds an attribute already there, or has to load
# a submodule from disk.
REPORT_LOADED = """
import json, sys
import epimemer.mcp.server
json.dump(
    {
        name: {"file": getattr(module, "__file__", None), "names": sorted(vars(module))}
        for name, module in list(sys.modules.items())
        if name == "epimemer" or name.startswith("epimemer.")
    },
    open(sys.argv[1], "w"),
)
"""

# The storage backend and the embedding provider the configuration names.
# `create_storage` and `create_embedding_provider` import exactly one of each,
# once, while the server starts and before the first tool call, so the code
# they load is the version the server started on. Importing them all up front
# would make every install import every optional extra, the Postgres driver
# and PyTorch among them, and a missing extra would stop a server that never
# asked for it.
STARTUP_CHOICES: dict[str, frozenset[str]] = {
    "epimemer.mcp.config": frozenset(
        {
            "epimemer.storage.surrealdb_adapter",
            "epimemer.storage.sqlite_adapter",
            "epimemer.storage.postgres_adapter",
            "epimemer.storage.memory",
            "epimemer.embeddings.sentence_transformers",
            "epimemer.embeddings.mock",
        }
    )
}


def _is_epimemer(module: str) -> bool:
    return module == "epimemer" or module.startswith("epimemer.")


def late_imports(source: str) -> Iterator[tuple[int, str, str | None]]:
    """Every Epimemer import inside a function body, as (line, module, name).

    `name` is what a `from` import takes from the module, and None for a plain
    `import`. Nested functions and classes inside a function count, since
    their bodies run after the server has started too.
    """

    def walk(node: ast.AST, in_function: bool) -> Iterator[tuple[int, str, str | None]]:
        for child in ast.iter_child_nodes(node):
            if in_function and isinstance(child, ast.Import):
                for alias in child.names:
                    if _is_epimemer(alias.name):
                        yield child.lineno, alias.name, None
            if in_function and isinstance(child, ast.ImportFrom) and child.level == 0:
                if _is_epimemer(child.module or ""):
                    for alias in child.names:
                        yield child.lineno, child.module, alias.name
            yield from walk(
                child,
                in_function or isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef),
            )

    yield from walk(ast.parse(source), False)


def reads_from_disk(module: str, name: str | None, loaded: dict[str, dict]) -> bool:
    """Whether this import would load a module that is not loaded yet.

    `from package import name` needs nothing from disk when the package
    already holds `name`; otherwise Python looks for a submodule of that name,
    which must then be loaded already.
    """
    if module not in loaded:
        return True
    if name is None or name in loaded[module]["names"]:
        return False
    return f"{module}.{name}" not in loaded


def _loaded_by_the_server(tmp_path) -> dict[str, dict]:
    report = tmp_path / "loaded.json"
    subprocess.run(
        [sys.executable, "-c", REPORT_LOADED, str(report)],
        check=True,
        timeout=120,
    )
    return json.loads(report.read_text())


def _startup_choice_files() -> dict[str, str]:
    """The source of each startup choice, found without importing it.

    They load at startup, so their own function bodies run while the server
    serves and are held to the same rule.
    """
    chosen = {target for targets in STARTUP_CHOICES.values() for target in targets}
    return {name: importlib.util.find_spec(name).origin for name in sorted(chosen)}


def test_the_finder_sees_an_import_in_a_nested_function():
    source = (
        "import epimemer.core\n"
        "def outer():\n"
        "    def inner():\n"
        "        from epimemer.core.types import Fact\n"
        "    import epimemer.storage.bm25\n"
    )
    assert sorted(late_imports(source)) == [
        (4, "epimemer.core.types", "Fact"),
        (5, "epimemer.storage.bm25", None),
    ]


def test_a_missing_submodule_reads_from_disk():
    loaded = {"epimemer.pipelines.timeline": {"names": ["ordering"]}}
    assert not reads_from_disk("epimemer.pipelines.timeline", "ordering", loaded)
    assert reads_from_disk("epimemer.pipelines.timeline", "functions", loaded)
    assert reads_from_disk("epimemer.pipelines.query", "types", loaded)


def test_importing_the_server_loads_every_module_its_functions_import(tmp_path):
    loaded = _loaded_by_the_server(tmp_path)
    files = {name: entry["file"] for name, entry in loaded.items() if entry["file"]}
    files |= _startup_choice_files()

    offenders = [
        f"{importer}:{line} imports " + (f"{name} from {module}" if name else module)
        for importer, path in sorted(files.items())
        for line, module, name in late_imports(open(path, encoding="utf-8").read())
        if module not in STARTUP_CHOICES.get(importer, frozenset())
        and reads_from_disk(module, name, loaded)
    ]
    assert not offenders, (
        "these imports inside functions would load Epimemer code from disk after "
        "the server started; import them at the top of the module:\n" + "\n".join(offenders)
    )
