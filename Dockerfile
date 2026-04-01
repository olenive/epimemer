FROM python:3.14-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy petritype (local dependency — build context is parent directory)
COPY petritype/ /petritype/

# Copy dependency files for layer caching
COPY epimemer/pyproject.toml epimemer/uv.lock* ./

# Rewrite petritype source path for container
RUN sed -i 's|path = "../petritype"|path = "/petritype"|' pyproject.toml

# Install dependencies
RUN uv sync --frozen --no-dev || uv sync --no-dev

# Copy application code
COPY epimemer/epimemer/ epimemer/

# Default: start MCP server on stdio
CMD ["uv", "run", "python", "-m", "epimemer.mcp.server"]
