# Lexical search: BM25 alongside vector retrieval

Keyword retrieval inside `search`, fused with the vector path by rank: BM25
over nodes and segments both, with declared `terms` driving the lexical arm
and a conservative statistical fallback when they are omitted. The code is
`epimemer/pipelines/query/lexical_search.py` and `fusion.py`, over
`text_search` on every storage backend. `docs/RETRIEVAL.md` §1 to §4 describe
what a caller sees. The rules are in §10.

---

## 1. The problem

Vector search alone cannot find a node reachable only by a rare token. The
default embedding model is `all-MiniLM-L6-v2`, 384-dim. WordPiece splits an
identifier like `JIRA-4417` into fragments, which are then mean-pooled with
every other token in the sentence. The identifier contributes almost no
distinguishing signal, and the query `"JIRA-4417"` embeds to approximately
"short alphanumeric string", which is close to every other ticket id in the
graph, so the wrong ticket ids rank about as high as the right one. Cosine
similarity has no notion of term rarity, which is exactly what BM25's IDF
supplies.

### 1.1 The upstream half, and why segments are indexed too

`store_decomposition` is agent-driven, so an agent that paraphrases
"ticket JIRA-4417 was closed" into "the deployment ticket was closed" leaves
the identifier nowhere in the graph, and no search of any kind recovers it
from nodes. Segments keep the raw text, so they are indexed as a second corpus
answering *where did I read that?*, and a segment hit bridges back to the
nodes extracted from it through `source_id` (`docs/RETRIEVAL.md` §3). A rare
identifier is almost always that second question, so this is the half of the
feature that closes the gap.

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
against them; `ISSUES.md` has the measured cost and the trigger for doing
something about it.

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

Two independent arms, each fed its own place by `plan_retrieval`, because a
place's token goes to whichever transition takes it. `plan_retrieval` also
resolves the lexical arm's terms and records whether the caller declared them,
which decides whether R2 applies. `search` runs the net in the runner's
concurrent mode, so the arms overlap, and neither can change what the other
computes. Both arms' seed ids survive to the end, which provenance needs. The
net is `epimemer/pipelines/query/hybrid_retrieval.py`.

### 3.1 Fusion: RRF, not score blending

Cosine similarity and BM25 are on incomparable scales with no calibration
between them. Any weighted sum is a magic number that gets re-tuned forever.
Reciprocal Rank Fusion uses only ranks: each list contributes `1/(k + rank)`,
rank 1-based, with `k = 60`, the conventional value. Pure, testable by
inspection, no tuning surface. It takes several ranked lists for a stronger
reason than vector-versus-lexical: the lexical arm contributes one ranked list
per node table plus one for segments (R5), because BM25 scores from different
tables are not on the same scale, and ranks are the only thing that may cross a
table boundary.

Pure RRF does not guarantee an exact identifier match lands in the top few.
Any node present in both lists scores at least `1/70 + 1/71 ≈ 0.0284`,
beating a lexical-only rank-1 at `1/61 ≈ 0.0164`, and the motivating
scenario, a graph where every ticket id embeds alike, is exactly where overlap
peaks. So the guarantee is made explicit: each declared term's top hit
survives to the final result (R2), a documented departure from pure RRF.

---

## 4. Storage protocol, and the cross-backend problem

`text_search` returns ids and BM25 scores for documents matching any term,
best first, and scores one corpus partition per call (R5). Scores are strictly
positive unless `verify_containment` rescued an exact hit (R8). Its `statuses`
filter defaults the way `vector_search`'s does, so the two seed routes cannot
disagree about whether a node exists (R7).

Taking pre-split `terms` rather than a query string puts tokenisation on the
caller, so every backend agrees on what the terms *are* before they disagree
about anything else.

**Parity, stated honestly.** SurrealDB scores in-engine with its own analyzer
(`class` tokenizer, `lowercase`, `ascii`, `snowball(english)` filters). The
in-memory, SQLite and Postgres backends share one BM25 in Python
(`storage/bm25.py`) and do not stem: Snowball is not reproducible in-tree, and
a partial stemmer disagrees in both directions. So `"deployments"` finds
`"deployment"` on SurrealDB and not on the other three. Exact score parity is
not achievable and no test asserts it. What the tests assert:

