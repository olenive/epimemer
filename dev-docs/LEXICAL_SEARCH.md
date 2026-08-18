# Lexical search: BM25 alongside vector retrieval

Design for adding keyword retrieval to `search`, fused with the existing vector
path. Decided 2026-08-17: **BM25** (not exact-token lookup), over **nodes and
segments** both, **fused into `search`** rather than exposed as a new tool.
Revised the same day after review: **declared `terms` drive the lexical arm**,
with a conservative statistical fallback when omitted, and zero-scored matches
never reach fusion — see §10.

**Built 2026-08-18** on branch `lexical-search` (unmerged). Construction
taught the engine several things the design did not know — §11 records them
and is the design of record where it conflicts with earlier sections.

---

## 1. The problem

`search` is vector-only. The only exact-text lookup anywhere in the storage
protocol is `get_node_by_content` (`storage/protocol.py:223`) — full-string
equality, used to upsert entity nodes by name. There is no substring, token, or
prefix match. `find_nodes` is pure graph traversal (`mcp/tools.py:923`).

So a node reachable only by a rare token is unreachable.

The default embedding model is `all-MiniLM-L6-v2`, 384-dim
(`mcp/config.py:37-39`). WordPiece splits an identifier like `JIRA-4417` into
fragments, which are then mean-pooled with every other token in the sentence.
The identifier contributes almost no distinguishing signal, and the query
`"JIRA-4417"` embeds to approximately "short alphanumeric string" — which is
close to *every other ticket ID in the graph*. The failure is not that the right
node ranks low; it is that the wrong ticket IDs rank about equally high. Cosine
similarity has no notion of term rarity, which is exactly what BM25's IDF
supplies.

### 1.1 The upstream half, and why segments are indexed too

`store_decomposition` is agent-driven — the agent writes the fact content. If it
paraphrases "ticket JIRA-4417 was closed" into "the deployment ticket was
closed", the identifier never enters the graph and **no** search of any kind
recovers it from nodes.

`RawDocument` and `Segment` both retain raw text (`core/types.py:340-358`). So
segments are indexed as a second corpus, and the two answer different questions:

| Corpus | Question it answers |
|---|---|
| Nodes | "What do I believe?" |
| Segments | "Where did I read that?" |

A rare identifier is almost always the second question. This is the half of the
feature that actually closes the stated gap.

And segments bridge back: a node's `source_id` is its `Segment.id`
(`core/types.py:372`). A segment hit therefore yields the nodes extracted from
that segment, which is the paraphrase case handled end to end — *the ID is in
segment S; here is what we concluded from S*.

---

## 2. What was verified against the running engine

SurrealDB **3.0.5**, probed in a throwaway namespace on 2026-08-17. These are
measurements, not recollections — several contradict what the 2.x documentation
says.

**2.1 The `SEARCH` keyword is gone.** SurrealDB 3.0 renamed it to `FULLTEXT`
*(server only — the Python SDK's embedded `mem://` core still speaks 2.x;
§11.1)*:

```sql
-- 2.x, and what the docs and every LLM say. Parse error on 3.0.5:
DEFINE INDEX idx ON fact FIELDS content SEARCH ANALYZER a BM25(1.2,0.75);
-- 3.0.5:
DEFINE INDEX idx ON fact FIELDS content FULLTEXT ANALYZER a BM25;
```

**2.2 Defining the index backfills existing rows.** A row created *before*
`DEFINE INDEX` was found by a later search. Existing graphs therefore need **no
migration step** — the index is built the first time `_setup_schema` runs
against them.

**2.3 The analyzer shreds identifiers, which is what makes them findable.**

```
search::analyze("epimemer_text", "Ticket JIRA-4417 E_TIMEOUT_503")
  → ["ticket", "jira", "-", "4417", "e", "_", "timeout", "_", "503"]
```

`4417` is a rare token with high IDF, so it discriminates. Searching
`"JIRA-4417"` against a corpus containing both `JIRA-4417` and `JIRA-4418`
returned **only** the former, at score 0.829.

