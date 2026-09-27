# Segmentation: the agent cuts, on evidence

**Status: decided 27 September 2026; Stage 0 built and measured.** The backlog entry
*LLM-guided and hybrid segmentation* in `PROPOSED_FEATURES.md` offered two
ways to get better cuts than a rule can make: the calling agent cuts, or the
server calls a model itself. The user chose the first. This document says
when the agent cuts, how it states a cut, what the server checks, and what is
recorded. §8 breaks the work into stages with the functions and tests each
one needs; §10 lists what is still open. Where an unbuilt section says
"does", read "would".

The one property this design protects: **the server makes no model calls of
its own.** That is why Epimemer has no API keys, no provider configuration,
no per-call cost, deterministic tests, and no opinion about which model a
client runs. Everything below is arranged so that better cuts arrive without
giving that up.

---

## 1. What a passage is for

Ingest is two steps. `segment` stores the document and cuts it into passages;
the agent reads each passage and hands back facts, inferences and topics with
`store_decomposition`. Every claim carries the id of the passage it came from
(`source_id` on the node, and `get_nodes_by_source` is the bridge back), and
`search` returns matching passages under `segments` to answer *where did I
read that?* rather than *what do I believe?* Passages are also the corpus the
lexical arm of search runs over. They are not embedded.

So a passage is the unit of provenance, and a cut is a decision about what a
reader will later be shown as the context of a claim. A passage that spans two
subjects muddies every claim's context; one that separates a sentence from its
qualifier can lose a hedge, a date or a speaker. The damage is quiet and it
lasts, because nothing downstream revisits a cut.

## 2. The two strategies, and how they fail

Both run without a model, and both stay.

**Paragraph**, the default, cuts on blank lines and falls back to one passage
for the whole document when there are none. Right for ordinary prose. Wrong,
predictably, for:

- a transcript or chat log with no blank lines, which becomes one passage;
- a bulleted list separated by blank lines, which becomes one passage per
  bullet, each too short to stand on its own;
- a long legal or academic paragraph covering several matters.

**Semantic**, TextTiling style, splits into sentences by regular expression,
embeds each sentence with the local model, and cuts wherever similarity
between neighbours drops more than one standard deviation below the mean.
Better on topic drift inside a paragraph. Its failures are the splitter's and
the threshold's: headings, dialogue, tables and code defeat the regular
expression, and the statistical threshold means a document with one abrupt
change gets no cut while a uniformly meandering one gets many.

Neither can see a speaker change, a clause boundary, a scene change, or prose
giving way to a table. A reader can, and the agent is a reader that already
has the whole document open.

## 3. The decision, and where it is taken

### 3.1 The agent cuts

The agent already reads the document end to end to decompose it, so it is in
a position to say where the passages should fall, at the cost of one more
judgment per document. The server verifies and stores; it never proposes a
cut by any means other than the two rules above.

### 3.2 After the programmatic cut, on evidence

The agent does not choose a strategy blind. The programmatic cut always runs
first, because it is free and right for most documents, and the agent
overrides it only when there is something to point at:

- **the server doubts its own cut** (§6), or
- **the agent recognises a shape the rules cannot see** (§7).

Judging a proposed cut is easier and more reliable than proposing one from
scratch, and cutting every document by hand would cost a judgment per
document and invite mistakes on long texts, for no gain on the prose that
paragraph cutting already handles. The exception is a document the agent
already knows is awkward, which it may cut on the first call (§5.1).

### 3.3 The procedure

1. Call `segment` as today. The document and its passages are stored.
2. Read the result: the passages, and `doubts` if the server has any.
3. If doubted, or a passage visibly spans two subjects or splits a sentence
   from what qualifies it, call `resegment` with the agent's own cuts. This
   is allowed only while no claim cites any of the document's passages.
4. Decompose.

Once `store_decomposition` has run, the passages are provenance and stay as
they are. A document that needs re-cutting after that is ingested again as a
new document; moving claims between passages is not a thing this design
offers (§9).

## 4. Stating a cut: anchors

### 4.1 The contract

