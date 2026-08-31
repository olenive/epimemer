"""A batch applies or it never existed.

`apply_reflection` applies in ten steps with no transaction across them, and it
cannot get one: the order is the anchoring rule's anchoring rule, where judgments are recorded
before the steps that retire the nodes they name. So the guarantee has to come
from the other end — nothing is attempted until the whole batch is known to be
applicable.

The measurement that produced the entry, reproduced by the first test below: a
valid similarity verdict beside a `relation_verdicts` entry with no `pair` left
**one similarity row committed** and returned `{"error": "'pair'"}`. The agent
cannot tell from that that half of its batch landed, and the entry that landed
suppresses its pair *permanently* — so fixing the malformed entry and resending,
which is the only sensible next move, is refused as a repeat verdict.

Two properties are asserted against each other throughout: **a malformed batch
writes nothing**, and **a refusable judgment still costs only its own entry**.
The second is what stops this fix from becoming the first defect's mirror image,
where one already-judged pair throws away nine good verdicts.
"""

import ast
from datetime import UTC, datetime
from pathlib import Path

import pytest

from epimemer.core.types import (
    BASE_METACONTEXT_ID,
    ClaimKind,
    DecisionKind,
    EdgeType,
    EmbeddingRecord,
    Fact,
    NodeEdge,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp import tools
from epimemer.pipelines.reflection.batch_validation import (
    DELIBERATELY_PER_ENTRY,
    REQUIRED_KEYS,
    malformed_entries,
)


@pytest.fixture
def embedding_provider() -> MockEmbeddingProvider:
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


_TWIN = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


async def _twin_facts(storage, embedding_provider) -> tuple[Fact, Fact]:
    """Two facts a reflect pass would nominate, in the one frame the graph has."""
    facts = [
        Fact(
            content="Ada Lovelace wrote the first algorithm.",
            source_id="doc-a",
            claim_kind=ClaimKind.EVENT,
        ),
        Fact(
            content="The first algorithm was written by Ada Lovelace.",
            source_id="doc-b",
            claim_kind=ClaimKind.EVENT,
        ),
    ]
    for fact in facts:
        await storage.store_node(fact)
        await storage.store_embedding(
            EmbeddingRecord(
                item_id=fact.id,
                model_id=embedding_provider.model_id,
                vector=_TWIN,
            )
        )
        await storage.store_edge(
            NodeEdge(
                src_id=fact.id,
                dst_id=BASE_METACONTEXT_ID,
                type=EdgeType.HAS_METACONTEXT,
            )
        )
    return facts[0], facts[1]


async def _similarity_rows(storage) -> int:
    return len(await storage.query_decisions(kinds=[DecisionKind.SIMILARITY]))


class TestNothingIsWrittenBeforeTheBatchIsChecked:
    async def test_the_measurement_from_the_entry(self, storage, embedding_provider):
        """A valid verdict beside a malformed one: the valid one used to land."""
        first, second = await _twin_facts(storage, embedding_provider)

        with pytest.raises(ValueError) as caught:
            await tools.apply_reflection(
                storage,
                embedding_provider,
                similarities=[
                    {
                        "pair": [first.id, second.id],
                        "verdict": "distinct",
                        "because": "different claims about the same person",
                    }
                ],
                relation_verdicts=[{"verdict": "distinct", "because": "x"}],
            )

        assert await _similarity_rows(storage) == 0
        assessed = await storage.get_edges_for(
            [first.id], direction="from", edge_type=EdgeType.ASSESSED
        )
        assert assessed[first.id] == [], "the suppression landed anyway"
        message = str(caught.value)
        assert "wrote nothing" in message
        assert "relation_verdicts[0]: 'pair' is required" in message

    async def test_the_permanent_suppression_is_named(self, storage, embedding_provider):
        """The refusal has to say why a partial write would have been costly."""
        with pytest.raises(ValueError) as caught:
            await tools.apply_reflection(
                storage,
                embedding_provider,
                similarities=[{"verdict": "distinct", "because": "x"}],
            )
        message = str(caught.value)
        assert "permanent" in message
        assert "repeat verdict" in message

    async def test_a_valid_batch_still_applies(self, storage, embedding_provider):
        first, second = await _twin_facts(storage, embedding_provider)
        result, _ = await tools.apply_reflection(
            storage,
            embedding_provider,
            similarities=[
                {
                    "pair": [first.id, second.id],
                    "verdict": "distinct",
                    "because": "different claims",
                }
            ],
        )
        assert result["similarities_recorded"] == 1
        assert await _similarity_rows(storage) == 1

    async def test_a_later_step_cannot_abort_an_earlier_one(self, storage, embedding_provider):
        """The malformed entry is in step 10; the write it protects is step 1."""
        first, second = await _twin_facts(storage, embedding_provider)
        with pytest.raises(ValueError):
            await tools.apply_reflection(
                storage,
                embedding_provider,
                similarities=[
                    {
                        "pair": [first.id, second.id],
                        "verdict": "distinct",
                        "because": "different claims",
                    }
                ],
                boundaries=[
                    {"node_id": first.id, "source_id": "doc-a", "endpoint": "end"}
                ],  # no `at`
            )
        assert await _similarity_rows(storage) == 0

    async def test_every_problem_is_listed_at_once(self, storage, embedding_provider):
        """Fix one, meet the next, resend: the treadmill this file is about."""
        with pytest.raises(ValueError) as caught:
            await tools.apply_reflection(
                storage,
                embedding_provider,
                splits=[{"topic_id": "t1"}],  # no subtopics
                enrichments=[{"new_content": "better"}],  # no topic_id
                judgments=[{"node_id": "n1", "direction": "up"}],  # no reason
            )
        message = str(caught.value)
        assert "splits[0]: 'subtopics' is required" in message
        assert "enrichments[0]: 'topic_id' is required" in message
        assert "judgments[0]: 'reason' is required" in message
        assert "3 entries are malformed" in message


class TestRefusableJudgmentsStillCostOneEntry:
    """The other half: this fix must not make a per-entry refusal batch-wide."""

    async def test_a_missing_kind_refuses_only_its_own_entry(self, storage, embedding_provider):
        """The label-verdict decision, which batch validation must not quietly promote."""
        first, second = await _twin_facts(storage, embedding_provider)
        result, _ = await tools.apply_reflection(
            storage,
            embedding_provider,
            similarities=[
                {
                    "pair": [first.id, second.id],
                    "verdict": "distinct",
                    "because": "different claims",
                }
            ],
            relation_verdicts=[
                {
                    "pair": ["works_for", "employs"],
                    "verdict": "distinct",
                    "because": "different relations",
                }
            ],  # no `kind` — refused, but the batch still applies
        )
        assert result["similarities_recorded"] == 1
        assert len(result["relation_verdicts_refused"]) == 1
        assert "`kind` is required" in result["relation_verdicts_refused"][0]["reason"]

    async def test_an_unknown_node_id_is_still_skipped(self, storage, embedding_provider):
        """Whether an entry *should* apply is the step's question, not this one's."""
        result, _ = await tools.apply_reflection(
            storage,
            embedding_provider,
            enrichments=[{"topic_id": "nope", "new_content": "x"}],
            judgments=[{"node_id": "nope", "direction": "up", "reason": "x"}],
        )
        assert result["topics_enriched"] == 0
        assert result["judgments_applied"] == 0


class TestWhatCountsAsMalformed:
    """Unit-level, because the shape of the answer is the design."""

    def test_a_well_formed_batch_has_no_problems(self):
        assert (
            malformed_entries(
                {
                    "similarities": [{"pair": ["a", "b"], "verdict": "distinct"}],
                    "parents": [{"children_ids": ["a", "b"], "content": "c"}],
                    "archivals": ["a"],
                }
            )
            == []
        )

    def test_an_empty_batch_has_no_problems(self):
        assert malformed_entries({}) == []
        assert malformed_entries({field: None for field in REQUIRED_KEYS}) == []

    @pytest.mark.parametrize("field,keys", sorted(REQUIRED_KEYS.items()))
    def test_every_required_key_is_checked(self, field, keys):
        """Each key, dropped on its own, is reported against its own field."""
        complete = _sample_entry(field)
        for key in keys:
            partial = {k: v for k, v in complete.items() if k != key}
            found = malformed_entries({field: [partial]})
            assert [(item.field, item.index) for item in found] == [(field, 0)]
            assert found[0].problem == f"{key!r} is required"

    def test_an_entry_that_is_not_an_object(self):
        found = malformed_entries({"splits": ["topic-1"]})
        assert found[0].problem.startswith("entry is str, not an object with")

    def test_a_string_where_a_list_belongs(self):
        """Iterating it yields characters, so the entry would apply to nobody."""
        found = malformed_entries({"merges": [{"source_ids": "abc", "content": "c"}]})
        assert found[0].problem == "'source_ids' must be a list, not str"

    def test_a_pair_that_does_not_name_two(self):
        found = malformed_entries({"similarities": [{"pair": ["only-one"], "verdict": "distinct"}]})
        assert found[0].problem == "'pair' names 1 thing(s); a verdict is about two"

    def test_an_unknown_supersession_reason(self):
        found = malformed_entries(
            {"supersessions": [{"old_id": "a", "by_id": "b", "because": "i_felt_like_it"}]}
        )
        assert "closed set" in found[0].problem
        assert "it_was_wrong" in found[0].problem

    def test_a_boundary_date_that_will_not_parse(self):
        found = malformed_entries(
            {
                "boundaries": [
                    {
                        "node_id": "n",
                        "source_id": "s",
                        "endpoint": "end",
                        "at": "last tuesday",
                    }
                ]
            }
        )
        assert "neither a datetime nor an ISO-8601 string" in found[0].problem

    def test_a_boundary_date_that_will(self):
        for at in ("2026-08-28T00:00:00+00:00", datetime.now(UTC)):
            assert (
                malformed_entries(
                    {
                        "boundaries": [
                            {
                                "node_id": "n",
                                "source_id": "s",
                                "endpoint": "end",
                                "at": at,
                            }
                        ]
                    }
                )
                == []
            )

    def test_an_archival_that_is_not_an_id(self):
        found = malformed_entries({"archivals": [{"node_id": "a"}]})
        assert found[0].problem == "entry is dict, not a node id"

    def test_the_index_identifies_the_entry(self):
        """Entries carry no ids, so the position is the whole handle."""
        found = malformed_entries(
            {
                "splits": [
                    {"topic_id": "a", "subtopics": ["x"]},
                    {"topic_id": "b"},
                ]
            }
        )
        assert [(item.field, item.index) for item in found] == [("splits", 1)]


def _sample_entry(field: str) -> dict:
    """A well-formed entry per field, so the parametrised test can drop one key."""
    samples = {
        "similarities": {"pair": ["a", "b"], "verdict": "distinct"},
        "relation_verdicts": {"pair": ["a", "b"]},
        "parents": {"children_ids": ["a", "b"], "content": "c"},
        "splits": {"topic_id": "t", "subtopics": ["a"]},
        "enrichments": {"topic_id": "t", "new_content": "c"},
        "merges": {"source_ids": ["a", "b"], "content": "c"},
        "supersessions": {"old_id": "a", "by_id": "b", "because": "it_was_wrong"},
        "judgments": {"node_id": "n", "direction": "up", "reason": "r"},
        "boundaries": {
            "node_id": "n",
            "source_id": "s",
            "endpoint": "end",
            "at": "2026-08-28T00:00:00+00:00",
        },
    }
    assert set(samples) == set(REQUIRED_KEYS), "sample entries drifted from the table"
    return samples[field]


def _required_subscripts() -> dict[str, set[str]]:
    """Every `spec["key"]` `apply_reflection` reads, per list it loops over.

    Read from the source rather than from a list somebody maintains, for the
    reason `test_every_kind_has_a_writer` gives: a guard whose reach is an
    accident of where the code sat is one that fails open. A required key added
    to a loop and not to `REQUIRED_KEYS` is exactly the defect batch validation was, arriving
    back through a door nobody watched.
    """
    tree = ast.parse(Path("epimemer/mcp/tools.py").read_text())
    fn = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "apply_reflection"
    )
    found: dict[str, set[str]] = {}
    for loop in (n for n in ast.walk(fn) if isinstance(n, ast.For)):
        # The `for spec in (some_list or [])` shape every step uses.
        if not (
            isinstance(loop.iter, ast.BoolOp)
            and isinstance(loop.iter.op, ast.Or)
            and isinstance(loop.iter.values[0], ast.Name)
            and isinstance(loop.target, ast.Name)
        ):
            continue
        field = loop.iter.values[0].id
        target = loop.target.id
        keys = {
            node.slice.value
            for stmt in loop.body
            for node in ast.walk(stmt)
            if isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id == target
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        }
        found.setdefault(field, set()).update(keys)
    return found