The punctuation tokens (`-`, `_`) are noise but harmless: they appear in nearly
every document, so their IDF is zero (see 2.5). Not worth a custom filter.

**2.4 A single `@@` match is conjunctive.** Every term must be present:

```sql
-- → [], though "ticket" alone matches 3 rows
WHERE content @1@ "ticket zzzznotpresent"
```

This matters more than it looks. Fused naively, a multi-word prose query would
return zero lexical hits and degrade to vector-only. OR semantics need one match
reference per term, with the scores summed — verified working:

```sql
SELECT id, search::score(1) + search::score(2) + search::score(3) AS score
FROM fact
WHERE content @1@ "deployment" OR content @2@ "rollback" OR content @3@ "ticket"
ORDER BY score DESC;
```

**2.5 SurrealDB's BM25 uses classic IDF, clamped at zero** *(3.0.5 server
only — the embedded core returns negative IDF; §11.2)*. Measured: a term in
2 of 4 documents scores exactly `0.0`; a term in 3 of 4 also scores `0.0`, not a
negative number. That fits `log((N - n + 0.5) / (n + 0.5))` — zero at `n = N/2`,
negative above and clamped.

This is a gift, not a defect. **A term more common than half the corpus
contributes nothing**, so lexical search naturally says nothing about prose
queries built from common words, and the score floor I would otherwise have had
to invent and tune comes from the engine for free. Lexical supplies precision on
rare tokens; vector supplies recall on prose. They fail in opposite directions,
which is the entire argument for fusing them.

It is also the exact formula the memory backend must reproduce — see §4.

> **Revised (2026-08-17, review).** The clamp zeroes the *score*, not the
> *match*. Measured: a term in 3 of 4 documents still returns **all three
> rows, tied at `0.0`**, in nondeterministic order — and RRF sees only ranks,
> so a zero-scored row at arbitrary tie rank 2 would fuse at `1/62`, nearly
> equal to the best vector hit's `1/61`. The floor is only "a gift" if the
> rows are dropped: **the lexical arm truncates its hit list to `score > 0`
> before anything ranks it**, on both backends, as part of `text_search`'s
> contract (§4). The paragraph above stands for scoring; membership is what
> it missed. Rules and tests in §10.

---

## 3. Where it plugs into the query net

The net is already named `hybrid_retrieval`, where "hybrid" has so far meant
vector + graph expansion. This makes the name honest:

```
                  ┌─> run_vector_search  ─> [VectorResults]  ─┐
[QueryRequest] ───┤                                           ├─> fuse_seeds ─>
                  └─> run_lexical_search ─> [LexicalResults] ─┘
                          [Seeds] ─> run_graph_expansion ─> [ExpandedResults] ─> …
```

Two independent transitions off the same place. The Petri runner already gives
concurrency for free, and neither branch can change what the other computes.

`hybrid_retrieval.py:124-131` currently drops `VectorResults.scored_nodes` when
building `ExpandedResults`. Both branches' seed ids must survive to the end
instead — see §6.

### 3.1 Fusion: RRF, not score blending

Cosine similarity and BM25 are on incomparable scales with no calibration
between them. Any weighted sum is a magic number that gets re-tuned forever.
Reciprocal Rank Fusion uses only ranks:

```python
def rrf_scores(rankings: Sequence[Sequence[str]], *, k: int = 60) -> dict[str, float]:
    """Fuse ranked id lists. Score = Σ 1/(k + rank), rank 1-based."""
```

Fifteen lines, pure, testable by inspection, no tuning surface. `k=60` is the
conventional value and there is no reason to depart from it.

> **Revised (2026-08-17, review).** `rankings` is plural for a stronger reason
> than vector-vs-lexical: the lexical arm contributes **one ranked list per
> node table, plus one for segments** (§10, R5). BM25 scores from different
> tables are not on the same scale — IDF's `N` is per index — so ranks are the
> only thing that may cross a table boundary, and RRF is already the tool that
> consumes ranks. Fusion therefore takes: one vector list, one list per node
> table, one segment list.