A cut is stated by **anchors**: for each passage, the text it begins with,
copied exactly from the document. The server walks them in order:

- the first anchor is located from the start of the document, every later
  one from the end of the previous anchor, taking the **first occurrence**;
- each passage runs from its anchor's start to the next anchor's start, and
  the last runs to the end of the document;
- leading and trailing whitespace is trimmed from each passage's text, with
  `span_start` and `span_end` kept accurate, exactly as the paragraph rule
  does today;
- text before the first anchor is refused rather than silently dropped
  (§4.2), so the anchors must cover the document.

An anchor is at least three words or twelve characters, whichever the agent
finds easier to satisfy; a shorter one is refused, since a two-word anchor
matches too early too often. An agent that needs a later occurrence of a
phrase lengthens the anchor until it is the first occurrence, which is
deterministic and needs no notion of ambiguity on the server.

One anchor at the document's start is a valid cut: it says the document is
one passage, and that is a judgment the agent is allowed to make.

### 4.2 Refusals

The whole call is refused, nothing written, naming every problem at once, on
the pattern `apply_reflection` uses for a malformed batch:

- an anchor that is not found at or after the previous cut, quoted;
- an anchor shorter than the minimum;
- two anchors that produce an empty passage (adjacent, or one inside the
  other);
- text before the first anchor that is not whitespace, quoted in part, so
  the agent sees what it left out;
- no anchors at all.

A refusal is prose, as `BoundaryRefused` is: nothing branches on it.

### 4.3 Rejected forms

- **Character offsets.** Exact, and error-prone for a model producing them
  over a long text. A wrong offset is silent: the passage exists, and it is
  the wrong stretch.
- **Full passage texts, verified to concatenate to the document.** The same
  check as anchors, paid for by echoing the entire document back to the
  server. The verification is worth having; the echo is not.
- **Cut markers inserted into the text.** Requires the agent to return the
  document with insertions, which is the echo again with a parsing step on
  top.

## 5. The two calls

### 5.1 `segment(..., cuts=[...])`

`segment` takes an optional `cuts`, a list of anchors. Given, the programmatic
strategy does not run and the passages are the agent's; `segmentation_strategy`
and `cuts` together is refused, since they answer the same question two ways.
Everything else about the call is unchanged: the document is stored, the
publisher is linked, the result lists passage ids and lengths.

### 5.2 `resegment(document_id, cuts, expected_graph, judge_token)`

A separate tool rather than `segment` with a `document_id` instead of
`content`. A tool whose required argument is *one of these two* is this
repository's own tell that two tools are wearing one name, and the two differ
in the guard they need: `segment` creates, `resegment` replaces under a
precondition.

The precondition: **no node cites any of the document's passages.** Checked
inside the same transaction as the replacement, through a new
`resegment_tx(document_id, segments)` on `StorageBackend`, implemented on
every backend. Granularity is the logical operation: a check in one call and
a replacement in the next would let a decomposition land between them and
leave claims pointing at passages that no longer exist. Refused with the
count of citing nodes when the precondition fails.

### 5.3 What a re-cut replaces

The old passages are **deleted**, and this is the one place the design steps
outside the append-only habit, so the reasoning is stated in full. Confirmed
by the user on 27 September 2026.

They are real rows by then: `segment` stores the document and every passage
before it returns the ids, and the passages join the lexical search corpus at
once. Nothing is held back until decomposition, because that would need the
server to keep state between the two calls, or the agent to echo the passages
back, and the ids would not survive a reconnect. So a re-cut replaces stored
rows, and the question is what to do with the ones it replaces.

A passage before decomposition is material, not a claim: it carries no judge,
no journal row, and nothing cites it. `segment_text`'s own comment already
says splitting text into paragraphs is not a verdict anybody would review.
Retiring instead of deleting would add a status to `Segment` and a filter to
every reader of segments: the lexical search corpus, `get_segments_for_document`,
`query_segments` for export, and the visualization. Four filters, each a place
a retired passage can leak back into a search result, against one guarded
delete of rows nothing refers to. Nothing epistemic is lost: the document is
untouched, and the cut it replaced is recorded on the document (§5.4).

