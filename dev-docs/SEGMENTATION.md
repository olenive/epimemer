# Segmentation: the agent cuts, on evidence

A design, built: the code is `epimemer/pipelines/segmentation/` and the `segment` and `resegment` tools.

There are two ways to get better cuts than a rule can make: the calling agent
cuts, or the server calls a model itself. The user chose the first.
`docs/INGEST.md` describes the behaviour for someone using it: the doubts, the
anchor contract and its refusals, re-cutting, and what the document records.
This document holds the reasons.

The one property this design protects: **the server makes no model calls of
its own.** That is why Epimemer has no API keys, no provider configuration,
no per-call cost, deterministic tests, and no opinion about which model a
client runs. Everything below is arranged so that better cuts arrive without
giving that up.

---

## 1. What a passage is for

A passage is the unit of provenance, so a cut decides what a reader will later
be shown as the context of a claim (`docs/INGEST.md` §1). The damage from a bad
cut is quiet and it lasts, because nothing downstream revisits a cut. Passages
are also the corpus the lexical arm of search runs over; they are not embedded.

## 2. The two strategies, and how they fail

Both run without a model, and both stay. **Paragraph**, the default, cuts on
blank lines. **Semantic**, TextTiling style, embeds each sentence with the
local model and cuts wherever similarity between neighbours drops more than one
standard deviation below the mean, so a document with one abrupt change gets no
cut while a uniformly meandering one gets many. `docs/INGEST.md` §2 lists where
each goes wrong.

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

The programmatic cut always runs first, because it is free and right for most
documents, and the agent overrides it only when there is something to point
at: the server doubts its own cut (§6), or the agent recognises a shape the
rules cannot see.

Judging a proposed cut is easier and more reliable than proposing one from
scratch, and cutting every document by hand would cost a judgment per
document and invite mistakes on long texts, for no gain on the prose that
paragraph cutting already handles. The exception is a document the agent
already knows is awkward, which it may cut on the first call.

## 4. Stating a cut: anchors

A cut is stated as **anchors**: for each passage, the text it begins with,
copied exactly from the document. The contract and its refusals are in
`docs/INGEST.md` §4. The decisions behind them:

- **The first occurrence wins**, searched from the end of the previous anchor.
  An agent that needs a later occurrence lengthens the anchor until it is the
  first, which is deterministic and needs no notion of ambiguity on the server.
- **An anchor is at least three words or twelve characters**, since a shorter
  one matches too early too often (§10).
- **Text before the first anchor is refused rather than silently dropped**, so
  the anchors must cover the document.
- **One refusal names every problem at once**, on the pattern
  `apply_reflection` uses for a malformed batch, and nothing is written. A
  missing anchor is quoted with any space other than an ASCII one escaped (a
  non-breaking space shows as `\xa0`), and is named as differing only in
  Unicode normalisation when its NFC or NFD form is found there. A refusal is
  prose, as `BoundaryRefused` is: nothing branches on it.

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

`segment` takes an optional `cuts`; given, no strategy runs and the result
carries no doubts. `docs/INGEST.md` §5 has the rest of the call.

### 5.2 `resegment(document_id, cuts, expected_graph, judge_token)`

A separate tool rather than `segment` with a `document_id` instead of
`content`. A tool whose required argument is *one of these two* is this
repository's own tell that two tools are wearing one name, and the two differ
in the guard they need: `segment` creates, `resegment` replaces under a
precondition.

The precondition: **no node cites any of the document's passages**, at any
status, since a retired claim still names its passage. It is checked inside
the same transaction as the replacement, by `resegment_tx` on
`StorageBackend`, which every backend implements in its own way. A check in
one call and a replacement in the next would let a decomposition land between
them and leave claims pointing at passages that no longer exist.

What none of the backends sees is a decomposition already in flight: one that
read the passages before the re-cut and writes its nodes after it. Its write is
not refused, and its nodes cite passages that are gone. Closing that would
take a check inside `write_batch_tx` that the passages still exist;
`dev-docs/ISSUES.md` records it as deferred, with its trigger.

`resegment` asks for no `reason`: the row is not a verdict and there is no
journal entry to carry prose.

