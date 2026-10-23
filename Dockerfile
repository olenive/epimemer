FROM python:3.14-slim

# The base tag floats, so the trust store is upgraded explicitly: a cached
# layer must not keep a bundle that trusts the emSign root whose intermediate
# keys were disclosed in October 2026.
RUN apt-get update \
    && apt-get install -y --no-install-recommends --only-upgrade ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /usr/local/bin/uv

WORKDIR /app

# Dependencies first, so a code change does not reinstall PyTorch.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project --extra sentence-transformers

COPY epimemer/ epimemer/
RUN uv sync --frozen --no-dev --extra sentence-transformers

# The MCP server, on stdio.
CMD ["uv", "run", "--no-sync", "epimemer", "serve"]
