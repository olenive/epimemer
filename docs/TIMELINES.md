# Timelines: when something happened, and in what order

A timeline is a chronology of the world the graph holds claims about: the fire,
the flood, the speech the mayor gave afterwards. Points on it carry the dates
their sources gave them, the order sources stated between them, and rules for
the things that happen again and again. The examples below come from one small
invented history: a graph called `riverford` holding three documents about a
river town, a parish register, a newspaper obituary and the minutes of a
council meeting. Nobody wrote a chronology of the town, so the graph builds one
out of what those three say.

Design notes: [dev-docs/TIMELINES.md](../dev-docs/TIMELINES.md). The tool
reference is [INTEGRATION.md](../INTEGRATION.md)'s Timeline Operations table.

---

## 1. Two clocks

Two questions about time look alike and are answered in different places.
*When was this claim true?* is **validity**, recorded per source as intervals
on the `sourced_from` edge: "the mill was owned by the Harker family from 1861
to 1904" is a claim with a period over which it held. *When did this happen,
and in what order?* is a **timeline**: "the mill burned down" is a point, and a
fact can be dated to it. A fact can carry both, in separate fields, so that
neither answer is read as the other. Validity has its own page,
[VALIDITY.md](VALIDITY.md), whose §1 also names the third clock, *when the
graph learned this*, which is `created_at` and is read by `graph_as_of`.

---

## 2. Points carry the dates their source gave

A timepoint holds what a source said about when something was, and nothing
else. Its **kind** is read off the fields rather than chosen:

| Kind | What the source gave | Example |
|---|---|---|
| `instant` | a start | "the fire broke out on 3 May 1897" |
| `interval` | a start and an end | "the flood lasted from 11 to 19 November 1899" |
| `vague` | a label, no date | "during the bad winter" |

`add_timepoint` derives the kind and names it in the response, so a caller can
see what it created. Passing `kind` is refused: a field that could disagree
with the dates beside it is worse than no field.

Two shapes are refused rather than stored. **An end with no start**: `start` is
where the mark goes on the axis, so a point with only an end has nowhere to be
drawn. Validity can express "ended in 1904, began we do not know when", because
an interval there has an endpoint that is explicitly unknown; a timepoint
cannot. And **a point with neither a date nor a label**, a mark with nothing on
it.

**A vague point never gets an approximate date.** "During the bad winter" does
not become 1899-01-01. The position it does get is derived from the order
sources state (§3), computed when a query runs, and reported beside the point
rather than written into it. A guessed date in `start` would be
indistinguishable from a date the parish register actually gave.

Dates are Gregorian, years 1 to 9999. Fiction set in a real-shaped calendar is
fully supported: a novel opening in May 1897, a game set in 2077. Geological
spans, sub-microsecond events and invented calendars cannot be stored today;
the backlog entry *Timelines on other clocks* in
[dev-docs/PROPOSED_FEATURES.md](../dev-docs/PROPOSED_FEATURES.md) sketches the
clock-and-unit model that would hold them.

---

## 3. A source can state an order without giving a date

The obituary says the mill burned "the summer before the great flood", and
dates neither. `order_timepoints` records that one source said one point came
before another: a list of `pairs` of `earlier_id` and `later_id`, with one
`source_id` and one `basis` for the batch. A list rather than one pair, because
a source that states an order usually states several at once, and one reading
of one source is one judgment. Each pair is stored as an *ordering constraint*,
which is the word the responses and the later tools use (`constraint_id`).

**`basis` has no default, and the two values are not degrees of confidence.**
`stated` means the source says the order in words. `inferred` means the judge
read it off tense or context. The distinction carries weight because a document
that narrates the fire and then the flood has stated nothing about which came
first: narrative order is not chronological order. That is also why extraction
never proposes an order. `store_decomposition` proposes dates and only dates,
because a detector looking for "before" in prose would fill the graph with
assertions no source made. The source and the judge are different people, and
both are named on the record: the obituary says it, and an agent read the
obituary and decided that it did.

### An undated point gets a position, and never a date

Once the council minutes date the flood to November 1899, the undated fire has
somewhere to be. `query_timeline` reports two bounds on it:

| Field | Means |
|---|---|
| `earliest` | the latest moment among the dated points that reach this one |
| `latest` | the earliest moment among the dated points this one reaches |

So the fire comes back with `latest` = 11 November 1899 and no `earliest`,
which is a real answer: before the flood, we do not know when. Either bound can
be absent, and a point nothing constrains gets neither.

**Bounds are computed per query and never written onto the point.** Retire the
obituary's assertion later and the bound goes with it, because nothing stored
it. This is the same discipline as §2: a derived position is reported beside
what a source said, never into it.

`query_timeline(basis="stated")` builds the order, and the bounds that follow
from it, out of stated assertions alone. Ask for it when two accounts disagree
and you want what was asserted in words rather than what was worked out. The
default counts both, since an inferred assertion is an honest record too. The
three ordering queries, `before`, `after` and `between`, name points by id and
answer over the same order; `between` is the intersection of the other two, so
a point that neither end reaches is not between them.

