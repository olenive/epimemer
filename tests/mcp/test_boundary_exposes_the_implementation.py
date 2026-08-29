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

import pytest

from epimemer.mcp import server, tools

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
        "the nomination bar, read from configuration so a caller cannot lower "
        "the bar its own merge is checked against",
    ("merge_inferences", "similarity_threshold"):
        "as merge_facts: a caller must not choose the bar it is judged by",
    ("merge_inferences", "warning_policy"):
        "resolved per graph at the boundary; an agent choosing its own advisory "
        "policy is a gate it can open",
    ("record_contradiction", "warning_policy"): "as merge_inferences",
    ("record_variant", "warning_policy"): "as merge_inferences",
    ("search", "record_retrieval"):
        "retrieval reinforcement is a property of a real read, not something a "
        "caller may switch off for its own reads",
}

# Supplied by the boundary for every tool, and never interesting here.
_PLUMBING = frozenset({
    "storage", "embedding_provider", "config", "judge", "event_bus", "ctx",
})


def _pairs() -> list[tuple[str, object, object]]:
    """Every MCP tool wrapping an implementation of the same name.

    Derived, not listed, for the reason the sweep guard is derived: a tool added
    later has to enter this test without anyone remembering to add it.
    """
    found = []
    for name in dir(server):
        if not name.startswith("memory_"):
            continue
        boundary = getattr(server, name)
        implementation = getattr(tools, name[len("memory_"):], None)
        if callable(boundary) and implementation is not None:
            found.append((name, boundary, implementation))
    return found


def test_the_derivation_finds_the_tools_that_exist():
    """The control. A derivation matching nothing makes every assertion over it
    vacuously true, which is the failure mode of deriving rather than listing."""
    names = {name for name, _, _ in _pairs()}
    assert {"memory_apply_reflection", "memory_search", "memory_claim_agent"} <= names
    assert len(names) > 20


@pytest.mark.parametrize("name,boundary,implementation", _pairs(), ids=lambda v: v if isinstance(v, str) else "")
def test_every_parameter_is_exposed_or_classified(name, boundary, implementation):
    tool = name[len("memory_"):]
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
        name[len("memory_"):]: impl for name, _, impl in _pairs()
    }
    stale = [
        (tool, parameter)
        for (tool, parameter), reason in NOT_AGENT_SETTABLE.items()
        if reason
        and (
            tool not in implementations
            or parameter not in inspect.signature(implementations[tool]).parameters
        )
    ]
    assert stale == []


def test_retained_reaches_the_agent():
    """The specific regression, named rather than left to the general rule.

    It is the one argument this guard was written because of, and a general
    assertion that happens to cover it today is not the same as saying so.
    """
    assert "retained" in inspect.signature(server.memory_apply_reflection).parameters
    assert "retained" in (server.memory_apply_reflection.__doc__ or "")
