#!/usr/bin/env python
"""Measure a real graph's text against the two things nobody had numbers for.

`bench.py` measures cost against a *synthetic* corpus — random words from a
17-word vocabulary — which is the right corpus for timing graph operations and
the wrong one for two open questions that depend on what real text looks like:

- **the embedding-window measurement** — `all-MiniLM-L6-v2` truncates at 256 word-pieces and
  nothing guards it. The entry says to take the token-length distribution over
  a real graph before choosing between its four options.
- **the nomination cap** — `reflect`'s candidate pair lists are quadratic and
  unbounded. Its projection to gigabytes borrows a 49% survival rate from a
  corpus the entry itself calls degenerate in this exact dimension.

Both want the same input, so this takes it once: a graph of real ingested text.

**Read-only by construction, and that is not incidental.** The only real graphs
on a developer's machine live in the `epimemer` namespace, which nothing may
write to. `SurrealDBStorage.connect()` defines tables and runs the FTS backfill
, so this reaches the rows over HTTP `SELECT` instead and never opens a
storage backend at all. A measurement that modified what it measured would be
worthless twice over.

**It reads the stored vectors rather than re-embedding.** They were written by
the real model at ingest (`model_id` is checked, not assumed), so the survival
rate is the distribution `reflect` actually sees rather than one re-derived
here under possibly different normalization.

Usage:
    uv run python scripts/corpus_measure.py --database memory
    uv run python scripts/corpus_measure.py --database memory,petritype-server
    uv run python scripts/corpus_measure.py --database memory --skip-survival

One JSON object per measurement is written to stdout; progress goes to stderr.
"""

import argparse
import base64
import inspect
import json
import statistics
import sys
import urllib.error
import urllib.request

import numpy as np

from epimemer.pipelines.reflection.contradiction_detection import detect_contradictions
from epimemer.pipelines.reflection.pair_scoring import similar_pairs
from epimemer.pipelines.reflection.topic_consolidation import find_similar_topic_pairs

# The model `SentenceTransformersProvider` defaults to, and the window the embedding-window measurement is
# about. Read from the model itself when the survival pass loads it; this is
# the value used for the tokenizer-only pass, where loading the full model to
# read one integer is not worth the seconds.
MODEL_NAME = "all-MiniLM-L6-v2"
WINDOW = 256

# ~580 bytes per surviving pair, measured directly in the nomination cap across the scored
# tuples, the nominated list and the response dicts.
BYTES_PER_PAIR = 580

# Which tables hold text, and under which field. Segments are separate from
# nodes throughout because the embedding-window measurement names them separately — they are a second corpus
# with a different length distribution, and averaging them together would hide
# whichever one is at risk.
NODE_TABLES = ("fact", "inference", "topic")
SEGMENT_TABLE = "segment"


def _threshold(function, name: str = "similarity_threshold") -> float:
    """The threshold this function actually defaults to.

    Read from the signature rather than restated here. A number copied out of
    the code and into a measuring instrument is the repo's recurring defect
    class — a rule stated in one place and re-derived, differently, somewhere
    else — and it fails silently, by reporting a rate for a threshold nothing
    uses.
    """
    return inspect.signature(function).parameters[name].default


def _thresholds() -> dict[str, float]:
    """The two thresholds `reflect` scores with, per table."""
    return {
        "fact": _threshold(detect_contradictions),
        "topic": _threshold(find_similar_topic_pairs),
    }


