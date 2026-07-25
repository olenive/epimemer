#!/usr/bin/env python
"""Measure Epimemer's cost as a graph grows.

ISSUES.md #14 documents a known scaling ceiling — full scans and per-node
round-trips in `list_sources`, `reflect` and `search` enrichment — and defers
the fix until "latency is felt". That trigger is only checkable if something
measures it, which is what this does.

Reuses the public tool functions (`segment_text` → `store_decomposition`,
`search`, `reflect`, `list_sources`) rather than touching storage directly, so
the numbers include the same enrichment work a real call pays for.

Embeddings are mocked **at the real model's width** (384 dimensions, the
`all-MiniLM-L6-v2` default) unless `--real-embeddings` is passed. Model
inference is a constant per text that would dominate and drown out the graph
costs this is built to expose; the vector *width* is kept because scan cost
scales with it. Numbers are therefore a floor: real ingest is slower by the
embedding time, real search by roughly one query embedding.

Usage:
    uv run python scripts/bench.py                      # mem://, N ∈ {100, 1000}
    uv run python scripts/bench.py --n 100,1000,10000
    uv run python scripts/bench.py --quick --n 10       # smoke test
    EPIMEMER_BENCH_URL=ws://localhost:8000/rpc uv run python scripts/bench.py

One JSON object per (operation, backend, n) is written to stdout; progress goes
to stderr, so `bench.py > run.jsonl` yields a clean record.
"""

import argparse
import asyncio
import json
import os
import random
import statistics
import sys
import time
from datetime import datetime, timezone

from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp.config import ServerConfig
from epimemer.mcp.tools import (
    graph_stats,
    list_sources,
    reflect,
    search,
    segment_text,
    store_decomposition,
)
from epimemer.storage.memory import InMemoryStorage
from epimemer.storage.surrealdb_adapter import SurrealDBStorage

_WORDS = (
    "memory graph topic fact inference segment vector embedding reflection decay "
    "context source relation timeline metacontext contradiction evidence"
).split()

# Each segment contributes this many nodes, so a target node count converts to
# a document count without ingesting and counting as we go.
_TOPICS_PER_SEGMENT = 1
_FACTS_PER_SEGMENT = 2
_INFERENCES_PER_SEGMENT = 1
_NODES_PER_SEGMENT = _TOPICS_PER_SEGMENT + _FACTS_PER_SEGMENT + _INFERENCES_PER_SEGMENT


def _sentence(rng: random.Random, words: int = 12) -> str:
    return " ".join(rng.choice(_WORDS) for _ in range(words)).capitalize() + "."


def _document(rng: random.Random, segments: int) -> str:
    """A document of `segments` paragraphs — the paragraph strategy splits on blank lines."""
    return "\n\n".join(" ".join(_sentence(rng) for _ in range(3)) for _ in range(segments))


def _decomposition(rng: random.Random, segment_ids: list[str]) -> list[dict]:
    """What an agent would send back for these segments."""
    return [
        {
            "segment_id": sid,
            "topics": [_sentence(rng, 4) for _ in range(_TOPICS_PER_SEGMENT)],
            "facts": [_sentence(rng, 8) for _ in range(_FACTS_PER_SEGMENT)],
            "inferences": [_sentence(rng, 8) for _ in range(_INFERENCES_PER_SEGMENT)],
        }
        for sid in segment_ids
    ]


def _percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile — no interpolation, so a p95 is always a real
    observation rather than a number nothing actually took."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(pct / 100 * len(ordered) + 0.5)) - 1)
    return ordered[index]


def _emit(record: dict) -> None:
    print(json.dumps(record), flush=True)


