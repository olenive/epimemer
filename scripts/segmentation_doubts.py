#!/usr/bin/env python
"""Measure how often `segment` would doubt its own cut, over a real graph.

`doubt_cut` (`epimemer/pipelines/segmentation/doubts.py`) reports structural
signs that a programmatic cut is poor, and its thresholds are guesses until
measured. This runs it over every document in one graph and prints how many
documents raised each kind of doubt, the rate, and up to three examples per
kind, so a threshold that fires on ordinary prose can be moved and a check that
never fires can be dropped (`dev-docs/SEGMENTATION.md`, "Calibrate before
building").

**Read-only by construction.** It is meant for the real graphs, which nothing
may write to. `SurrealDBStorage.connect()` defines tables and indexes and runs
schema migrations, and pointed at a misspelt graph name it would create it, so
this never opens a storage backend. It sends `INFO FOR NS` and `SELECT` over
HTTP, the way `scripts/corpus_measure.py` does, and `read_only` refuses any
other statement before it leaves the process. A graph the namespace does not
list is refused rather than read as empty.

Connection settings come from the arguments, or else from the `EPIMEMER_*`
variables the server reads (`epimemer/mcp/config.py`); a `ws://.../rpc` server
URL is turned into its HTTP endpoint. There is no default URL or graph, so
nothing is measured by accident.

Usage:
    uv run python scripts/segmentation_doubts.py \\
        --url http://HOST:PORT --namespace NAMESPACE --graph GRAPH \\
        --user USER --password PASSWORD
"""

import argparse
import asyncio
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable, Mapping, Sequence

from pydantic import BaseModel

from epimemer.core.types import RawDocument, Segment
from epimemer.pipelines.segmentation.doubts import DoubtKind, doubt_cut

# Examples printed per kind of doubt.
EXAMPLES_PER_KIND = 3

# One SurrealQL statement in, its rows out.
Sql = Callable[[str], Awaitable[object]]

# A document and its passages, in document order.
Cut = tuple[RawDocument, list[Segment]]

# The statements this script may send. Everything else is refused unsent.
_READ_PREFIXES = ("SELECT ", "INFO ")


class WriteRefused(Exception):
    """A statement that is not a single read, refused before it was sent."""


class GraphNotFound(Exception):
    """The namespace has no graph of that name."""


class Example(BaseModel):
    source: str
    passages: int
    detail: str


class Report(BaseModel):
    documents: int
    raised: dict[str, int]
    examples: dict[str, list[Example]]


def read_only(sql: Sql) -> Sql:
    """`sql`, refusing anything but one `SELECT` or `INFO` statement.

    A second statement after a semicolon is refused too: the prefix check would
    otherwise pass `SELECT ...; DELETE ...` whole.
    """

    async def guarded(statement: str):
        body = statement.strip().removesuffix(";")
        if ";" in body or not (body.upper() + " ").startswith(_READ_PREFIXES):
            raise WriteRefused(f"only a single SELECT or INFO is sent, refused: {statement!r}")
        return await sql(statement)

    return guarded


def http_base(url: str) -> str:
    """The HTTP endpoint for a SurrealDB server URL, as the server config spells it."""
    if "://" not in url or url.startswith(("mem://", "memory", "file://", "surrealkv://")):
        raise ValueError(f"not a server this can reach over HTTP: {url}")
    scheme, rest = url.split("://", 1)
    scheme = {"ws": "http", "wss": "https"}.get(scheme, scheme)
    rest = rest.rstrip("/").removesuffix("/rpc")
    return f"{scheme}://{rest}"


