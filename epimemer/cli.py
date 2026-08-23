"""The `epimemer` command: what only a user may do.

One subcommand group today — `agents` — and it exists because of a rule rather
than for convenience. Approving an agent id is the act that makes review
provable (`dev-docs/REVIEW_MODE.md` §2.2), so no MCP tool may perform it: a tool
the agent can call cannot establish that the *user* called it. The two channels
that terminate at a person are `ctx.elicit`, which the server raises in-band,
and this command, which the agent cannot run.

**It does not work against every backend, and that is checked rather than
hoped.** Approvals live in per-graph settings *inside the storage backend*, and
an embedded store (`mem://`, `file://`, `surrealkv://`, or the in-memory
backend) lives inside the server process — a second connection to `mem://` is a
separate store, not a second view of one (`ISSUES.md` #16). Writing there would
report success into a store the running server will never read, so it refuses
and names `EPIMEMER_APPROVED_AGENTS`, which the server reads at connect.
"""

import argparse
import asyncio
import sys
from datetime import datetime, timezone

from epimemer.core.types import current_description
from epimemer.mcp.config import ServerConfig, create_storage, load_config
from epimemer.mcp.tools import approve_agent_ids
from epimemer.storage.protocol import StorageBackend, resolve_require_judge
from epimemer.storage.surrealdb_adapter import is_embedded_url


def unreachable_store(config: ServerConfig) -> str | None:
    """Why this command cannot reach the server's store, or None if it can.

    Prose rather than a flag, and returned rather than raised, because the two
    ways to fail here look identical from the outside and want different
    advice.
    """
    if config.storage_backend != "surrealdb":
        return (
            "This server uses the in-memory backend, which lives inside the "
            "server process — nothing this command writes can reach it."
        )
    if is_embedded_url(config.surrealdb_url):
        return (
            f"EPIMEMER_SURREALDB_URL is {config.surrealdb_url!r}, an embedded "
            f"store: it lives inside the server process, and a second "
            f"connection to it is a separate store rather than a second view "
            f"of the same one."
        )
    return None


def _embedded_advice(reason: str, agent_id: str | None, action: str) -> str:
    """What to do instead, named for the thing the user was trying to do.

    Two settings live behind this wall and they have different environment
    variables, so one generic message would send half of its readers to the
    wrong one.
    """
    if action == "require":
        return (
            f"{reason}\n\n"
            f"Set the policy where the server will read it instead — set\n"
            f"    EPIMEMER_REQUIRE_JUDGE=true\n"
            f"before starting the server. It applies to every graph this server "
            f"opens, which a per-graph setting written here would not."
        )
    ids = agent_id or "<id>"
    return (
        f"{reason}\n\n"
        f"Approve the id where the server will read it instead — set\n"
        f"    EPIMEMER_APPROVED_AGENTS={ids}\n"
        f"before starting the server, or answer the prompt that claim_agent "
        f"raises in a client that supports elicitation."
    )


async def _with_storage(config: ServerConfig, graph: str | None, run):
    """Open the configured backend, land on `graph`, run, and close."""
    storage: StorageBackend = create_storage(config)
    await storage.connect()
    try:
        if graph:
            await storage.switch_database(graph)
        return await run(storage)
    finally:
        await storage.close()


async def _confirm(storage: StorageBackend, agent_id: str) -> str:
    """Admit `agent_id` to the active graph, and say what changed.

    The confirmation is stamped on the agent's **current description version**
    where one exists, because that is what the user is vouching for — the
    wording in front of them, not the id in the abstract (§2.3). An id approved
    before the agent has ever claimed it is admitted with nothing to stamp,
    which is the ordinary case: the refusal is what tells the user the id
    exists.
    """
    approved = await approve_agent_ids(storage, [agent_id])
    agent = await storage.get_agent(agent_id)
    if agent is None:
        return (
            f"Approved '{agent_id}' in graph '{storage.current_database}'. "
            f"It has not claimed an identity here yet; its next claim_agent "
            f"will be recorded.\nApproved ids: {', '.join(approved)}"
        )

    version = current_description(agent)
    if version is None or version.confirmed_at is not None:
        return (
            f"Approved '{agent_id}' in graph '{storage.current_database}'.\n"
            f"Approved ids: {', '.join(approved)}"
        )

    confirmed = version.model_copy(
        update={"confirmed_at": datetime.now(timezone.utc)}
    )
    await storage.upsert_agent(
        agent.model_copy(update={"descriptions": [*agent.descriptions[:-1], confirmed]})
    )
    return (
        f"Approved '{agent_id}' in graph '{storage.current_database}' and "
        f"confirmed its current description ({version.digest}):\n"
        f"  {version.text}\n"
        f"Approved ids: {', '.join(approved)}"
    )


