"""Reciprocal Rank Fusion for the query layer.

Retrieval now has more than one arm, and their scores cannot be compared.
Cosine similarity and BM25 live on different scales with no calibration between
them, and BM25's own scale is per index — IDF's N counts one table — so even the
lexical arm's several lists are mutually incomparable
(`dev-docs/LEXICAL_SEARCH.md` §10, R5).

Ranks are the only thing that may cross those boundaries, and RRF is the fusion
that consumes nothing else. It has no weight to tune, which is the argument for
it: any weighted sum of a similarity and a BM25 score is a magic number that
gets re-tuned forever, by whoever is looking at whichever query disappointed
them that week.
"""

from collections.abc import Iterable, Sequence

# The conventional RRF constant. Larger flattens the contribution curve — the
# gap between rank 1 and rank 2 shrinks as k grows — and 60 is the value the
# literature settled on. There is no reason to depart from it here, and a
# tunable would reintroduce exactly the knob this fusion exists to avoid.
_K = 60


def rrf_scores(rankings: Sequence[Sequence[str]], *, k: int = _K) -> dict[str, float]:
    """Fuse ranked id lists into one score per id. `Σ 1/(k + rank)`, rank 1-based.

    A rank-1 hit contributes `1/61`, the most any single list can give — so an
    exact identifier match arrives near the top even though the other arm never
    saw it. It is not *guaranteed* the top, and that matters: an id present in
    two lists at ranks 10 and 11 scores `1/70 + 1/71`, which beats `1/61`.
    Agreement between independent arms outweighing one arm's best guess is
    correct behaviour, and it is also why `fuse` takes `protected` — see there.

    An id absent from a list simply contributes nothing for it; there is no
    penalty and no imputed rank.
    """
    scores: dict[str, float] = {}
    for ranking in rankings:
        for position, item_id in enumerate(ranking, start=1):
            scores[item_id] = scores.get(item_id, 0.0) + 1 / (k + position)
    return scores


def fuse(
    rankings: Sequence[Sequence[str]],
    *,
    limit: int,
    protected: Iterable[str] = (),
    k: int = _K,
) -> list[str]:
    """Fused ids, best first, cut to `limit` — plus any `protected` id it found.

    **`protected` is a deliberate departure from pure RRF** (R2). A term the
    caller *declared* is a statement of intent, not a guess, so its best hit
    survives the top-k cut. Without it the guarantee fails exactly where the
    feature is most needed: when every ticket id in the graph embeds alike, the
    vector list is ten near-identical nodes that also appear in the lexical
    list, each of them outscoring the one exact match, which then lands at rank
    11 and falls off a top-10 result.

    A rescued id keeps its fused position rather than being promoted to the
    front — protection is about presence, not ranking, and ranking it above
    better-supported results would be the weighted-sum problem arriving by a
    different door. It also *extends* the result rather than displacing
    anything: the alternative is silently dropping a legitimate hit to make
    room. The overflow is bounded by the number of declared terms.

    A protected id that appears in no ranking is not invented. Protection
    rescues a hit that was scored and cut, never one nobody found.

    Ties break on id. RRF ties are common — every id alone at the same rank in
    its own list scores identically — and the arbitrary order the engines
    return would otherwise make two runs of one search disagree.
    """
    scores = rrf_scores(rankings, k=k)
    ordered = sorted(scores, key=lambda item_id: (-scores[item_id], item_id))

    kept = ordered[:limit]
    rescued = [
        item_id for item_id in dict.fromkeys(protected) if item_id in scores and item_id not in kept
    ]
    if not rescued:
        return kept
    return sorted([*kept, *rescued], key=lambda item_id: (-scores[item_id], item_id))
