"""BM25 lexical scoring, and the analyzer every backend agrees on.

Vector search has no notion of term rarity. `all-MiniLM-L6-v2` splits
`JIRA-4417` into word pieces and mean-pools them with the rest of the sentence,
so the query embeds to roughly "short alphanumeric string" — which is close to
every *other* ticket id in the graph. BM25's IDF is exactly the missing
statistic (`dev-docs/LEXICAL_SEARCH.md` §1).

SurrealDB scores in-engine. This module is what the in-memory backend uses so
the two can be siblings rather than a feature and its absence, and it is where
the arithmetic is pinned to numbers measured against the real engine
(`tests/storage/test_bm25.py`).

Two things it deliberately does not do:

- **It does not stem.** The engine runs `snowball(english)`; reproducing
  Snowball exactly is not achievable, and a partial stemmer disagrees in both
  directions rather than one. The guarantee the backends share is set parity on
  *rare* terms — identifiers, error codes, names — which no stemmer touches.
- **It builds no index.** Every call re-analyzes the corpus it is given. A
  cached index would need invalidating on every write, and the backend this
  serves is the ephemeral one; a stale lexical index is a worse bug than a
  linear scan is a cost.
"""

import math
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence

# Okapi BM25's term-frequency saturation (k1) and length-normalisation (b)
# parameters, at the values SurrealDB's `BM25` index default to. They are named
# rather than inlined because changing either silently re-ranks every lexical
# result, and `TestEngineParity` is what would catch it.
_K1 = 1.2
_B = 0.75


def _character_class(char: str) -> str | None:
    """The token class a character belongs to, or None for whitespace.

    Mirrors SurrealDB's `class` tokenizer, which cuts a token wherever the
    character class changes: `JIRA-4417` becomes `jira`, `-`, `4417`, and the
    rare piece is separable from the common prefix. Everything that is not a
    letter, a digit or whitespace is one class, so a run like `.,;` is a single
    token — verified against `search::analyze`.
    """
    if char.isspace():
        return None
    if char.isalpha():
        return "alpha"
    if char.isdigit():
        return "digit"
    return "symbol"


def _fold(token: str) -> str:
    """Lowercase, and strip combining marks so `Café` matches `cafe`.

    The engine's `ascii` filter goes further and drops what it cannot fold. This
    keeps it: a Cyrillic or CJK token that folds to nothing would make non-Latin
    content lexically unreachable here, and disagreeing with the engine about a
    corpus neither of them ranks well is the cheaper of the two errors.
    """
    decomposed = unicodedata.normalize("NFKD", token.lower())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def contains_term(text: str, term: str) -> bool:
    """Whether `text` holds `term` literally, up to the analyzer's folding.

    The adjacency signal the full-text index cannot give. A conjunctive token
    match asks only whether every token of `JIRA-4417` is somewhere in the
    document, which "the JIRA migration hit error 4417 -" satisfies; there are
    no phrase queries on either engine, so containment of the original string is
    what separates the ticket from the coincidence (`LEXICAL_SEARCH.md` §10, R8).

    Folded, but deliberately **not stemmed** — exactness is unstemmed by
    definition, and this is the one place where the backends' disagreement about
    stemming cannot reach: the check runs on the same folded strings in both.

    Not a search in its own right. It only ever partitions documents the token
    match already returned, which is what keeps `JIRA-44170` — a containing
    string and the wrong ticket — out: `44170` is not the token `4417`, so it is
    never a candidate to check.
    """
    return _fold(term) in _fold(text)


def containment_first(
    candidates: Sequence[tuple[str, float]],
    documents: Mapping[str, str],
    terms: Sequence[str],
) -> list[tuple[str, float]]:
    """R8's ordering: documents holding a term literally, then the rest.

    `candidates` is whatever the token match returned, scored and *unfiltered* —
    the floored rows included, because a floored row is exactly what containment
    is here to rescue. Containing rows keep their score for the record but do
    not depend on it; the remainder still obeys the zero rule.

    Lives here, in the module both backends already share, rather than being
    written out twice. One rule stated in one place and re-derived, differently,
    somewhere else is the shape of half the bugs this project has found.
    """
    containing = [
        hit
        for hit in candidates
        if any(contains_term(documents.get(hit[0], ""), term) for term in terms)
    ]
    exact = {doc_id for doc_id, _ in containing}
    token_only = [hit for hit in candidates if hit[0] not in exact and hit[1] > 0.0]
    for half in (containing, token_only):
        half.sort(key=lambda hit: (-hit[1], hit[0]))
    return [*containing, *token_only]


