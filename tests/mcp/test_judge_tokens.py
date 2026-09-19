"""One connection, several agents, and a write credited to the right one.

Claude Code hands a subagent the same MCP connection as the agent that spawned
it, so both claim against one session. The session binding holds a single
judge, which means the second claim overwrote the first and every later write
from either agent was stamped with whichever judge claimed last: a graph that
says a subagent judged what its parent judged, and a review by agent that
answers for the wrong model.

The token is the fix. `claim_agent` mints one per claim and hands it back, and
a write that carries it is credited to that claim's judge whatever the session
binding has since become. A write that carries none keeps today's behaviour,
the most recent claim, so nothing that worked before changes.

**A token this session never issued refuses the write.** Falling back to the
session judge is precisely the defect, so it is the one thing the resolution
rule may not do.
"""

import json
from types import SimpleNamespace

import pytest

from epimemer.core.types import (
    BASE_METACONTEXT_ID,
    EdgeType,
    EmbeddingRecord,
    Fact,
    NodeEdge,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp.config import ServerConfig
from epimemer.mcp.retrieval_records import new_record_log
from epimemer.mcp.server import _bound_judge, memory_claim_agent, memory_update


@pytest.fixture
def embedder():
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


@pytest.fixture
def config():
    return ServerConfig(storage_backend="memory", embedding_provider="mock")


def _deps(storage, embedder, config) -> dict:
    """What the boundary reads off the lifespan, and nothing else."""
    return {
        "storage": storage,
        "embedding_provider": embedder,
        "config": config,
        "event_bus": None,
        "retrievals": new_record_log(),
        # Every judge prompt opens with it, and these claims go through the
        # prompts even though this client answers none of them.
        "version": "9.9.9",
    }


def _ctx(deps: dict, *, session: bool = True):
    """A stand-in context: session state as a dict, and no channel to the user.

    Two closures over one dict rather than a class, and `state` is left visible
    so a test can read what the boundary stored. `session=False` is the
    transport that has no session at all, where every `get_state` raises and the
    boundary keeps its state on the lifespan instead.

    `elicit` raises, which is the elicitation-less client: an id the user
    approved out of band still binds, so a claim needs no answer here.
    """
    state: dict = {}

    async def get_state(key):
        if not session:
            raise RuntimeError("no session exists")
        return state.get(key)

    async def set_state(key, value):
        if not session:
            raise RuntimeError("no session exists")
        state[key] = value

    async def elicit(message, response_type=None):
        raise RuntimeError("this client cannot put a question to the user")

    return SimpleNamespace(
        lifespan_context=deps,
        get_state=get_state,
        set_state=set_state,
        elicit=elicit,
        state=state,
    )


async def _claim(ctx, agent_id: str, description: str = "an agent") -> dict:
    """One claim through the boundary, returning the tool's result."""
    response = json.loads(
        await memory_claim_agent(
            agent_id=agent_id,
            description=description,
            ctx=ctx,
            expected_graph=ctx.lifespan_context["storage"].current_database,
        )
    )
    return response["result"]


async def _fact(storage, embedder, content: str) -> Fact:
    """A node a write can be aimed at."""
    node = Fact(content=content, source_id="seg1")
    await storage.store_node(node)
    await storage.store_edge(
        NodeEdge(src_id=node.id, dst_id=BASE_METACONTEXT_ID, type=EdgeType.HAS_METACONTEXT)
    )
    [vector] = await embedder.embed([content])
    await storage.store_embedding(
        EmbeddingRecord(item_id=node.id, model_id=embedder.model_id, vector=vector)
    )
    return node


async def _update(ctx, node_id: str, new_content: str, **kwargs) -> dict:
    """A write through the boundary, returning the tool's response."""
    return json.loads(
        await memory_update(
            node_id=node_id,
            new_content=new_content,
            because="it_was_wrong",
            ctx=ctx,
            expected_graph=ctx.lifespan_context["storage"].current_database,
            **kwargs,
        )
    )


async def _judge_of(storage, response: dict) -> str | None:
    """Who the graph says judged the node that write created."""
    node = await storage.get_node(response["result"]["new_node_id"])
    return None if node.judged_by is None else node.judged_by.agent_id


class TestTheClaimHandsBackAToken:
    async def test_claim_returns_a_token(self, storage, embedder, config):
        await storage.set_approved_agent_ids(["critic"])
        ctx = _ctx(_deps(storage, embedder, config))

        claimed = await _claim(ctx, "critic")

        assert claimed["status"] == "claimed"
        assert claimed["session_bound"] is True
        assert isinstance(claimed["judge_token"], str) and claimed["judge_token"]

    async def test_two_claims_get_two_tokens(self, storage, embedder, config):
        """One token per claim, so the second agent's does not displace the
        first's, which is the whole point of minting rather than binding."""
        await storage.set_approved_agent_ids(["critic", "editor"])
        ctx = _ctx(_deps(storage, embedder, config))

        first = await _claim(ctx, "critic")
        second = await _claim(ctx, "editor")

        assert first["judge_token"] != second["judge_token"]

    async def test_a_refused_claim_mints_nothing(self, storage, embedder, config):
        """A token is a credential for writing as a judge, and a claim the user
        refused admitted no judge to write as."""
        ctx = _ctx(_deps(storage, embedder, config))

        refused = await _claim(ctx, "self-appointed")

        assert refused["status"] == "refused"
        assert "judge_token" not in refused


class TestAWriteIsCreditedToTheTokenItCarries:
    async def test_two_claims_on_one_session_each_credit_their_own_judge(
        self, storage, embedder, config
    ):
        """The defect, stated as behaviour: a parent and a subagent share one
        connection, both claim, and each write lands on the judge that claimed
        it rather than on whichever claimed last."""
        await storage.set_approved_agent_ids(["critic", "editor"])
        deps = _deps(storage, embedder, config)
        ctx = _ctx(deps)
        parent = await _claim(ctx, "critic")
        child = await _claim(ctx, "editor")
        first = await _fact(storage, embedder, "the treaty was signed in Vienna")
        second = await _fact(storage, embedder, "the treaty was signed in 1815")

        by_parent = await _update(
            ctx,
            first.id,
            "the treaty was signed in Vienna in 1815",
            judge_token=parent["judge_token"],
        )
        by_child = await _update(
            ctx, second.id, "the treaty was signed in June 1815", judge_token=child["judge_token"]
        )

        assert await _judge_of(storage, by_parent) == parent["agent_id"]
        assert await _judge_of(storage, by_child) == child["agent_id"]
        # The session binding is still the last claim, untouched by any of this.
        bound = await _bound_judge(ctx)
        assert bound is not None and bound.agent_id == child["agent_id"]

    async def test_a_write_without_a_token_uses_the_most_recent_claim(
        self, storage, embedder, config
    ):
        """Today's behaviour, kept: every client that never passes a token
        writes exactly as it did before."""
        await storage.set_approved_agent_ids(["critic", "editor"])
        ctx = _ctx(_deps(storage, embedder, config))
        await _claim(ctx, "critic")
        latest = await _claim(ctx, "editor")
        node = await _fact(storage, embedder, "the treaty was signed in Vienna")

        written = await _update(ctx, node.id, "the treaty was signed in Vienna in 1815")

        assert await _judge_of(storage, written) == latest["agent_id"]

    async def test_a_token_outlives_the_claim_that_replaced_it(self, storage, embedder, config):
        """The first agent keeps writing as itself for as long as it holds the
        token, however many times its neighbour re-claims."""
        await storage.set_approved_agent_ids(["critic", "editor"])
        ctx = _ctx(_deps(storage, embedder, config))
        parent = await _claim(ctx, "critic")
        for _ in range(3):
            await _claim(ctx, "editor")
        node = await _fact(storage, embedder, "the treaty was signed in Vienna")

        written = await _update(
            ctx,
            node.id,
            "the treaty was signed in Vienna in 1815",
            judge_token=parent["judge_token"],
        )

        assert await _judge_of(storage, written) == parent["agent_id"]


class TestATokenThisSessionNeverIssued:
    async def test_an_unknown_token_refuses_the_write(self, storage, embedder, config):
        """Refused rather than credited to the session judge. Silent fallback is
        the defect this feature exists to close, so a token that resolves to
        nothing has to stop the write rather than quietly become somebody."""
        await storage.set_approved_agent_ids(["critic"])
        ctx = _ctx(_deps(storage, embedder, config))
        await _claim(ctx, "critic")
        node = await _fact(storage, embedder, "the treaty was signed in Vienna")

        response = await _update(
            ctx, node.id, "the treaty was signed in Vienna in 1815", judge_token="not-a-token"
        )

        assert "error" in response
        assert "claim_agent" in response["error"]
        assert "result" not in response or response.get("result") is None
        # Nothing written: the node is untouched and no replacement exists.
        assert (await storage.get_node(node.id)).content == "the treaty was signed in Vienna"
        assert len(await storage.query_nodes()) == 1

    async def test_a_token_from_another_session_is_unknown(self, storage, embedder, config):
        """Tokens are session state, so a second connection has never heard of
        this one. A token that worked across sessions would be a bearer
        credential for an identity the user approved for somebody else."""
        await storage.set_approved_agent_ids(["critic"])
        deps = _deps(storage, embedder, config)
        elsewhere = _ctx(deps)
        claimed = await _claim(elsewhere, "critic")
        here = _ctx(deps)
        node = await _fact(storage, embedder, "the treaty was signed in Vienna")

        response = await _update(
            ctx=here,
            node_id=node.id,
            new_content="the treaty was signed in Vienna in 1815",
            judge_token=claimed["judge_token"],
        )

        assert "error" in response and "claim_agent" in response["error"]


class TestApprovalIsRecheckedBehindTheToken:
    async def test_a_token_whose_judge_is_no_longer_approved_reads_as_unknown(
        self, storage, embedder, config
    ):
        """The same rule the session binding follows: approval is per graph and
        re-checked on every write, so the `use_graph` check is not a single
        point of failure. Unknown rather than an error, because recording the
        name would assert an approval that no longer exists.
        """
        await storage.set_approved_agent_ids(["critic"])
        ctx = _ctx(_deps(storage, embedder, config))
        claimed = await _claim(ctx, "critic")
        node = await _fact(storage, embedder, "the treaty was signed in Vienna")

        await storage.set_approved_agent_ids(["someone-else"])
        written = await _update(
            ctx,
            node.id,
            "the treaty was signed in Vienna in 1815",
            judge_token=claimed["judge_token"],
        )

        assert await _judge_of(storage, written) is None

    async def test_a_strict_graph_refuses_a_token_whose_judge_it_no_longer_approves(
        self, storage, embedder, config
    ):
        """The two rules compose exactly as they do for the session binding: a
        graph that requires a judge has no unknown to fall back to."""
        await storage.set_approved_agent_ids(["critic"])
        ctx = _ctx(_deps(storage, embedder, config))
        claimed = await _claim(ctx, "critic")
        node = await _fact(storage, embedder, "the treaty was signed in Vienna")

        await storage.set_approved_agent_ids(["someone-else"])
        await storage.set_require_judge(True)
        response = await _update(
            ctx,
            node.id,
            "the treaty was signed in Vienna in 1815",
            judge_token=claimed["judge_token"],
        )

        assert "error" in response and "claim_agent" in response["error"]

    async def test_a_graph_switch_leaves_the_token_readable(self, storage, embedder, config):
        """Tokens survive a switch and the judge behind one is re-checked
        against the graph the write lands in, which is what the session binding
        does one layer up. A token cleared on switch would refuse writes an
        agent could fix only by re-claiming, and re-claiming is what it would
        have to do anyway once the new graph turned out not to approve it.
        """
        await storage.set_approved_agent_ids(["critic"])
        ctx = _ctx(_deps(storage, embedder, config))
        claimed = await _claim(ctx, "critic")

        await storage.switch_database("elsewhere")
        await storage.set_approved_agent_ids(["critic"])
        node = await _fact(storage, embedder, "the treaty was signed in Vienna")
        written = await _update(
            ctx,
            node.id,
            "the treaty was signed in Vienna in 1815",
            judge_token=claimed["judge_token"],
        )

        assert await _judge_of(storage, written) == claimed["agent_id"]


class TestASessionlessClientGetsATokenToo:
    """Session state needs a session. Without one the binding lives on the
    lifespan, and the tokens have to live beside it or a client with no sessions
    would be handed a token that refuses every write it is passed to."""

    async def test_a_claim_without_a_session_still_mints_a_token(self, storage, embedder, config):
        await storage.set_approved_agent_ids(["critic"])
        ctx = _ctx(_deps(storage, embedder, config), session=False)

        claimed = await _claim(ctx, "critic")

        assert claimed["status"] == "claimed"
        assert claimed["session_bound"] is False, "reported, so the caller can see what happened"
        assert claimed["judge_token"]

    async def test_that_token_credits_the_write(self, storage, embedder, config):
        await storage.set_approved_agent_ids(["critic", "editor"])
        ctx = _ctx(_deps(storage, embedder, config), session=False)
        parent = await _claim(ctx, "critic")
        await _claim(ctx, "editor")
        node = await _fact(storage, embedder, "the treaty was signed in Vienna")

        written = await _update(
            ctx,
            node.id,
            "the treaty was signed in Vienna in 1815",
            judge_token=parent["judge_token"],
        )

        assert await _judge_of(storage, written) == parent["agent_id"]

    async def test_an_unknown_token_still_refuses(self, storage, embedder, config):
        await storage.set_approved_agent_ids(["critic"])
        ctx = _ctx(_deps(storage, embedder, config), session=False)
        await _claim(ctx, "critic")
        node = await _fact(storage, embedder, "the treaty was signed in Vienna")

        response = await _update(
            ctx, node.id, "the treaty was signed in Vienna in 1815", judge_token="not-a-token"
        )

        assert "error" in response and "claim_agent" in response["error"]
