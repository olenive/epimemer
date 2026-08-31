"""Every notebook's imports still resolve.

Two of the six notebooks sat broken for four months — `02_decomposition`
imported a package that had been deleted, `05_reflection` a module that had —
and nothing noticed, because nothing imports or runs the notebooks. They are
demo material, outside the suite by construction.

**Importing a notebook does not detect this, which is why this file parses
instead of importing.** A marimo notebook keeps every import inside an
`@app.cell` function body, so `exec_module` on the broken `05_reflection.py`
completed without error: the dead import only raises when the cell is called.
That was measured against the deleted file before this test was written, and it
is the reason the obvious version of this check would have passed on both
notebooks it exists to catch.

So the check is static. Collect every import in the file, cell bodies included,
and confirm the module resolves and the names exist. That catches exactly what
killed both notebooks — a name the codebase no longer exports.

There are two checks, split by what a failure would mean.

`test_every_notebook_imports` covers `epimemer` imports and **always runs**: a
name this codebase no longer exports is a defect no environment excuses.

`test_every_notebook_dependency_is_declared` covers every import including
third-party, and **skips where the demo dependencies are absent**. Those
live in the `notebooks` extra, so `uv sync --extra notebooks` is what makes this
one run; without it the failure would say only "you did not install the extra",
which is not news and gets tests deleted.

That skip costs something real and it is worth naming: **a notebook that adds a
new undeclared dependency makes this test skip rather than fail** for anyone
without the extra, because the missing module is both the defect and the skip
condition. It catches the case for whoever has the extra installed — which is
whoever edits notebooks — and no one else. Closing that properly means
resolving declared extras statically instead of importing, which is more
machinery than the defect is worth.

A third gap belongs to both: **a notebook whose imports are fine but whose body
reads something gone still passes.** `05_reflection.py` rendered a "Value Decay"
section against a Petri net place removed by the field with no reader, and
rendered it silently — the section was guarded by `if _dp and _dp.tokens`, so
a deleted phase reads as a
phase that produced nothing. Catching that needs execution, which needs
providers, storage and a runtime.
"""

import ast
import asyncio
import importlib
from pathlib import Path

import pytest

NOTEBOOK_DIR = Path(__file__).resolve().parent.parent / "notebooks"
FIRST_PARTY = "epimemer"


def _notebooks() -> list[Path]:
    return sorted(NOTEBOOK_DIR.glob("*.py"))


def _imports_in(tree: ast.AST) -> list[tuple[str, tuple[str, ...]]]:
    """Every (module, imported names) pair in the file, cell bodies included.

    `ast.walk` rather than `tree.body` deliberately: a marimo notebook's imports
    live inside function bodies, so a top-level scan finds `import marimo` and
    nothing else — including nothing that has ever broken.

    Relative imports are skipped (`level=0` only): notebooks are standalone
    scripts, not a package, so there is no anchor to resolve them against.
    """
    found: list[tuple[str, tuple[str, ...]]] = []
    for node in ast.walk(tree):
        match node:
            case ast.Import(names=aliases):
                found.extend((alias.name, ()) for alias in aliases)
            case ast.ImportFrom(module=module, names=aliases, level=0) if module:
                # A star import names nothing to check beyond the module itself.
                found.append((module, tuple(a.name for a in aliases if a.name != "*")))
    return found


def _is_first_party(module: str) -> bool:
    return module == FIRST_PARTY or module.startswith(f"{FIRST_PARTY}.")


def _unresolved(module: str, names: tuple[str, ...]) -> list[str]:
    """What is wrong with one import statement; empty if it would work.

    Which imports reach here is the caller's choice, not this function's — the
    two tests below check different subsets for different reasons.
    """
    if module == "__future__":
        return []

    try:
        imported = importlib.import_module(module)
    except ImportError as exc:
        return [f"import {module} -> {type(exc).__name__}: {exc}"]

    missing = []
    for name in names:
        if hasattr(imported, name):
            continue
        try:
            # `from pkg import mod` binds a submodule, which is not an attribute
            # of the package until something imports it.
            importlib.import_module(f"{module}.{name}")
        except ImportError:
            missing.append(f"from {module} import {name} -> no such name")
    return missing


def _problems(notebook: Path, keep) -> list[str]:
    tree = ast.parse(notebook.read_text(), filename=str(notebook))
    return [
        problem
        for module, names in _imports_in(tree)
        if keep(module)
        for problem in _unresolved(module, names)
    ]


@pytest.mark.parametrize("notebook", _notebooks(), ids=lambda path: path.name)
def test_every_notebook_imports(notebook: Path):
    problems = _problems(notebook, _is_first_party)

    assert not problems, f"{notebook.name}:\n  " + "\n  ".join(problems)


