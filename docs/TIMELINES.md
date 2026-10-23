# Timelines: when something happened, and in what order

A timeline is a chronology of the world the graph holds claims about: the fire,
the flood, the speech the mayor gave afterwards. Points on it carry the dates
their sources gave them, the order sources stated between them, and rules for
the things that happen again and again. The examples come from an invented
graph called `riverford`, holding three documents about a river town: a parish
register, a newspaper obituary and the minutes of a council meeting.

Design notes: [dev-docs/TIMELINES.md](../dev-docs/TIMELINES.md). The tool
reference is [INTEGRATION.md](../INTEGRATION.md)'s Timeline Operations table.

---

## 1. Two clocks

*When was this claim true?* is **validity**, recorded per source as intervals
on the `sourced_from` edge: "the mill was owned by the Harker family from 1861
to 1904". *When did this happen, and in what order?* is a **timeline**: "the
mill burned down" is a point, and a fact can be dated to it. A fact can carry
both, in separate fields. Validity has its own page,
[VALIDITY.md](VALIDITY.md), whose §1 also names the third clock, *when the
graph learned this*, which is `created_at` and is read by `graph_as_of`.

---

## 2. Points carry the dates their source gave

A timepoint holds what a source said about when something was, and nothing
else. Its **kind** is read off the fields:

| Kind | What the source gave | Example |
|---|---|---|
| `instant` | a start | "the fire broke out on 3 May 1897" |
| `interval` | a start and an end | "the flood lasted from 11 to 19 November 1899" |
| `vague` | a label, no date | "during the bad winter" |

`add_timepoint` derives the kind and names it in the response; there is no
`kind` argument. It refuses **an end with no start**, since `start` is where
the mark goes on the axis, and **a point with neither a date nor a label**.

**A vague point never gets an approximate date.** The position it does get is
derived from the order sources state (§3), computed when a query runs, and
reported beside the point rather than written into it.

Dates are Gregorian, years 1 to 9999, which covers fiction set in a
real-shaped calendar (a novel opening in May 1897, a game set in 2077).
Geological spans, sub-microsecond events and invented calendars cannot be
stored; *Timelines on other clocks* in
[dev-docs/PROPOSED_FEATURES.md](../dev-docs/PROPOSED_FEATURES.md) is the
proposal for them.

---

## 3. A source can state an order without giving a date

The obituary says the mill burned "the summer before the great flood", and
dates neither. `order_timepoints` records that a source said one point came
before another: a list of `pairs`, each `{earlier_id, later_id}`, with one
`source_id` and one `basis` for the batch. Each pair is stored as an *ordering
constraint* (`constraint_id` in the responses).

**`basis` has no default, and the two values are not degrees of confidence.**
`stated` means the source says the order in words. `inferred` means the judge
read it off tense or context. A document that narrates the fire and then the
flood has stated nothing about which came first: narrative order is not
chronological order. For the same reason `store_decomposition` proposes dates
and never an order. Besides dates it proposes an undated point for a named
period ("during the Cold War", "in the Middle Ages"); a capitalised name that
is not a period, such as "in the README", proposes nothing.

### An undated point gets a position, and never a date

Once the council minutes date the flood to November 1899, the undated fire has
somewhere to be. `query_timeline` reports two bounds on it:

| Field | Means |
|---|---|
| `earliest` | the latest moment among the dated points that reach this one |
| `latest` | the earliest moment among the dated points this one reaches |

So the fire comes back with `latest` = 11 November 1899 and no `earliest`:
before the flood, we do not know when. Either bound can be absent, and a point
nothing constrains gets neither.

**Bounds are computed per query and never written onto the point**, so
retiring the obituary's assertion later takes the bound with it.

`query_timeline(basis="stated")` builds the order, and its bounds, from stated
assertions alone; ask for it when two accounts disagree and you want what was
asserted in words. The default, `all`, counts both. The three ordering
queries, `before`, `after` and `between`, name points by id and answer over
the same order; `between` is the intersection of the other two, so a point
that neither end reaches is not between them.