async def _require(storage: StorageBackend, setting: str, default: bool) -> str:
    """Set, clear, or read this graph's require-a-judge policy (§3.3.1).

    Here rather than in an MCP tool for the reason approvals are: a gate the
    agent can open is decoration. `default` restores *follow the server's
    setting*, which is deliberately not the same as writing today's value of it.
    """
    required = {"on": True, "off": False, "default": None}[setting]
    await storage.set_require_judge(required)

    effective = resolve_require_judge(await storage.get_require_judge(), default)
    graph = storage.current_database
    if required is None:
        return (
            f"Graph '{graph}' now follows the server setting for requiring a "
            f"judge, which is currently {'on' if default else 'off'}."
        )
    if not required:
        return f"Graph '{graph}' no longer requires a judge on writes."

    approved = await storage.get_approved_agent_ids()
    if not approved:
        # Said now rather than discovered by the next write failing: this is the
        # one setting that can make a working graph refuse everything.
        return (
            f"Graph '{graph}' now requires a judge on every write — and **no id "
            f"is approved here**, so every write will be refused until one is. "
            f"Run `epimemer agents confirm <id>`, or set "
            f"EPIMEMER_APPROVED_AGENTS before starting the server."
        )
    return (
        f"Graph '{graph}' now requires a judge on every write. Approved ids: "
        f"{', '.join(approved)}. Effective: {'on' if effective else 'off'}."
    )


async def _list(storage: StorageBackend) -> str:
    approved = await storage.get_approved_agent_ids()
    agents = sorted(await storage.list_agents(), key=lambda a: a.id)
    override = await storage.get_require_judge()
    lines = [
        f"graph: {storage.current_database}",
        f"approved ids: {', '.join(approved) if approved else '(none)'}",
        f"requires a judge: "
        + ("follows the server setting" if override is None
           else ("yes" if override else "no")),
        "",
    ]
    if not agents:
        lines.append("No agent has claimed an identity in this graph.")
    for agent in agents:
        version = current_description(agent)
        seen = agent.last_seen_at.isoformat() if agent.last_seen_at else "never"
        lines.append(f"{agent.id}  (last seen {seen}, {len(agent.descriptions)} version(s))")
        if version is not None:
            # Said plainly on every listing: the description is the agent's own
            # assertion, and the only part carrying human weight is whether a
            # person confirmed it (§2.4).
            mark = (
                f"confirmed {version.confirmed_at.isoformat()}"
                if version.confirmed_at is not None
                else "self-reported, unconfirmed"
            )
            lines.append(f"    {version.text}")
            lines.append(f"    [{version.digest}] {mark}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="epimemer", description="Epimemer administration."
    )
    sub = parser.add_subparsers(dest="group", required=True)

    agents = sub.add_parser("agents", help="Judges, and which ids may judge.")
    agents_sub = agents.add_subparsers(dest="action", required=True)

    confirm = agents_sub.add_parser(
        "confirm", help="Admit an agent id to a graph, as the user."
    )
    confirm.add_argument("agent_id")
    confirm.add_argument(
        "--graph", help="Graph to approve in (default: the configured one)."
    )

    listing = agents_sub.add_parser("list", help="Agents and approved ids in a graph.")
    listing.add_argument(
        "--graph", help="Graph to read (default: the configured one)."
    )

    require = agents_sub.add_parser(
        "require", help="Whether writes to a graph must name a judge."
    )
    require.add_argument(
        "setting", choices=("on", "off", "default"),
        help="'default' clears the graph's own answer and follows the server.",
    )
    require.add_argument(
        "--graph", help="Graph to set (default: the configured one)."
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config()

    if args.action in ("confirm", "require"):
        unreachable = unreachable_store(config)
        if unreachable is not None:
            print(
                _embedded_advice(
                    unreachable, getattr(args, "agent_id", None), args.action
                ),
                file=sys.stderr,
            )
            return 2
        run = (
            (lambda s: _confirm(s, args.agent_id)) if args.action == "confirm"
            else (lambda s: _require(s, args.setting, config.require_judge))
        )
        print(asyncio.run(_with_storage(config, args.graph, run)))
        return 0

    # Listing an embedded store is not wrong, only empty — it opens a store
    # nobody has written to. Saying so beats printing "(none)" as though it
    # were the server's answer.
    unreachable = unreachable_store(config)
    if unreachable is not None:
        print(f"Note: {unreachable}\n", file=sys.stderr)
    print(asyncio.run(_with_storage(config, args.graph, _list)))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