@pytest.mark.parametrize("notebook", _notebooks(), ids=lambda path: path.name)
def test_every_notebook_dependency_is_declared(notebook: Path):
    # Both sentinels are modules the notebooks import directly, so neither is a
    # guess at what the extra happens to pull in transitively.
    absent = "notebook dependencies absent — run `uv sync --extra notebooks`"
    pytest.importorskip("petritype.plotting.simple_graphviz", reason=absent, exc_type=ImportError)
    pytest.importorskip("graphviz", reason=absent, exc_type=ImportError)

    problems = _problems(notebook, lambda _: True)

    assert not problems, f"{notebook.name}:\n  " + "\n  ".join(problems)


def test_there_are_notebooks_to_check():
    """An empty parameterization passes, which would retire the check in silence.

    The same shape as the bug above: something stops being exercised and nothing
    says so. If the notebooks move or go, this fails and the decision gets made
    deliberately.
    """
    assert _notebooks(), f"no notebooks found under {NOTEBOOK_DIR}"


# --- Running the cells, which is the gap the module docstring names ---------


class _UIStub:
    """Stands in for a marimo UI element, which needs a running frontend.

    A notebook reads `element.value`, so the stub carries whatever `value=` the
    cell constructed it with and answers every other attribute with another
    stub. That is enough to reach the code under test: what the widget renders
    is marimo's business, and what the notebook *does with the value* is this
    project's.
    """

    def __init__(self, value=None):
        self.value = value

    def __call__(self, *args, **kwargs):
        return _UIStub(kwargs.get("value", args[0] if args else None))

    def __getattr__(self, name):
        return _UIStub()

    def __bool__(self):
        return True


def _returned_names(cell: ast.AST) -> list[str]:
    """The names a cell hands to the cells below it.

    marimo compiles dataflow into the signature and the final `return`: a cell
    declares what it needs as arguments and what it defines as returns. Reading
    both is what lets this run them in order without marimo present.
    """
    last = cell.body[-1]
    if not isinstance(last, ast.Return) or last.value is None:
        return []
    match last.value:
        case ast.Tuple(elts=elts):
            return [e.id for e in elts if isinstance(e, ast.Name)]
        case ast.Name(id=name):
            return [name]
    return []


def _cells(tree: ast.AST) -> list[ast.AST]:
    return [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(getattr(d, "attr", "") == "cell" for d in node.decorator_list)
    ]


def _run_cells(notebook: Path) -> list[str]:
    """Execute every cell in order, returning what went wrong.

    The environment is real except for the frontend: `InMemoryStorage` and the
    mock embedding provider are what the notebooks construct themselves, so
    nothing is substituted for the system under test.
    """
    tree = ast.parse(notebook.read_text(), filename=str(notebook))
    env: dict = {"mo": _UIStub()}
    problems: list[str] = []
    for cell in _cells(tree):
        names = _returned_names(cell)
        runnable = type(cell)(
            name="_cell",
            args=cell.args,
            body=cell.body,
            decorator_list=[],
            returns=None,
            type_comment=None,
            type_params=[],
        )
        module = ast.Module(body=[runnable], type_ignores=[])
        ast.fix_missing_locations(module)
        scope = dict(env)
        try:
            exec(compile(module, str(notebook), "exec"), scope)
            result = scope["_cell"](*[env.get(a.arg, _UIStub()) for a in cell.args.args])
            if asyncio.iscoroutine(result):
                result = asyncio.run(result)
            if names:
                values = result if isinstance(result, tuple) else (result,)
                env.update(dict(zip(names, values, strict=True)))
        except Exception as exc:
            line = getattr(cell, "lineno", "?")
            problems.append(f"line {line}: {type(exc).__name__}: {exc}")
    return problems


@pytest.mark.parametrize("notebook", _notebooks(), ids=lambda path: path.name)
def test_every_notebook_runs(notebook: Path):
    """The third gap, closed: a notebook whose imports resolve and whose body does not.

    `06_orchestration.py` offered an `ingest` action the orchestration net has
    never had — the real four are `segment`, `store_decomposition`, `search` and
    `reflect` — and looked for an `IngestInput` place that does not exist. Every
    import resolved, so both checks above passed while the notebook raised on
    load. That is the case the module docstring called out as needing execution.

    Execution turns out to be cheap, because marimo compiles the dataflow into
    the cell signatures: each cell declares what it consumes and what it
    produces, so running them in file order with a stub for the UI reproduces
    what marimo would do. Only the frontend is absent.

    What this still does not catch is a cell whose output is *wrong* rather than
    raising — the `if _dp and _dp.tokens` shape, where a deleted phase reads as
    a phase that produced nothing. Asserting on rendered output would pin the
    prose of every notebook to a test, which costs more than that class is
    worth.
    """
    absent = "notebook dependencies absent — run `uv sync --extra notebooks`"
    pytest.importorskip("petritype.plotting.simple_graphviz", reason=absent, exc_type=ImportError)
    pytest.importorskip("graphviz", reason=absent, exc_type=ImportError)

    problems = _run_cells(notebook)

    assert not problems, f"{notebook.name}:\n  " + "\n  ".join(problems)