---

## 4. When sources disagree

The newspaper obituary says the fire came before the flood. A reader of the
parish register concludes the opposite. Both assertions are recorded and
neither is refused, because **the first source read is not assumed right**: a
disagreement is held rather than resolved at the moment of writing. The graph
opens a *temporal contradiction* and carries on. Two kinds open one:

- **A cycle.** The stated order loops: the fire before the flood, the flood
  before the fire. Dates take part in the check, so an assertion that the flood
  came first, when the flood is dated 1899 and the fire 1897, is a cycle too.
  Two dated points whose intervals overlap settle nothing and impose no order.
- **Crossed bounds.** A point is squeezed until its `earliest` is later than
  its `latest`. Nothing loops, and the assertions still cannot all hold.

While a contradiction is open, the assertions on it are set aside from the
derived order, and the points they name come back **`contested`**, with the
contradiction's id.

**`contested` on a point says the order around it is disputed, never that its
date is wrong.** A contested point keeps every date a source gave it and gets
no derived position. A date is what a source said about one point, an ordering
assertion is what a source said about a pair, and a dispute between them is a
reason to stop trusting the derived order rather than a reason to remove a
date. A date a source got wrong is corrected at the claim level, with
`supersede_by`.

On a search result the flag has its own name: a fact linked to a contested
point carries **`date_contested`**, separate from `contested` on the claim
itself. An agent reading "the treasury was empty at the time of the fire" needs
to know the fire's place in time is disputed, and must not read that as doubt
about the treasury.

### The three verdicts

`resolve_temporal_contradiction` answers one, and `because` is required for all
three because the sentence is the judgment.

**Retire one assertion, with a reason.** The three real reasons are that the
source was misread, that the source is unreliable, or that the order it gives is
narrative rather than chronological. The contradiction closes and every other
assertion in it returns to the live order, unless a second open contradiction
still holds it.

**The point is really two events.** Both sources are right, each about a
different occurrence of what the point names: the town had two fires. The point
is what is wrong, so the verdict splits it. A new point takes the same label,
and the assertions and facts the judge names move to it. A fact's date is part
of what it claims, so nothing moves a fact off its date without someone
deciding it, and anything the judge does not name stays where it is.

**Hold.** The sources genuinely disagree and nothing on hand settles it. The
contradiction stays open, the point stays contested, and `reflect` stops
nominating it. A hold is cleared by new evidence, an ordering assertion or a
dated point touching any point in the contradiction, and by nothing else. It is
not a snooze, and nothing reopens it on a schedule.

### Two points that turn out to be one moment

Extraction makes one point per distinct phrase, so the obituary's "the mill
fire" and the council minutes' "the night the mill went up" are two marks for
one event. `merge_timepoints` folds one into the other: every ordering
assertion naming the retired point moves to the survivor, and so does every
fact dated to it. All of them, not a chosen subset, because a merge asserts the
two are one moment, so every fact about either is a fact about it. The retired
point is kept, marked with the survivor it became.

**The merge is refused while a live assertion orders the two against each
other**, and the refusal names it. A source that said the mill fire came before
the night the mill went up said they are not one moment. Retire that assertion
with a reason first, then merge.

---

## 5. Things that recur

Market day comes every seventh day. The parish service is on the second Tuesday
of every month. Those are rules, and `add_recurrence` stores the rule once
rather than a row of dates, in one of two ways:

- **By arithmetic**: an `anchor` and a `period`. "Every third day from the
  founding" needs no calendar, so this works on an invented timeline too.
- **By the calendar**: `rrule`, an RFC 5545 string, the format calendar
  software uses, for anything said in months and weekdays.

**Occurrences are computed when someone asks, and never stored.** A stored list
and the rule that made it are two things that can disagree. `query_timeline`
computes a rule's occurrences into its answer beside the stored points, capped
per rule per call: a rule can produce occurrences without end, so the response
says `truncated` for that rule and names the window it did cover. Asking for
more than the ceiling gets the ceiling.

An occurrence is identified by **`occurrence_start`**, the start the rule gives
it. There is no occurrence number: for a calendar rule, numbering would need a
walk from the rule's first occurrence, cheap near it and not cheap a century
later, so a number that had to be capped would be missing exactly where most
queries land. "The twelfth market" is a label a fact can carry in its own words
if a source says it. The response to `add_recurrence` previews the first few
occurrences, and it is worth reading: an rrule string is easy to mistype in a
way that still parses, and the preview is where that shows up.

### Two levels a fact can attach to