A rank-1 lexical hit scores `1/61`, which is the maximum any single list can
contribute — so an exact identifier match always lands in the top few. That is
the behaviour the feature exists to produce.

> **Corrected (2026-08-17, review).** "Always lands in the top few" is false
> under list overlap: any node present in *both* lists scores at least
> `1/70 + 1/71 ≈ 0.0284`, beating a lexical-only rank-1 at `1/61 ≈ 0.0164`.
> And the motivating scenario — a graph where every ticket ID embeds alike —
> is exactly where overlap peaks, so the exact match can fall to rank ~11 and
> off a top-10 cut. The guarantee is therefore made explicit rather than
> assumed: **each declared term's top hit survives to the final result**
> (§10, rule R2) — a deliberate, documented departure from pure RRF. Fallback
> (undeclared) terms get no such protection.

---

## 4. Storage protocol, and the cross-backend problem

One new method, implemented fully on **both** backends — no capability flags, no
`hasattr` probing:

```python
async def text_search(
    self,
    terms: Sequence[str],
    *,
    corpus: Literal["nodes", "segments"],
    k: int = 10,
    node_type: NodeType | None = None,
    status: NodeStatus = NodeStatus.ACTIVE,
) -> Sequence[tuple[str, float]]:
    """Ids and BM25 scores for documents matching ANY term, best first.

    Scores are strictly positive: a match whose BM25 score is 0.0 (the IDF
    clamp, §2.5) is not a result. Both backends enforce this — it is part of
    the contract, not an optimisation.

    One call scores one corpus partition. When corpus="nodes", node_type is
    required: node tables carry their own BM25 statistics, so a merged
    multi-type list would compare incomparable scores (§10, R5). The caller
    makes one call per table and fuses ranks.

    `status` mirrors vector_search: ACTIVE by default, same meaning, so the
    two seed routes cannot disagree about whether a node exists (§10, R7).
    Ignored for corpus="segments" — segments have no status.
    """
```

Taking pre-split `terms` rather than a query string puts tokenization on the
caller, so both backends agree on what the terms *are* before they disagree
about anything else.

A second new method is needed for the segment bridge in §1.1, and it has no
current equivalent:

```python
async def get_nodes_by_source(
    self, source_ids: Sequence[str]
) -> dict[str, list[EpistemicNode]]:
    """Nodes extracted from each segment.

    Batched — one statement, not one per id.
    """
```

**The parity problem, stated honestly.** SurrealDB scores in-engine with its own
analyzer (`class` tokenizer, `lowercase`/`ascii`/`snowball(english)` filters).
The memory backend must implement BM25 in Python. Exact score parity is
**not achievable** — the stemmers alone will differ on edge cases — and any test
asserting it will be flaky.

What is achievable, and what the tests assert:

- **Set parity** on unambiguous queries: a rare term returns the same id set on
  both backends.
- **Order parity** where scores are unambiguous: the rare-term hit outranks the
  common-term hit on both.
- **The zero rule**: a term in more than half the corpus scores `0.0` on both.
  This is the one formula detail that must be copied deliberately rather than
  arrived at, because Python BM25 recipes overwhelmingly use the `+1`-smoothed
  IDF that never reaches zero.

> **Corrected (2026-08-17, review): "the corpus" is per table on SurrealDB,
> and parity is restored by copying that, not fighting it.** Nodes live in
> three tables (`topic` / `fact` / `inference`, `surrealdb_adapter.py:547`),
> each with its own FTS index, so BM25's `N` is per table. Measured: the same
> term with the same hit count (2 docs) scores `0.0` in a 4-row table and
> `0.9615` in a 10-row table. Consequences, binding on the parity plan above:
> the **memory backend partitions its corpus by node type** to match; the
> **zero rule is per-type** on both backends (a term in more than half the
> *facts* clamps in the fact list even if rare among nodes overall); **order
> parity holds within a list only** — no test may compare order across node
> types, because no such order exists. Set parity on rare terms is unaffected.
> This is also the honest semantic reading: a term in 30 of 50 topics does not
> discriminate among topics, however rare it is among facts.