### 5.3 What a re-cut replaces

The old passages are **deleted**, and this is the one place the design steps
outside the append-only habit. The user confirmed it.

They are real rows by then: `segment` stores every passage before it returns
the ids, and the passages join the lexical search corpus at once. Holding them
back until decomposition would need the server to keep state between the two
calls, or the agent to echo the passages back, and the ids would not survive a
reconnect.

A passage before decomposition is material, not a claim: it carries no judge,
no journal row, and nothing cites it. Retiring instead of deleting would add a
status to `Segment` and a filter to every reader of segments, each a place a
retired passage can leak back into a search result, against one guarded
delete of rows nothing refers to. Nothing epistemic is lost: the document is
untouched, and the cut it replaced is recorded on the document (§5.4).

The precondition is what makes this safe, and it is enforced in the
transaction, never by convention.

### 5.4 What is recorded

Every path that cuts writes a `segmentation` entry into the document's
`metadata`, so a reader of a passage can tell whether its boundaries were a
rule's or a judgment's, and a re-cut document keeps the rule's result in
`history`:

```
{"strategy": "agent", "passages": 2,
 "history": [{"strategy": "paragraph", "passages": 1,
              "replaced_at": "...", "replaced_by": {"agent_id": ..., "digest": ...}}]}
```

With no judge, `replaced_by` is absent rather than null, since every backend
drops a None-valued key on the way in.

No journal row. A document and its passages carry no judge by design (*who
pasted this text* is a different question from *who judged what it says*),
and the ingest judgment is the `ingest` row `store_decomposition` writes. The
agent that cut is named in the metadata, which is enough for a reader and
does not put a segmentation in front of `review` as though it were a verdict
about the world.

## 6. Doubts: the server on its own cut

### 6.1 The checks

After a programmatic strategy runs, a pure function over the passages reports
structural signs that the cut is poor; `docs/INGEST.md` §3 has the kinds and
their thresholds. The thresholds are constants in one module, not settings: a
per-graph setting would make the calibration a property of whichever graph was
measured (§6.3). Cuts the agent supplied are not doubted; the agent chose
them.

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
over every graph the server held: 148 documents in the six that held anything,
84 of them in the development graph.

| Doubt | Documents | Rate | What they were |
|---|---|---|---|
| `single_passage` | 17 | 11.5% | every one a session note written as one paragraph, 850 to 2,000 characters (15 in the development graph) |
| `outsized` | 3 | 2.0% | all genuine: a passage four to seven times the median, in two dev notes and one docs page |
| `mid_sentence` | 1 | 0.7% | a false positive: a markdown heading line as its own passage, followed by a paragraph starting with a lower-case identifier |
| `fragments` | 0 | 0% | no list-heavy or transcript corpus exists yet |

The measurement set two thresholds. `single_passage` needs more than 1,200
characters, where the first guess was 600: a paragraph-sized note left whole
is the author's own cut, while a dozen sentences with no break are worth a
look. And a passage that is a markdown heading, or a single line shorter than
80 characters, does not count as an unfinished sentence, so `mid_sentence` is
not raised across a title.

`fragments` has not been exercised: nothing measured could have raised it.
The first list-heavy or transcript corpus ingested should be measured before
its threshold is trusted.

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
- **A `segmentation_strategy` setting for "agent".** There is nothing to
  configure: the agent supplies cuts or it does not.

## 10. Open

- **The anchor minimum.** Three words or twelve characters clears the anchors
  an agent writes for each shape: a speaker turn
  (`INTERVIEWER: Where were you`), a numbered clause (`3. Termination`), a chat
  line (`[09:40] ben: separate thing`). The bare label `WITNESS:` is refused,
  and it is the anchor the minimum exists for, since it finds that speaker's
  first turn wherever the agent meant. The one natural anchor it trips is a
  short heading: `## Fees`, `# Changelog` and `2. Term` are two words, marker
  included, and under twelve characters, so the agent carries the anchor into
  the next line, newline included, which is the copy a model is likeliest to
  get wrong. Accepting a whole line as an anchor would let those through, since
  a heading's line is usually unique; it waits until a real refusal says it is
  needed.