def _progress(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


async def _seed(storage, provider, config, *, docs: int, segments: int, rng) -> float:
    """Ingest `docs` documents. Returns elapsed seconds."""
    start = time.perf_counter()
    for i in range(docs):
        seg_result, _ = await segment_text(
            _document(rng, segments), storage, provider, config, source=f"bench-doc-{i}"
        )
        await store_decomposition(
            seg_result["document_id"],
            _decomposition(rng, [s["segment_id"] for s in seg_result["segments"]]),
            storage,
            provider,
        )
    return time.perf_counter() - start


async def _time_search(storage, provider, *, runs: int, rng) -> list[float]:
    latencies = []
    for _ in range(runs):
        query = _sentence(rng, 5)
        start = time.perf_counter()
        await search(query, storage, provider, k=10, graph_hops=1)
        latencies.append((time.perf_counter() - start) * 1000)
    return latencies


async def _timed(coro_factory) -> float:
    """Milliseconds for one call."""
    start = time.perf_counter()
    await coro_factory()
    return (time.perf_counter() - start) * 1000


async def _open_storage(backend: str, url: str | None, database: str):
    if backend == "memory":
        storage = InMemoryStorage()
    else:
        storage = SurrealDBStorage(url=url, database=database)
    await storage.connect()
    return storage


async def _run_one(
    *,
    backend: str,
    url: str | None,
    nodes: int,
    segments: int,
    searches: int,
    skip_reflect: bool,
    real_embeddings: bool,
    seed: int,
    common: dict,
) -> str | None:
    """Benchmark one (backend, size) point. Returns a created database name, if any."""
    rng = random.Random(seed)
    docs = max(1, round(nodes / (segments * _NODES_PER_SEGMENT)))
    database = f"bench_{nodes}_{int(time.time())}"

    if real_embeddings:
        from epimemer.embeddings.sentence_transformers import SentenceTransformersProvider

        provider = SentenceTransformersProvider()
    else:
        provider = MockEmbeddingProvider(model_id="bench-embed", dimension=384)

    config = ServerConfig(embedding_provider="mock", segmentation_strategy="paragraph")
    storage = await _open_storage(backend, url, database)
    tags = {**common, "backend": backend, "nodes_target": nodes, "documents": docs}

    try:
        _progress(f"[{backend}] seeding ~{nodes} nodes ({docs} docs × {segments} segments)...")
        elapsed = await _seed(storage, provider, config, docs=docs, segments=segments, rng=rng)
        stats, _ = await graph_stats(storage, default_reflect_threshold=10)
        _emit({
            **tags,
            "operation": "store_decomposition",
            "nodes_actual": stats["total_nodes"],
            "edges": stats["total_edges"],
            "seconds": round(elapsed, 3),
            "docs_per_min": round(docs / elapsed * 60, 1) if elapsed else None,
        })

        _progress(f"[{backend}] search ×{searches}...")
        latencies = await _time_search(storage, provider, runs=searches, rng=rng)
        _emit({
            **tags,
            "operation": "search",
            "runs": searches,
            "p50_ms": round(statistics.median(latencies), 2),
            "p95_ms": round(_percentile(latencies, 95), 2),
            "max_ms": round(max(latencies), 2),
        })

        _progress(f"[{backend}] list_sources...")
        _emit({
            **tags,
            "operation": "list_sources",
            "ms": round(await _timed(lambda: list_sources(storage)), 2),
        })

        if not skip_reflect:
            _progress(f"[{backend}] reflect (slowest step; minutes at 10k)...")
            _emit({
                **tags,
                "operation": "reflect",
                "ms": round(await _timed(lambda: reflect(storage, provider)), 2),
            })
    finally:
        await storage.close()

    return database if backend == "surrealdb" else None


async def _drop(url: str, databases: list[str]) -> None:
    """Remove the databases this run created. Prefix-guarded: it will not touch
    anything it did not name itself."""
    storage = SurrealDBStorage(url=url, database="bench_cleanup")
    await storage.connect()
    try:
        for name in databases:
            if not name.startswith("bench_"):
                continue
            await storage.delete_database(name)
            _progress(f"dropped {name}")
    finally:
        await storage.close()


async def _main(args) -> None:
    sizes = [int(n) for n in args.n.split(",")]
    backends = ["memory"]
    if args.url:
        backends.append("surrealdb")

    common = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "segments_per_doc": args.segments,
        "embeddings": "real" if args.real_embeddings else "mock-384",
    }
    created: list[str] = []

    for nodes in sizes:
        for backend in backends:
            name = await _run_one(
                backend=backend,
                url=args.url,
                nodes=nodes,
                segments=args.segments,
                searches=args.searches,
                skip_reflect=args.skip_reflect or args.quick,
                real_embeddings=args.real_embeddings,
                seed=args.seed,
                common=common,
            )
            if name:
                created.append(name)

    if created and not args.keep:
        await _drop(args.url, created)
    elif created:
        _progress(f"kept: {', '.join(created)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--n", default="100,1000", help="node counts, comma-separated")
    parser.add_argument("--segments", type=int, default=5, help="segments per document")
    parser.add_argument("--searches", type=int, default=20, help="search calls to time")
    parser.add_argument("--seed", type=int, default=1, help="corpus RNG seed")
    parser.add_argument(
        "--url",
        default=None,
        help="SurrealDB ws:// URL; also read from EPIMEMER_BENCH_URL. Adds a second backend.",
    )
    parser.add_argument("--quick", action="store_true", help="smoke run: skip reflect")
    parser.add_argument("--skip-reflect", action="store_true", help="skip the reflect timing")
    parser.add_argument(
        "--real-embeddings", action="store_true", help="use sentence-transformers"
    )
    parser.add_argument(
        "--keep", action="store_true", help="keep the SurrealDB databases this run creates"
    )
    args = parser.parse_args()

    if args.url is None:
        args.url = os.environ.get("EPIMEMER_BENCH_URL") or None
    if args.quick:
        args.searches = min(args.searches, 5)

    asyncio.run(_main(args))


if __name__ == "__main__":
    main()
