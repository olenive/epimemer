"""Tests for structured JSON logging."""

import json
import logging

from epimemer.logging.structured import (
    JSONFormatter,
    ToolInvocationLog,
    log_tool_call,
    setup_logging,
)


class TestJSONFormatter:

    def test_outputs_valid_json(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["level"] == "INFO"
        assert parsed["message"] == "hello"
        assert "timestamp" in parsed

    def test_includes_structured_data(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="test",
            args=(),
            exc_info=None,
        )
        record.structured_data = {"tool": "memory.ingest", "latency_ms": 42.5}
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["data"]["tool"] == "memory.ingest"
        assert parsed["data"]["latency_ms"] == 42.5


class TestToolInvocationLog:

    def test_model_validates(self):
        entry = ToolInvocationLog(
            tool_name="memory.search",
            input_summary="query=hello",
            output_summary="nodes=3",
            latency_ms=15.2,
            nodes_touched=3,
            llm_calls=0,
        )
        assert entry.tool_name == "memory.search"
        assert entry.latency_ms == 15.2
        assert entry.error is None

    def test_defaults(self):
        entry = ToolInvocationLog(tool_name="test")
        assert entry.input_summary == ""
        assert entry.llm_calls == 0
        assert entry.timestamp is not None


class TestLogToolCall:

    def test_emits_to_logger(self):
        # Set up logging to capture output
        setup_logging(level="DEBUG")
        logger = logging.getLogger("epimemer.mcp")

        # Add a handler that captures records
        captured: list[logging.LogRecord] = []

        class CaptureHandler(logging.Handler):
            def emit(self, record):
                captured.append(record)

        handler = CaptureHandler()
        logger.addHandler(handler)

        try:
            entry = ToolInvocationLog(
                tool_name="memory.ingest",
                input_summary="content_length=100",
                output_summary="segments=2",
                latency_ms=50.0,
                nodes_touched=6,
                llm_calls=3,
            )
            log_tool_call(entry)

            assert len(captured) >= 1
            record = captured[-1]
            assert hasattr(record, "structured_data")
            assert record.structured_data["tool_name"] == "memory.ingest"
            assert record.structured_data["llm_calls"] == 3
        finally:
            logger.removeHandler(handler)
