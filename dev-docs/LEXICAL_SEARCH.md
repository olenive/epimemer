# Lexical search: BM25 alongside vector retrieval

Keyword retrieval inside `search`, fused with the vector path by rank. BM25
rather than exact-token lookup, over nodes and segments both, and fused into
`search` rather than exposed as a new tool. Declared `terms` drive the lexical
arm, with a conservative statistical fallback when they are omitted, and
zero-scored matches never reach fusion. The rules are in §10.

---

## 1. The problem

Vector search alone cannot find a node reachable only by a rare token. The
default embedding model is `all-MiniLM-L6-v2`, 384-dim. WordPiece splits an
identifier like `JIRA-4417` into fragments, which are then mean-pooled with
every other token in the sentence. The identifier contributes almost no
distinguishing signal, and the query `"JIRA-4417"` embeds to approximately
"short alphanumeric string", which is close to every other ticket id in the
graph. The failure is not that the right node ranks low; it is that the wrong
ticket ids rank about equally high. Cosine similarity has no notion of term
rarity, which is exactly what BM25's IDF supplies.

The only other text lookup in the storage protocol is `get_node_by_content`:
full-string equality, used to upsert entity nodes by name. `find_nodes` is
pure graph traversal.

### 1.1 The upstream half, and why segments are indexed too

`store_decomposition` is agent-driven: the agent writes the fact content. If
it paraphrases "ticket JIRA-4417 was closed" into "the deployment ticket was
closed", the identifier never enters the graph and no search of any kind
recovers it from nodes.

`RawDocument` and `Segment` both retain raw text, so segments are indexed as
a second corpus, and the two answer different questions:

| Corpus | Question it answers |
|---|---|
| Nodes | "What do I believe?" |
| Segments | "Where did I read that?" |

A rare identifier is almost always the second question. This is the half of
the feature that closes the stated gap.

Segments bridge back: a node's `source_id` is its `Segment.id`, so a segment
hit yields the nodes extracted from that segment (`get_nodes_by_source`).
That is the paraphrase case handled end to end: *the id is in segment S; here
is what we concluded from S*.

---

## 2. What the engine does

Measured against a SurrealDB 3.0.5 server. Several of these contradict the
2.x documentation. The embedded `mem://` core the Python SDK ships is an
older engine and differs again; §11 has the differences.

**2.1 The index keyword is `FULLTEXT` on 3.x.** 2.x used `SEARCH`, and each
engine rejects the other's spelling. `_setup_schema` negotiates the dialect.

**2.2 Defining the index backfills existing rows.** A row created before
`DEFINE INDEX` is found by a later search, so existing graphs need no
migration step. The index is built the first time `_setup_schema` runs
against them (§5 has the cost).

**2.3 The analyzer shreds identifiers, which is what makes them findable.**

```
search::analyze("epimemer_text", "Ticket JIRA-4417 E_TIMEOUT_503")
  → ["ticket", "jira", "-", "4417", "e", "_", "timeout", "_", "503"]
```

`4417` is a rare token with high IDF, so it discriminates. Searching
`"JIRA-4417"` against a corpus containing both `JIRA-4417` and `JIRA-4418`
returns only the former. The punctuation tokens are noise but harmless: they
appear in nearly every document, so their IDF is zero (§2.5).

**2.4 A single `@@` match is conjunctive.** Every term must be present, so a
multi-word prose query fused naively would return zero lexical hits and
degrade to vector-only. OR semantics need one match reference per term, with
the scores summed:

```sql
SELECT id, search::score(1) + search::score(2) + search::score(3) AS score
FROM fact
WHERE content @1@ "deployment" OR content @2@ "rollback" OR content @3@ "ticket"
ORDER BY score DESC;
```

**2.5 The server's BM25 uses classic IDF, clamped at zero.** A term in half
the documents or more scores exactly `0.0`. That fits
`log((N - n + 0.5) / (n + 0.5))`, zero at `n = N/2`, negative above and
clamped. A term more common than half the corpus contributes nothing, so
lexical search naturally says nothing about prose queries built from common
words, and the score floor that would otherwise need inventing and tuning
comes from the engine. Lexical supplies precision on rare tokens; vector
supplies recall on prose. They fail in opposite directions, which is the
argument for fusing them.

The clamp zeroes the *score*, not the *match*: a term in 3 of 4 documents
still returns all three rows, tied at `0.0`, in nondeterministic order. RRF
sees only ranks, so a zero-scored row at arbitrary tie rank 2 would fuse at
`1/62`, nearly equal to the best vector hit's `1/61`. The floor is only
useful if those rows are dropped, which is rule R1.

