# Similar-inference edges

A design, built: the code is `epimemer/pipelines/reflection/similar_inferences.py` for the nomination and `epimemer/pipelines/query/corroboration.py` for the consequence.

`reflect` offers pairs of inferences that say nearly the same thing from
evidence they do not share, the agent records whether they agree, and
corroboration shows that agreement beside its count rather than in it.

---

## 1. What is nominated

`reflect` returns `similar_inferences`: pairs of **active** inferences whose
embeddings score at or above the one nomination bar (0.80) and which

- rest on **no common premise**, by either edge that records one
  (`derived_from` from the inference, `supports` into it);
- stand in **at least one common metacontext**, because `one_claim` refuses a
  pair that does not, and a nomination that can only be declined is a worklist
  that cannot be worked;
- have **not been judged**: no live `similarity`, `contradiction`,
  `variant_of` or `assessed` edge between them.

Each side comes with its own premises, since seeing two separate bodies of
evidence is what the agent is judging. The sweep is global over active
inferences, and every read after the scoring is asked only about inferences
in a pair that cleared the bar.

**On the graphs measured so far this list is empty.** The merge sweep's
header records 123 real inferences, 5,053 pairs and none at the bar (maximum
0.66), with the best-scoring pairs sharing vocabulary while saying different
things. The list costs a read of the active inferences, their embeddings and
one matrix product to establish that, and fills as a graph accumulates
readings of the same conclusion.

## 2. Why a pair sharing a premise is not nominated here

It is already offered, as `inference_merge_candidates`. The two lists
partition similar inference pairs by premise overlap, so no pair is asked
about twice under two framings: *are these one conclusion to merge* for a pair
drawn on common evidence, *do these agree* for a pair drawn on separate
evidence.

The verdict index is shared, so a pair judged on either list is suppressed on
both. That matters when a fact merge brings two premises together: a pair
offered here can then qualify for the merge list, and a verdict already
recorded on it carries over rather than being asked again.

One pair is offered by neither list: a pair sharing a premise whose
metacontext sets overlap without being equal. The merge list drops it because
`merge_inferences` would refuse it, and this list leaves it to the merge list.

## 3. The verdict path

`apply_reflection(similarities=[…])` takes any pair of like nodes, so these
pairs are judged through it, `one_claim` where the two draw the same
conclusion and `distinct` where they do not, with everything else as
`docs/REFLECTION.md` §6 describes.

## 4. What corroboration does

A `similarity` neighbour that is an inference is **reported, not counted**. It
comes back under `agreeing_inferences` on the subject's corroboration, each
entry the inference's id and the first hundred characters of its content, and
the count is what it would be without the edge.

That makes three treatments of a neighbour:

- **counted**: a restatement of the claim, with what supports it;
- **excluded**: a neighbour joined by `contradiction`, `variant_of` or
  `retracted_similarity`, or retired as `corrected`;
- **reported without counting**: a look-alike about another period
  (`adjacent_periods`), and an agreeing inference (`agreeing_inferences`).

Each neighbour gets one treatment, and the period check comes first, so an
inference whose sources date it to another period is reported as adjacent.

**Why this treatment.** A conclusion is supported by its own premises. Counting
its partner would let the agent's verdict that two readings agree raise a
number callers read as independent sources, so a judgment about wording would
move a measure of evidence. The agreement is still real information, and where
a search returned only one of the two, the report is the only place the other
appears.

**It applies to every inference neighbour**, including a `one_claim` recorded
on an `inference_merge_candidates` pair or an edge made with `link`: the edge
does not record which list nominated it, and the reason above does not depend
on it.

## 5. What was rejected

- **Excluding it**, as a contradiction is excluded. The two inferences do not
  conflict, and leaving the agreement out of the response would hide
  something the graph knows, where `corroboration.py` shows its working
  throughout.
- **A new verdict or edge type for agreement.** `similarities` already takes
  any like pair, and the suppression, retraction, reopen and shape rules come
  with it. The neighbour's node type is enough for corroboration to tell
  agreement from restatement, so a second edge type would add a reader to
  every one of those mechanisms for no information.
- **A lower bar for this list.** The measured pairs below 0.80 share
  vocabulary and say different things, which is a worklist of declines.
- **Telling edges apart by the list that nominated them**, counting a
  `one_claim` from the merge list and reporting one from this list. The edge
  records no origin, and the two would differ only in whether the premises
  overlap, which does not change what agreement is evidence of.