def analyze(text: str) -> list[str]:
    """`text` as the tokens BM25 scores over.

    The same function analyzes documents and query terms, which is the point:
    whatever it gets wrong, it gets wrong identically on both sides.
    """
    tokens: list[str] = []
    current: list[str] = []
    current_class: str | None = None

    for char in text:
        char_class = _character_class(char)
        if char_class != current_class and current:
            tokens.append("".join(current))
            current = []
        current_class = char_class
        if char_class is not None:
            current.append(char)
    if current:
        tokens.append("".join(current))

    return [folded for token in tokens if (folded := _fold(token))]


def _inverse_document_frequency(n_documents: int, n_matching: int) -> float:
    """How much a term's presence discriminates, floored at nothing.

    The classic unsmoothed form, `log((N - n + 0.5) / (n + 0.5))`, which crosses
    zero at `n = N/2` and would go negative above it. **Not** the `1 +` smoothed
    variant that Python BM25 recipes overwhelmingly use: that never reaches zero,
    and the zero is the whole point. A term more common than half the corpus
    says nothing about which document you want, so it contributes nothing —
    which is the score floor this system would otherwise have had to invent and
    tune (`LEXICAL_SEARCH.md` §2.5).

    SurrealDB 3.0.5 clamps here too. The embedded engine the tests run against
    returns the negative value instead, which is why the truncation callers
    apply is `score > 0` rather than `score != 0`: it agrees with both.
    """
    return max(0.0, math.log((n_documents - n_matching + 0.5) / (n_matching + 0.5)))


def _term_frequency_weight(
    occurrences: int, document_length: int, average_length: float, k1: float, b: float
) -> float:
    """Saturating term frequency, normalised for document length."""
    if average_length <= 0:
        return 0.0
    normalised = 1 - b + b * document_length / average_length
    return occurrences * (k1 + 1) / (occurrences + k1 * normalised)


def bm25_scores(
    documents: Mapping[str, str],
    terms: Sequence[str],
    *,
    k1: float = _K1,
    b: float = _B,
) -> dict[str, float]:
    """BM25 score per document for any of `terms`, keyed by document id.

    A **term is a conjunction**: `"JIRA-4417"` analyzes to three tokens and a
    document matches it only by containing all three, which is what separates
    4417 from 4418. **Terms are ORed**, and a term absent from the whole corpus
    contributes zero rather than excluding everything — the trap in §2.4, where
    one unmatched word in a multi-word query returns no lexical hits at all and
    the fused search degrades silently to vector-only.

    Documents matching no term are **absent** from the result. Documents that
    match but score `0.0` are **present**, because the clamp above zeroes the
    score and not the membership: callers drop them (R1), and a scorer that
    filtered here would leave no way to tell "matched, but says nothing" from
    "did not match".

    `documents` is the whole corpus partition, at any status. IDF is a statement
    about the corpus, and SurrealDB's index counts every row in the table
    regardless of status — so filtering before scoring would make the two
    backends compute different numbers from the same graph.
    """
    if not documents or not terms:
        return {}

    analyzed = {doc_id: analyze(text) for doc_id, text in documents.items()}
    term_tokens = [tokens for term in terms if (tokens := analyze(term))]
    if not term_tokens:
        return {}

    n_documents = len(analyzed)
    average_length = sum(len(tokens) for tokens in analyzed.values()) / n_documents
    document_frequency = Counter(
        token for tokens in analyzed.values() for token in set(tokens)
    )
    idf = {
        token: _inverse_document_frequency(n_documents, document_frequency[token])
        for tokens in term_tokens
        for token in tokens
    }

    scores: dict[str, float] = {}
    for doc_id, tokens in analyzed.items():
        occurrences = Counter(tokens)
        matched = [
            token
            for term in term_tokens
            if all(token in occurrences for token in term)
            for token in term
        ]
        if not matched:
            continue
        scores[doc_id] = sum(
            idf[token]
            * _term_frequency_weight(
                occurrences[token], len(tokens), average_length, k1, b
            )
            for token in matched
        )
    return scores
