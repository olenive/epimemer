FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Dependencies first, so a code change does not reinstall PyTorch.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project --extra sentence-transformers

COPY epimemer/ epimemer/
RUN uv sync --frozen --no-dev --extra sentence-transformers

# The MCP server, on stdio.
CMD ["uv", "run", "--no-sync", "epimemer", "serve"]
