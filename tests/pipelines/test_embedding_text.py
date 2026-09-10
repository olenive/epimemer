"""One definition of what a node is embedded on, and guards that keep it one.

The behavioural tests here are short because the behaviour is: an undescribed
topic embeds on its content, exactly as it did before the field existed. The
structural tests are the substance. A second concatenation written at some
future embed site would be invisible in review and would show up months later
as a topic that fails to match itself, so both halves are asserted mechanically:
every embed site reaches the function, and no other module joins the two fields.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from epimemer.core.types import Fact, Inference, Topic
from epimemer.pipelines.embedding_text import embedding_text
from epimemer.pipelines.metacontexts import TAG_EXTRACTION_METHOD

PACKAGE = Path(__file__).resolve().parents[2] / "epimemer"
THE_ONE_PLACE = PACKAGE / "pipelines" / "embedding_text.py"

# The embed calls that hand the provider something other than a node, with the
# argument as it is written. Each embeds text no node owns, so there are no two
# fields to join and nothing for `embedding_text` to do.
NON_NODE_EMBED_ARGUMENTS = {
    # `vector_search`: the query a caller typed.
    "[query_text]",
    # `relation_consolidation`: relation label strings, which are not nodes.
    "[label for (label, _) in keys]",
    # `semantic_similarity`: raw passage text, embedded before any node exists.
    "texts",
    # The split sweep in `tools.py`: the facts and inferences gathered under a
    # topic, scored for internal variance. Claims carry no description.
    "material",
}


def _python_sources() -> list[Path]:
    return sorted(PACKAGE.rglob("*.py"))


def _embed_calls() -> list[tuple[Path, ast.Call, str]]:
    """Every `<provider>.embed(...)` call in the package, with its source."""
    found: list[tuple[Path, ast.Call, str]] = []
    for path in _python_sources():
        source = path.read_text()
        for node in ast.walk(ast.parse(source)):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "embed"
                and node.args
            ):
                found.append((path, node, source))
    return found


class TestAnUndescribedTopicEmbedsAsItAlwaysHas:
    """A topic nobody has described embeds exactly as it did before the field."""

    def test_a_topic_with_no_description_returns_its_content(self):
        topic = Topic(content="retrieval-provenance", source_id=None)

        assert embedding_text(topic) == topic.content

    @pytest.mark.parametrize(
        "content",
        [
            "reflect",
            "a paragraph-length statement of a theme, with. punctuation in it",
            "issue-16",
        ],
    )
    def test_the_text_is_byte_identical_to_the_content(self, content):
        assert embedding_text(Topic(content=content, source_id=None)) == content

    def test_a_fact_embeds_on_its_content(self):
        fact = Fact(content="Labour won the election", source_id="s1")

        assert embedding_text(fact) == fact.content

    def test_an_inference_embeds_on_its_content(self):
        inference = Inference(content="The result was a realignment", source_id="s1")

        assert embedding_text(inference) == inference.content


class TestADescribedTopicNodeFromATagJoinsTheTwo:
    """The definition, stated once here so a change to it has to be deliberate."""

    def test_the_description_follows_the_name_as_a_second_sentence(self):
        topic = Topic(
            content="reflect",
            description="The consolidation sweep over the graph.",
            source_id=None,
            extraction_method=TAG_EXTRACTION_METHOD,
        )

        assert embedding_text(topic) == "reflect. The consolidation sweep over the graph."

    def test_an_undescribed_tag_is_unmoved(self):
        topic = Topic(content="reflect", source_id=None, extraction_method=TAG_EXTRACTION_METHOD)

        assert embedding_text(topic) == "reflect"


class TestADescribedStatementTopicEmbedsOnItsContentAlone:
    """A statement topic's content is already the prose a description restates.

    Joining one there would move a stored topic away from the same topic
    arriving undescribed at ingest, which is exactly where topic similarity is
    meant to catch a duplicate: the asymmetric hazard, paid for nothing, since
    the content already says what the topic covers.
    """

    def test_a_described_statement_topic_returns_its_content(self):
        topic = Topic(
            content="How the graph consolidates itself over repeated passes",
            description="Covers reflect, merge and archival.",
            source_id="seg-1",
        )

        assert embedding_text(topic) == topic.content

    def test_a_described_parent_topic_returns_its_content(self):
        """Nothing synthesised by reflect joins either: only tags do."""
        topic = Topic(
            content="Consolidation",
            description="Covers reflect, merge and archival.",
            source_id="seg-1",
            extraction_method="agent:parent_synthesis",
        )

        assert embedding_text(topic) == topic.content


class TestEveryEmbedSiteReachesTheFunction:
    """A node is embedded through `embedding_text` or the site is named here.

    The failure this catches is a new embed site written the obvious way,
    passing `node.content`, which would work perfectly until the first topic
    gained a description and then silently embed half of it.
    """

    def test_no_embed_call_passes_a_node_without_the_function(self):
        offenders = []
        for path, call, source in _embed_calls():
            argument = ast.get_source_segment(source, call.args[0]) or ""
            normalised = " ".join(argument.split())
            if "embedding_text" in normalised or normalised in NON_NODE_EMBED_ARGUMENTS:
                continue
            offenders.append(f"{path.relative_to(PACKAGE.parent)}:{call.lineno}: {normalised}")

        assert not offenders, (
            "these embed calls neither go through `embedding_text` nor are named "
            "as embedding something other than a node. Route a node through "
            "`embedding_text`; add a non-node site to `NON_NODE_EMBED_ARGUMENTS` "
            "with the reason: " + "; ".join(offenders)
        )

    def test_the_named_non_node_sites_all_still_exist(self):
        """An allowlist that outlives its call site stops guarding anything."""
        seen = set()
        for _, call, source in _embed_calls():
            argument = ast.get_source_segment(source, call.args[0]) or ""
            seen.add(" ".join(argument.split()))

        assert NON_NODE_EMBED_ARGUMENTS <= seen, (
            "these embed arguments are allowlisted but no longer written "
            f"anywhere: {sorted(NON_NODE_EMBED_ARGUMENTS - seen)}"
        )


class TestOnlyOneModuleJoinsTheTwoFields:
    """`content` and `description` are concatenated in one place and no other.

    Scans for the two shapes the join can take, an f-string interpolating a
    `.description` and a `+` with one on either side, and asserts they occur
    only in the module that owns the definition.
    """

    def _files_joining_a_description(self) -> set[Path]:
        found: set[Path] = set()
        for path in _python_sources():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.JoinedStr):
                    parts = [
                        part.value for part in node.values if isinstance(part, ast.FormattedValue)
                    ]
                elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
                    parts = [node.left, node.right]
                else:
                    continue
                if any(
                    isinstance(part, ast.Attribute) and part.attr == "description" for part in parts
                ):
                    found.add(path)
        return found

    def test_the_join_lives_only_in_the_embedding_text_module(self):
        joining = self._files_joining_a_description()

        assert joining == {THE_ONE_PLACE}, (
            "a description is concatenated outside "
            f"{THE_ONE_PLACE.relative_to(PACKAGE.parent)}, which is a second "
            "definition of what a node is embedded on: "
            f"{sorted(str(p.relative_to(PACKAGE.parent)) for p in joining)}"
        )