- **Set parity** on unambiguous queries: a rare term returns the same id set
  on every backend.
- **Order parity within one list**: the rare-term hit outranks the
  common-term hit on every backend. No test compares order across node types,
  because no such order exists (R5).
- **The zero rule, per type**: a term in more than half of one table's rows
  scores `0.0` on every backend. This is the one formula detail that must be copied
  deliberately, because Python BM25 recipes overwhelmingly use the
  `+1`-smoothed IDF that never reaches zero. The in-memory, SQLite and
  Postgres backends partition their corpus by node type to match the tables (R6).

Lexical search is the one protocol method where the backends genuinely
differ, a narrowing of the guarantee `get_nodes` states for itself ("a
batching of cost, not of answer").

---

## 8. Rejected

- **Exact-token lookup instead of BM25.** No ranking, and no way to say a
  common word contributes nothing.
- **A separate `lexical_search` tool.** The agent would have to guess which
  tool to call; fusion makes one call do both.
- **Query length as the "is this an identifier" heuristic.** "find JIRA-123"
  and "deployment problems yesterday" are the same length with opposite
  intents. Per-term corpus rarity, which the engine already computes, is the
  statistic that separates them; declared terms are the reliable path (§10).
- **A custom analyzer filter to drop punctuation tokens.** Their IDF is
  already zero.

---

## 9. Knock-on effects

- **Ranking feedback.** Retrieval must not feed ranking. RRF takes ranks from
  vector and BM25 only: no use-count, no popularity term.
- **Embedding truncation.** `all-MiniLM-L6-v2` truncates at 256 word-pieces;
  no node comes near it, and segments, which do, are never embedded
  (`BENCHMARKS.md`, *Node text does not reach the embedding window, and
  segments are never embedded*). BM25 is the only thing that reads segments.

---

## 10. The rules

Who decides that a token is load-bearing? Inferring it server-side from query
shape is guessing, and the caller is an agent, not a search box: it can be
asked.

- **R1, the zero-score rule.** Lexical hit lists truncate to `score > 0`
  before fusion, on every backend, as `text_search`'s contract. The IDF clamp
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
- **R6, parity by partitioning.** The Python BM25 the memory, SQLite and
  Postgres backends share is computed over per-type corpora matching
  SurrealDB's tables. The zero rule is per type;
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
  near-miss then returns at a positive score R1 cannot catch.
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
  lives in one place, and the string comparison runs in Python over fetched
  rows, never in the FTS `WHERE`. The fallback path does
  not check containment, so declaring a common word returns its zero-scored containing
  hits where the fallback returns none: that is what declaring means. The
  term's list is ordered exact-first then by rank; re-sorting on the BM25 sum
  at the end would undo the rescue.

  The rescue needs reach: the hit it rescues sits at the bottom of a score
  ordering, so on SurrealDB the declared-term fetch is widened to k×100
  (`_CONTAINMENT_OVERFETCH`). Measured on 3.0.5 over 3,000 facts, that costs
  1.6 ms for a rare declared term against 1.9 ms plain, and 24.2 ms against
  16.8 ms in the worst case, every token in every document. A term whose
  tokens are common across more than about 1,000 documents can still have its
  literal match fall outside the window; identifiers have a rare token almost
  by definition, and the upgrade if it ever bites is a containment scan when a
  saturated window comes back all zero-scored.

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
  query layer (per-token refs break 3.0.5's conjunctive matching). R8 removes
  the user-facing consequence: exact-containing hits for declared terms no
  longer depend on the score, so the divergence is confined to fallback
  ranking.

Parity across engines is rare-term only, like parity across backends. A
change to SurrealDB's `text_search` therefore needs `make test-integration` as
well as the unit suite, because the embedded engine the unit suite runs
differs from the server.
