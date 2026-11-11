"""What a retrieval record may carry to wherever the dashboard is served.

A hub bound to a loopback address is reachable from this machine only. A hub
bound to any other address is reachable by whoever can route to it, and
nothing in front of it authenticates a browser, so a retrieval record reaches
such a hub's readers as structural metadata: which record, which tool, which
graph, which ids, and whether a cap cut it. The query text and the response
text stay in the session process (`RETRIEVAL_PROVENANCE.md` §3.2).

The record is handled here as the plain dict the wire carries, because this
layer sits below `mcp/` and must not import the record type back.
`mcp.retrieval_records.structural_only` applies the same rule to the record
type, through `structural_record`, so there is one list of what is kept.
"""

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

# What a structural record keeps. Listed rather than subtracted, so a field
# added to the record later stays out until somebody decides it is safe to hand
# out; `query` and `response_text` are always present, and empty.
STRUCTURAL_FIELDS = ("record_id", "at", "tool", "graph", "retrieved", "truncated")
STRUCTURAL_NODE_FIELDS = ("node_id", "provenance", "score")


def on_loopback(host: str) -> bool:
    """Whether `host` is a loopback address, reachable from this machine only."""
    return host in LOOPBACK_HOSTS


def structural_record(record: dict) -> dict:
    """`record` with only its structural fields, and the query and response emptied.

    An undeclared `retrieved` (None) stays None: "we do not know what this
    returned" is itself the fact worth keeping.
    """
    kept = {field: record[field] for field in STRUCTURAL_FIELDS if field in record}
    retrieved = record.get("retrieved")
    kept["retrieved"] = (
        [
            {field: node[field] for field in STRUCTURAL_NODE_FIELDS if field in node}
            for node in retrieved
            if isinstance(node, dict)
        ]
        if isinstance(retrieved, list)
        else None
    )
    return {**kept, "query": "", "response_text": ""}