---

## 4. When sources disagree

The obituary says the fire came before the flood; a reader of the parish
register concludes the opposite. Both assertions are recorded and neither is
refused: the graph opens a *temporal contradiction* and carries on. Two kinds
open one:

- **A cycle.** The stated order loops. Dates take part in the check, so an
  assertion that the flood came first, when the flood is dated 1899 and the
  fire 1897, is a cycle too. Two dated points whose intervals overlap impose
  no order.
- **Crossed bounds.** A point is squeezed until its `earliest` is later than
  its `latest`.

While a contradiction is open, its assertions are set aside from the derived
order, and the points they name come back **`contested`**, with the
contradiction's id.

**`contested` on a point says the order around it is disputed, never that its
date is wrong.** A contested point keeps every date a source gave it and gets
no derived position. A date a source got wrong is corrected at the claim
level, with `supersede_by`.

On a search result the flag has its own name: a fact linked to a contested
point carries **`date_contested`**, separate from `contested` on the claim
itself, so doubt about when the fire was is not read as doubt about what the
fact says.

### The three verdicts

`resolve_temporal_contradiction` answers one with a `verdict`, and `because`
is required for all three because the sentence is the judgment.

**`retire_constraint`, with `constraint_id`.** One assertion should not be
believed: the source was misread, the source is unreliable, or the order it
gives is narrative rather than chronological. The contradiction closes and
every other assertion in it returns to the live order, unless a second open
contradiction still holds it.

**`not_the_same_event`, with `point_id` and `constraints_to_move`.** Both
sources are right, each about a different occurrence: the town had two fires.
A new point takes the same label, but not the dates, so a point with no label
cannot be split. The constraints named move to it, and so do the facts named
in `nodes_to_move`; anything not named stays where it is.

**`hold`.** The sources genuinely disagree and nothing on hand settles it. The
contradiction stays open, the point stays contested, and `reflect` stops
nominating it. Only new evidence clears a hold: an ordering assertion touching
any point in the contradiction. A new dated point clears nothing by itself,
since nothing ties it to a held point until an assertion does. Nothing reopens
a hold on a schedule.

### Two points that turn out to be one moment

Two phrases can name one moment: an obituary's "the mill fire" and the
minutes' "the night the mill went up", each added as a point, are two marks
for one event. `merge_timepoints` folds `merged_id` into `survivor_id`: every ordering
assertion naming the retired point moves to the survivor, and so does every
fact dated to it. The retired point is kept, marked with the survivor it
became.

**The merge is refused while a live assertion orders the two against each
other**, and the refusal names it. Retire that assertion with a reason first,
then merge.

---

## 5. Things that recur

Market day comes every seventh day; the parish service is on the second
Tuesday of every month. `add_recurrence` stores such a rule once, in one of two
ways:

- **By arithmetic**: an `anchor` and a `period`. This needs no calendar, so it
  works on an invented timeline too.
- **By the calendar**: `rrule`, an RFC 5545 string, for anything said in
  months and weekdays.

**Occurrences are computed when someone asks, and never stored.**
`query_timeline` computes a rule's occurrences into its answer beside the
stored points, at most `occurrence_cap` per rule per call up to a fixed
ceiling; a rule that hit the cap is marked `truncated` with the window it did
cover. The ordering queries (`before`, `after` and `between`) leave
occurrences out: an occurrence takes part in the order only once it is
materialised as a point (below).

An occurrence is identified by **`occurrence_start`**, the start the rule gives
it. The response to `add_recurrence` previews the first few occurrences: read
it, since an rrule string is easy to mistype in a way that still parses.

### Two levels a fact can attach to