The corpus for IDF is per table. Nodes live in three tables (`topic`, `fact`,
`inference`), each with its own index, so the same term with the same hit
count scores `0.0` in a 4-row table and `0.9615` in a 10-row table. Rule R5
follows from that.

---

## 3. Where it plugs into the query net

The net is named `hybrid_retrieval`, and lexical search is what makes the
name honest:

```
                  ┌─> run_vector_search  ─> [VectorResults]  ─┐
[QueryRequest] ───┤                                           ├─> fuse_seeds ─>
                  └─> run_lexical_search ─> [LexicalResults] ─┘
                          [Seeds] ─> run_graph_expansion ─> [ExpandedResults] ─> …
```

Two independent transitions off the same place. The Petri runner gives
concurrency for free, and neither branch can change what the other computes.
Both branches' seed ids survive to the end, which is what §6's provenance
needs.

### 3.1 Fusion: RRF, not score blending

Cosine similarity and BM25 are on incomparable scales with no calibration
between them. Any weighted sum is a magic number that gets re-tuned forever.
Reciprocal Rank Fusion uses only ranks:

```python
def rrf_scores(rankings: Sequence[Sequence[str]], *, k: int = 60) -> dict[str, float]:
    """Fuse ranked id lists. Score = Σ 1/(k + rank), rank 1-based."""
```

Pure, testable by inspection, no tuning surface. `k=60` is the conventional
value. `rankings` is plural for a stronger reason than vector-versus-lexical:
the lexical arm contributes one ranked list per node table plus one for
segments (R5), because BM25 scores from different tables are not on the same
scale, and ranks are the only thing that may cross a table boundary.

Pure RRF does not guarantee an exact identifier match lands in the top few.
Any node present in both lists scores at least `1/70 + 1/71 ≈ 0.0284`,
beating a lexical-only rank-1 at `1/61 ≈ 0.0164`, and the motivating
scenario, a graph where every ticket id embeds alike, is exactly where overlap
peaks. So the guarantee is made explicit: each declared term's top hit
survives to the final result (R2), a documented departure from pure RRF.

---

## 4. Storage protocol, and the cross-backend problem

```python
async def text_search(
    self,
    terms: Sequence[str],
    *,
    corpus: Literal["nodes", "segments"],
    k: int = 10,
    node_type: NodeType | None = None,
    statuses: frozenset[NodeStatus] = frozenset({NodeStatus.ACTIVE}),
    verify_containment: bool = False,
) -> Sequence[tuple[str, float]]:
```

Ids and BM25 scores for documents matching any term, best first. Scores are
strictly positive unless `verify_containment` rescued an exact hit (R8). One
call scores one corpus partition: when `corpus="nodes"`, `node_type` is
required (R5). `statuses` mirrors `vector_search`'s default so the two seed
routes cannot disagree about whether a node exists (R7), and is ignored for
segments, which have no status.

Taking pre-split `terms` rather than a query string puts tokenisation on the
caller, so both backends agree on what the terms *are* before they disagree
about anything else. Two more methods serve the segment bridge:
`get_nodes_by_source(source_ids)`, batched, and `get_segments(ids)`, because
§6's `segments` response key needs segment text.

**Parity, stated honestly.** SurrealDB scores in-engine with its own analyzer
(`class` tokenizer, `lowercase`, `ascii`, `snowball(english)` filters). The
memory backend implements BM25 in Python and does not stem: Snowball is not
reproducible in-tree, and a partial stemmer disagrees in both directions. So
`"deployments"` finds `"deployment"` on SurrealDB and not in memory. Exact
score parity is not achievable and no test asserts it. What the tests assert:

- **Set parity** on unambiguous queries: a rare term returns the same id set
  on both backends.
- **Order parity within one list**: the rare-term hit outranks the
  common-term hit on both. No test compares order across node types, because
  no such order exists (R5).
- **The zero rule, per type**: a term in more than half of one table's rows
  scores `0.0` on both. This is the one formula detail that must be copied
  deliberately, because Python BM25 recipes overwhelmingly use the
  `+1`-smoothed IDF that never reaches zero. The memory backend partitions
  its corpus by node type to match the tables (R6).

