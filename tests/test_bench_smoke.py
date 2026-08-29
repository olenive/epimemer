"""The benchmark harness runs.

`scripts/bench.py` is a measuring instrument, not library code, so it has no
unit tests — but it calls the public tool functions by name and keyword, and
those move. Without this it would rot silently and only be discovered the next
time someone needed a number, which is exactly when they cannot wait for it.

Runs the script as a subprocess, the way it is actually used, at the smallest
size that still exercises every stage.
"""

import importlib.util
import json
import random
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "bench.py"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=180,
    )


@pytest.fixture(scope="module")
def bench():
    """The script as a module — it is not on the import path.

    The corpus generators are pure, and the properties that matter about them
    are properties of the text they produce, not of a run: a subprocess would
    have to infer them from timings.
    """
    sys.path.insert(0, str(_SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("bench", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_quick_run_emits_one_json_record_per_operation():
    result = _run("--quick", "--n", "10")

    assert result.returncode == 0, result.stderr
    records = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    operations = [r["operation"] for r in records]

    assert operations == [
        "store_decomposition", "corpus", "embedding", "search", "list_sources",
        "annotations",
    ]
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

    annotations = by_op["annotations"]
    assert annotations["result_set"] > 0
    for annotation in ("review_labels", "validity", "corroboration"):
        assert annotations[f"{annotation}_ms"] >= 0

    # The corpus is recorded beside the timings taken over it. Without this a
    # cost figure cannot be compared with a later one, which is how the
    # templated corpus's survival rate went unquestioned for as long as it did.
    corpus = by_op["corpus"]
    assert corpus["items"] > 0
    assert corpus["threshold"] > 0
    assert corpus["survivors"] >= 0
    assert corpus["dated_facts"] == 0
    assert corpus["unsound_inferences"] == 0

    embedding = by_op["embedding"]
    assert embedding["batch_32"]["texts_per_sec"] > 0
    assert embedding["batch_1"]["ms_per_text"] >= 0


def test_the_default_corpus_is_the_one_that_describes_reality():
    """The corpus default and the scenario defaults answer different questions,
    and they go opposite ways.

    A benchmark should default to measuring the thing it exists to measure, so
    the corpus defaults to the one whose survival rate resembles real prose —
    `templated` held the default for a day on comparability grounds, and
    comparability belongs in a labelled row rather than in a default nobody
    re-reads. The scenario dials default off for the opposite reason: planting,
    attribution, edge density and dating each describe a graph that has become
    something, and defaulting one on would misrepresent an ordinary graph as
    surely as the 17-word corpus did.
    """
    result = _run("--quick", "--n", "10")
    records = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]

    assert all(r["corpus"] == "diverse" for r in records)

    assert all(r["publishers"] == 0 for r in records)
    assert all(r["similarity_degree"] == 0 for r in records)
    assert all(r["duplicate_groups"] == 0 for r in records)
    assert all(r["dated_share"] == 0.0 for r in records)


def test_every_record_names_the_corpus_it_was_taken_over():
    """What replaced the old default as the guarantee of comparability. A row
    that does not say which corpus produced it cannot be compared with a later
    one, whichever way the default happens to be set."""
    result = _run("--quick", "--n", "10")
    records = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]

    assert records
    for record in records:
        assert record["corpus"] in ("templated", "diverse")
        assert record["embeddings"] in ("real", "mock-384")
    assert next(r for r in records if r["operation"] == "annotations")[
        "similarity_edges"
    ] == 0


def test_the_corpus_flags_actually_change_the_corpus():
    """Otherwise the annotation timings would measure an empty walk and report
    it as a cost — the failure mode where a benchmark looks cheap because it
    benchmarks nothing."""
    result = _run(
        "--quick", "--n", "40", "--publishers", "4", "--similarity-degree", "3"
    )

    assert result.returncode == 0, result.stderr
    records = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    annotations = next(r for r in records if r["operation"] == "annotations")

    assert annotations["similarity_edges"] > 0
    assert annotations["corroboration_ms"] > 0


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


def test_the_dated_corpus_dates_something():
    """Every reflect figure recorded before the dating pass existed was taken
    over an undated graph, where the soundness check returns before comparing
    anything. A share that silently dated nothing would leave that true while
    reading as though it had been fixed."""
    result = _run("--quick", "--n", "40", "--dated-share", "1.0")

    assert result.returncode == 0, result.stderr
    corpus = next(
        json.loads(line) for line in result.stdout.splitlines()
        if line.strip() and json.loads(line)["operation"] == "corpus"
    )
    assert corpus["dated_facts"] > 0


class TestPlantedDuplicates:
    """The corpus's surviving pairs are an input, and have to stay one.

    Widening the vocabulary was tried first and does not work: survival steps
    from ~1% to 0 with nothing in between, because what survives a pair scorer
    is shared phrasing and a random generator never restates anything. Planting
    is what puts the survivor count back under control, so the properties that
    make it controllable are the ones worth pinning.
    """

    def test_the_pair_count_is_arithmetic_not_an_observation(self, bench):
        assert bench._planted_pairs(1000, 4, 5) == 4 * 10
        assert bench._planted_pairs(1000, 1, 50) == 1225
        assert bench._planted_pairs(1000, 0, 5) == 0
        # A cluster of one is not a cluster, and would otherwise plant a group
        # contributing no pairs while reporting that it had.
        assert bench._planted_pairs(1000, 4, 1) == 0

    def test_a_pool_too_small_for_the_groups_asked_for_says_so(self, bench):
        """The count reported has to be the count planted. Reporting the asked-for
        number would put a survivor total in the record that the corpus cannot
        contain."""
        assert bench._planted_pairs(12, 10, 5) == 2 * 10

    def test_clusters_land_in_the_pool_and_nothing_else_moves(self, bench):
        plain = bench._fact_pool(
            random.Random("seed"), count=200, corpus="diverse", groups=0, size=5
        )
        planted = bench._fact_pool(
            random.Random("seed"), count=200, corpus="diverse", groups=4, size=5
        )
        changed = [i for i, (a, b) in enumerate(zip(plain, planted)) if a != b]

        # Four clusters of five: the first of each keeps its original wording.
        assert len(changed) == 4 * 4

    def test_the_templated_corpus_refuses_the_planting_rather_than_faking_it(
        self, bench
    ):
        """It already survives at ~1% by accident, so clusters planted in it
        would be lost against a floor nobody chose."""
        plain = bench._fact_pool(
            random.Random("seed"), count=200, corpus="templated", groups=0, size=5
        )
        planted = bench._fact_pool(
            random.Random("seed"), count=200, corpus="templated", groups=8, size=5
        )
        assert plain == planted

    def test_the_stream_outlives_the_pool_it_was_sized_for(self, bench):
        """A segmenter returning more segments than the run asked for must not
        end the run — and the tail is unplanted, every cluster being in the head."""
        stream = bench._fact_stream(
            random.Random("seed"), count=4, corpus="diverse", groups=0, size=5
        )
        assert len([next(stream) for _ in range(40)]) == 40


class TestPlantingLeavesTheRestOfTheCorpusAlone:
    """The guard on a comparison, not on the corpus.

    Planting consumes randomness — a sample, and a substitution per restated
    word. On one shared stream every topic, inference and document body after
    the first cluster would differ too, and a reflect timing put beside its
    unplanted twin would be comparing two corpora rather than one corpus with
    and without duplicates. This was measured wrongly once before the streams
    were split.
    """

    @pytest.mark.asyncio
    async def test_topics_are_identical_planted_or_not(self, bench):
        from epimemer.core.types import NodeType
        from epimemer.embeddings.mock import MockEmbeddingProvider
        from epimemer.mcp.config import ServerConfig
        from epimemer.storage.memory import InMemoryStorage

        async def topics(groups: int) -> list[str]:
            storage = InMemoryStorage()
            await storage.connect()
            await bench._seed(
                storage,
                MockEmbeddingProvider(model_id="bench-embed", dimension=384),
                ServerConfig(
                    embedding_provider="mock", segmentation_strategy="paragraph"
                ),
                docs=4, segments=3, publishers=0, corpus="diverse",
                duplicate_groups=groups, duplicate_size=4, facts_per_segment=2,
                rng=random.Random(1), fact_rng=random.Random("facts:1"),
            )
            return sorted(
                node.content
                for node in await storage.query_nodes(node_type=NodeType.TOPIC)
            )

        assert await topics(0) == await topics(2)