def _progress(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _emit(record: dict) -> None:
    print(json.dumps(record), flush=True)


def _sql(url: str, user: str, password: str, namespace: str, database: str, query: str):
    """One read-only SQL statement. Raises rather than returning a partial result."""
    credentials = base64.b64encode(f"{user}:{password}".encode()).decode()
    request = urllib.request.Request(
        f"{url}/sql",
        data=query.encode(),
        headers={
            "Accept": "application/json",
            "Authorization": f"Basic {credentials}",
            "surreal-ns": namespace,
            "surreal-db": database,
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.load(response)
    if not payload or payload[0].get("status") != "OK":
        raise RuntimeError(f"query failed: {payload}")
    return payload[0]["result"]


def _texts(sql, table: str, field: str) -> list[str]:
    rows = sql(f"SELECT {field} FROM {table};")
    return [row[field] for row in rows if row.get(field)]


def _priors(sql) -> dict:
    """How many supplied priors arrive carrying a reason (the confidence prior's open question).

    `confidence` is a prior the agent supplies and `confidence_basis` is the one
    line saying why. The basis is asked for by tool guidance rather than enforced
    at the boundary, and the accepted risk was that absence would then mean
    nothing — *no basis given* and *guidance not read* being indistinguishable.
    This is what tells the two apart, and the fallback if guidance is not
    producing them is refusal at the tool boundary.

    Three populations, and only the first owes a basis:

    - **rated non-default** — a supplied 0.3/0.7/0.9. Guidance asks for a line.
    - **unrated** — the field absent, which is what the ladder says to store
      when there is no specific reason to doubt or trust. Owes nothing.
    - **legacy 0.5** — written before the confidence prior landed on 2026-08-19, when the field
      was a non-nullable default. These read as *rated ordinary* though nobody
      rated them, which is the carry-over the confidence prior left behind.

    `confidence_basis` lives in `node.metadata`, deliberately apart from the
    numbers every ranker reads (`tools.py`) — so a query looking for it beside
    `value.confidence`, where it reads as though it belongs, finds nothing and
    reports a confident zero.
    """
    tables = ",".join(NODE_TABLES)
    rows = sql(
        f"SELECT count() AS n, value.confidence AS confidence, "
        f"metadata.confidence_basis IS NOT NONE AS basis "
        f"FROM {tables} GROUP BY confidence, basis;"
    )
    counts = {"rated_non_default": 0, "with_basis": 0, "unrated": 0, "legacy_default": 0}
    for row in rows:
        confidence, count = row.get("confidence"), row["n"]
        if confidence is None:
            counts["unrated"] += count
        elif confidence == 0.5:
            counts["legacy_default"] += count
        else:
            counts["rated_non_default"] += count
            if row.get("basis"):
                counts["with_basis"] += count
    owed = counts["rated_non_default"]
    return {
        **counts,
        "nodes": sum(counts[key] for key in ("rated_non_default", "unrated", "legacy_default")),
        "basis_pct": round(100.0 * counts["with_basis"] / owed, 2) if owed else None,
    }


def _distribution(lengths: list[int], window: int) -> dict:
    """Where the lengths sit, and how many cross the window.

    Percentiles are nearest-rank, matching `bench.py`: a reported p95 is a
    length something actually has rather than an interpolation between two.
    """
    if not lengths:
        return {"count": 0}
    ordered = sorted(lengths)

    def rank(pct: float) -> int:
        index = min(len(ordered) - 1, int(round(pct / 100 * len(ordered) + 0.5)) - 1)
        return ordered[index]

    over = [n for n in ordered if n > window]
    return {
        "count": len(ordered),
        "min": ordered[0],
        "median": int(statistics.median(ordered)),
        "p90": rank(90),
        "p95": rank(95),
        "p99": rank(99),
        "max": ordered[-1],
        "over_window": len(over),
        "over_window_pct": round(len(over) / len(ordered) * 100, 2),
        # How much is lost where anything is lost. Nothing else reports this,
        # and "3 nodes affected" reads very differently at 260 tokens than at
        # 2,000 — the first loses a clause, the second loses most of the text.
        "worst_lost_pct": (
            round(max((n - window) / n for n in over) * 100, 1) if over else 0.0
        ),
    }


def _vectors(sql, table: str) -> tuple[np.ndarray, int]:
    """Stored vectors for one table's nodes, and how many nodes had none.

    Joins on the embedding table by `item_id`, which is how the storage layer
    keys them. Nodes without a stored vector are counted rather than
    substituted: `reflect` cannot score what it cannot embed either, so an
    absent vector is a real reduction in the candidate set, not a gap to fill.
    """
    ids = [row["uid"] for row in sql(f"SELECT uid FROM {table};") if row.get("uid")]
    if not ids:
        return np.zeros((0, 0)), 0
    rows = sql(
        f"SELECT item_id, vector FROM embedding WHERE model_id = '{MODEL_NAME}';"
    )
    by_item = {row["item_id"]: row["vector"] for row in rows if row.get("vector")}
    found = [by_item[i] for i in ids if i in by_item]
    return np.array(found, dtype=np.float64), len(ids) - len(found)


def _survival(vectors: np.ndarray, threshold: float) -> dict:
    """The share of pairs clearing `threshold`, and what that projects to.

    Uses `similar_pairs` — the same function `reflect` scores with — so the
    zero-vector rule and the blocking are whatever the real path does.
    """
    n = len(vectors)
    total = n * (n - 1) // 2
    if total == 0:
        return {"items": n, "pairs": 0}
    survivors = similar_pairs(vectors, threshold)
    rate = len(survivors) / total
    scores = [score for _, _, score in survivors]
    return {
        "items": n,
        "threshold": threshold,
        "pairs": total,
        "survivors": len(survivors),
        "survival_rate_pct": round(rate * 100, 4),
        "top_score": round(max(scores), 4) if scores else None,
        "median_score": round(statistics.median(scores), 4) if scores else None,
        # What the measured rate says about the sizes the nomination cap projects to. The rate
        # is the measurement; these are arithmetic on it, and are labelled as
        # projections so nobody reads them as observations.
        "projected": {
            str(facts): {
                "pairs": round(facts * (facts - 1) / 2 * rate),
                "mb": round(facts * (facts - 1) / 2 * rate * BYTES_PER_PAIR / 1e6, 1),
            }
            for facts in (2_000, 5_000, 10_000)
        },
    }


def _score_spread(vectors: np.ndarray) -> dict:
    """Where *all* pairs sit, not just the survivors.

    A survival rate alone cannot say whether a corpus sits far below the
    threshold or just under it, and those project differently: a corpus whose
    p99.9 is 0.55 would need the threshold halved before pair counts mattered,
    while one at 0.79 is a rounding error away from a very different number.
    """
    if len(vectors) < 2:
        return {}
    unit = vectors / np.where(
        np.linalg.norm(vectors, axis=1, keepdims=True) == 0.0, 1.0,
        np.linalg.norm(vectors, axis=1, keepdims=True),
    )
    scores = (unit @ unit.T)[np.triu_indices(len(unit), k=1)]
    return {
        f"score_p{pct}": round(float(np.percentile(scores, pct)), 4)
        for pct in (50, 90, 99, 99.9)
    } | {"score_max": round(float(scores.max()), 4)}


def _scaling(vectors: np.ndarray, threshold: float, *, seed: int = 1) -> list[dict]:
    """Survival rate at growing subset sizes of the same corpus.

    The projection in the nomination cap multiplies a measured rate by a pair count, which is
    only sound if the rate does not itself move with size. It might: as a graph
    fills in one domain its facts grow more mutually similar, and a rate rising
    with n would make the projection an underestimate by an unknown factor.
    Three sizes rather than two, for the reason ISSUES.md already records about
    fitting exponents — the short fit reads fixed effects as curvature.
    """
    rng = np.random.default_rng(seed)
    n = len(vectors)
    rows = []
    for size in (s for s in (50, 100, 200, 400, 800) if s <= n):
        subset = vectors[rng.choice(n, size=size, replace=False)]
        pairs = size * (size - 1) // 2
        rows.append({
            "items": size,
            "pairs": pairs,
            "survivors": len(similar_pairs(subset, threshold)),
            "survival_rate_pct": round(
                len(similar_pairs(subset, threshold)) / pairs * 100, 4
            ),
        })
    return rows


def _synthetic_control(threshold: float, *, count: int, seed: int = 1) -> dict:
    """The same measurement over `bench.py`'s own generated text.

    This is the control the whole of the nomination cap turns on. Its projection multiplies a
    **49% survival rate for real `all-MiniLM-L6-v2` on "similarly templated
    text"** by a real graph's fact count — but the templated text is sentences
    drawn from a 17-word vocabulary, so a high rate there may say more about
    the generator than about the model. Measuring both corpora through one
    model at one threshold is the only way to tell which.

    Imports the generator from `bench.py` rather than reimplementing it, so
    "the bench corpus" means the corpus the bench actually uses.
    """
    import random
    sys.path.insert(0, str(__file__.rsplit("/", 1)[0]))
    from bench import _sentence

    from epimemer.embeddings.sentence_transformers import SentenceTransformersProvider

    rng = random.Random(seed)
    texts = [_sentence(rng, 8) for _ in range(count)]
    provider = SentenceTransformersProvider()
    import asyncio
    vectors = np.array(asyncio.run(provider.embed(texts)), dtype=np.float64)
    return {
        "measurement": "synthetic_control", "corpus": "bench-templated",
        "embeddings": "real", **_survival(vectors, threshold),
        **_score_spread(vectors),
    }


def _token_lengths(texts: list[str]) -> list[int]:
    """Word-piece lengths as `model.encode` would count them.

    `add_special_tokens=True` matches it: [CLS] and [SEP] are inside the 256, so
    the content budget is really 254 and a text is cut two tokens earlier than a
    naive count suggests.
    """
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(f"sentence-transformers/{MODEL_NAME}")
    return [
        len(tokenizer.encode(text, add_special_tokens=True, truncation=False))
        for text in texts
    ]


def _measure(sql, database: str, *, skip_survival: bool) -> dict[str, list[str]]:
    """Emit this database's measurements; return its texts for the pooled pass."""
    _emit({"measurement": "priors", "database": database, **_priors(sql)})

    _progress(f"[{database}] tokenizing...")
    texts = {table: _texts(sql, table, "content") for table in NODE_TABLES}
    texts["segment"] = _texts(sql, SEGMENT_TABLE, "text")

    for corpus, corpus_texts in texts.items():
        _emit({
            "measurement": "truncation", "database": database, "corpus": corpus,
            "window": WINDOW, **_distribution(_token_lengths(corpus_texts), WINDOW),
        })

    if skip_survival:
        return texts

    for table, threshold in _thresholds().items():
        _progress(f"[{database}] scoring {table} pairs at {threshold}...")
        vectors, missing = _vectors(sql, table)
        _emit({
            "measurement": "survival", "database": database, "corpus": table,
            "without_vectors": missing,
            **_survival(vectors, threshold), **_score_spread(vectors),
        })
    return texts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--url", default="http://localhost:8000", help="SurrealDB HTTP URL")
    parser.add_argument("--namespace", default="epimemer", help="namespace to read")
    parser.add_argument(
        "--database", default="memory", help="database(s) to read, comma-separated"
    )
    parser.add_argument("--user", default="root")
    parser.add_argument("--password", default="root")
    parser.add_argument(
        "--skip-survival", action="store_true", help="token lengths only"
    )
    parser.add_argument(
        "--synthetic-control",
        type=int,
        default=0,
        help=(
            "also score this many bench-generated sentences through the real "
            "model, as a control on the 49%% figure the nomination cap's projection uses"
        ),
    )
    args = parser.parse_args()

    databases = [name.strip() for name in args.database.split(",")]
    pooled: dict[str, list[np.ndarray]] = {table: [] for table in _thresholds()}
    pooled_texts: dict[str, list[str]] = {}

    for database in databases:
        def sql(query: str, database: str = database):
            return _sql(
                args.url, args.user, args.password, args.namespace, database, query
            )

        try:
            for corpus, texts in _measure(
                sql, database, skip_survival=args.skip_survival
            ).items():
                pooled_texts.setdefault(corpus, []).extend(texts)
            if not args.skip_survival:
                for table in pooled:
                    vectors, _ = _vectors(sql, table)
                    if len(vectors):
                        pooled[table].append(vectors)
        except (urllib.error.URLError, RuntimeError) as error:
            _progress(f"[{database}] unreachable or empty: {error}")

    # Pooled truncation, emitted whenever more than one graph was read. The
    # per-database rows are the honest unit — this one exists because the
    # question the embedding-window measurement asks ("does real node text reach the window?") is about the
    # text, not about which graph it came from, and a pooled p95 is what a
    # decision about the embedding path would be taken against.
    if len(databases) > 1:
        for corpus, texts in pooled_texts.items():
            _emit({
                "measurement": "truncation", "database": "+".join(databases),
                "corpus": corpus, "window": WINDOW,
                **_distribution(_token_lengths(texts), WINDOW),
            })

    if args.synthetic_control:
        _progress(f"[control] embedding {args.synthetic_control} bench sentences...")
        _emit(_synthetic_control(
            _thresholds()["fact"], count=args.synthetic_control
        ))

    # Pooled across every graph read, purely to widen the range the scaling
    # check has to work with. Pooling two graphs is not itself a realistic
    # corpus — it is two corpora — but the question here is whether the rate
    # moves with n, and for that a wider n is worth more than a purer one.
    for table, chunks in pooled.items():
        if not chunks:
            continue
        vectors = np.vstack(chunks)
        threshold = _thresholds()[table]
        _progress(f"[pooled] {table} scaling at {threshold} over {len(vectors)}...")
        _emit({
            "measurement": "scaling", "database": "+".join(databases),
            "corpus": table, "threshold": threshold, "items": len(vectors),
            "steps": _scaling(vectors, threshold), **_score_spread(vectors),
        })


if __name__ == "__main__":
    main()
