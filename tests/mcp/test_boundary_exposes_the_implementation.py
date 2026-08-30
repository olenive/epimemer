"""Every implementation parameter is either exposed to the agent or classified.

The fourth instance of one defect shape in two days, and the one that got
furthest: `apply_reflection` grew a `retained` argument, the implementation
read it, the implementation's docstring documented it, two review rounds
scrutinised its semantics — and the MCP boundary never forwarded it. The
feature was unreachable from an agent, and the tool description an agent
actually reads did not mention it. Nothing failed. The unit tests called the
implementation directly and passed; the boundary tests never named the
argument, so its absence looked exactly like every other argument they also
did not name.

**A blanket parity assertion would be wrong**, which is why this is a
classification rather than an equality. Several parameters must *not* reach the
agent: `warning_policy` and `similarity_threshold` are server configuration, and
`claim_agent`'s approval arguments exist precisely so the agent cannot set them
— a gate an agent can open is decoration. Demanding those be exposed would turn
this guard into a security defect.

So the rule is the one this repo already uses for advisory stances: **a total
map, where absence is a failure rather than a default.** Every implementation
parameter is exposed at the boundary, or listed below with the reason it is
not. A parameter added later and classified nowhere fails here — which is the
only outcome that would have caught `retained`.
"""

import inspect
import re
from pathlib import Path

import pytest

from epimemer.mcp import server, tools

# Tools whose implementation is not `tools.<name after the prefix>`. Listed
# because the convention is a convention and not a rule — and the first version
# of this guard resolved by convention alone, silently skipping the four that do
# not follow it. A population enumerated by convention with silent misses is the
# defect this file exists to catch, reproduced inside the guard.
IMPLEMENTATION_ALIASES: dict[str, str] = {
    "memory_segment": "segment_text",
    "memory_add_timepoint": "add_timeline_timepoint",
    "memory_get_metacontexts": "get_metacontexts_for_node",
}

# Tools with no `tools.*` counterpart at all, and why. Declared rather than
# skipped: "there is nothing to compare" and "I failed to find it" look
# identical from here, and only one of them is fine.
NO_IMPLEMENTATION: dict[str, str] = {
    "epimemer_viz_status": (
        "implemented inline at the boundary — it reads process-local "
        "visualization state (`ctx.lifespan_context`) rather than the graph, so "
        "there is no storage-facing function to compare against"
    ),
}

# Arguments the boundary supplies rather than the agent, with the reason. The
# reason is the point: a bare list would accumulate entries nobody can defend,
# and this is the file where "why is that not exposed?" has to have an answer.
NOT_AGENT_SETTABLE: dict[tuple[str, str], str] = {
    ("claim_agent", "approve_id"):
        "approval is the user's, through elicitation or the CLI — an agent that "
        "could set it would be approving itself",
    ("claim_agent", "confirm_description"):
        "same channel as approval: `confirmed_at` may only be set by a path "
        "that terminates at the user",
    ("claim_agent", "confirmed_identity"):
        "the identity the user picked, supplied by the elicitation, never by "
        "the caller proposing it",
    ("claim_agent", "now"):
        "the clock, injected for tests",
    ("configure_warnings", "default_warning_policy"):
        "the process default from ServerConfig; the agent sets the per-graph "
        "override, not the default it falls back to",
    ("judge_importance", "importance_step"):
        "server configuration — how far one judgment moves a node is a policy, "
        "not a per-call choice",
    ("merge_facts", "similarity_threshold"):
        "the nomination bar. The boundary passes nothing and the "
        "implementation's own default applies, so a caller cannot lower the bar "
        "its own merge is checked against",
    ("merge_inferences", "similarity_threshold"):
        "as merge_facts: a caller must not choose the bar it is judged by",
    ("merge_inferences", "warning_policy"):
        "the boundary passes the process default and the implementation "
        "resolves the per-graph override from it; an agent choosing its own "
        "advisory policy is a gate it can open",
    ("record_contradiction", "warning_policy"): "as merge_inferences",
    ("record_variant", "warning_policy"): "as merge_inferences",
    ("search", "record_retrieval"):
        "retrieval reinforcement is a property of a real read, not something a "
        "caller may switch off for its own reads",
    ("apply_reflection", "merge_similarity_threshold"):
        "closed 2026-08-30 for the reason the merge bars above are closed, and "
        "with more at stake: topic `merges` is the one consolidation that "
        "retires nodes from the active graph, and an agent passing 0.0 could "
        "retire arbitrary topics. It was the one open door beside a principle "
        "the rest of this list already states",
    ("use_graph", "seed_agent_ids"):
        "judge approval seeding, from the environment at start-up — an agent "
        "that could seed the approved list would be approving itself by another "
        "route",
    ("graph_stats", "default_reflect_threshold"):
        "the process default from ServerConfig; the agent reads the resolved "
        "value, it does not choose the fallback",
    ("configure_reflection", "default_threshold"):
        "as graph_stats: the agent sets the per-graph override, never the "
        "default it falls back to",
}