The precondition is what makes this safe, and it is enforced in the
transaction, never by convention.

### 5.4 What is recorded

The document's `metadata` gains a `segmentation` entry, written by every path
that cuts:

```
{"strategy": "paragraph" | "semantic" | "agent",
 "passages": <count>,
 "history": [{"strategy": "paragraph", "passages": 1,
              "replaced_at": "...", "replaced_by": <judge or null>}]}
```

Today the document does not say how it was cut at all. After this, a reader
of a passage can tell whether its boundaries were a rule's or a judgment's,
and a re-cut document keeps the rule's result in `history`.

No journal row. A document and its passages carry no judge by design (*who
pasted this text* is a different question from *who judged what it says*),
and the ingest judgment is the `ingest` row `store_decomposition` writes. The
agent that cut is named in the metadata, which is enough for a reader and
does not put a segmentation in front of `review` as though it were a verdict
about the world.

## 6. Doubts: the server on its own cut

### 6.1 The checks

After the programmatic strategy runs, a pure function over the resulting
passages reports structural signs that the cut is poor. Closed vocabulary,
thresholds as constants in one module, not settings:

| Doubt | Raised when |
|---|---|
| `single_passage` | one passage, and the document runs past 1,200 characters, about a dozen sentences |
| `fragments` | more than half the passages are under about forty characters |
| `outsized` | one passage is several times the median length |
| `mid_sentence` | a cut where the passage before does not end in terminal punctuation and the passage after begins in lower case |

The result carries them as `doubts: [{kind, detail}]`, with `detail` naming
the passages involved. A doubt refuses nothing: the passages are stored, and
the agent re-cuts or proceeds. Cuts the agent supplied are not doubted; the
agent chose them.

### 6.2 Not warnings

`warnings` on other tools are `Advisory` rows: a closed vocabulary the
reviewing agent groups on, each kind with a stance that decides whether a
`proceeded_despite_warning` journal row is written, and a `notify_user` flag
that means raise it with a person. A doubt is none of that. It argues with
nothing the agent did, it wants no person, and it is not a verdict. Putting
it under `warnings` would either dilute the dashboard with rows about
paragraph breaks or force a stance on a thing that has none. Its own key,
its own small vocabulary.

### 6.3 Calibrate before building

Measured on 27 September 2026 with `scripts/segmentation_doubts.py`, read-only,
over every graph the server held: 148 documents in the six that hold anything
(memory 84, petritype-server 48, freeagent-agent 5, petritype-rs-dev 5,
rcloud 5, cyberpunk-2020 1; default, main and cyberpunk-ttrpg are empty).

| Doubt | Documents | Rate | What they were |
|---|---|---|---|
| `single_passage` | 17 | 11.5% | every one a session note written as one paragraph, 850 to 2,000 characters (memory 15, petritype-server 2) |
| `outsized` | 3 | 2.0% | all genuine: a passage four to seven times the median, in two dev notes and one docs page |
| `mid_sentence` | 1 | 0.7% | a false positive: a markdown heading line as its own passage, followed by a paragraph starting with a lower-case identifier |
| `fragments` | 0 | 0% | no list-heavy or transcript corpus exists yet |

Two thresholds moved as a result. `single_passage` now needs more than 1,200
characters rather than 600: a paragraph-sized note left whole is the author's
own cut, while a dozen sentences with no break are worth a look. And a passage
that is a markdown heading, or a single line shorter than 80 characters, no
longer counts as an unfinished sentence, so `mid_sentence` is not raised
across a title.

`fragments` has not been exercised: nothing measured could have raised it.
The first list-heavy or transcript corpus ingested should be measured before
Stage 1's thresholds are trusted.

## 7. Guidance for the agent

What the guide (`epimemer_prompts/DEFAULT.md`) says, in substance. The shapes
the rules cannot see, and where the cut goes:

- **Transcripts and dialogue**: at speaker turns, keeping a question with its
  answer when they are short.
- **Contracts and specifications**: at clause or numbered-section boundaries,
  keeping a heading with the text under it.