This is a real narrowing of the guarantee `get_nodes` states for itself ("a
batching of cost, not of answer"). Lexical search is the one protocol method
where the backends genuinely differ, and saying so here beats pretending
otherwise.

---

## 5. SurrealDB schema

In `_setup_schema`, which runs on every connect and is idempotent via
`IF NOT EXISTS`:

```sql
DEFINE ANALYZER IF NOT EXISTS epimemer_text
  TOKENIZERS class
  FILTERS lowercase, ascii, snowball(english);

-- per node table, plus segment on its `text` field
DEFINE INDEX IF NOT EXISTS idx_{table}_fts ON {table}
  FIELDS content FULLTEXT ANALYZER epimemer_text BM25;
```

Index definition backfills (§2.2), and `_setup_schema` runs inside
`connect()`, which has no progress reporting. Measured (3.0.5, median of 3,
documents = nodes + segments): 2,000 in 1.0 s; 6,000 in 3.8 s; 20,000 in
19 s. Steady-state connect stays around 30 ms. It happens exactly once per
graph, somewhere the user cannot see; `ISSUES.md` carries the trigger for
doing something about it.

---

## 6. What `search` returns

Seeds arrive by three routes, and each returned node carries its provenance:

```python
class SeedProvenance(StrEnum):
    VECTOR = "vector"  # embedding similarity
    LEXICAL = "lexical"  # BM25 on node content
    SEGMENT = "segment"  # BM25 on a segment, bridged via source_id
    EXPANDED = "expanded"  # pulled in by graph expansion from a seed
```

`RETRIEVAL_PROVENANCE.md` adds `direct` for tools that return nodes without
ranking them. Exactness (R8) is not exposed here: an exact-containing hit is
still `lexical`.

Segment hits also surface directly, under their own key, since a segment is
not a graph node and must not be pretended into one:

```python
result = {
    "nodes": [...],  # each with `provenance` and, where scored, `score`
    "segments": [...],  # id, text, source document, BM25 score
    "edges": [...],
}
```

---

## 7. Tests

`tests/storage/test_bm25.py`, `tests/mcp/`, and the integration suite. Two
assertion shapes matter:

- The headline test, `test_search_finds_an_identifier_vector_search_cannot`,
  asserts in provenance terms: `JIRA-4417`'s fact is present as a lexical
  seed and `JIRA-4418`'s is not a lexical seed. At `graph_hops ≥ 2` the
  near-miss is legitimately reachable by expansion, and the vector arm may
  legitimately seed it too. It deliberately stays on the fallback path: the
  identifier is a rare token and R3 must find it unaided.
- OR-across-terms is asserted against `text_search` directly, never through
  fused `search`, where the vector arm would let a conjunctive implementation
  pass vacuously.

Storage protocol changes mean `make test-integration SURREAL_PORT=8123` runs
alongside the unit suite.

---

## 8. Rejected

- **Exact-token lookup instead of BM25.** No ranking, and no way to say a
  common word contributes nothing.
- **A separate `lexical_search` tool.** The agent would have to guess which
  tool to call; fusion makes one call do both.
- **Score blending.** Incomparable scales (§3.1).
- **Query length as the "is this an identifier" heuristic.** "find JIRA-123"
  and "deployment problems yesterday" are the same length with opposite
  intents. Per-term corpus rarity, which the engine already computes, is the
  statistic that separates them; declared terms are the reliable path (§10).
- **A custom analyzer filter to drop punctuation tokens.** Their IDF is
  already zero.

---

## 9. Knock-on effects

- **Archival.** Nodes findable only by lexical query used to be permanent
  `never_retrieved` candidates. Making them findable changes what gets
  archived, arguably a fix, but a behaviour change.
- **`retrieved_at`.** `_record_retrieval` stamps whatever nodes came back,
  whichever route they arrived by.
- **Ranking feedback.** Retrieval must not feed ranking. RRF takes ranks from
  vector and BM25 only: no use-count, no popularity term.
- **Embedding truncation.** `all-MiniLM-L6-v2` truncates at 256 word-pieces.
  Measured, no node comes near it (81 word-pieces at worst), and segment
  text, which does cross it, is never embedded at all: BM25 is the only thing
  that reads segments, not a mitigation for a truncation that happens to them.

---

## 10. The rules

Who decides that a token is load-bearing? Inferring it server-side from query
shape is guessing, and the caller is an agent, not a search box: it can be
asked.

- **R1, the zero-score rule.** Lexical hit lists truncate to `score > 0`
  before fusion, on both backends, as `text_search`'s contract. The IDF clamp
  then means what §2.5 wants it to mean: common terms contribute nothing,
  including membership.
- **R2, declared terms.** `search` takes an optional `terms: list[str]`.
  Declared terms are authoritative statements of intent: each declared term's
  top-scoring hit survives to the final result, even past the top-k cut. Under
  R5 the guarantee is per list: it can protect up to one node per type, plus
  the segment bridge, because there is no cross-table score with which to pick
  a single winner. Cost shape: one `text_search` per declared term per
  partition, since per-term attribution is what the guarantee requires.
- **R3, the fallback.** With `terms` omitted, the lexical arm runs over the
  query's own tokens with R1 applied: rare tokens fire, common ones vanish,
  no invented threshold, and no R2 protection. An agent that types the
  identifier into the query without declaring it still mostly benefits; the
  reliable path is declaring. Known residue: mid-frequency terms in long prose
  queries survive R1 and can, under OR-sum, outvote a single rare term. That
  is the cost of not declaring, and the docstring says so.
- **R4, the docstring teaches the pattern.** The `search` tool description
  tells the agent to pass identifiers, names, and exact phrases it cares
  about as `terms`.
- **R5, one list per table.** The lexical arm produces one ranked list per
  node table plus one for segments; fusion consumes lists and only ranks
  cross a table boundary. `text_search(corpus="nodes")` requires `node_type`;
  a merged multi-type list is forbidden by contract because the scores it
  would sort are incomparable (§2.5).
- **R6, parity by partitioning.** The memory backend computes BM25 over
  per-type corpora matching SurrealDB's tables. The zero rule is per type;
  order parity is asserted within a list only.
- **R7, the status gate.** The FTS index matches every row regardless of
  status, while `vector_search` filters to ACTIVE by default. Without a
  matching gate, a CORRECTED node, a claim concluded wrong and kept off by
  default, would return as a lexical seed, ranked high precisely when it holds
  a rare identifier. So `text_search` takes `statuses` with the same default
  as `vector_search`, and the segment bridge obeys the same rule, or it is a
  side door around the gate. Both arms take their status semantics from the
  retrieval surface, never from their own defaults: two arms disagreeing about
  whether a historical claim exists would be a two-panel bug inside one tool.
  `WHERE` exclusion does not change the index's corpus counts, so scores
  drift slightly as nodes retire; harmless under rank fusion, recorded so
  nobody chases it as a bug. **The status filter wraps the FTS query as a
  subquery.** Inlining any non-match predicate into the OR-of-match-refs
  `WHERE` makes 3.0.5 drop the FTS index and match disjunctively, and the
  near-miss then returns at a positive score R1 cannot catch. Proven
  load-bearing by patching it back;
  `test_containment_keeps_the_index_over_a_real_connection` guards the same
  trap for R8.
- **R8, declared terms verify containment.** Token matching and substring
  matching fail in opposite directions, and each filters the other's false
  positives: token boundaries exclude `JIRA-44170` from a `JIRA-4417` query
  (digit runs tokenise whole), while containment excludes scattered
  co-occurrence ("the JIRA migration hit error 4417"), which conjunctive
  tokens cannot, since the FTS has no phrase queries. For each declared term:
  candidates come from the FTS token match (an exact-containing document
  necessarily contains the tokens); they are partitioned by normalised
  containment of the original string (lowercase and ascii folding, no
  stemming, since exactness is unstemmed by definition); exact-containing hits
  rank above token-only hits within the term's list and are exempt from R1,
  their evidence being the containment rather than the score; R2 protects the
  top exact hit when one exists, else the top token hit. The partition rule
  lives once, in `bm25.containment_first`, and the string comparison runs in
  Python over fetched rows, never in the FTS `WHERE`. The fallback path is
  unchanged, so declaring a common word returns its zero-scored containing
  hits where the fallback returns none: that is what declaring means. The
  term's list is ordered exact-first then by rank; re-sorting on the BM25 sum
  at the end would undo the rescue.

  The rescue needs reach: the hit it rescues sits at the bottom of a score
  ordering, so the declared-term fetch is widened to k×100. Measured on
  3.0.5 over 3,000 facts (median of 5): a rare declared term costs 1.6 ms with
  containment against 1.9 ms plain; the worst case, every token in every
  document, 24.2 ms against 16.8 ms. Residual, stated in the code: a term
  whose tokens are common across more than about 1,000 documents can still
  have its literal match fall outside the window. Outside the feature's
  purpose, since identifiers have a rare token almost by definition; the
  upgrade if it ever bites is a containment scan when a saturated window
  comes back all zero-scored.

---

## 11. Two engines

The Python SDK's embedded `mem://` core is an older SurrealDB than the 3.0.5
server, and the two differ in ways the adapter has to absorb:

- **Dialect.** The embedded core requires 2.x `SEARCH`; the server requires
  `FULLTEXT`. `_setup_schema` negotiates.
- **Negative IDF.** The embedded core returns negative IDF where the server
  clamps at zero. A per-reference `math::max(…, 0)` aligns single-token terms
  and cross-term sums, but not tokens inside one multi-token term:
  `JIRA-4417`, in a corpus where every document says `JIRA`, is lost on the
  embedded core and scores positively on the server. Not fixable from the
  query layer (per-token refs break 3.0.5's conjunctive matching). Pinned by
  `test_a_multi_token_term_of_mostly_common_words_is_lost_here`. R8 removes
  the user-facing consequence: exact-containing hits for declared terms no
  longer depend on the score, so the divergence is confined to fallback
  ranking.

Parity across engines is rare-term only, like parity across backends.
