#!/usr/bin/env python
"""Measure Epimemer's cost as a graph grows.

batching documents a known scaling ceiling — full scans and per-node
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

The corpus is deliberately plain: no publisher attribution and no similarity
edges, which is what every figure recorded before 2026-08-20 was taken over.
`--publishers` and `--similarity-degree` add each, and exist because the
read-time annotations cost what the graph has *become* rather than what size it
is — corroboration walks the similarity neighbourhood and counts publishers, so
against the plain corpus it would measure an empty walk over nothing.

**Two corpora, and `diverse` is the default from 2026-08-29.** `--corpus
templated` draws sentences from a 17-word vocabulary, which makes each one a
near-restatement of every other and survives the pair scorer at ~1% under the
real model — two orders above real prose. `--corpus diverse` frames sentences
over wide slots instead, survives at ~0, and plants restatement clusters
(`--duplicate-groups`, `--duplicate-size`) so the surviving-pair population is
an **input** rather than a property of the generator nobody chose.

The default was `templated` for one day, on the grounds that every recorded
figure had been taken over it. That is the wrong side of the trade: a benchmark
should default to measuring the thing it exists to measure, and comparability
belongs in a labelled row rather than in a default nobody re-reads. Every
emitted record names its corpus, so a historical comparison is
`--corpus templated` and the old rows say which they were.

**Widening the vocabulary alone does not work, and was tried first
(2026-08-29).** Survival goes 1.13% → 0.0% between a 17-word bag and a 200-word
one and stays there at 2,000 and 20,000 — the dial steps straight over the
~0.01% real prose sits at, because what survives a pair scorer is shared
*phrasing*, and a random generator never restates anything. Planting is what
puts survivors back under control rather than under a vocabulary size.

**The corpus only reaches a score through the provider, so under the mock it
does not reach one at all.** The mock hashes text rather than reading it, and
its vectors sit in a band the corpus cannot move: measured 2026-08-29 at 1,200
facts, `templated` survives at 0.0573% and `diverse` at 0.0473% — the same
number twice, and both a fact about the hash. So the corpus choice is inert
without `--real-embeddings`, and a run that asks for planted clusters without
them is asking for something that cannot happen; that case says so on stderr.

Embedding cost is reported on its own as well. An ingest figure folds model
inference in with the graph work, and neither can be attributed without the
other.

Usage:
    uv run python scripts/bench.py                      # mem://, N ∈ {100, 1000}
    uv run python scripts/bench.py --n 100,1000,10000
    uv run python scripts/bench.py --quick --n 10       # smoke test
    EPIMEMER_BENCH_URL=ws://localhost:8000/rpc uv run python scripts/bench.py

    # the annotation costs behind BENCHMARKS.md's corroboration table
    uv run python scripts/bench.py --n 400,2000 --skip-reflect \
        --publishers 4 --similarity-degree 10

    # a corpus that survives the pair scorer like prose, with a known
    # population of near-duplicates planted in it
    uv run python scripts/bench.py --n 2400 --corpus diverse --real-embeddings \
        --duplicate-groups 60 --duplicate-size 10

    # where reflect's time goes, and the soundness check with something to compare
    uv run python scripts/bench.py --n 2400 --reflect-phases --dated-share 1.0

One JSON object per (operation, backend, n) is written to stdout; progress goes
to stderr, so `bench.py > run.jsonl` yields a clean record.
"""

import argparse
import asyncio
import inspect
import json
import os
import random
import statistics
import sys
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta

import numpy as np