def http_sql(url: str, user: str, password: str, namespace: str, database: str | None) -> Sql:
    """One statement over HTTP `/sql`. Raises rather than returning a partial result."""
    credentials = base64.b64encode(f"{user}:{password}".encode()).decode()
    headers = {
        "Accept": "application/json",
        "Authorization": f"Basic {credentials}",
        "surreal-ns": namespace,
    }
    if database is not None:
        headers["surreal-db"] = database

    def send(statement: str):
        request = urllib.request.Request(
            f"{http_base(url)}/sql", data=statement.encode(), headers=headers
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.load(response)
        if not payload or payload[0].get("status") != "OK":
            raise RuntimeError(f"query failed: {payload}")
        return payload[0]["result"]

    async def sql(statement: str):
        return await asyncio.to_thread(send, statement)

    return sql


def _row(record: dict) -> dict:
    """A stored row as the model reads it: the application id is `uid`."""
    row = {k: v for k, v in record.items() if k != "id"}
    if "uid" in row:
        row["id"] = row.pop("uid")
    return row


async def read_graph(namespace_sql: Sql, graph_sql: Sql, database: str) -> list[Cut]:
    """Every document in the graph with its passages, read with two SELECTs."""
    info = await namespace_sql("INFO FOR NS;")
    databases = info.get("databases", {}) if isinstance(info, dict) else {}
    if database not in databases:
        raise GraphNotFound(
            f"no graph named {database!r} in this namespace; it has: "
            + (", ".join(sorted(databases)) or "none")
        )
    documents = [
        RawDocument.model_validate(_row(r)) for r in await graph_sql("SELECT * FROM document;")
    ]
    passages: dict[str, list[Segment]] = {}
    for record in await graph_sql("SELECT * FROM segment;"):
        segment = Segment.model_validate(_row(record))
        passages.setdefault(segment.source_id, []).append(segment)
    return [
        (doc, sorted(passages.get(doc.id, []), key=lambda s: s.span_start))
        for doc in sorted(documents, key=lambda d: (d.source or "", d.id))
    ]


def tally(graph: Sequence[Cut]) -> Report:
    """How many documents raised each kind of doubt, with the first few examples."""
    raised = {kind.value: 0 for kind in DoubtKind}
    examples: dict[str, list[Example]] = {kind.value: [] for kind in DoubtKind}
    for doc, segments in graph:
        for doubt in doubt_cut(doc.content, segments):
            kind = doubt.kind.value
            raised[kind] += 1
            if len(examples[kind]) < EXAMPLES_PER_KIND:
                examples[kind].append(
                    Example(
                        source=doc.source or doc.id, passages=len(segments), detail=doubt.detail
                    )
                )
    return Report(documents=len(graph), raised=raised, examples=examples)


def render(report: Report, database: str) -> str:
    lines = [f"graph: {database}", f"documents: {report.documents}"]
    for kind, count in report.raised.items():
        rate = 100 * count / report.documents if report.documents else 0.0
        lines.append(f"{kind}: {count} ({rate:.1f}%)")
    for kind, examples in report.examples.items():
        if examples:
            lines.append("")
            lines.append(f"{kind} examples:")
            lines.extend(
                f"  {e.source} ({e.passages} passage{'' if e.passages == 1 else 's'}): {e.detail}"
                for e in examples
            )
    return "\n".join(lines)


async def measure(namespace_sql: Sql, graph_sql: Sql, database: str) -> str:
    return render(tally(await read_graph(namespace_sql, graph_sql, database)), database)


def parse_args(argv: Sequence[str], env: Mapping[str, str]) -> argparse.Namespace:
    """Arguments first, then the server's own `EPIMEMER_*` variables."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--url",
        default=env.get("EPIMEMER_SURREALDB_URL"),
        help="SurrealDB server URL, http:// or ws://.../rpc (EPIMEMER_SURREALDB_URL)",
    )
    parser.add_argument(
        "--namespace",
        default=env.get("EPIMEMER_SURREALDB_NAMESPACE", "epimemer"),
        help="namespace (EPIMEMER_SURREALDB_NAMESPACE)",
    )
    parser.add_argument(
        "--graph",
        default=env.get("EPIMEMER_GRAPH") or env.get("EPIMEMER_SURREALDB_DATABASE"),
        help="graph to read (EPIMEMER_GRAPH, then EPIMEMER_SURREALDB_DATABASE)",
    )
    parser.add_argument(
        "--user",
        default=env.get("EPIMEMER_SURREALDB_USER", "root"),
        help="(EPIMEMER_SURREALDB_USER)",
    )
    parser.add_argument(
        "--password",
        default=env.get("EPIMEMER_SURREALDB_PASS", "root"),
        help="(EPIMEMER_SURREALDB_PASS)",
    )
    args = parser.parse_args(list(argv))
    if not args.url or not args.graph:
        parser.error(
            "name the server and the graph: --url and --graph, or their EPIMEMER_* variables"
        )
    return args


def main() -> None:
    args = parse_args(sys.argv[1:], os.environ)
    namespace_sql = read_only(http_sql(args.url, args.user, args.password, args.namespace, None))
    graph_sql = read_only(http_sql(args.url, args.user, args.password, args.namespace, args.graph))
    try:
        print(asyncio.run(measure(namespace_sql, graph_sql, args.graph)))
    except (urllib.error.URLError, RuntimeError, GraphNotFound, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
