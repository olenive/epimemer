"""The doubt measurement reads a graph, counts what it finds, and writes nothing.

`scripts/segmentation_doubts.py` runs `doubt_cut` over every document in a
named graph so the thresholds can be calibrated on real text before anything
is built on them. It is pointed at the user's real graphs, so the property that
matters most is that it cannot change them: it reaches the rows with `SELECT`
alone and never opens a storage backend, whose `connect()` defines tables and
runs migrations.

The HTTP transport is replaced here by the embedded store's own connection,
which answers the same statements with the same rows.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp import tools
from epimemer.mcp.config import ServerConfig
from epimemer.storage.surrealdb_adapter import SurrealDBStorage

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "segmentation_doubts.py"


def _module():
    """Load the script as a module: it is not on the import path."""
    sys.path.insert(0, str(_SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("segmentation_doubts", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script():
    return _module()


PROSE = (
    "The committee met on Tuesday to review the budget. Several members raised "
    "concerns about the cost of the new building.\n\n"
    "After a long discussion, the proposal was sent back to the finance office "
    "for revision. A vote is expected next month."
)
TRANSCRIPT = "\n".join(
    f"Speaker {i % 2 + 1}: this is line {i} of a long conversation about the harbour works."
    for i in range(20)
)
LIST = "\n\n".join(
    [
        "Apples are sold at the market.",
        "Pears are sold at the market too.",
        "Plums are sold there on Fridays.",
        "The market opens at eight in the morning on weekdays.",
    ]
)
SPLIT = (
    "After the second visit to the site, which took most of the afternoon, the "
    "inspector found the wiring sound, although the report\n\n"
    "noted that the fuse box would need replacing within the year."
)
# Documents ingested, by source name, and the one doubt each raises.
CORPUS = {
    "minutes-1": (PROSE, None),
    "minutes-2": (PROSE.replace("Tuesday", "Thursday"), None),
    "harbour-call": (TRANSCRIPT, "single_passage"),
    "harbour-call-2": (TRANSCRIPT.replace("harbour", "canal"), "single_passage"),
    "shopping": (LIST, "fragments"),
    "inspection": (SPLIT, "mid_sentence"),
}


@pytest.fixture
async def graph():
    """A mem:// SurrealDB graph with the corpus ingested through `segment`."""
    store = SurrealDBStorage(url="mem://", database="field-notes")
    await store.connect()
    embedder = MockEmbeddingProvider(model_id="mock-embed", dimension=8)
    config = ServerConfig(storage_backend="memory", embedding_provider="mock")
    for source, (content, _) in CORPUS.items():
        await tools.segment_text(content, store, embedder, config, source=source)
    yield store
    await store.close()


def recording(store: SurrealDBStorage, statements: list[str]):
    """The embedded connection standing in for HTTP, noting every statement."""

    async def sql(statement: str):
        statements.append(statement)
        return await store.db.query(statement)

    return sql


class TestTheReport:
    async def test_counts_rates_and_examples(self, script, graph):
        sql = recording(graph, [])

        report = await script.measure(sql, sql, "field-notes")

        lines = report.splitlines()
        assert "documents: 6" in lines
        assert "single_passage: 2 (33.3%)" in lines
        assert "fragments: 1 (16.7%)" in lines
        assert "outsized: 0 (0.0%)" in lines
        assert "mid_sentence: 1 (16.7%)" in lines
        assert any(line.startswith("  harbour-call (1 passage): passage 0") for line in lines)
        assert any(line.startswith("  shopping (4 passages): 3 of 4") for line in lines)
        assert any(line.startswith("  inspection (2 passages): passage 0") for line in lines)

    async def test_examples_stop_at_three(self, script):
        documents = [
            (script.RawDocument(content=TRANSCRIPT, source=f"call-{i}"), None) for i in range(5)
        ]
        graph = [
            (
                doc,
                [
                    script.Segment(
                        source_id=doc.id,
                        text=doc.content,
                        span_start=0,
                        span_end=len(doc.content),
                    )
                ],
            )
            for doc, _ in documents
        ]

        report = script.tally(graph)

        assert report.raised["single_passage"] == 5
        assert len(report.examples["single_passage"]) == 3

    async def test_an_empty_graph_reports_zero_rather_than_dividing_by_it(self, script):
        rendered = script.render(script.tally([]), "empty")

        assert "documents: 0" in rendered.splitlines()
        assert "fragments: 0 (0.0%)" in rendered.splitlines()


