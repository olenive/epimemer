"""MCP response types for the Epimemer memory server.

ResponseMeta carries observability data on every tool response.
ToolResponse wraps the result + metadata for JSON serialization.
"""

from pydantic import BaseModel, Field

from epimemer.mcp.retrieval_records import RetrievedNode


class ResponseMeta(BaseModel):
    """Metadata included in every MCP tool response."""

    nodes_searched: int = 0
    nodes_returned: int = 0
    graph_hops: int = 0
    latency_ms: float = 0.0
    source_types: dict[str, int] = Field(default_factory=dict)

    # In-process only. `exclude=True` keeps these out of the `_meta` the agent
    # sees: the ids are for the dashboard, and putting them on the wire would
    # cost the agent tokens to read a list it has no use for.
    #
    # Tools declare their own ids rather than the choke point guessing at them
    # — walking an arbitrary result dict for id-shaped keys would guess
    # differently per tool and break silently when a shape changed
    # (RETRIEVAL_PROVENANCE.md §2.1).
    #
    # `None` means the tool never declared, and the record is flagged
    # undeclared; `[]` means it declared and returned nothing. The distinction
    # is what makes a forgotten declaration visible rather than silent.
    retrieved: list[RetrievedNode] | None = Field(default=None, exclude=True)


class ToolResponse(BaseModel):
    """Standard envelope for all MCP tool responses.

    Serializes with a `_meta` key in JSON output via alias.
    """

    result: dict
    meta: ResponseMeta = Field(default_factory=ResponseMeta, alias="_meta")

    model_config = {"populate_by_name": True}
