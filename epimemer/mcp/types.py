"""MCP response types for the Epimemer memory server.

ResponseMeta carries observability data on every tool response.
ToolResponse wraps the result + metadata for JSON serialization.
"""

from pydantic import BaseModel, Field


class ResponseMeta(BaseModel):
    """Metadata included in every MCP tool response."""

    nodes_searched: int = 0
    nodes_returned: int = 0
    graph_hops: int = 0
    latency_ms: float = 0.0
    source_types: dict[str, int] = Field(default_factory=dict)


class ToolResponse(BaseModel):
    """Standard envelope for all MCP tool responses.

    Serializes with a `_meta` key in JSON output via alias.
    """

    result: dict
    meta: ResponseMeta = Field(default_factory=ResponseMeta, alias="_meta")

    model_config = {"populate_by_name": True}