# Supplied by the boundary for every tool, and never interesting here.
_PLUMBING = frozenset({
    "storage", "embedding_provider", "config", "judge", "event_bus", "ctx",
})


def _registered() -> list[str]:
    """Every function registered as an MCP tool, read from the registration.

    From the decorator rather than from `dir(server)`, because what matters is
    what an agent can call. The first version of this guard walked names
    beginning `memory_`, which missed the nine registered as `epimemer_*`
    entirely — and missing them looked exactly like the tools it had no reason
    to mention.
    """
    source = Path(server.__file__).read_text()
    return re.findall(r"@mcp\.tool\(name=[^)]*\)\s*\nasync def (\w+)", source)


def _implementation(name: str):
    """The `tools.*` function a registered tool calls, or None if it has none."""
    if name in NO_IMPLEMENTATION:
        return None
    stem = IMPLEMENTATION_ALIASES.get(name) or name.split("_", 1)[1]
    return getattr(tools, stem, None)


def _pairs() -> list[tuple[str, object, object]]:
    """Every registered tool with an implementation to compare against."""
    return [
        (name, getattr(server, name), implementation)
        for name in _registered()
        if (implementation := _implementation(name)) is not None
    ]


def test_every_registered_tool_is_paired_or_classified():
    """**No silent misses.** A tool this guard cannot resolve must fail here
    rather than drop out of the population, because a tool that is not in the
    population is one whose parameters are never checked — and its absence looks
    exactly like the absence of every tool that resolved fine.

    This is the fifth instance in this codebase of one shape: a population
    enumerated by convention, with the misses invisible. Twelve of forty-six
    tools were invisible to the first version of this file.
    """
    unresolved = [
        name for name in _registered()
        if name not in NO_IMPLEMENTATION and _implementation(name) is None
    ]
    assert unresolved == [], (
        f"{', '.join(unresolved)} is registered as an MCP tool but this guard "
        f"cannot find its implementation, so its parameters are checked by "
        f"nothing. Add it to IMPLEMENTATION_ALIASES, or to NO_IMPLEMENTATION "
        f"with the reason it has none."
    )


def test_the_derivation_finds_every_tool():
    """The control. A derivation matching nothing makes every assertion over it
    vacuously true, which is the failure mode of deriving rather than listing."""
    registered = _registered()
    assert len(registered) == len(set(registered)), "a tool matched twice"
    assert len(registered) > 40
    assert len(_pairs()) == len(registered) - len(NO_IMPLEMENTATION)
    assert {"memory_apply_reflection", "epimemer_review", "memory_segment"} <= set(
        registered
    )


def test_the_declared_exceptions_are_all_live():
    """An alias or exemption naming a tool that no longer exists is a reason
    nobody can check, which is how a hand-kept list rots into a list of
    guesses."""
    registered = set(_registered())
    assert set(IMPLEMENTATION_ALIASES) <= registered
    assert set(NO_IMPLEMENTATION) <= registered
    assert all(
        getattr(tools, stem, None) is not None
        for stem in IMPLEMENTATION_ALIASES.values()
    )


@pytest.mark.parametrize("name,boundary,implementation", _pairs(), ids=lambda v: v if isinstance(v, str) else "")
def test_every_parameter_is_exposed_or_classified(name, boundary, implementation):
    tool = name.split("_", 1)[1]
    exposed = set(inspect.signature(boundary).parameters)
    unexposed = [
        parameter
        for parameter in inspect.signature(implementation).parameters
        if parameter not in exposed
        and parameter not in _PLUMBING
        and (tool, parameter) not in NOT_AGENT_SETTABLE
    ]
    assert unexposed == [], (
        f"{tool} accepts {', '.join(unexposed)} but the MCP boundary does not "
        f"forward it, so an agent cannot reach it and the tool description does "
        f"not mention it. Expose it, or add it to NOT_AGENT_SETTABLE with the "
        f"reason it is the server's to supply."
    )


def test_the_classification_has_no_stale_entries():
    """An entry naming a parameter that no longer exists is a reason nobody can
    check, and it is how a list of exceptions rots into a list of guesses."""
    implementations = {
        name.split("_", 1)[1]: impl for name, _, impl in _pairs()
    }
    stale = [
        (tool, parameter)
        for (tool, parameter) in NOT_AGENT_SETTABLE
        if tool not in implementations
        or parameter not in inspect.signature(implementations[tool]).parameters
    ]
    assert stale == []


def test_every_classification_states_a_reason():
    """The reason is the mechanism, not decoration: a blank one would suppress
    the exposure check while asserting nothing, which is an exemption nobody
    has to defend."""
    blank = [pair for pair, reason in NOT_AGENT_SETTABLE.items() if not reason.strip()]
    assert blank == []
    assert all(reason.strip() for reason in NO_IMPLEMENTATION.values())


def test_retained_reaches_the_agent():
    """The specific regression, named rather than left to the general rule.

    It is the one argument this guard was written because of, and a general
    assertion that happens to cover it today is not the same as saying so.
    """
    assert "retained" in inspect.signature(server.memory_apply_reflection).parameters
    assert "retained" in (server.memory_apply_reflection.__doc__ or "")
