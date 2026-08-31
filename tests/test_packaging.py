"""The wheel carries the agent guidance, not just the code.

`epimemer_prompts/DEFAULT.md` is the full guide to using the tools well, and
`INTEGRATION.md` tells a reader to open it and paste it into their agent's
instructions. Version 0.1.0 shipped without it: 91 files in the wheel and not
one `.md`, so everyone who installed from PyPI was pointed at a file they did
not have.

The cause is worth stating, because it looks like it cannot happen. The build
already finds the directory — `[tool.setuptools.packages.find]` matches
`epimemer*` and needs no `__init__.py` to do it — so the package was present
and simply contributed no files. **A package with nothing to contribute and a
package that does not exist produce identical wheels**, which is why nothing
failed and why the gap survived a release.

So the guard is on the declaration rather than on the file. Asking whether the
guidance is readable would pass in a checkout no matter what `pyproject.toml`
said, since the repository is the one place it is always present.
"""

from __future__ import annotations

import tomllib
from fnmatch import fnmatch
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = ROOT / "epimemer_prompts"


def _package_data() -> dict[str, list[str]]:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        config = tomllib.load(handle)
    return config["tool"]["setuptools"]["package-data"]


def test_every_prompt_file_is_declared_as_package_data():
    """Each guidance file matches a pattern, so adding one cannot silently
    leave it out of the next release the way DEFAULT.md was left out of 0.1.0."""
    patterns = _package_data().get("epimemer_prompts", [])
    undeclared = [
        path.name
        for path in sorted(PROMPTS.glob("*.md"))
        if not any(fnmatch(path.name, pattern) for pattern in patterns)
    ]
    assert undeclared == [], (
        f"{', '.join(undeclared)} is in epimemer_prompts/ but no package-data "
        f"pattern matches it, so `pip install epimemer` will not carry it. Add "
        f"a pattern under [tool.setuptools.package-data]."
    )


def test_the_guidance_the_docs_name_is_there_to_declare():
    """The control. Every assertion above is vacuously true over an empty
    directory, and a moved or renamed file would empty it."""
    assert (PROMPTS / "DEFAULT.md").is_file()


def test_the_guidance_is_readable_as_package_data():
    """What a reader does with it: open it and find the guidance, rather than
    an empty file that satisfies the declaration and says nothing."""
    text = (PROMPTS / "DEFAULT.md").read_text()
    assert "store_decomposition" in text
    assert len(text) > 10_000
