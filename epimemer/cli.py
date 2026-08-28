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
separate store, not a second view of one. Writing there would
report success into a store the running server will never read, so it refuses
and names `EPIMEMER_APPROVED_AGENTS`, which the server reads at connect.
"""

import argparse
import asyncio
import sys
from datetime import datetime, timezone

from epimemer.core.types import (
    BASE_METACONTEXT_ID,
    JudgeRef,
    QUARANTINE_METACONTEXT_ID,
    RelationLabel,
    agent_name,
    current_description,
    live_agents,
    resolve_agent,
)
from epimemer.mcp.config import ServerConfig, create_storage, load_config
from epimemer.mcp.tools import (
    approved_labels,
    rename_judge,
    seed_approved_judges,
)
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
    if action == "backfill":
        # The one command here whose refusal costs nothing, and the message has
        # to say so — otherwise a user reads it as a graph they cannot fix.
        return (
            f"{reason}\n\n"
            f"Nothing is lost: every write path that names a label creates its "
            f"record, so this graph's vocabulary fills in as it is used. This "
            f"command only exists to do it in one go on a long-lived graph, "
            f"and it is never a precondition for anything."
        )
    if action == "declare":
        # An embedded graph cannot be declared from out here, and it does not
        # need to be: it lives and dies with the server process, so it is
        # rebuilt rather than migrated. Saying that is the whole message —
        # otherwise this reads as a graph stuck in a state nothing can fix.
        return (
            f"{reason}\n\n"
            f"An embedded graph is rebuilt rather than declared: it lives "
            f"inside the server process, so start again with a fresh one and "
            f"every node will name its frame at ingest. This command is for "
            f"long-lived graphs on a served store."
        )
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


async def _confirm(storage: StorageBackend, handle: str) -> str:
    """Admit the judge `handle` names to the active graph, and say what changed.

    **A handle, because a person types names.** Since the three-layer split the
    approved list holds opaque keys, so a name is resolved to the judge
    that holds it — otherwise approving an existing judge by name would admit a
    second, empty identity keyed on its name. A handle matching nothing is
    admitted as itself, which is what seeding a judge that has not claimed yet
    has always meant.

    The confirmation is stamped on the judge's **current description version**
    where one exists, because that is what the user is vouching for — the
    wording in front of them, not the identity in the abstract (§2.3). One
    approved before the agent has ever claimed it is admitted with nothing to
    stamp, which is the ordinary case: the refusal is what tells the user it
    exists.
    """
    agents = await storage.list_agents()
    agent = resolve_agent(agents, handle)
    approved = await seed_approved_judges(storage, [handle])
    labels = ", ".join(approved_labels(approved, agents))
    if agent is None:
        return (
            f"Approved '{handle}' in graph '{storage.current_database}'. "
            f"No judge here answers to it yet; its next claim_agent "
            f"will be recorded.\nApproved judges: {labels}"
        )

    name = agent_name(agent)
    version = current_description(agent)
    if version is None or version.confirmed_at is not None:
        return (
            f"Approved '{name}' in graph '{storage.current_database}'.\n"
            f"Approved judges: {labels}"
        )

    confirmed = version.model_copy(
        update={"confirmed_at": datetime.now(timezone.utc)}
    )
    await storage.upsert_agent(
        agent.model_copy(update={"descriptions": [*agent.descriptions[:-1], confirmed]})
    )
    return (
        f"Approved '{name}' in graph '{storage.current_database}' and "
        f"confirmed its current description ({version.digest}):\n"
        f"  {version.text}\n"
        f"Approved judges: {labels}"
    )


async def _rename(storage: StorageBackend, handle: str, name: str, same: bool) -> str:
    """Rename a judge, or consolidate two that were always one.

    Here for the reason approval is here: a handle an agent could rename is a
    handle an agent could point at another judge's history (§2.2). `--same-judge`
    is the answer to the question a name collision raises, given up front
    because a command has nowhere to ask it — the elicitation prompt does ask,
    and that is the other channel this reaches from.
    """
    result = await rename_judge(
        storage, handle=handle, name=name, same_judge=same
    )
    if result["status"] == "same_judge_needed":
        return (
            f"{result['reason']}\n\nIf they are the same judge, run this again "
            f"with --same-judge."
        )
    return result.get("message") or result.get("reason", result["status"])


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
    stored = await storage.list_agents()
    agents = sorted(live_agents(stored), key=agent_name)
    override = await storage.get_require_judge()
    lines = [
        f"graph: {storage.current_database}",
        f"approved judges: "
        + (", ".join(approved_labels(approved, stored)) if approved else "(none)"),
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
        lines.append(
            f"{agent_name(agent)}  "
            f"(last seen {seen}, {len(agent.descriptions)} version(s))"
        )
        # The key and the ids consolidated into it, on their own line: not for
        # reading, but this is the only place they can be seen at all, and a
        # reviewer chasing a `judged_by` out of an old row needs them.
        lines.append(f"    key {agent.id}")
        if agent.former_ids:
            lines.append(
                f"    also recorded as {', '.join(agent.former_ids)}"
            )
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


async def _backfill_relations(storage: StorageBackend) -> str:
    """Give every label already in use a record, and say how many were new.

    **A convenience, never a precondition**. Every write path that names a
    label creates its record, so a graph that has been touched since this
    shipped needs nothing from here — this is for a long-lived graph that wants
    its whole vocabulary at once. It matters that it is not the only remedy:
    this command **refuses embedded backends**, which is the default development
    configuration, and an agent cannot run it at all.

    **No judge.** A backfilled record is not a claim that anyone introduced the
    label; it is the record catching up with edges that already exist. Only
    `link` records a coiner.

    Idempotent, and it never touches a record that exists — a label already
    described must not lose its description to a rerun.
    """
    from epimemer.pipelines.reflection.relation_consolidation import (
        related_edges_of_active_nodes,
    )

    seen: list[tuple[str, str]] = []
    for edge in await related_edges_of_active_nodes(storage):
        pair = (edge.label or "", edge.kind)
        if pair[0] and pair not in seen:
            seen.append(pair)

    created = 0
    for name, kind in seen:
        if await storage.get_relation_label(name, kind) is None:
            await storage.store_relation_label(RelationLabel(name=name, kind=kind))
            created += 1

    if not seen:
        return (
            f"Graph '{storage.current_database}' uses no user-tier relation "
            f"labels, so there is nothing to record."
        )
    return (
        f"Graph '{storage.current_database}': {len(seen)} label(s) in use, "
        f"{created} newly recorded, {len(seen) - created} already had a record.\n"
        + "\n".join(f"  {name}  ({kind})" for name, kind in seen)
    )


async def _declare_frames(
    storage: StorageBackend, frame: str, handle: str | None, assume_yes: bool
) -> str:
    """Stamp `frame` on every node in this graph that carries no frame at all.

    **The user's act, which is why it is here and not a tool.** Nothing derives
    the answer from the content: somebody is stating that the claims in a graph
    written before frames were required were always about one world, and owning
    having said so. An agent asserting that about its own past writes would be
    marking its own homework, which is the reasoning that keeps judge approval
    on this side of the wall too.

    It asks before writing, because the sweep is not reversible in one step: a
    wrong frame comes off one node at a time with `reframe`, and the count is
    the only thing that tells the user how big the claim they are about to make
    actually is.
    """
    from epimemer.mcp.tools import create_metacontext
    from epimemer.pipelines.frames import declare_frames

    unframed = await storage.count_nodes_without_frame()
    graph = storage.current_database
    if unframed == 0:
        return (
            f"Graph '{graph}': every node already names a frame. Nothing to "
            f"declare."
        )

    judge = None
    if handle is not None:
        agent = resolve_agent(await storage.list_agents(), handle)
        judge = JudgeRef(
            agent_id=agent.id if agent is not None else handle.strip(),
            digest=(
                version.digest
                if agent is not None and (version := current_description(agent))
                else ""
            ),
        )

    if not assume_yes:
        answer = input(
            f"Declare {unframed} unframed node(s) in graph '{graph}' as "
            f"'{frame}'? This states that they were always claims in that "
            f"frame. [y/N] "
        )
        if answer.strip().lower() not in ("y", "yes"):
            return f"Nothing declared in graph '{graph}'."

    # The frame is created here when it is missing, and only here. The sweep
    # itself refuses a frame that does not exist — a bulk stamp pointing at
    # nothing is the isolation failure this is meant to end — but a person
    # declaring *this graph is about the real world* is also entitled to say
    # that frame exists. No agent reaches this path.
    created = ""
    if await storage.get_metacontext(frame) is None:
        await create_metacontext(
            frame.replace("-", " ").title(), storage, metacontext_id=frame,
            description=f"Declared for graph '{graph}'.",
        )
        created = f"Created metacontext '{frame}', which this graph did not have.\n"

    result = await declare_frames(storage, frame=frame, judge=judge)
    by = f" by '{handle}'" if handle else " with no judge recorded"
    return (
        created
        + f"Graph '{graph}': declared {result.declared} node(s) as "
        f"'{result.frame}'{by}; {result.already_framed} already named a frame "
        f"and were left alone.\n"
        f"One journal row records the sweep. Check completeness with "
        f"graph_stats: nodes_without_frame should now be 0."
    )


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

    rename = agents_sub.add_parser(
        "rename", help="Rename a judge, or consolidate two that are one."
    )
    rename.add_argument("agent_id", help="The judge: its name, key, or a former key.")
    rename.add_argument("name", help="What it should be called from now on.")
    rename.add_argument(
        "--same-judge", action="store_true",
        help=(
            "If the new name already belongs to another judge, consolidate the "
            "two: nothing is deleted and no decision is rewritten."
        ),
    )
    rename.add_argument(
        "--graph", help="Graph to write in (default: the configured one)."
    )

    listing = agents_sub.add_parser("list", help="Judges and approvals in a graph.")
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

    relations = sub.add_parser(
        "relations", help="The user-tier relationship vocabulary."
    )
    relations_sub = relations.add_subparsers(dest="action", required=True)
    backfill = relations_sub.add_parser(
        "backfill",
        help="Give every label already in use a record. Idempotent.",
    )
    backfill.add_argument(
        "--graph", help="Graph to write in (default: the configured one)."
    )

    frames = sub.add_parser(
        "frames", help="Epistemic frames — which world a claim is about."
    )
    frames_sub = frames.add_subparsers(dest="action", required=True)
    declare = frames_sub.add_parser(
        "declare",
        help=(
            "State which frame this graph's unframed nodes were always in. "
            "Idempotent; skips any node that already names a frame."
        ),
    )
    declare.add_argument(
        "--frame",
        default=BASE_METACONTEXT_ID,
        help=(
            f"Frame to declare (default: {BASE_METACONTEXT_ID}). Use "
            f"'{QUARANTINE_METACONTEXT_ID}' for a graph nobody can vouch for — "
            f"no agent may write that one."
        ),
    )
    declare.add_argument(
        "--judge",
        help=(
            "Judge to record as having declared it. Omitted, the edges carry "
            "no judge, which reads as nobody having said so."
        ),
    )
    declare.add_argument(
        "--yes", action="store_true", help="Skip the confirmation prompt."
    )
    declare.add_argument(
        "--graph", help="Graph to write in (default: the configured one)."
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config()

    if args.action in ("confirm", "require", "rename", "backfill", "declare"):
        unreachable = unreachable_store(config)
        if unreachable is not None:
            print(
                _embedded_advice(
                    unreachable, getattr(args, "agent_id", None), args.action
                ),
                file=sys.stderr,
            )
            return 2
        if args.action == "confirm":
            run = lambda s: _confirm(s, args.agent_id)
        elif args.action == "rename":
            run = lambda s: _rename(s, args.agent_id, args.name, args.same_judge)
        elif args.action == "backfill":
            run = _backfill_relations
        elif args.action == "declare":
            run = lambda s: _declare_frames(s, args.frame, args.judge, args.yes)
        else:
            run = lambda s: _require(s, args.setting, config.require_judge)
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
