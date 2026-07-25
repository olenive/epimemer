"""The benchmark harness runs.

`scripts/bench.py` is a measuring instrument, not library code, so it has no
unit tests — but it calls the public tool functions by name and keyword, and
those move. Without this it would rot silently and only be discovered the next
time someone needed a number, which is exactly when they cannot wait for it.

Runs the script as a subprocess, the way it is actually used, at the smallest
size that still exercises every stage.
"""

import json
import subprocess
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "bench.py"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=180,
    )


def test_quick_run_emits_one_json_record_per_operation():
    result = _run("--quick", "--n", "10")

    assert result.returncode == 0, result.stderr
    records = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    operations = [r["operation"] for r in records]

    assert operations == ["store_decomposition", "search", "list_sources"]
    assert all(r["backend"] == "memory" for r in records)


def test_records_carry_the_measurements_they_promise():
    result = _run("--quick", "--n", "10")
    by_op = {
        json.loads(line)["operation"]: json.loads(line)
        for line in result.stdout.splitlines()
        if line.strip()
    }

    ingest = by_op["store_decomposition"]
    assert ingest["nodes_actual"] > 0
    assert ingest["edges"] > 0
    assert ingest["docs_per_min"] > 0

    assert by_op["search"]["p50_ms"] >= 0
    assert by_op["search"]["p95_ms"] >= by_op["search"]["p50_ms"]
    assert by_op["list_sources"]["ms"] >= 0


def test_reflect_is_timed_unless_skipped():
    """--quick skips reflect because it dominates the runtime; a plain run must
    not, or the slowest operation would go unmeasured."""
    quick = _run("--quick", "--n", "10")
    full = _run("--n", "10", "--searches", "2")

    assert "reflect" not in quick.stdout
    assert full.returncode == 0, full.stderr
    assert '"operation": "reflect"' in full.stdout


def test_progress_stays_off_stdout():
    """`bench.py > run.jsonl` has to yield parseable JSON lines and nothing else."""
    result = _run("--quick", "--n", "10")

    for line in result.stdout.splitlines():
        if line.strip():
            json.loads(line)
    assert "seeding" in result.stderr
