# Ingest: passages, doubts and the agent's own cut

Ingest is two calls. `segment` stores a document and cuts it into passages;
the agent reads each passage and hands back topics, facts and inferences with
`store_decomposition`. This page is about the first call: how the server cuts,
how it says when it doubts its own cut, and how the agent states a better one.

For the tool signatures and parameter lists, see
[INTEGRATION.md](../INTEGRATION.md#available-tools). For the design and the
reasons behind it, see `dev-docs/SEGMENTATION.md`.

---

## 1. What a passage is for

A passage is the unit of provenance. Every claim records the passage it came
from (`source_id` on the node), and `search` returns the passages its keyword
arm matched under `segments`, to answer *where did I read that?* rather than
*what do I believe?* ([RETRIEVAL.md](RETRIEVAL.md)).

So a cut decides what a reader will later be shown as the context of a claim.
A passage that spans two subjects muddies the context of every claim in it,
and one that separates a sentence from its qualifier can lose a hedge, a date
or a speaker. Nothing downstream revisits a cut once claims cite it, so the
time to get it right is before decomposing.

## 2. How the server cuts

The server cuts by rule and never calls a language model. Two rules are available,
chosen by `EPIMEMER_SEGMENTATION_STRATEGY` or per call with
`segmentation_strategy`:

| Strategy | Cuts | Goes wrong on |
|---|---|---|
| `paragraph` (default) | at blank lines; a document with none is one passage | a transcript or chat log with no blank lines, a bulleted list with blank lines between items, a long paragraph covering several matters |
| `semantic` | where the similarity between neighbouring sentences drops sharply | headings, dialogue, tables and code, which the sentence splitter misreads; a document with one abrupt change and no others |

Paragraph cutting is right for most prose. The agent overrides it only when
there is something to point at: a doubt from the server, or a shape the rules
cannot see.

## 3. Doubts

After a rule has cut, the server checks the passages for structural signs of a
poor cut and reports them in the `segment` result as `doubts`, each a
`{kind, detail}` whose detail names the passages by position:

| Kind | Raised when |
|---|---|
| `single_passage` | one passage, and the document runs past 1,200 characters |
| `fragments` | more than half the passages are under 40 characters, in a cut of at least four |
| `outsized` | a passage is more than four times the median length, in a cut of at least three |
| `mid_sentence` | a passage ends without terminal punctuation and the next begins in lower case; a heading or a short single line does not count |

A doubt refuses nothing: the passages are stored either way, and the agent
re-cuts or carries on. Doubts are separate from `warnings`: a doubt is about
the server's own cut and never asks for a person. A cut the agent supplied is
never doubted.

## 4. Stating a cut: anchors

The agent states a cut as **anchors**, one per passage: the text the passage
begins with, copied exactly from the document.

```python
segment(
    content=transcript,
    cuts=[
        "INTERVIEWER: Where were you",
        "INTERVIEWER: Let us turn",
    ],
)
```

The server reads them in order:

- The first anchor is found from the start of the document, and every later
  one from the end of the anchor before it, at its **first occurrence**. To
  reach a later occurrence of a phrase, lengthen the anchor until that
  occurrence is the first.
- Each passage runs from its anchor to the next anchor, and the last to the
  end of the document. Whitespace around a passage is trimmed, and its span
  still points at its text exactly.
- The first anchor must begin the document. Whitespace before it is fine;
  anything else would belong to no passage, so it is refused rather than
  dropped.
- An anchor is at least three words or twelve characters. A shorter one
  matches too early too often: `WITNESS:` finds that speaker's first turn,
  wherever it was meant. A heading of one short word, such as `## Fees`, is
  the usual case that falls short; carry the anchor into the line below it,
  newline included.
- One anchor at the start is a valid cut. It says the document is one
  passage.

A list with anything wrong in it is refused whole, and nothing is written. The
refusal names every problem at once: an anchor not found after the one before,
quoted; an anchor that is too short; a passage left empty; text before the
first anchor, quoted in part; or no anchors at all.

## 5. When to cut, and with which call

The procedure:

1. Call `segment` as usual.
2. Read the passages and any `doubts`.
3. If a doubt is right, or a passage visibly spans two subjects or separates
   a sentence from what qualifies it, call `resegment` with your own cuts.
4. Decompose against the passage ids the last call returned.

For a document you already know the rules will cut badly, pass `cuts` to
`segment` on the first call instead. `cuts` and `segmentation_strategy`
answer the same question, so giving both is refused.

Where the cut goes, by the shape of the document:

| Shape | Cut |
|---|---|
| Transcripts and dialogue | at speaker turns, keeping a short question with its answer |
| Contracts and specifications | at clause or numbered-section boundaries, keeping a heading with the text under it |
| Narrative | at scene changes, never inside a scene |
| Prose mixed with tables or code | anywhere but between a table or code block and the sentence that introduces it |
| Chat logs and notes | at changes of subject, not at message boundaries |

For every shape, a passage is the smallest stretch that stands on its own as
the context of a claim, and a sentence stays with what qualifies it. When the
rule's cut is right, leave it.

## 6. Re-cutting

`resegment(document_id, cuts)` replaces the passages of a document that
`segment` stored. It is allowed only while no claim cites any of them:

- The check and the replacement are one transaction, so a decomposition
  cannot land between them. Nodes at any status count, retired ones included.
- Refused, the result says `resegmented: false` and gives the count in
  `citing_nodes`, and nothing has changed. Once `store_decomposition` has run
  the passages are provenance and stay as they are. To cut a decomposed
  document differently, ingest it again as a new document.
- Accepted, the old passages are **deleted** and the new ones take their
  place, so the keyword arm of `search` stops returning the old ones. The cut
  they made is kept on the document (§7).

A re-cut asks for no reason and writes no journal row, so `review` never
shows one.

## 7. What the document records

Every path that cuts records how it cut in the document's
`metadata["segmentation"]`:

```json
{"strategy": "agent", "passages": 2,
 "history": [{"strategy": "paragraph", "passages": 1,
              "replaced_at": "2026-10-08T12:00:00+00:00",
              "replaced_by": {"agent_id": "archivist", "digest": "..."}}]}
```

`strategy` is `paragraph`, `semantic` or `agent`. `history` is empty until a
re-cut, which appends the cut it replaced, oldest first, with when and by
which judge. A re-cut made with no judge claimed has no `replaced_by` key. A
document stored without this record gets one on its first re-cut, with the
passage count it replaced and the strategy left `null`.

## 8. What the calls return

`segment`:

| Key | Holds |
|---|---|
| `document_id` | the stored document |
| `active_graph` | the graph it was stored in, to check before decomposing |
| `segments` | `[{segment_id, char_count}]`, in document order |
| `doubts` | `[{kind, detail}]`, empty when the cut looks sound or the agent supplied it |

`resegment`, accepted:

| Key | Holds |
|---|---|
| `resegmented` | `true` |
| `document_id`, `active_graph` | as for `segment` |
| `segments` | the new passages, `[{segment_id, char_count}]` |
| `replaced` | how many passages the re-cut deleted |

`resegment`, refused because claims cite the passages: `resegmented: false`,
`document_id`, `citing_nodes` and `refused`, a sentence saying what to do
instead. Malformed cuts and an unknown document come back as an `error`, as a
malformed `segment` does.
