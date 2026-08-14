"""Guards on the instructions, because a wrong README is a defect users hit first.

The README once documented ``surreal start --user root --pass root`` under a
heading promising persistence. That command is silently in-memory: ``[PATH]``
defaults to ``memory``, so the container starts, accepts writes, and drops the
whole graph on restart without an error. The script and the durability test had
both been corrected; only the README was left re-deriving the flags, wrongly.
"""

from __future__ import annotations

import re
from pathlib import Path

README = Path(__file__).resolve().parent.parent / "README.md"

# A shell invocation of the SurrealDB server, with whatever follows the
# credentials. Line continuations are folded first, so a command split across
# several lines is matched whole.
SURREAL_START = re.compile(r"start\s+--user\s+root\s+--pass\s+root([^\n]*)")


def _folded(text: str) -> str:
    return text.replace("\\\n", " ")


def test_every_documented_surrealdb_start_names_an_on_disk_path():
    commands = SURREAL_START.findall(_folded(README.read_text()))

    assert commands, "expected the README to document starting SurrealDB"
    for tail in commands:
        assert "rocksdb:" in tail, (
            "a documented SurrealDB start has no storage path, so it runs "
            f"in-memory and loses the graph on restart: ...{tail.strip()!r}"
        )