class TestTheGuardDoesNotDriftFromTheCode:
    def test_the_scan_finds_the_loops(self):
        """A scan that matched nothing would pass every assertion below."""
        found = _required_subscripts()
        assert set(found) >= set(REQUIRED_KEYS), (
            f"the AST scan lost sight of {set(REQUIRED_KEYS) - set(found)}"
        )

    @pytest.mark.parametrize("field", sorted(REQUIRED_KEYS))
    def test_every_key_the_loop_reads_is_declared(self, field):
        read = _required_subscripts()[field]
        declared = set(REQUIRED_KEYS[field]) | set(DELIBERATELY_PER_ENTRY.get(field, ()))
        assert read <= declared, (
            f"apply_reflection reads {sorted(read - declared)} from a "
            f"{field} entry and nothing checks for it before step 1 writes. "
            f"Add it to REQUIRED_KEYS, or to DELIBERATELY_PER_ENTRY with the "
            f"reason it is refused per entry instead."
        )

    @pytest.mark.parametrize("field", sorted(REQUIRED_KEYS))
    def test_nothing_is_declared_that_the_loop_never_reads(self, field):
        """The other direction: a key checked for and no longer needed."""
        read = _required_subscripts()[field]
        assert set(REQUIRED_KEYS[field]) <= read, (
            f"REQUIRED_KEYS demands {sorted(set(REQUIRED_KEYS[field]) - read)} "
            f"on a {field} entry, which apply_reflection no longer reads"
        )