Linked to the **rule**, a fact holds at every occurrence: "market day is held
in the square". That is `create_timelink` with `recurrence_id` in place of
`timepoint_id`, and the response gives the rule's label and the bounds it is in
force between. Linked to **one occurrence**, a fact is about that one: "the
market of 8 June 1897 was held in the churchyard". The second needs that
occurrence to be a real point, which `add_timepoint` makes when given
`recurrence_id` and `occurrence_start` in place of dates. The point takes its dates and its words
from the rule, and can then be linked, ordered and disputed like any other.
Asking twice gives the same point back. `query_timeline` shows both levels on
every occurrence it returns: the rule's facts under `linked_via_rule`, repeated
on each occurrence they hold at, and that occurrence's own facts under
`linked`.

### Exceptions

`record_recurrence_exception` records an occurrence that broke the pattern:
`cancelled` for one that did not happen, `moved` with `moved_to` for one that
happened at another time. A cancelled occurrence is left out of query answers,
and materialising one is refused, since making a point of something the graph
records as not having happened is always a mistake. A moved occurrence keeps
the start the rule gave it as its identity and is reported at its new date, so
materialising it before and after the move gives one point, not two.

### Ending a rule never retires it

"Christmas is 24 to 26 December" does not stop having been true, so a rule has
no lifecycle and nothing supersedes it. What a later reading changes is where
it ends. `end_recurrence` appends an end date with its reason, and the most
recent entry is in force, so "we thought the market ended in 1890, then learned
it was 1893" reads as two entries rather than as one answer that quietly
replaced another. The response gives the whole history, and omitting the date
takes an end back, which is how one recorded in error comes off.

### "Next" is measured on the timeline's own clock

A timeline carries a `reference_time`, its own present, set at
`create_timeline` or later with `set_reference_time`. Leave it unset for
anything that tracks real time; unset means *follow the clock*, which is not
the same as passing today's date, since that would freeze the timeline's
present at the moment somebody typed it. Asked for the next occurrence with no
window, `query_timeline` answers from that present, so a timeline anchored in
May 1897 answers in 1897 where the wall clock would be wrong by more than a
century. A caller who wants "next" measured from some other moment passes
`next_after`: the timeline's clock is the default, never something the caller
cannot override.

---

## 6. Reading the decisions back

**`reflect` nominates open temporal contradictions** in its own
`temporal_contradictions` list, beside the contradictions between claims. A
held one is left alone; an unanswered one comes back on every reflect until it
is answered or held. The answer goes to `resolve_temporal_contradiction` rather
than to `apply_reflection`, because its subjects are ordering assertions and
timepoints, which are neither nodes nor topics, and because the verdict is
reached outside a reflect sweep at least as often as inside one. See
[REFLECTION.md](REFLECTION.md).

**Every write above names a judge and lands in the decision journal**, under
its own kind: `temporal_order` for a stated order, `temporal_verdict` for a
contradiction settled or held, `timepoint_merge`, `recurrence`,
`recurrence_bound` and `recurrence_exception`. `review` reads them back, least
certain first, so a later agent or a person can see which orders were asserted
without the merges and the rules mixed in.
[ATTRIBUTION.md](ATTRIBUTION.md) covers the journal and the tools that read it.

**Four timeline tools name no judge**, because they assert nothing:
`create_timeline`, `set_reference_time`, `add_timepoint` and `create_timelink`
put marks on an axis and attach facts to them. The six that record what a source
said or what a judge decided go through the same gate as any other write.

---

## 7. What the dashboard draws today

The visualisation panel shows a timeline's dated points on a vertical axis, past
at the top. Four things beyond the dates are drawn.

**A point the order places.** A point nobody dated, that a source said came
after one dated point and before another, is drawn as a hatched band spanning
those two, with its label written on it word for word. Resolving a vague label
adds a position; it never replaces the words. With only one bound known the band
runs a short way past it and fades out, which is how the panel says "after the
fire, we do not know when" without drawing an edge nobody asserted.

**A point whose order is disputed.** A contested point keeps whatever place it
had, its date or the tray, and gains a red zigzag beside it. The mark says the
order here is disputed, not that the date is wrong: what two sources disagreed
about is the sequence, and the date one of them gave still stands. Resting on
the mark names the contradiction.

**What a rule says happens over and over.** A recurrence rule's occurrences are
small beads on a dotted spine in a lane beside the axis, one lane per rule. The
dots mean the graph has said nothing between one occurrence and the next: the
rule produced them, and nothing else is claimed. An occurrence somebody turned
into a real timepoint is drawn as an ordinary point instead, because that is
what it now is. A moved occurrence sits on the date it moved to.

Occurrences are worked out on the spot and never stored, so the panel has to
pick a window. It uses the span of the timeline's dated points widened by one
period of the rule on each side, which brings in the occurrence either side of
the data. A timeline with no dated points uses its own stated present, and one
with neither draws no beads, since the only alternative is to enumerate from
today and put marks where the graph never said anything.

**What stays in the tray.** Points nothing constrains, contested points with no
date, and points belonging to another timeline's clock. A chip in the tray is a
point the panel has nowhere honest to put.
