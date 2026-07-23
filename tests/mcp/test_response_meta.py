"""Guards on the shape of the `_meta` envelope returned by every tool."""

import json

from epimemer.mcp.types import ResponseMeta, ToolResponse


def test_response_meta_has_no_llm_calls_field():
    # epimemer makes no LLM calls of its own (decomposition/segmentation are
    # agent-driven), so a `llm_calls` counter could only ever report 0. It was
    # removed rather than left as a structurally-false signal.
    assert "llm_calls" not in ResponseMeta.model_fields


def test_serialized_meta_omits_llm_calls():
    payload = ToolResponse(result={}, meta=ResponseMeta()).model_dump_json(by_alias=True)
    assert "llm_calls" not in json.loads(payload)["_meta"]