class TestItReadsWhatTheBackendReads:
    async def test_documents_and_passages_match_the_storage_backend(self, script, graph):
        sql = recording(graph, [])

        read = await script.read_graph(sql, sql, "field-notes")

        assert len(read) == len(CORPUS)
        for doc, segments in read:
            stored = await graph.get_segments_for_document(doc.id)
            assert [s.id for s in segments] == [s.id for s in stored]
            assert doc.content == (await graph.get_document(doc.id)).content


class TestItWritesNothing:
    async def test_every_statement_is_a_read(self, script, graph):
        statements: list[str] = []
        sql = recording(graph, statements)

        await script.measure(script.read_only(sql), script.read_only(sql), "field-notes")

        assert statements
        assert all(s.lstrip().upper().startswith(("SELECT ", "INFO ")) for s in statements)

    @pytest.mark.parametrize(
        "statement",
        [
            "UPSERT document CONTENT {} WHERE uid = 'x'",
            "DEFINE TABLE IF NOT EXISTS document SCHEMALESS",
            "SELECT * FROM document; DELETE document",
            "  delete document",
            "INFO FOR NS; REMOVE DATABASE x",
        ],
    )
    async def test_the_guard_refuses_anything_else(self, script, graph, statement):
        statements: list[str] = []
        guarded = script.read_only(recording(graph, statements))

        with pytest.raises(script.WriteRefused):
            await guarded(statement)

        assert statements == [], "a refused statement must never reach the server"

    async def test_a_graph_that_does_not_exist_is_refused_not_created(self, script, graph):
        statements: list[str] = []
        sql = recording(graph, statements)

        with pytest.raises(script.GraphNotFound, match="field-noets"):
            await script.measure(sql, sql, "field-noets")

        assert all(not s.upper().startswith("SELECT") for s in statements)


class TestConnectionSettings:
    @pytest.mark.parametrize(
        ("given", "http"),
        [
            ("ws://localhost:8000/rpc", "http://localhost:8000"),
            ("wss://db.example.org/rpc", "https://db.example.org"),
            ("http://127.0.0.1:8123", "http://127.0.0.1:8123"),
            ("http://127.0.0.1:8123/", "http://127.0.0.1:8123"),
        ],
    )
    def test_the_server_url_becomes_the_http_endpoint(self, script, given, http):
        assert script.http_base(given) == http

    def test_an_embedded_url_is_refused(self, script):
        with pytest.raises(ValueError, match="mem://"):
            script.http_base("mem://")

    def test_arguments_fall_back_to_the_server_environment(self, script):
        env = {
            "EPIMEMER_SURREALDB_URL": "ws://example:9000/rpc",
            "EPIMEMER_SURREALDB_NAMESPACE": "ns",
            "EPIMEMER_SURREALDB_USER": "reader",
            "EPIMEMER_SURREALDB_PASS": "secret",
            "EPIMEMER_GRAPH": "notes",
        }

        args = script.parse_args([], env)

        assert (args.url, args.namespace, args.user, args.password, args.graph) == (
            "ws://example:9000/rpc",
            "ns",
            "reader",
            "secret",
            "notes",
        )

    def test_arguments_win_over_the_environment(self, script):
        args = script.parse_args(
            ["--url", "http://127.0.0.1:8123", "--graph", "other"],
            {"EPIMEMER_SURREALDB_URL": "ws://example:9000/rpc", "EPIMEMER_GRAPH": "notes"},
        )

        assert (args.url, args.graph) == ("http://127.0.0.1:8123", "other")

    def test_graph_falls_back_to_the_database_setting(self, script):
        args = script.parse_args(
            ["--url", "http://h"], {"EPIMEMER_SURREALDB_DATABASE": "default-graph"}
        )

        assert args.graph == "default-graph"

    def test_no_url_and_no_graph_is_an_error_rather_than_a_default(self, script):
        with pytest.raises(SystemExit):
            script.parse_args([], {})