This is a real narrowing of the guarantee that `get_nodes` states in its own
docstring ("a batching of *cost*, not of *answer*"). Lexical search is the first
protocol method where the backends genuinely differ, and pretending otherwise
would be worse than saying so here.

---

## 5. SurrealDB schema

Into `_setup_schema` (`surrealdb_adapter.py:529`), which already runs on every
connect and is already idempotent via `IF NOT EXISTS`:

```sql
DEFINE ANALYZER IF NOT EXISTS epimemer_text
  TOKENIZERS class
  FILTERS lowercase, ascii, snowball(english);

-- per node table, plus segment on its `text` field
DEFINE INDEX IF NOT EXISTS idx_{table}_fts ON {table}
  FIELDS content FULLTEXT ANALYZER epimemer_text BM25;
```

**Risk worth naming:** index definition backfills (2.2), and `_setup_schema`
runs inside `connect()`, which has no progress reporting. On an existing graph
of a few thousand nodes the first connect after this ships will be slower than
every connect before it, once. `IF NOT EXISTS` means it happens exactly once,
but it happens somewhere the user cannot see. This should be measured on a
realistic graph before it ships, not after. **Measured 2026-08-18: §11.5 —
1 s at 2,000 documents, 19 s at 20,000, inside `connect()`.**

---

## 6. What `search` returns

Seeds now arrive by three routes, and flattening that away would throw out the
most useful thing the feature produces. Each returned node carries its
provenance:

```python
class SeedProvenance(StrEnum):
    VECTOR = "vector"      # embedding similarity
    LEXICAL = "lexical"    # BM25 on node content
    SEGMENT = "segment"    # BM25 on a segment, bridged via source_id
    EXPANDED = "expanded"  # pulled in by graph expansion from a seed
```

Segment hits also surface directly, under their own key, since a segment is not
a graph node and must not be pretended into one:

```python
result = {
    "nodes": [...],      # each with `provenance` and, where scored, `score`
    "segments": [...],   # id, text, source document, BM25 score
    "edges": [...],
}
```

**This enum is why lexical should land before the retrieval-provenance viz
work** (`RETRIEVAL_PROVENANCE.md`), not after. That feature's record was going
to distinguish two tiers (matched / expanded); with lexical it is four.
Designing the field as a boolean now means rebuilding it later, and "this came
back on an exact token match, not on similarity" is precisely the diagnostic
the focus panel exists to show.

---

## 7. Tests, written first

Per the `ISSUES.md` workflow — each named test failing for its stated reason
before the code that satisfies it.

**The headline test, and it should be written first of all:**

```
test_search_finds_an_identifier_vector_search_cannot
```

Store facts containing `JIRA-4417` and `JIRA-4418` plus filler; search
`"JIRA-4417"`; assert the right one is returned and the near-miss is not. This
must be shown failing on today's vector-only path — it is the whole reason the
feature exists, and a version of it that passes before the change is testing
nothing.

> **Revised (2026-08-17, review): the assertion is about seeds, not the whole
> result.** Both ticket facts share a source document, so at `graph_hops ≥ 2`
> the near-miss is *legitimately* reachable from the right seed (fact →
> document → sibling fact) — that is expansion doing its job, and "the
> near-miss is not returned" would fail against correct behaviour. Exact
> match and related-by-connection are different provenances (§6), so assert
> in those terms: `JIRA-4417`'s fact is present **as a lexical seed**;
> `JIRA-4418`'s fact is **not a seed of any kind** (it may appear only as
> `expanded`). True at every hop count, and it pins the actual claim —
> lexical discriminates 4417 from 4418 — rather than a side effect.
>
> **Corrected again at construction (2026-08-18):** "not a seed of any kind"
> was still too strong — the *vector* arm may legitimately seed the near-miss
> (they embed alike; lexical does not control the vector top-k, and on the
> test corpus the vector ranking included both tickets). The assertion as
> built: 4417 **is** a lexical seed, 4418 **is not** — shown failing on the
> vector-only tree. §11.8.