Linked to the **rule**, a fact holds at every occurrence: "market day is held
in the square". That is `create_timelink` with `recurrence_id` in place of
`timepoint_id`, and the response gives the rule's label and the bounds it is in
force between. Linked to **one occurrence**, a fact is about that one: "the
market of 8 June 1897 was held in the churchyard". That needs the occurrence
to be a real point, which `add_timepoint` makes when given `recurrence_id` and
`occurrence_start` in place of dates. The point takes its dates and its words
from the rule, and can then be linked, ordered and disputed like any other.
Asking twice gives the same point back. `query_timeline` shows both levels on
every occurrence: the rule's facts under `linked_via_rule`, and that
occurrence's own facts under `linked`.

### Exceptions

`record_recurrence_exception` records an occurrence that broke the pattern:
`kind="cancelled"` for one that did not happen, `kind="moved"` with `moved_to`
for one that happened at another time. A cancelled occurrence is left out of
query answers, and materialising one is refused. A moved occurrence keeps the
start the rule gave it as its identity and is reported at its new date, so
materialising it before and after the move gives one point. An occurrence
holds one exception, so recording a second replaces the first. A range query
selects occurrences by the start the rule gave them, so one moved out of the
window still comes back, at its new date, and one moved into the window from
outside it does not.

### Ending a rule never retires it

"Christmas is 24 to 26 December" does not stop having been true, so a rule has
no lifecycle. `end_recurrence` appends an end date (`ends_at`) with its
reason, and the most recent entry is in force, so "we thought the market ended
in 1890, then learned it was 1893" reads as two entries. The response gives
the whole history, and omitting `ends_at` takes an end back.

### "Next" is measured on the timeline's own clock

A timeline carries a `reference_time`, its own present, set at
`create_timeline` or later with `set_reference_time`. Leave it unset for
anything that tracks real time: unset means *follow the clock*, whereas
passing today's date would freeze the present at that moment. Asked for the
next occurrence with no window, `query_timeline` answers from that present, so
a timeline anchored in May 1897 answers in 1897. Pass `next_after` to measure
"next" from another moment.

---

## 6. Reading the decisions back

**`reflect` nominates open temporal contradictions** in its own
`temporal_contradictions` list, beside the contradictions between claims. A
held one is left alone; an unanswered one comes back on every reflect until it
is answered or held. The answer goes to `resolve_temporal_contradiction`, not
`apply_reflection`. See [REFLECTION.md](REFLECTION.md).

**Every write above names a judge and lands in the decision journal**, under
its own kind: `temporal_order` for a stated order, `temporal_verdict` for a
contradiction settled or held, `timepoint_merge`, `recurrence`,
`recurrence_bound` and `recurrence_exception`. `review` reads them back, least
certain first. [ATTRIBUTION.md](ATTRIBUTION.md) covers the journal and the
tools that read it.

**Four timeline tools name no judge**, because they assert nothing:
`create_timeline`, `set_reference_time`, `add_timepoint` and `create_timelink`
put marks on an axis and attach facts to them. The six that record what a source
said or what a judge decided go through the same gate as any other write.

---

## 7. What the dashboard draws

The visualisation panel shows a timeline's dated points on a vertical axis, past
at the top. Beside the axis it draws when each claim was true, one lane per
source, as [VALIDITY.md](VALIDITY.md) §6 describes. For the timeline itself:

**A point the order places.** An undated point that a source put after one
dated point and before another is a hatched band spanning those two, with its
label written on it word for word. With only one bound known the band runs a
short way past it and fades out: "after the fire, we do not know when".

**A point whose order is disputed.** A contested point keeps whatever place it
had, its date or the tray, and gains a red zigzag beside it, which marks the
sequence as disputed rather than the date. Resting on the mark names the
contradiction.

**What a rule says happens over and over.** A recurrence rule's occurrences are
small beads on a dotted spine in a lane beside the axis, one lane per rule. An
occurrence somebody turned into a real timepoint is drawn as an ordinary point
instead. A moved occurrence sits on the date it moved to.

The panel enumerates occurrences over the span of the timeline's dated points.
A timeline with no dated points uses its own stated present, and one with
neither draws no beads rather than enumerating from today.

**What stays in the tray.** Points nothing constrains, contested points with no
date, and points belonging to another timeline's clock.
