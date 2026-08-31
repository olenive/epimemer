"""Structured JSON logging for the Epimemer MCP server.

Provides a JSON log formatter and a structured log entry model
for recording every MCP tool invocation with timing and metadata.
"""

import json
import logging
from datetime import UTC, datetime

from pydantic import BaseModel, Field


class ToolInvocationLog(BaseModel):
    """Structured log entry for an MCP tool invocation."""

    tool_name: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    input_summary: str = ""
    output_summary: str = ""
    latency_ms: float = 0.0
    nodes_touched: int = 0
    error: str | None = None


class JSONFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # If the record has structured data attached, include it
        if hasattr(record, "structured_data"):
            entry["data"] = record.structured_data
        return json.dumps(entry, default=str)


_logger = logging.getLogger("epimemer.mcp")


def setup_logging(
    level: str = "INFO",
    log_file: str | None = None,
) -> None:
    """Configure the epimemer logger with JSON formatting.

    Args:
        level: Log level name (DEBUG, INFO, WARNING, ERROR).
        log_file: Optional file path for log output. If None, logs to stdout.
    """
    logger = logging.getLogger("epimemer")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove existing handlers to avoid duplicates on repeated calls
    logger.handlers.clear()

    formatter = JSONFormatter()

    if log_file:
        handler: logging.Handler = logging.FileHandler(log_file)
    else:
        handler = logging.StreamHandler()

    handler.setFormatter(formatter)
    logger.addHandler(handler)


def log_tool_call(entry: ToolInvocationLog) -> None:
    """Emit a structured log entry for an MCP tool invocation."""
    record = _logger.makeRecord(
        name=_logger.name,
        level=logging.INFO,
        fn="",
        lno=0,
        msg=f"Tool call: {entry.tool_name}",
        args=(),
        exc_info=None,
    )
    record.structured_data = entry.model_dump(mode="json")
    _logger.handle(record)