Then:

- `test_bm25_idf_is_zero_for_a_term_in_most_of_the_corpus` — the §2.5 rule, on
  the memory backend, as a pure-function test of the scorer.
- `test_text_search_agrees_across_backends_on_a_rare_term` — set parity (§4).
  Integration, gated on `SURREAL_PORT`.
- `test_rrf_promotes_a_rank_one_lexical_hit` — pure, no storage.
- `test_text_search_is_or_across_terms` — guards the §2.4 trap. *(Revised
  2026-08-17: was `test_lexical_terms_are_or_not_and`, which as written could
  assert through fused `search` and pass vacuously — the vector arm supplies
  results even when a conjunctive lexical arm returns `[]`.)* Integration,
  against **`text_search` directly**, parametrized over both backends
  (SurrealDB gated on `SURREAL_PORT`): seed facts containing `deployment` and
  none containing `zzzznotpresent`; `terms=["deployment", "zzzznotpresent"]`
  returns the deployment docs (a single-`@@` implementation returns `[]` and
  fails), **and** each doc's score equals its score for
  `terms=["deployment"]` alone — an absent term contributes zero rather than
  blocking. Never route this test through `search`.
- `test_segment_hit_bridges_to_its_extracted_nodes` — the §1.1 paraphrase case:
  the ID exists only in the segment, and the node is still returned.
- `test_search_response_labels_seed_provenance` — §6.
- `test_fts_index_is_defined_for_every_node_table` — schema guard, integration.
- `test_zero_scored_matches_never_reach_fusion` — §10 R1: a term above the IDF
  clamp matches rows but contributes nothing to the fused result. Pure on the
  memory scorer; integration on the adapter.
- `test_declared_term_top_hit_survives_overlapping_lists` — §10 R2: identifier
  at lexical rank 1, a vector list of `k` nodes that all also appear in the
  lexical list; the identifier's node is still returned. Pure RRF fails this
  test — that is the point of it.
- `test_prose_query_without_terms_adds_no_lexical_noise` — §10 R3: a query of
  only common words, no `terms`; the result set equals the vector-only result.
- `test_same_term_clamps_in_one_table_and_scores_in_another` — §10 R5: a term
  in more than half the facts but few topics scores `0.0` in the fact list and
  positively in the topic list, on both backends. Pins the per-type zero rule;
  a single-corpus memory implementation fails it.
- `test_a_corrected_node_is_not_a_lexical_seed` — §10 R7: a CORRECTED node
  containing the searched identifier is not returned, by either arm, on either
  backend. The index-only implementation (no status clause) fails it.
- `test_segment_bridge_respects_the_status_gate` — §10 R7: a segment hit whose
  only extracted node is CORRECTED bridges to nothing (the segment itself may
  still be reported).
- `test_an_exact_containing_hit_survives_zero_scored_tokens` — §10 R8, the
  §11.2 rescue: a corpus where every document says `JIRA`; declared term
  `JIRA-4417`; the exact document returns even where the term's BM25 score
  is ≤ 0. Asserted at the fusion level, both backends — the engine-level
  divergence test stays as the pin of engine truth beneath it.
- `test_exact_containment_outranks_scattered_cooccurrence` — §10 R8: a
  document containing the literal `JIRA-4417` outranks one containing `JIRA`
  and `4417` apart, for the declared term.
- `test_a_longer_identifier_is_not_a_containment_match` — §10 R8's boundary
  half: `JIRA-44170` is not returned for declared `JIRA-4417` — the token
  match excludes it from candidacy before containment is ever checked.

Storage protocol changes mean `make test-integration SURREAL_PORT=8123` runs
alongside the unit suite.

---

## 8. Commit sequence

1. `text_search` on the protocol + memory backend BM25 + pure-function tests.
2. `text_search` on the SurrealDB adapter + analyzer/index schema + parity
   tests.