from epimemer.core.temporal import (
    IntervalBasis,
    PreciseInstant,
    ValidityInterval,
)
from epimemer.core.types import (
    BASE_METACONTEXT_ID,
    EdgeType,
    Metacontext,
    NodeEdge,
    NodeType,
    Topic,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp.config import ServerConfig
from epimemer.mcp.tools import (
    _hierarchy_annotations,
    _metacontext_labels_for,
    graph_stats,
    list_sources,
    reflect,
    search,
    segment_text,
    store_decomposition,
)
from epimemer.pipelines.query.corroboration import corroboration_for
from epimemer.pipelines.query.validity import validity_for
from epimemer.pipelines.reflection.contradiction_detection import detect_contradictions
from epimemer.pipelines.reflection.pair_scoring import similar_pairs
from epimemer.pipelines.reflection.review import review_labels_for
from epimemer.pipelines.reflection.soundness import find_unsound_inferences
from epimemer.storage.memory import InMemoryStorage
from epimemer.storage.surrealdb_adapter import SurrealDBStorage
from epimemer.visualization.event_bus import InProcessEventBus
from epimemer.visualization.events import TransitionCompleted

_PUBLISHERS = ("Alpha Wire", "Beta Press", "Gamma Times", "Delta News")

_WORDS = (
    "memory graph topic fact inference segment vector embedding reflection decay "
    "context source relation timeline metacontext contradiction evidence"
).split()

# The diverse corpus: a frame with wide slots rather than a wider word bag. The
# slots multiply, so two independent draws rarely share enough phrasing to
# score, while every sentence stays ordinary English. Measured over 400 facts
# at the fact threshold (2026-08-29): 2 surviving pairs in 79,800 — 0.0025%,
# against 1.13% for the templated corpus and 0.0105% for real facts in a real
# graph. Restatements are planted rather than hoped for; see `_fact_pool`.
_NOUNS = """harbour orchard ledger glacier turbine sediment archive lantern quarry pasture
trawler seminar bursary aqueduct foundry marsh compass parchment vineyard causeway
kiln estuary almanac terrace bellows reservoir cloister granary lattice moraine
foothill spillway tannery hedgerow rookery saltpan windmill boathouse chapel toll
census cargo ferry bridge tunnel warehouse cellar dockyard smithy mill
survey charter levy tariff quota bursar warden steward provost curate""".split()

_ADJECTIVES = """coastal derelict provisional saline weathered municipal itinerant brackish
disused ornamental tidal seasonal fortified shallow overgrown temperate arid
communal wooden slate northern outlying disputed narrow steep gilded frozen
sparse crowded ancient rebuilt flooded quiet remote sunken vacant walled""".split()

# Past tense and intransitive, so both verb slots in a frame read as English.
_VERBS = """collapsed expanded flooded reopened silted froze burned thrived faltered drained
shifted split merged closed reappeared dwindled hardened settled cracked warmed
lapsed doubled vanished recovered stalled widened sank rose fractured cleared""".split()

_QUALIFIERS = (
    "after the long drought",
    "before the second survey",
    "during the winter closure",
    "under the new charter",
    "beyond the parish boundary",
    "within a single season",
    "against the surveyor's advice",
    "throughout the rebuilding",
    "despite the shortfall",
    "following the boundary dispute",
    "since the last audit",
    "near the old crossing",
)


def _diverse_topic(rng: random.Random) -> str:
    return (
        f"{rng.choice(_ADJECTIVES)} {rng.choice(_NOUNS)} and "
        f"{rng.choice(_ADJECTIVES)} {rng.choice(_NOUNS)}"
    )


def _diverse_fact(rng: random.Random) -> str:
    return (
        f"The {rng.choice(_ADJECTIVES)} {rng.choice(_NOUNS)} {rng.choice(_VERBS)} "
        f"while the {rng.choice(_ADJECTIVES)} {rng.choice(_NOUNS)} {rng.choice(_VERBS)} "
        f"{rng.choice(_QUALIFIERS)}."
    )


def _diverse_inference(rng: random.Random) -> str:
    return (
        f"Because the {rng.choice(_ADJECTIVES)} {rng.choice(_NOUNS)} {rng.choice(_VERBS)}, "
        f"the {rng.choice(_ADJECTIVES)} {rng.choice(_NOUNS)} {rng.choice(_VERBS)} "
        f"{rng.choice(_QUALIFIERS)}."
    )


# Which generator each corpus uses for each kind of text. Data rather than a
# branch per call site: the corpus reaches four places, and adding a third is a
# block here instead of four `if`s spread over the file. `prose` is the document
# body — what the segmenter splits, never what gets embedded.
_GENERATORS: dict[str, dict[str, Callable[[random.Random], str]]] = {
    "templated": {
        "topic": lambda rng: _sentence(rng, 4),
        "fact": lambda rng: _sentence(rng, 8),
        "inference": lambda rng: _sentence(rng, 8),
        "prose": lambda rng: _sentence(rng, 12),
    },
    "diverse": {
        "topic": _diverse_topic,
        "fact": _diverse_fact,
        "inference": _diverse_inference,
        "prose": _diverse_fact,
    },
}

CORPORA: tuple[str, ...] = tuple(_GENERATORS)

# One source's wording turned into another's. Substitutions only — a
# restatement has to stay the same claim, or the pair it forms is not the pair
# a duplicate corpus is being built to produce.
_RESTATEMENTS = {
    "the": "that",
    "while": "as",
    "after": "following",
    "before": "ahead of",
    "during": "through",
    "despite": "in spite of",
    "because": "since",
    "within": "inside",
    "beyond": "past",
    "near": "close to",
}

# Each segment contributes this many nodes, so a target node count converts to
# a document count without ingesting and counting as we go.
_TOPICS_PER_SEGMENT = 1
_FACTS_PER_SEGMENT = 2
_INFERENCES_PER_SEGMENT = 1


def _nodes_per_segment(facts: int) -> int:
    return _TOPICS_PER_SEGMENT + facts + _INFERENCES_PER_SEGMENT


def _sentence(rng: random.Random, words: int = 12) -> str:
    return " ".join(rng.choice(_WORDS) for _ in range(words)).capitalize() + "."


def _document(rng: random.Random, segments: int, corpus: str) -> str:
    """A document of `segments` paragraphs — the paragraph strategy splits on blank lines."""
    prose = _GENERATORS[corpus]["prose"]
    return "\n\n".join(" ".join(prose(rng) for _ in range(3)) for _ in range(segments))


def _restate(text: str, rng: random.Random) -> str:
    """The same claim as a second source would put it.

    The first word is left alone: substituting it costs the sentence its capital
    and makes the restatement look like a different *kind* of text rather than
    the same claim again, which is the one thing this must not do.
    """
    words = text.split()
    return " ".join(
        words[:1]
        + [
            _RESTATEMENTS.get(word.lower(), word) if rng.random() < 0.7 else word
            for word in words[1:]
        ]
    )


def _planted_pairs(pool: int, groups: int, size: int) -> int:
    """Surviving pairs the planted clusters contribute, by construction.

    Arithmetic rather than a measurement, and the point of planting: a corpus
    whose survivor count is known before the run can be varied deliberately,
    where one that emerges from a vocabulary size can only be discovered
    afterwards.
    """
    if groups <= 0 or size < 2:
        return 0
    fits = min(groups, pool // size)
    return fits * size * (size - 1) // 2


def _fact_pool(rng: random.Random, *, count: int, corpus: str, groups: int, size: int) -> list[str]:
    """Every fact this run will ingest, restatement clusters already placed.

    Built up front rather than per document because a cluster spans documents —
    the case being modelled is one claim reported by several sources, which is
    exactly what a per-document generator cannot produce.

    Planting is a `diverse`-corpus move. The templated corpus already survives
    at ~1% by accident, so clusters planted in it would be lost against a floor
    nobody chose, and the flags are ignored rather than pretending otherwise.
    """
    make = _GENERATORS[corpus]["fact"]
    texts = [make(rng) for _ in range(count)]
    if corpus != "diverse" or groups <= 0 or size < 2:
        return texts
    fits = min(groups, count // size)
    positions = rng.sample(range(count), fits * size)
    for start in range(0, fits * size, size):
        cluster = positions[start : start + size]
        original = texts[cluster[0]]
        for index in cluster[1:]:
            texts[index] = _restate(original, rng)
    return texts


def _fact_stream(
    rng: random.Random, *, count: int, corpus: str, groups: int, size: int
) -> Iterator[str]:
    """The planted pool, then more of the same.

    The pool is sized from the segment count the run asked for. A segmenter that
    returns more segments than that should not end the run, and the tail is
    unplanted by construction — every cluster is in the pool's head.
    """
    yield from _fact_pool(rng, count=count, corpus=corpus, groups=groups, size=size)
    make = _GENERATORS[corpus]["fact"]
    while True:
        yield make(rng)


def _decomposition(
    rng: random.Random,
    segment_ids: list[str],
    corpus: str,
    facts: Iterator[str],
    facts_per_segment: int,
) -> list[dict]:
    """What an agent would send back for these segments.

    `facts_per_segment` is also **premises per inference**: the decomposition
    links a segment's one inference to that segment's facts, so raising it is
    the only way to reach the part of the soundness check that is quadratic in
    a single inference's dated premises.
    """
    generate = _GENERATORS[corpus]
    return [
        {
            "segment_id": sid,
            "topics": [generate["topic"](rng) for _ in range(_TOPICS_PER_SEGMENT)],
            "facts": [next(facts) for _ in range(facts_per_segment)],
            "inferences": [generate["inference"](rng) for _ in range(_INFERENCES_PER_SEGMENT)],
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


async def _seed(
    storage,
    provider,
    config,
    *,
    docs: int,
    segments: int,
    publishers: int,
    corpus: str,
    duplicate_groups: int,
    duplicate_size: int,
    facts_per_segment: int,
    rng,
    fact_rng,
) -> float:
    """Ingest `docs` documents. Returns elapsed seconds.

    `publishers` spreads the documents over that many `published_by` entities;
    0 attributes nothing, which is the corpus every figure recorded before
    2026-08-20 was taken over. Attribution is what corroboration counts, so a
    run measuring it needs some.

    **Facts draw from their own stream**, and that is what makes a planted run
    comparable with an unplanted one. Planting consumes randomness — a sample
    and a substitution per restated word — so on a shared stream every topic,
    inference and document body after the first cluster would differ too, and a
    reflect timing put beside its unplanted twin would be comparing two corpora
    rather than one corpus with and without duplicates.
    """
    # Every ingest names its frame, so the graph needs one. A real graph gets
    # this once from `create_metacontext`; a benchmark builds its own world.
    await storage.store_metacontext(
        Metacontext(
            id=BASE_METACONTEXT_ID,
            content="The Real",
            description="Claims about the real world.",
        )
    )
    entities: dict[str, Topic] = {}
    facts = _fact_stream(
        fact_rng,
        count=docs * segments * facts_per_segment,
        corpus=corpus,
        groups=duplicate_groups,
        size=duplicate_size,
    )
    start = time.perf_counter()
    for i in range(docs):
        seg_result, _ = await segment_text(
            _document(rng, segments, corpus),
            storage,
            provider,
            config,
            source=f"bench-doc-{i}",
        )
        if publishers:
            # Resolve-or-create by exact name, as `_upsert_entity_topic` does —
            # a fresh Topic per document would make every document its own
            # publisher and the count meaningless.
            name = _PUBLISHERS[i % min(publishers, len(_PUBLISHERS))]
            if name not in entities:
                entities[name] = Topic(
                    content=name, source_id=None, extraction_method="agent:source"
                )
                await storage.store_node(entities[name])
            await storage.store_edge(
                NodeEdge(
                    src_id=seg_result["document_id"],
                    dst_id=entities[name].id,
                    type=EdgeType.RELATED,
                    label="published_by",
                    kind="attribution",
                )
            )
        await store_decomposition(
            seg_result["document_id"],
            _decomposition(
                rng,
                [s["segment_id"] for s in seg_result["segments"]],
                corpus,
                facts,
                facts_per_segment,
            ),
            storage,
            provider,
            metacontext_id=BASE_METACONTEXT_ID,
        )
    return time.perf_counter() - start


async def _wire_similarity(storage, *, degree: int, rng) -> int:
    """Give each fact `degree` similarity partners. Returns the edge count.

    Stands in for what `apply_reflection` writes over a reflected-over graph.
    **Synthetic, and the distinction matters**: the degree is a dial here, not
    an observation, so it shows how a cost scales with edge density without
    saying what density a real graph reaches.
    """
    if degree <= 0:
        return 0
    facts = list(await storage.query_nodes(node_type=NodeType.FACT))
    if len(facts) < 2:
        return 0
    written = 0
    for fact in facts:
        for partner in rng.sample(facts, min(degree, len(facts))):
            if partner.id == fact.id:
                continue
            await storage.store_edge(
                NodeEdge(
                    src_id=fact.id,
                    dst_id=partner.id,
                    type=EdgeType.SIMILARITY,
                )
            )
            written += 1
    return written


async def _time_annotations(storage, provider, *, runs: int, rng) -> dict:
    """Each read-time annotation `search` applies, over a real result set.

    Timed separately from `search` itself because they are the part that grows
    with what the graph has *become* rather than with its size — and because
    corroboration is opt-in on exactly this evidence. The node
    set is whatever a real `search(k=10, graph_hops=1)` returns, expansion
    included, since that is what the annotations are handed.
    """
    result, _ = await search(_sentence(rng, 5), storage, provider, k=10, graph_hops=1)
    node_ids = [n["id"] for n in result["nodes"]]
    nodes = list((await storage.get_nodes(node_ids)).values())

    async def median_ms(factory) -> float:
        return round(statistics.median([await _timed(factory) for _ in range(runs)]), 3)

    return {
        "result_set": len(node_ids),
        "hierarchy_ms": await median_ms(lambda: _hierarchy_annotations(nodes, storage)),
        "metacontexts_ms": await median_ms(lambda: _metacontext_labels_for(node_ids, storage)),
        "review_labels_ms": await median_ms(lambda: review_labels_for(nodes, storage)),
        "validity_ms": await median_ms(lambda: validity_for(node_ids, storage)),
        "corroboration_ms": await median_ms(lambda: corroboration_for(node_ids, storage)),
    }


async def _wire_validity(storage, *, share: float, rng) -> int:
    """Give a share of documents a period, and their facts the same one.

    **Every `reflect` figure recorded before 2026-08-29 was taken over an
    undated corpus**, where the soundness phase returns at its first check: with
    nothing dated there is nothing to compare, and its whole measured cost is
    the reads that discover that. Its pairwise part is quadratic in a single
    inference's *dated* premises, and over an undated graph that part has never
    run at all — which is a very different thing from having run cheaply.

    Dated **per document rather than per fact**, so that facts read from one
    source share a period. Dating each fact independently would make almost
    every premise pair disjoint and flag nearly every inference, which measures
    an alarm rather than the comparison the alarm sits behind.

    Intervals go on the `sourced_from` edge because that is where a period
    lives: an interval is one source's assertion, so it hangs off the edge
    naming that source.

    Synthetic, and the same kind of dial `--similarity-degree` is — the spread
    of dates is chosen here and observed nowhere.
    """
    if share <= 0:
        return 0
    facts = list(await storage.query_nodes(node_type=NodeType.FACT))
    edges = await storage.get_edges_for(
        [fact.id for fact in facts], direction="from", edge_type=EdgeType.SOURCED_FROM
    )
    epoch = datetime(2000, 1, 1, tzinfo=UTC)
    periods: dict[str, tuple[datetime, datetime]] = {}
    dated = 0
    for node_edges in edges.values():
        for edge in node_edges:
            if rng.random() >= share:
                continue
            if edge.dst_id not in periods:
                start = epoch + timedelta(days=rng.randrange(0, 3650))
                periods[edge.dst_id] = (start, start + timedelta(days=365))
            start, end = periods[edge.dst_id]
            edge.validity = [
                ValidityInterval(
                    start=PreciseInstant(at=start),
                    end=PreciseInstant(at=end),
                    basis=IntervalBasis.STATED,
                )
            ]
            await storage.store_edge(edge)
            dated += 1
    return dated


async def _time_embedding(provider, *, corpus: str, runs: int, rng) -> dict:
    """What the embedding provider costs on its own, apart from the ingest it
    hides inside.

    Every ingest figure here folds model inference in with the graph work, so
    neither can be attributed without this: a slow ingest is a slow model or a
    slow graph, and one number cannot say which.

    Three batch sizes because the provider batches. Per-text cost falls as a
    batch fills, and a reading taken at one size cannot say where it stops
    falling — which is the number that decides whether ingest should batch
    harder.

    One warm-up call first. A real provider loads its model lazily, and that
    load would otherwise be charged to whichever batch happened to run first.
    """
    make = _GENERATORS[corpus]["fact"]
    await provider.embed([make(rng)])
    sizes = (1, 32, 256)
    measured = {}
    for size in sizes:
        texts = [make(rng) for _ in range(size)]
        ms = statistics.median(
            [await _timed(lambda texts=texts: provider.embed(texts)) for _ in range(runs)]
        )
        measured[f"batch_{size}"] = {
            "ms": round(ms, 3),
            "ms_per_text": round(ms / size, 4),
            "texts_per_sec": round(size / ms * 1000, 1) if ms else None,
        }
    return measured


def _fact_threshold() -> float:
    """The threshold `reflect` scores fact pairs at.

    Read off the function that owns it rather than written down again here. A
    number kept in two places is one that will disagree with itself, and a
    survival rate reported against a threshold nothing uses is worse than no
    survival rate at all.
    """
    return inspect.signature(detect_contradictions).parameters["similarity_threshold"].default


async def _corpus_survival(storage, *, planted_pairs: int, dated_facts: int) -> dict:
    """What this run's corpus looks like to the scorer `reflect` uses.

    Recorded on **every** run, because a cost figure whose corpus is not
    recorded beside it cannot be checked against a later one. The templated
    corpus's survival rate stood unquestioned until something measured it, and
    that is the failure this line closes: the corpus stops being an assumption
    the reader has to reconstruct from flags.

    Reads the stored vectors rather than re-embedding, so the scores are the
    ones `reflect` sees. `read_ms` is separated from `score_ms` because on a
    networked backend the read is the part that binds.
    """
    facts = list(await storage.query_nodes(node_type=NodeType.FACT))
    # What the soundness phase will actually find, recorded beside the cost it
    # will be charged. An undated corpus flags nothing *and compares nothing*,
    # and a soundness timing with no such line beside it cannot say which.
    unsound = len(await find_unsound_inferences(storage))
    start = time.perf_counter()
    records = await storage.get_embeddings_for_items([fact.id for fact in facts])
    read_ms = (time.perf_counter() - start) * 1000

    vectors = np.array([rows[0].vector for rows in records.values() if rows], dtype=np.float64)
    threshold = _fact_threshold()
    count = len(vectors)
    pairs = count * (count - 1) // 2
    if pairs == 0:
        return {
            "items": count,
            "pairs": 0,
            "dated_facts": dated_facts,
            "unsound_inferences": unsound,
            "read_ms": round(read_ms, 2),
        }

    start = time.perf_counter()
    survivors = similar_pairs(vectors, threshold)
    score_ms = (time.perf_counter() - start) * 1000
    return {
        "items": count,
        "threshold": threshold,
        "pairs": pairs,
        "survivors": len(survivors),
        "survival_rate_pct": round(len(survivors) / pairs * 100, 4),
        # What planting put there. The gap between this and `survivors` is what
        # the generator contributed on its own — the quantity a corpus built by
        # widening a vocabulary can only discover after the fact.
        "planted_pairs": planted_pairs,
        "dated_facts": dated_facts,
        "unsound_inferences": unsound,
        "read_ms": round(read_ms, 2),
        "score_ms": round(score_ms, 2),
    }


async def _reflect_phases(storage, provider) -> dict:
    """Where `reflect`'s time goes, phase by phase.

    A **second**, watched run rather than instrumentation of the first. The
    plain `reflect` figure is the one every recorded number was taken as, and a
    bus subscriber inside it would fold publishing into a timing nothing else
    pays. `reflect` reads only, so the second run does the same work.

    This is what makes a phase's share readable on a backend other than the
    in-memory one, without a profiler. Three batched reads are arithmetic in
    process and three round-trips over a network, so a share measured in one
    place transfers to the other only by accident — and the same subscriber
    answers in both.
    """
    bus = InProcessEventBus()
    durations: dict[str, float] = {}

    async def record(event: TransitionCompleted) -> None:
        durations[event.transition_name] = round(event.duration_ms, 2)

    bus.subscribe(TransitionCompleted, handler=record)
    total = await _timed(lambda: reflect(storage, provider, event_bus=bus))
    return {
        "watched_ms": round(total, 2),
        # Shares rather than only milliseconds: the milliseconds are this
        # machine's, and the share is the part that survives being read on
        # another one.
        "phases": {
            name: {"ms": ms, "share_pct": round(ms / total * 100, 1) if total else None}
            for name, ms in durations.items()
        },
    }


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


async def _open_storage(backend: str, url: str | None, namespace: str, database: str):
    """Open one backend.

    `namespace` defaults away from `epimemer` on purpose. This script creates
    and drops databases, and pointing it at a server holding real graphs should
    not put throwaway ones in the same namespace as them — the prefix guard in
    `_drop` protects against deleting the wrong database, not against writing in
    the wrong place.
    """
    if backend == "memory":
        storage = InMemoryStorage()
    else:
        storage = SurrealDBStorage(url=url, namespace=namespace, database=database)
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
    reflect_phases: bool,
    real_embeddings: bool,
    namespace: str,
    publishers: int,
    similarity_degree: int,
    dated_share: float,
    facts_per_segment: int,
    corpus: str,
    duplicate_groups: int,
    duplicate_size: int,
    seed: int,
    common: dict,
) -> str | None:
    """Benchmark one (backend, size) point. Returns a created database name, if any."""
    rng = random.Random(seed)
    fact_rng = random.Random(f"facts:{seed}")
    docs = max(1, round(nodes / (segments * _nodes_per_segment(facts_per_segment))))
    database = f"bench_{nodes}_{int(time.time())}"

    if real_embeddings:
        from epimemer.embeddings.sentence_transformers import SentenceTransformersProvider

        provider = SentenceTransformersProvider()
    else:
        provider = MockEmbeddingProvider(model_id="bench-embed", dimension=384)

    config = ServerConfig(embedding_provider="mock", segmentation_strategy="paragraph")
    storage = await _open_storage(backend, url, namespace, database)
    tags = {**common, "backend": backend, "nodes_target": nodes, "documents": docs}

    try:
        _progress(f"[{backend}] seeding ~{nodes} nodes ({docs} docs × {segments} segments)...")
        elapsed = await _seed(
            storage,
            provider,
            config,
            docs=docs,
            segments=segments,
            publishers=publishers,
            corpus=corpus,
            duplicate_groups=duplicate_groups,
            duplicate_size=duplicate_size,
            facts_per_segment=facts_per_segment,
            rng=rng,
            fact_rng=fact_rng,
        )
        similarity_edges = await _wire_similarity(storage, degree=similarity_degree, rng=rng)
        dated_facts = await _wire_validity(storage, share=dated_share, rng=rng)
        stats, _ = await graph_stats(storage, default_reflect_threshold=10)
        _emit(
            {
                **tags,
                "operation": "store_decomposition",
                "nodes_actual": stats["total_nodes"],
                "edges": stats["total_edges"],
                "seconds": round(elapsed, 3),
                "docs_per_min": round(docs / elapsed * 60, 1) if elapsed else None,
            }
        )

        _progress(f"[{backend}] corpus survival...")
        _emit(
            {
                **tags,
                "operation": "corpus",
                **await _corpus_survival(
                    storage,
                    planted_pairs=_planted_pairs(
                        docs * segments * facts_per_segment,
                        duplicate_groups,
                        duplicate_size,
                    ),
                    dated_facts=dated_facts,
                ),
            }
        )

        _progress(f"[{backend}] embedding throughput...")
        _emit(
            {
                **tags,
                "operation": "embedding",
                "runs": 3,
                **await _time_embedding(provider, corpus=corpus, runs=3, rng=rng),
            }
        )

        _progress(f"[{backend}] search ×{searches}...")
        latencies = await _time_search(storage, provider, runs=searches, rng=rng)
        _emit(
            {
                **tags,
                "operation": "search",
                "runs": searches,
                "p50_ms": round(statistics.median(latencies), 2),
                "p95_ms": round(_percentile(latencies, 95), 2),
                "max_ms": round(max(latencies), 2),
            }
        )

        _progress(f"[{backend}] list_sources...")
        _emit(
            {
                **tags,
                "operation": "list_sources",
                "ms": round(await _timed(lambda: list_sources(storage)), 2),
            }
        )

        _progress(f"[{backend}] read-time annotations...")
        _emit(
            {
                **tags,
                "operation": "annotations",
                "runs": min(searches, 15),
                "similarity_edges": similarity_edges,
                **await _time_annotations(storage, provider, runs=min(searches, 15), rng=rng),
            }
        )

        if not skip_reflect:
            _progress(f"[{backend}] reflect (slowest step; minutes at 10k)...")
            _emit(
                {
                    **tags,
                    "operation": "reflect",
                    "ms": round(await _timed(lambda: reflect(storage, provider)), 2),
                }
            )
            if reflect_phases:
                _progress(f"[{backend}] reflect again, watched, for the phase split...")
                _emit(
                    {
                        **tags,
                        "operation": "reflect_phases",
                        **await _reflect_phases(storage, provider),
                    }
                )
    finally:
        await storage.close()

    return database if backend == "surrealdb" else None


async def _drop(url: str, namespace: str, databases: list[str]) -> None:
    """Remove the databases this run created. Prefix-guarded: it will not touch
    anything it did not name itself."""
    storage = SurrealDBStorage(url=url, namespace=namespace, database="bench_cleanup")
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
        "timestamp": datetime.now(UTC).isoformat(),
        "segments_per_doc": args.segments,
        "embeddings": "real" if args.real_embeddings else "mock-384",
        "publishers": args.publishers,
        "similarity_degree": args.similarity_degree,
        "dated_share": args.dated_share,
        "facts_per_segment": args.facts_per_segment,
        "corpus": args.corpus,
        "duplicate_groups": args.duplicate_groups,
        "duplicate_size": args.duplicate_size,
    }
    created: list[str] = []

    # The corpus only reaches the scores through the provider, and the mock
    # provider hashes whole strings: a planted restatement is a different string
    # and therefore an unrelated vector. Warned on the *planting* rather than on
    # the corpus, now that `diverse` is the default: a note on every default run
    # is one nobody reads, and the corpus is inert under the mock either way.
    # Planting is the case where the caller asked for something specific that
    # will not happen, which is worth interrupting for.
    if args.duplicate_groups > 0 and not args.real_embeddings:
        _progress(
            "note: planted clusters need --real-embeddings. The mock provider "
            "hashes text rather than reading it, so a restatement is simply a "
            "different string and no planted pair will survive. Survival in "
            "this run is the hash's geometry, not the corpus's."
        )

    for nodes in sizes:
        for backend in backends:
            name = await _run_one(
                backend=backend,
                url=args.url,
                nodes=nodes,
                segments=args.segments,
                searches=args.searches,
                skip_reflect=args.skip_reflect or args.quick,
                reflect_phases=args.reflect_phases,
                real_embeddings=args.real_embeddings,
                namespace=args.namespace,
                publishers=args.publishers,
                similarity_degree=args.similarity_degree,
                dated_share=args.dated_share,
                facts_per_segment=args.facts_per_segment,
                corpus=args.corpus,
                duplicate_groups=args.duplicate_groups,
                duplicate_size=args.duplicate_size,
                seed=args.seed,
                common=common,
            )
            if name:
                created.append(name)

    if created and not args.keep:
        await _drop(args.url, args.namespace, created)
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
        "--reflect-phases",
        action="store_true",
        help=(
            "run reflect a second time, watched, and report each phase's share. "
            "Costs a second reflect, which is why it is not always on — but it "
            "is the only way to read a phase's share on a backend where the "
            "reads are round-trips."
        ),
    )
    parser.add_argument("--real-embeddings", action="store_true", help="use sentence-transformers")
    parser.add_argument(
        "--namespace",
        default="epimemer_bench",
        help=(
            "SurrealDB namespace for the throwaway databases. Deliberately not "
            "`epimemer`, which is where real graphs live."
        ),
    )
    parser.add_argument(
        "--publishers",
        type=int,
        default=0,
        help=(
            "spread documents over this many published_by entities (max 4). "
            "0, the default, attributes nothing — the corpus every figure "
            "recorded before 2026-08-20 was taken over. Corroboration counts "
            "publishers, so a run measuring it wants some."
        ),
    )
    parser.add_argument(
        "--similarity-degree",
        type=int,
        default=0,
        help=(
            "similarity partners per fact, standing in for a reflected-over "
            "graph. Synthetic: a dial on edge density, not an observation of "
            "one. 0 by default, again to leave the historical corpus unchanged."
        ),
    )
    parser.add_argument(
        "--facts-per-segment",
        type=int,
        default=_FACTS_PER_SEGMENT,
        help=(
            "facts each segment decomposes to, which is also premises per "
            f"inference. {_FACTS_PER_SEGMENT} is what every recorded figure was "
            "taken at; raising it with --dated-share is what reaches the "
            "soundness check's quadratic part, which two premises cannot."
        ),
    )
    parser.add_argument(
        "--dated-share",
        type=float,
        default=0.0,
        help=(
            "share of facts given a period their source asserts. 0, the "
            "default, is the undated corpus every figure recorded before "
            "2026-08-29 was taken over — and on which the soundness phase "
            "returns before comparing anything, so its pairwise cost is "
            "unmeasured rather than small."
        ),
    )
    parser.add_argument(
        "--corpus",
        choices=CORPORA,
        default="diverse",
        help=(
            "which text to generate. 'diverse', the default from 2026-08-29, "
            "frames sentences over wide slots and survives the pair scorer at "
            "~0, like real prose. 'templated' is the 17-word vocabulary every "
            "figure recorded before that date was taken over, and survives at "
            "~1%% under the real model — pass it to reproduce one of them."
        ),
    )
    parser.add_argument(
        "--duplicate-groups",
        type=int,
        default=0,
        help=(
            "restatement clusters to plant in a 'diverse' corpus — one claim "
            "reported by several sources, which is the near-duplicate case no "
            "ingested corpus here has ever contained. Each contributes "
            "size×(size-1)/2 surviving pairs by construction. 0 by default: "
            "planting is the worst case, and a real graph's facts survive "
            "at ~0.01%%, so a planted default would misrepresent an ordinary "
            "graph as surely as the 17-word corpus did. Ignored for "
            "'templated', which already survives at ~1%% by accident."
        ),
    )
    parser.add_argument(
        "--duplicate-size",
        type=int,
        default=5,
        help="facts per planted cluster; 5 is a claim with five sources",
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