- **Narrative**: at scene changes, never inside a scene.
- **Mixed prose, tables and code**: a table or code block stays with the
  sentence that introduces it.
- **Chat logs and notes**: at changes of subject, not at message boundaries.

And the rules that hold for every shape: a passage is the smallest stretch
that stands on its own as the context of a claim; a sentence is never
separated from what qualifies it; when the programmatic cut is right, leave
it. The guide also says plainly that a re-cut is possible only before
decomposition, and that a document already decomposed is re-ingested rather
than re-cut.

## 8. Stages

Each stage is one commit or one tightly coupled group, tests written first,
both backends through the `storage` fixture where storage is touched.

**Stage 0: measure and record.**
- `segmentation/doubts.py`: `doubt_cut(content, segments) -> list[Doubt]`,
  pure. Tests: each doubt on a document built to trigger it and only it;
  ordinary prose raises none.
- `segment_text` records `metadata["segmentation"]` for both existing
  strategies. Test: the document read back names the strategy and count.
- A script under `scripts/` runs `doubt_cut` over every document in a named
  graph and prints the rates; it was run on every graph the server held, six
  of them non-empty, and the numbers and the thresholds they settled are in
  §6.3.

**Stage 1: anchors.**
- `segmentation/anchors.py`: `segments_from_anchors(document, anchors) ->
  list[Segment] | AnchorsRefused`, pure. Tests: coverage, first occurrence,
  lengthened anchor selecting a later occurrence, every refusal in §4.2 named
  at once, whitespace trimming with accurate spans, one anchor equals one
  passage.
- `segment(cuts=...)` in `tools.py` and the `server.py` wrapper; refused with
  `segmentation_strategy`. Tests through the tool: passages stored with the
  agent's boundaries, `segmentation.strategy == "agent"`, no doubts raised.

**Stage 2: re-cutting.**
- `resegment_tx` on `StorageBackend`, memory, SurrealDB and the instrumented
  wrapper, with the parity tests the repository uses for protocol methods.
  Tests: replaces passages and stamps history in one transaction; refuses
  with the citing count when any node cites a passage; nothing changes on
  refusal.
- `resegment` tool and wrapper. Tests: end to end through `segment`,
  `resegment`, `store_decomposition`; a `resegment` after decomposition is
  refused; the lexical arm no longer returns a replaced passage.

**Stage 3: words.**
- `DEFAULT.md` §7 guidance and the procedure; `docs/` gets an ingest section
  or a new `docs/INGEST.md` (there is none today), covering cuts, doubts and
  re-cutting; `INTEGRATION.md` if it lists result keys; `CHANGELOG.md`
  under Unreleased. The `PROPOSED_FEATURES.md` entry is already a pointer
  here and is deleted when Stage 3 ships.

## 9. Rejected

- **The server calls a model.** Consistent quality regardless of client, at
  the price of keys, cost, model choice and non-determinism inside the
  server, and every property that follows from their absence. Easy to give
  up later, hard to win back.
- **A three-step ingest for everyone**: `segment` proposes without storing,
  the agent confirms or cuts, then stores. Every document pays a round trip
  so that the awkward minority can be re-cut, and the two-step flow the
  guide, the docs and every client already follow changes shape.
- **Re-cutting after decomposition.** Claims would have to move between
  passages, and a claim's passage is what search shows as its context. That
  is a new document version, not a re-cut, and ingesting again gives it.
- **Retiring replaced passages instead of deleting them.** §5.3.
- **Doubts as `Advisory` kinds.** §6.2.
- **A `segmentation_strategy` setting for "agent".** There is nothing to
  configure: the agent supplies cuts or it does not.

## 10. Open

Settled: a re-cut deletes the passages it replaces, guarded in the
transaction (§5.3), confirmed by the user on 27 September 2026.


- **Whether a re-cut wants a `reason`.** The row is not a verdict and there
  is no journal entry to carry prose, so none is asked for. If the
  `history` entry turns out to be read and the reader wants to know why,
  add it then.
- **The anchor minimum.** Three words or twelve characters is a starting
  point; Stage 1's tests will show whether real anchors trip it.