3. `get_nodes_by_source` on both backends.
4. RRF fusion helper, pure, with tests.
5. The lexical transition and fusion in the query net; `SeedProvenance` carried
   through to `QueryResult`.
6. `search` wiring: segment corpus, `segments` key in the response, docstring.

Each is independently green. Nothing before step 5 changes what `search`
returns, so the first four can land without touching agent-visible behaviour.

---

## 9. Knock-on effects

- **Archival.** Nodes findable only by lexical query are currently
  `never_retrieved` archival candidates
  (`pipelines/reflection/archival.py:192`). Making them findable changes what
  gets archived. Not a problem — arguably a fix — but it is a behaviour change
  and should be noted rather than discovered.
- **`retrieved_at`.** Unchanged: `_record_retrieval` stamps whatever nodes came
  back, whichever route they arrived by.
- **Ranking feedback.** Same rule as `_record_retrieval` already documents
  (`mcp/tools.py:653-657`): retrieval must not feed ranking. RRF takes ranks
  from vector and BM25 only. No use-count, no popularity term.
- **Embedding truncation, unresolved and adjacent.** `all-MiniLM-L6-v2`
  truncates at 256 word-pieces, and no content-length guard was found anywhere.
  A long fact's tail is silently absent from its embedding today. Lexical search
  incidentally mitigates this — BM25 indexes the whole field — but the
  underlying gap is separate and still unaddressed. It belongs in `ISSUES.md`,
  not here. **Filed 2026-08-18 as `ISSUES.md` #59**, which carries the options
  and the measurement that has to come first.

---

## 10. Post-review revisions (2026-08-17)

A review the day this was decided found two related defects — zero-scored
matches reach fusion (§2.5), and the RRF "top few" guarantee fails under list
overlap (§3.1) — and one design question underneath both: **who decides that a
token is load-bearing?** Inferring it server-side from query shape is guessing,
and guessing is what this codebase rejects elsewhere (`RETRIEVAL_PROVENANCE.md`
§2.1: tools declare their ids; the wrapper does not guess). The caller is an
agent, not a search box: it can be asked.

Query length, for the record, was considered and rejected as the heuristic:
"find JIRA-123" and "deployment problems yesterday" are the same length with
opposite intents. Per-term corpus rarity — IDF, which the engine already
computes — is the statistic that separates them, and R1/R3 use it.

**The rules, binding on the implementation:**

- **R1 — the zero-score rule.** Lexical hit lists truncate to `score > 0`
  before fusion, on both backends, as `text_search`'s contract (§4). The IDF
  clamp then means what §2.5 wanted it to mean: common terms contribute
  nothing, including membership.
- **R2 — declared terms.** `search` gains an optional `terms: list[str]`.
  Declared terms are authoritative statements of intent: **each declared
  term's top-scoring hit survives to the final result**, even past the top-k
  cut. This is a deliberate departure from pure RRF, made here rather than
  discovered in the implementation.
- **R3 — the fallback.** With `terms` omitted, the lexical arm runs over the
  query's own tokens with R1 applied — rare tokens fire, common ones vanish,
  no invented threshold, and **no R2 protection**. An agent that types the
  identifier into the query without declaring it still mostly benefits; the
  reliable path is declaring. Known residue: mid-frequency terms in long prose
  queries survive R1 and can, under OR-sum, outvote a single rare term — that
  is the cost of not declaring, and the docstring says so.
- **R4 — the docstring teaches the pattern.** The `search` tool description
  tells the agent to pass identifiers, names, and exact phrases it cares about
  as `terms` — the same lever this repo uses for `because` and importance.

- **R5 — one list per table.** The lexical arm produces one ranked list per
  node table plus one for segments; fusion consumes lists and only ranks cross
  a table boundary. `text_search(corpus="nodes")` requires `node_type` — a
  merged multi-type list is forbidden by contract, because the scores it would
  sort are incomparable (measured: same term, same hit count, `0.0` in a
  4-row table vs `0.9615` in a 10-row table).
