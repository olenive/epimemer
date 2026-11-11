"""The guidance reaches the agent through the server, not through a paste.

Before this, `epimemer_prompts/DEFAULT.md` reached an agent only if someone
copied it into that agent's instructions, and the server's own `instructions`
string was five sentences. Now `RULES.md` is the instructions string, in every
context on connect, and `DEFAULT.md` is the `guide` prompt for a person and
the `guide` tool, read a section at a time, for an agent. The files stay the
single source, so each guard here compares the served text with the file.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp import guidance
from epimemer.mcp.config import ServerConfig
from epimemer.mcp.retrieval_records import new_record_log
from epimemer.mcp.server import mcp as epimemer_mcp
from epimemer.mcp.server import new_connection_state
from epimemer.storage.memory import InMemoryStorage

PROMPTS = Path(__file__).resolve().parents[2] / "epimemer_prompts"

# The instructions string sits in every context window of every client, so it
# has a budget. RULES.md was 2.2 KB when this was set; the full guide is many
# times that, and the point of the split is that the two never converge.
RULES_BUDGET_BYTES = 4_000


def test_the_server_instructions_are_the_rules_file():
    assert epimemer_mcp.instructions == (PROMPTS / "RULES.md").read_text(encoding="utf-8")


def test_the_rules_stay_short_enough_to_sit_in_every_context():
    assert len(epimemer_mcp.instructions.encode()) <= RULES_BUDGET_BYTES


async def test_the_rules_name_the_tool_that_holds_the_rest():
    """A reader of the rules has to be told where the full guide is, and the
    name has to be a tool the server registers: an agent can call a tool and
    has no way to fetch a prompt."""
    assert "the tool `guide`" in epimemer_mcp.instructions
    assert "guide" in {tool.name for tool in await epimemer_mcp.list_tools()}


async def test_the_guide_prompt_is_the_default_file(server):
    names = {prompt.name for prompt in await server.list_prompts()}
    assert "guide" in names
    rendered = await server.render_prompt("guide")
    assert [message.role for message in rendered.messages] == ["user"]
    assert rendered.messages[0].content.text == (PROMPTS / "DEFAULT.md").read_text(encoding="utf-8")


async def test_the_rules_and_the_guide_agree_on_which_tools_take_no_graph():
    """The one fact both files state outright. The registry is the oracle:
    a tool without `expected_graph` in its schema."""
    names = {
        tool.name
        for tool in await epimemer_mcp.list_tools()
        if "expected_graph" not in tool.parameters.get("properties", {})
    }
    assert names, "every tool takes expected_graph, so the texts are wrong to name any"
    for text in (epimemer_mcp.instructions, (PROMPTS / "DEFAULT.md").read_text()):
        for name in names:
            assert f"`{name}`" in text, f"{name} takes no expected_graph and the text omits it"


# --- The guide, a section at a time ----------------------------------------

GUIDE = (PROMPTS / "DEFAULT.md").read_text(encoding="utf-8")

# A section is what an agent reads in place of the whole guide, so one that
# grows to a large share of the guide has stopped doing its job. The largest
# was 3.7 KB when this was set, and the whole guide 26 KB. What a tool's
# description already says stays out of the guide, which is what keeps both
# numbers down.
SECTION_BUDGET_CHARS = 6_000
GUIDE_BUDGET_CHARS = 32_000


def test_the_cut_loses_nothing():
    """The overview and the sections, joined in order, are the file."""
    cut = guidance.cut_guide(GUIDE)

    assert cut.overview + "".join(section.text for section in cut.sections) == GUIDE


def test_every_section_has_a_key_an_agent_can_ask_for():
    cut = guidance.cut_guide(GUIDE)
    keys = [section.key for section in cut.sections]

    assert len(keys) == len(set(keys)) > 1
    for section in cut.sections:
        assert section.text.startswith(f"### {section.title} {{#{section.key}}}\n")


def test_no_section_outgrows_its_budget():
    for section in guidance.cut_guide(GUIDE).sections:
        assert len(section.text) <= SECTION_BUDGET_CHARS, section.key
    assert len(GUIDE) <= GUIDE_BUDGET_CHARS


@pytest.mark.parametrize(
    "text",
    [
        "### A section with no key\nbody\n",
        "### One {#same}\nbody\n### Two {#same}\nbody\n",
        "### Everything {#all}\nbody\n",
    ],
    ids=["no key", "a key used twice", "the key that means the whole guide"],
)
def test_a_guide_with_a_section_nobody_could_ask_for_is_refused(text):
    with pytest.raises(ValueError):
        guidance.cut_guide(text)


def test_no_section_returns_the_overview_and_the_list():
    cut = guidance.cut_guide(GUIDE)

    response = guidance.guide_response(None, GUIDE)

    assert response["overview"] == cut.overview.strip()
    assert [entry["section"] for entry in response["sections"]] == [s.key for s in cut.sections]
    assert all(entry["title"] and entry["chars"] > 0 for entry in response["sections"])


def test_a_key_returns_that_section_and_only_it():
    cut = guidance.cut_guide(GUIDE)
    wanted = cut.sections[1]

    response = guidance.guide_response(wanted.key, GUIDE)

    assert response == {"section": wanted.key, "text": wanted.text.strip()}


def test_all_returns_the_whole_guide():
    assert guidance.guide_response("all", GUIDE) == {"section": "all", "text": GUIDE}


def test_an_unknown_key_is_refused_with_the_list():
    response = guidance.guide_response("no-such-section", GUIDE)

    assert "no-such-section" in response["refused"]
    assert response["sections"] == guidance.guide_response(None, GUIDE)["sections"]
    assert "text" not in response


@asynccontextmanager
async def _lifespan(server):
    yield {
        "storage": InMemoryStorage(),
        "embedding_provider": MockEmbeddingProvider(model_id="mock-embed", dimension=8),
        "config": ServerConfig(storage_backend="memory", embedding_provider="mock"),
        "event_bus": None,
        "viz_session": None,
        "viz_hub_url": None,
        "retrievals": new_record_log(),
        "connection_state": new_connection_state(),
        "version": "test",
        "read_installed_version": lambda: None,
        "guide": GUIDE,
    }


@pytest.fixture
async def server():
    original = epimemer_mcp._lifespan
    epimemer_mcp._lifespan = _lifespan
    async with _lifespan(epimemer_mcp) as ctx:
        epimemer_mcp._lifespan_result = ctx
        yield epimemer_mcp
        epimemer_mcp._lifespan_result = None
    epimemer_mcp._lifespan = original


async def _guide(server, **kwargs) -> dict:
    result = await server.call_tool("guide", kwargs)
    return json.loads(result.content[0].text)["result"]


async def test_the_guide_tool_serves_the_default_file(server):
    """Through the server, with no graph named and no judge claimed: reading
    the guide is how an agent learns to do both."""
    contents = await _guide(server)
    keys = [entry["section"] for entry in contents["sections"]]

    assert keys == [section.key for section in guidance.cut_guide(GUIDE).sections]
    for key in keys:
        section = await _guide(server, section=key)
        assert section["text"] in GUIDE


async def test_the_guide_tool_refuses_a_section_it_does_not_have(server):
    assert "refused" in await _guide(server, section="no-such-section")


async def test_the_guide_served_is_the_text_read_at_startup(server):
    """The file on disk can change underneath a running server when the package
    is upgraded; the server goes on serving the guide its code was written for."""
    started_with = "The overview.\n\n### The only section {#only}\nIts text.\n"
    server._lifespan_result["guide"] = started_with

    contents = await _guide(server)
    rendered = await server.render_prompt("guide")

    assert [entry["section"] for entry in contents["sections"]] == ["only"]
    assert (await _guide(server, section="only"))["text"].endswith("Its text.")
    assert rendered.messages[0].content.text == started_with


async def test_the_server_reads_the_guide_file_as_it_starts(started_deps):
    assert started_deps["guide"] == GUIDE