- **R6 — parity by partitioning.** The memory backend computes BM25 over
  per-type corpora matching SurrealDB's tables. The zero rule is per-type;
  order parity is asserted within a list only.
- **R2 under R5:** a declared term's survival guarantee is per list — it can
  protect up to one node per type (plus the segment bridge). There is no
  cross-table score with which to pick a single winner, so the design does
  not pretend to.
- **R7 — the status gate.** The FTS index matches every row regardless of
  status; `vector_search` filters to ACTIVE by default. Without a matching
  gate, a CORRECTED node — a claim concluded *wrong*, kept off by default per
  #53 T3 — returns as a lexical seed, ranked high precisely when it holds a
  rare identifier. So: `text_search` takes `status` with the same ACTIVE
  default as `vector_search` (a `WHERE status = …` clause on SurrealDB —
  `idx_{table}_status` already exists); the **segment bridge obeys the same
  rule** (nodes reached via `get_nodes_by_source` are filtered like direct
  seeds, or the bridge is a side door around the gate); and when #53 T3's
  retrieval surface lands (HISTORICAL on by default with lineage collapse,
  CORRECTED reachable but off), **both arms take their status semantics from
  that surface, not from their own defaults** — two arms disagreeing about
  whether a historical claim exists would be #56's two-panel bug reborn
  inside one tool. Statistics note, no action: `WHERE` exclusion does not
  change the index's corpus counts, so scores drift slightly as nodes retire
  — harmless under rank fusion, recorded here so nobody chases it as a bug.
  **Implementation note (2026-08-18): the status filter must wrap the FTS
  query as a subquery.** Inlining any non-match predicate into the
  OR-of-match-refs `WHERE` makes 3.0.5 drop the FTS index and match
  disjunctively — the near-miss returns at a *positive* score R1 cannot
  catch. Measured, and proven load-bearing by patching it back (§11.4).
- **R8 — declared terms verify containment** *(added 2026-08-18 after
  construction; built same day — §11.9)*. Token matching and substring matching fail
  in opposite directions, and each filters the other's false positives:
  token boundaries exclude `JIRA-44170` from a `JIRA-4417` query (digit runs
  tokenize whole), while containment excludes scattered co-occurrence
  ("the JIRA migration hit error 4417"), which conjunctive tokens cannot —
  the FTS has no phrase queries, so containment is the only adjacency signal
  available. For each **declared** term: candidates come from the FTS token
  match as today (an exact-containing document necessarily contains the
  tokens, so the candidate set cannot miss one); candidates are partitioned
  by **normalized containment of the original string** (the analyzer's
  lowercase/ascii folding, no stemming — exactness is unstemmed by
  definition) — complete *within the widened fetch window*; the residual is
  stated in §11.9; exact-containing hits rank above token-only hits within
  the term's list and are **exempt from R1** — their evidence is the
  containment, not the BM25 score; R2 protects the top *exact* hit when one
  exists, else the top token hit. The fallback path is unchanged. The
  containment check runs client-side in the adapter over fetched candidates,
  **never in the FTS `WHERE`** (§11.4's trap). This also erases §11.2's
  user-facing consequence: an exact match can no longer be lost to
  engine-dependent scoring, confining that divergence to fallback ranking.

The headline test in §7 deliberately stays on the **fallback** path (the
identifier is a rare token; R3 must find it unaided); the new tests above pin
R1–R3, R5 and R6 individually.

---

## 11. Construction notes (2026-08-18)

Built on branch `lexical-search`, all §8 steps, both backends; unit,
integration and frontend suites green. What construction taught, reported by
the implementer and ruled accepted 2026-08-18. **Where these conflict with
earlier sections, these win.**

1. **Two engines, two dialects.** The Python SDK's embedded `mem://` core is
   an older SurrealDB that rejects `FULLTEXT` and requires 2.x `SEARCH`; the
   3.0.5 server rejects `SEARCH`. `_setup_schema` negotiates the dialect
   (`surrealdb_adapter.py`, both DDL strings). §2's measurements are the
   server's.
2. **The IDF clamp (§2.5) is server-only.** The embedded core returns
   negative IDF. A per-reference `math::max(…, 0)` aligns single-token terms
   and cross-term sums — but not tokens *inside* one multi-token term:
   `JIRA-4417`, in a corpus where every document says `JIRA`, is lost on the
   embedded core and scores `1.0895` on the server. Not fixable from the
   query layer (per-token refs break 3.0.5's conjunctive matching — measured,
   the near-miss leaks). Pinned by
   `test_a_multi_token_term_of_mostly_common_words_is_lost_here`. Parity
   across *engines* is rare-term only, like parity across backends.
   **Mitigated by R8 (2026-08-18):** exact-containing hits for declared terms
   no longer depend on the score, so the user-facing loss disappears and the
   divergence is confined to fallback ranking.
3. **The memory backend does not stem.** Deliberate: Snowball is not
   reproducible in-tree, and a partial stemmer disagrees in both directions.
   Consequence: `"deployments"` finds `"deployment"` on SurrealDB and not in
   memory. Cross-backend guarantees are exact-token, which is the feature's
   purpose anyway.
4. **R7's status filter is a subquery, not a `WHERE` clause** — see the
   implementation note under R7. Inlined predicates silently disable the FTS
   index on 3.0.5 and `@@` turns disjunctive.
5. **Backfill measured (§5's gate).** Median of 3, documents = nodes +
   segments: 2,000 → 1.0 s; 6,000 → 3.8 s; 20,000 → 19 s — inside
   `connect()`, no progress reporting; steady connect stays ~30 ms.
   Acceptable at current graph sizes; numbers live in `BENCHMARKS.md`.
6. **One protocol addition beyond §4: `get_segments(ids)`**, both backends —
   §6's `segments` response key needs segment text, and nothing could fetch
   a segment by its own id.
7. **R2's cost shape:** one `text_search` per declared term per partition —
   per-term attribution is what the survival guarantee requires. The
   fallback stays at one call per partition.
8. **The headline assertion was corrected once more** (see §7's dated note):
   the claim under test is lexical-side only — 4417 is a lexical seed, 4418
   is not.
9. **R8 built (2026-08-18, same branch).** How it landed, and three
   consequences ruled accepted:
   - `text_search` gains `verify_containment: bool = False` on the protocol
     and both backends; the declared path turns it on, the fallback path is
     untouched. The partition rule lives once, in `bm25.containment_first`;
     the string comparison runs in Python over fetched rows, never in the
     FTS `WHERE` (§11.4). New engine-trap guard:
     `test_containment_keeps_the_index_over_a_real_connection`.
   - **The rescue needs reach.** The hit R8 rescues sits at the bottom of a
     score ordering, so the declared-term fetch widened ×10 (k×100 vs
     k×10). Measured on 3.0.5 over 3,000 facts (median of 5): rare declared
     term 1.6 ms with containment vs 1.9 ms plain; worst case — every token
     in every document — 24.2 ms vs 16.8 ms. **Residual, stated in the
     code:** a term whose tokens are common across more than ~1,000
     documents can still have its literal match fall outside the window.
     Outside the feature's purpose (identifiers have a rare token almost by
     definition); the known upgrade if it ever bites is a containment scan
     when a saturated window comes back all zero-scored.
   - **Declaring a common word now returns its zero-scored containing
     hits** where R1 previously returned none. That is R8 as written, and
     what declaring means — and why the fallback path was left alone.
   - **The declared path's per-term ordering is rank-built, not
     score-summed:** re-sorting on the BM25 sum at the end undid the rescue,
     so a term's list is ordered exact-first then by rank, and fusion
     consumes it as ranks like every other list.
   - Exactness is *not* exposed at the tool surface — provenance stays
     `lexical`; `RETRIEVAL_PROVENANCE.md` owns that vocabulary if it ever
     wants the distinction.
