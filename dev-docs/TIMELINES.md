# Timelines: dated points, stated order, recurrence

Decisions taken on 2026-09-16, in two rounds: the five that shape the model,
then eight consequences settled after a first draft raised them (§8). This
document replaces the *Specialized timelines* entry in `PROPOSED_FEATURES.md`,
which proposed three timeline types. There will be one. This document records
the decisions, works out what follows from them, defines the tool surface and
the storage shape, and gives a build order in stages that each ship on their
own.

The existing model is `Timepoint` and `Timeline` in `epimemer/core/types.py`,
the pure functions in `epimemer/pipelines/timeline/functions.py`, and the five
MCP tools `create_timeline`, `set_reference_time`, `add_timepoint`,
`query_timeline` and `create_timelink`.

Two different questions about time are kept apart in this codebase, and this
document is about only one of them:

- **When was a claim true?** That is *valid time*, handled by
  `epimemer/core/temporal.py` and `VALIDITY_DESIGN.md`: intervals recorded per
  source on the `sourced_from` edge.
- **When did something happen, and in what order?** That is what a timeline
  holds, and what this document extends.

A timepoint's `start` and `end` are what a source stated. That rule is already
in `temporal.py` and it is kept here: a date the system computed is never
written into the same field as a date a source gave.

Dates are Gregorian `datetime` values, years 1 to 9999. Fiction set in a
real-shaped calendar (a novel in 1897, a game in 2077) is fully supported.
Geological and astronomical spans, sub-microsecond events, and invented
calendars are not representable today; *Timelines on other clocks* in
`PROPOSED_FEATURES.md` sketches how they would be. One rule for this build
follows from that: nothing added here may assume the coordinate is a date
where a number would do, so that the later change is a type swap and not a
rewrite. Ordering, bounds and periodic recurrence are all arithmetic on
coordinates; only `CalendarRule` is about months and weekdays.

---

## 1. What a caller gets

Today a timeline is a list of points that the dashboard draws. A caller can add
points, link nodes to them, and ask for the points nearest to a date or inside a
date range. Three questions it cannot answer, and after this work it can:

1. **What happened near this point, or between these two?** Range and
   proximity queries over dated points, including intervals that straddle the
   asked window, and `between`, `before` and `after` queries that name two
   points by id instead of by date.
2. **What order did these come in, when nobody dated them?** A source that
   says "the fire came before the flood" without dating either can be recorded
   as saying so. A point with no date can then sit between two points that do
   have dates. This is the main value of the work: it is what empties the
   dashboard's undated tray (the list of points that have no position on the
   axis, `TIMELINE_VISUALISATION.md` §12.6).
3. **What recurs, and when is the next one?** A rule stored once, occurrences
   computed on demand, and "next" measured against the timeline's own clock
   rather than the server's.

Nothing here lets the graph assert an order that no source stated.

---

## 2. One container, three capabilities

`PreciseTimeline`, `VagueTimeline` and `CyclicalTimeline` will not exist.
Precise versus vague is a property of a point, not of the timeline it sits on:
one chronology holds both "14 July 1789" and "during the Terror", and someone
asking what happened in 1793 wants both. So there is one `Timeline`, and the
three proposals become three capabilities of it: queries over dated points,
ordering constraints between points, and recurrence rules stored beside the
points.

### 2.1 `Timepoint` gets a derived `kind`

```
kind: "instant" | "interval" | "vague"
```

The kind is **derived from the fields when the model is validated, and never
supplied by a caller**: `start` alone is an instant, `start` with `end` is an
interval, `label` alone is vague. A caller that passes `kind` is refused, so
the field can never disagree with the dates beside it.

Existing records read back as the kind their fields imply, so there is no
migration. Validation tightens in two places that used to pass silently:

- `end` without `start` is refused. The validity model can represent "ended
  then, began we do not know when" (`ValidityInterval` has an unknown start
  for this); a timepoint cannot, because `start` is where the dashboard places
  the mark.
- A point with neither a date nor a label is refused. It would be a mark with
  nothing on it.

**A vague point never stores an approximate date.** Its position is computed
from ordering constraints when a query runs (§3.3) and returned as `earliest`
and `latest` on the query result. It is never written back onto the record.
Writing a computed guess into `start` would make it indistinguishable from a
date a source stated, which is the mistake `temporal.py` avoids by never
resolving a `NamedInstant` to a date.

A materialised recurrence occurrence (§3.5) is an ordinary instant or interval
that also carries `recurrence_id` and `occurrence_start`, the start the rule
gave it before any move. It is not a fourth kind: it has real dates and
behaves as a point in every query.

A timepoint can also be retired by a merge (§3.4), in which case it carries
`merged_into` naming the survivor and takes no further part in queries.

### 2.2 `OrderingConstraint`

An ordering constraint is an assertion by one source that one point came before
another: "this source says A came before B". It is the same shape of thing as a
validity interval, which is also an assertion by one source.

| Field | Holds |
|---|---|
| `id` | Stable id, which a verdict names |
| `earlier_id`, `later_id` | Timepoint ids on this timeline |
| `source_id` | The document or node that asserts the order |
| `basis` | `stated` or `inferred`, no default |
| `because` | One line from the judge, optional |
| `judged_by` | The judge who recorded it (`JudgeRef`) |
| `asserted_at` | When it was recorded |
| `disputed` | True while an open contradiction lists it (§3.2) |

`basis` has the same meaning and the same no-default rule as `IntervalBasis`
on a validity interval. `stated` means the source says the order in words
("the fire came before the flood"). `inferred` means the judge read the order
off the text, for example from tense or context. The distinction matters
because a source that narrates the fire and then the flood has not stated
which came first; narrative order is not chronological order. This is also why
extraction never proposes constraints (§4.6).

The judge and the source are different things. The source says; the judge read
the source and decided that it said that.

Two sources disagreeing is two constraints, both kept. A source contradicting
itself is also two constraints, both kept. This is the same treatment
`record_contradiction` gives claims: the graph holds the disagreement instead
of picking a winner.

### 2.3 `TemporalContradiction`

A set of constraints that cannot all hold, recorded when a write creates it.
Two kinds: a **cycle** in the ordering (§3.1), and **crossed bounds**, where a
point's computed `earliest` is later than its `latest` (§3.3).

| Field | Holds |
|---|---|
| `id` | Stable id |
| `kind` | `cycle` or `crossed_bounds` |
| `edges` | The edges involved, in order for a cycle: each one either a constraint id or a date-derived pair `(a_id, b_id)` (§3.1) |
| `point_id` | For crossed bounds, the squeezed point |
| `constraint_ids` | The constraints involved; these are what `disputed` marks |
| `sources` | The source ids behind those constraints |
| `found_at` | When it was found |
| `resolution` | `None` while open, otherwise the verdict (§3.4), append-only |
| `held` | True after a *hold* verdict (§3.4): still open, not nominated |

Open contradictions that are not held are what `reflect` nominates. A verdict
closes one, or holds it.

### 2.4 `Recurrence`

| Field | Holds |
|---|---|
| `id`, `label` | Identity, and the words for it ("Christmas", "market day") |
| `rule` | `PeriodicRule` or `CalendarRule` |
| `duration` | How long one occurrence lasts; zero for an instant |
| `bounds` | First and last occurrence, either end optional |
| `bound_changes` | Append-only list of `(ends_at, because, judged_by, at)` |
| `exceptions` | Occurrences that did not happen, or moved |
| `source_id`, `judged_by`, `asserted_at` | Provenance, as on a constraint |

Two rule kinds:

- **`PeriodicRule(anchor: datetime, period: timedelta)`.** Occurrence `n` is
  `anchor + n * period`, where `n` is an integer and may be negative. This
  works on any timeline, including an invented one: "every third day from the
  founding" needs arithmetic and no calendar.
- **`CalendarRule(rrule: str)`.** An RFC 5545 recurrence rule, the format
  calendar software uses, enumerated with `dateutil.rrule`. "The second
  Tuesday of every month" is a statement about the Gregorian calendar, which
  is the only calendar timepoints have (see the opening of this document), so
  no flag distinguishes timelines that may use it: every timeline may.

An occurrence is identified within its rule by **`occurrence_start`**, the
start the rule produces for it. That is unique within one rule and needs no
counting. There is no occurrence number: for a calendar rule the number would
need a walk from the rule's first occurrence, which is cheap near it and not
cheap a century later, so a number that had to be capped would be missing
exactly where most queries land. "The twelfth market" is a label a fact can
carry in its own words if a source says it.

**Exceptions are included from the start.** A rule with no way to say "the
1943 service was cancelled" forces a caller to choose between recording
something false and abandoning the rule to write every occurrence by hand. The
second is worse, because the recurrence is then lost.

```
RecurrenceException(occurrence_start, kind: "cancelled" | "moved",
                    moved_to: datetime | None, source_id, judged_by, at)
```

A moved occurrence keeps its original `occurrence_start` as its identity and
is reported at `moved_to`. The identity has to survive the move, or
materialising it before and after the move would produce two points.

**A rule never retires through supersession.** `VALIDITY_DESIGN.md` decided
this (its T2 section): a recurrence rule such as "Christmas is 24 to 26
December, annually" never stops being true, so it has no lifecycle. Two things
can happen to a rule instead, both append-only:

- A rule that stops applying gets a `bound_changes` entry with a `because`.
  The effective `bounds.end` is the most recent entry, so "we thought it
  stopped in 1990, then learned it was 1993" is readable rather than
  overwritten.
- A rule that was wrong is replaced by a new rule. The old one is kept with a
  note saying which rule replaced it.

### 2.5 Storage: still one record, written whole

`Timeline` gains three lists beside `timepoints`: `constraints`,
`temporal_contradictions` and `recurrences`. Nothing becomes a graph node and
nothing becomes a separate table.

**The whole-record write stays, and this is what it costs.** A timeline is one
row on both backends, `store_timeline` replaces the row, and `write_batch_tx`
already treats timelines as the one upsert in an otherwise insert-only batch
(`TIMELINE_VISUALISATION.md` §7.1). Adding a timepoint already rewrote every
timepoint; now it rewrites the constraints and recurrences too. At the sizes
this is designed for, a few hundred points and a few hundred constraints, that
is roughly 60 KB of points and 125 KB of constraints, so one write moves a
couple of hundred kilobytes. That is fine for a tool call. It would not be fine
at ten thousand points, where each added point would rewrite megabytes. The
change to make when that day comes is a `timepoint` table keyed by
`timeline_id`, and the trigger for making it is a measurement.

Two things come free with one record. It is atomic, so a constraint and the
contradiction it caused are never seen apart. And the graph bundle format needs
no version bump: `timelines.jsonl` already embeds timepoints, adding fields to
a section is defined as not a format change (`GRAPH_BUNDLES.md` §5), and the
new lists ride along.

One thing does not come free. Reading a timeline record, changing it and
writing it back is last-writer-wins when two callers do it at once: a
constraint one agent added can vanish under another agent's write, with
nothing to say it existed. `add_timeline_timepoint` has always had this
property. It stays, because every Epimemer server today is a stdio process
with one client, so two writers on one timeline means two servers on one
database, which is the parked live-sharing case. The fix when that case
arrives is a version number on the record checked on write, and it is listed
under *Sharing a graph between users* in `PROPOSED_FEATURES.md`.

---

## 3. Semantics

### 3.1 The cycle check, and why dates take part

When a constraint is inserted, the timeline checks whether `later_id` already
reaches `earlier_id`. The graph it searches has two kinds of edge:

- **Live constraints**: `earlier_id → later_id` for every constraint that is
  not disputed.
- **Date-derived edges**, computed from the points themselves. Two dated points
  get an edge when their dates settle the order beyond doubt: A's last moment
  is at or before B's first moment. A point's last moment is `end` for an
  interval and `start` for an instant; its first moment is `start`. Two dated
  points whose intervals overlap get no edge in either direction, so they
  impose nothing. This is the same discipline as `compare_intervals` in
  `temporal.py`: an answer only where the answer is certain.

Dates take part because the interesting contradiction is the one that crosses
them. A source asserting "the flood came before the fire" when the fire is
dated 1897 and the flood 1899 is a disagreement worth holding, and a check over
constraints alone would not see it.

The check is one graph traversal from `later_id` with parent pointers, so the
cycle can be reported as a path. It runs over a list that is already in the
record. At a few hundred points there is no index and none is wanted.

**The check runs on two tools, not one.** `order_timepoints` can close a cycle
by adding a constraint. `add_timepoint` can close one by adding a *dated*
point, because a new date creates date-derived edges against every other dated
point, and one of those can complete a loop with existing constraints. If the
second case were missed, a contradiction would go unrecorded while the system
appeared to be checking for it.

A constraint whose `earlier_id` equals its `later_id` is refused outright
rather than recorded as a one-edge cycle. A point cannot precede itself; that
is a malformed request, not a disagreement between sources.

### 3.2 Nothing is refused, and what `disputed` excludes

When a cycle closes, every constraint on it is marked `disputed`, and the cycle
is recorded as a `TemporalContradiction` with the constraint set, the sources
and the time. The insert still succeeds. The graph holds the disagreement; it
does not decide it.

A disputed constraint is excluded from exactly three things:

1. the transitive closure that `between`, `before` and `after` walk;
2. the bounds computation (§3.3);
3. the cycle check itself, so the next check runs against the live set and a
   resolved cycle is not detected again.

It is excluded from nothing else. It is still stored, still returned by reads,
and still carries its source and judge, because what the sources said is the
record, and dropping half of it would make the contradiction unreadable.

**A point on a disputed cycle is reported as contested**, with the
contradiction id, and gets no derived position. A *dated* point on a disputed
cycle keeps its date and is marked contested beside it. The asymmetry is
deliberate: a date is what a source stated about that point, a constraint is
what a source stated about a pair, and a dispute between the two is a reason
to stop trusting the derived order, not a reason to remove a date. Correcting a
date a source got wrong is done at the claim level, with `supersede_by`, not
here.

**A constraint returns to live when no open contradiction lists it.** A
constraint caught in two cycles stays disputed until both are resolved; that
follows from the rule and needs no special case.

### 3.3 Bounds for a vague point

Over the live graph (live constraints plus date-derived edges), for a point
`P`:

- `earliest` is the latest "last moment" among the dated points that reach
  `P` (points `D` with a path `D → P`).
- `latest` is the earliest "first moment" among the dated points that `P`
  reaches (points `D` with a path `P → D`).

Either can be absent. A point with only successors gets a `latest` and no
`earliest`, which is a real answer: "before 1897, we do not know when". A point
with neither is a point nothing constrains, and it stays in the tray.

Bounds are never written to the record. They are computed per query, reported
beside the point, and recomputed next time, so retiring a constraint later
takes its bound away with it.

**Bounds can cross without there being a cycle, and a crossing is a
contradiction too.** Two dated points that overlap have no date-derived edge
between them. So with `A = [1890, 1910]`, `B = [1900, 1920]`, and constraints
`A → P` and `P → B`, the result is `earliest = 1910` and `latest = 1900`: the
two constraints cannot both hold, and there is no loop for the cycle check to
find. So a second check runs after every write that adds a constraint or a
dated point: bounds are recomputed for every vague point, and a point whose
`earliest` is later than its `latest` opens a `TemporalContradiction` of kind
`crossed_bounds`, naming the point and the constraints on the two paths that
produced the bounds. Those constraints are disputed and the point is contested,
the same as for a cycle. At a few hundred points the recomputation is one
traversal per vague point and costs nothing worth measuring.

### 3.4 Verdicts: retire, split, hold, and the merge beside them

A contradiction is answered by a judge's verdict, recorded append-only. Nothing
is deleted and no constraint's stored content is edited. Three verdicts answer
a contradiction:

**Retire one constraint, with a reason.** The reasons are the real ones: the
source was misread, the source is unreliable, or the order it gives is
narrative rather than chronological. The constraint is marked retired with the
reason and the judge, the contradiction is closed, and every other constraint
in it returns to live unless another open contradiction still holds it.

**Both sources are right, and the point is really two events.** The sources
are each correct about a different occurrence of the thing the point names, so
the point is what is wrong, not either constraint. The verdict splits it: a new
timepoint is created with the same label, a new id, and `split_from` naming
the original, and the constraints the judge names are re-pointed at the new
point. Both constraints stay live. The contradiction closes.

The split also moves the facts the judge names. Each `TIMELINK` from a named
node to the original point is **retired**, kept with `superseded_by` naming
the new link and the verdict as the reason, and a new link to the new point is
written. Nodes the judge does not name stay on the original. Facts are moved
one by one, by a decision, because a fact's date is part of what it claims and
nothing in this graph changes a claim's evidence without someone deciding it.
This is the one place a `TIMELINK` can be retired, apart from the merge below;
there is no general tool for detaching a fact from its date.

**Hold.** The sources genuinely disagree and nothing on hand settles it. The
contradiction stays open and its constraints stay disputed, so the point is
still reported as contested and gets no position, but `reflect` stops
nominating it. It is nominated again when new evidence arrives: a constraint
or a dated point added that touches any point in the contradiction clears
`held`, because new evidence is what a held contradiction is waiting for. This
is not a snooze: nothing reopens it on a schedule.

A fourth verdict is not an answer to a contradiction but the reverse of the
split, and it lives beside them:

**Merge: two points are one moment.** Extraction creates one point per
distinct phrase, so "the launch" and "the go-live" from two documents become
two points that a judge may later recognise as one event. The merge names a
survivor and the point to retire. The retired point is kept with
`merged_into`. Every constraint naming it is re-pointed at the survivor, every
`TIMELINK` to it is retired and rewritten to the survivor (all of them, not a
chosen subset: a merge asserts the two are one, so every fact about either is
a fact about it), and the cycle and crossing checks run again, since combining
two points can create a contradiction neither had alone. One refusal: if any
constraint orders the two points against each other ("the launch came before
the go-live"), the merge is refused and names it. A source that ordered them
said they are not one moment, and the judge has to retire that constraint
first, with a reason, before merging.

`reflect` nominates open, unheld temporal contradictions beside the claim
contradictions it already nominates, on the same terms: an unanswered
nomination comes back on every reflect until it is answered or held.

### 3.5 Occurrences: computed, capped, materialised on demand

**Occurrences are computed and never stored.** A rule plus a window gives a
list; storing the list would make the rule and its expansion two things that
can disagree.

*Range mode* enumerates inside the asked window only. For a `PeriodicRule` the
index range is arithmetic, `ceil((window_start - anchor) / period)` to
`floor((window_end - anchor) / period)`, clipped to `bounds`. For a
`CalendarRule` it is `rrulestr(...).between(after, before, inc=True)`, which
does the same inside `dateutil`.

*Nearest mode* enumerates nothing. For a periodic rule the nearest occurrence
to a target is one division; for a calendar rule it is one `before()` and one
`after()` call. This deserves a test of its own, because the obvious
implementation of "nearest" is "enumerate, then sort", which works on every
small example and hangs on a daily rule anchored a century back.

**The cap** on occurrences is per rule per call, a named constant. When it
fires, the response says `truncated: true` and reports the window actually
covered. Without a cap, one plausible query could enumerate without end.

An occurrence is identified by `occurrence_start`, the start the rule produces
for it (§2.4). Nothing is counted.

**Exceptions apply during enumeration.** A cancelled occurrence is omitted, or
returned marked `cancelled` if the caller asks for cancelled ones. A moved
occurrence is returned at `moved_to`, with its original `occurrence_start`
beside it as its identity.

**Materialisation** turns one occurrence into a real timepoint so that it can
be linked, ordered and disputed like any other. It happens on demand, through
one route: `add_timepoint` with `recurrence_id` and `occurrence_start` in
place of dates. That tool's job is already "put a point on this timeline"; a
second way of naming the point is better than giving three tools an
alternative addressing mode. Materialisation is idempotent, keyed on
`(recurrence_id, occurrence_start)`, so calling it twice returns the same id.
It refuses a start the
rule does not produce, and it refuses a cancelled occurrence, since
materialising something the graph records as not having happened is always a
mistake. A moved occurrence materialises at its moved date.

A fact attaches at one of **two levels**, and they mean different things.
Linked to the **rule**, it holds at every occurrence: "the service is at the
parish church". Linked to one **occurrence**, which materialises that
occurrence: "the 1897 service was moved to the hall". Nothing collapses the
second into the first, and a materialised occurrence's validity intervals are
never combined into the rule (`VALIDITY_DESIGN.md` §3.3).

### 3.6 `reference_time` and the meaning of "next"

"The next occurrence" means the next one after the **timeline's** reference
time, resolved the way `TIMELINE_VISUALISATION.md` §6.4 already resolves it:
unset means the wall clock, set means the timeline's own present. So a
timeline whose reference time is May 1897, asked for the next occurrence of a
weekly service, answers in 1897.

This is the first place `reference_time` affects a query answer. Until now it
has been stored, reported on every `query_timeline` call, and used by the
dashboard to place the now-line, and nothing computed an answer from it. Two
consequences. It has to be resolved once per call and passed down, not read
again inside the enumerator, or a long call could see two different values of
"now". And a caller who wants the next occurrence after some other moment
passes that moment as `next_after`: the timeline's clock is the default, never
something the caller cannot override.

---

## 4. Tool surface

Every tool below takes `expected_graph`, reads included, as every Epimemer tool
does (`epimemer_prompts/RULES.md`). **A write names a judge; a query names
none.** A constraint is a judgment about what a source asserts, a verdict is a
judgment outright, a recurrence is a judgment that a rule was stated, and a
query asserts nothing.

### 4.1 `order_timepoints` (new, write, judge)

```
order_timepoints(timeline_id, pairs: [{earlier_id, later_id}],
                 source_id, basis, because=None, expected_graph)
```

A list of pairs with one source and one basis, because a source that states an
order usually states several at once, and one reading of one source should be
one journal row. That is the rule `store_decomposition` already follows.

Returns, per pair, the constraint id and whether it was created or already
existed (idempotent on `(earlier_id, later_id, source_id)`), and for the batch
any `temporal_contradictions` opened, each with its cycle and the constraint
ids now disputed. Opening a contradiction does not fail the call.

### 4.2 `resolve_temporal_contradiction` (new, write, judge)

```
resolve_temporal_contradiction(timeline_id, contradiction_id,
                               verdict: "retire_constraint" | "not_the_same_event" | "hold",
                               constraint_id=None, point_id=None,
                               constraints_to_move=None, nodes_to_move=None,
                               because, expected_graph)
```

`because` is required with no default, as it is on `supersede_by`: the sentence
is the judgment. `retire_constraint` needs `constraint_id`;
`not_the_same_event` needs `point_id` and `constraints_to_move`, and takes
`nodes_to_move` for the facts whose links move to the new point (§3.4);
`hold` needs only `because`.

Returns which constraints returned to live, which stayed disputed and under
which other contradiction, and for a split the new timepoint id, the links
retired and rewritten, and the nodes left on the original.

### 4.2a `merge_timepoints` (new, write, judge)

```
merge_timepoints(timeline_id, survivor_id, merged_id, because, expected_graph)
```

The merge of §3.4. Refuses when a live constraint orders the two points
against each other, naming it. Returns the constraints re-pointed, the links
retired and rewritten, and any contradiction the merge opened.

**Why a dedicated tool rather than a verdict route inside `apply_reflection`.**
Three reasons, the third the strongest. `apply_reflection`'s verdict lists all
name nodes and topics; a constraint is neither, and a split writes a timepoint,
not a node. `apply_reflection` writes one atomic batch over nodes and edges,
and folding a whole-record timeline write into that batch gains nothing and
couples two things that fail differently. And a temporal verdict is reached
outside reflect at least as often as inside it, by a caller who saw `contested`
on a `query_timeline` result and knows at once which source was misread; a
route that existed only inside a reflect sweep would make them run a sweep to
use it. `reflect` still nominates, and `apply_reflection`'s response says where
to send the answer.

### 4.3 `add_recurrence` (new, write, judge)

```
add_recurrence(timeline_id, label, duration,
               anchor=None, period=None, rrule=None,
               bounds_start=None, bounds_end=None,
               source_id, expected_graph)
```

Exactly one of `(anchor, period)` or `rrule`.

Returns the recurrence id **and a preview of the first few occurrences**. An
rrule string is easy to mistype, and without a preview the mistake is invisible
until someone queries a window months later.

### 4.4 `end_recurrence` (new, write, judge)

```
end_recurrence(timeline_id, recurrence_id, ends_at, because, expected_graph)
```

Appends a bound change. It never retires the rule (§2.4). Returns the effective
`bounds.end` and the full `bound_changes` history, so a second correction shows
what it corrected.

### 4.5 `record_recurrence_exception` (new, write, judge)

```
record_recurrence_exception(timeline_id, recurrence_id, occurrence_start,
                            kind: "cancelled" | "moved", moved_to=None,
                            source_id, because=None, expected_graph)
```

Implied by the decisions rather than named in them: exceptions are stored, and
stored things need a writer. Refuses an `occurrence_start` the rule does not
produce, and refuses `moved` without `moved_to`.

### 4.6 `query_timeline` (changed, no judge)

New parameters: `between: [id, id]`, `before: id`, `after: id`,
`basis: "all" | "stated" = "all"`, `include_recurrences: bool = True`,
`include_contested: bool = True`, `occurrence_cap`,
`next_after: datetime | None`.

The three ordering modes run over the transitive closure of live constraints
and date-derived edges, computed on read. `between` is the intersection of
"after A" and "before B": a point that neither reaches is not between them.

`basis="stated"` builds the closure and the bounds from stated constraints
only, leaving out the ones a judge inferred from tense or context. This is the
same choice validity queries already offer, and it answers "what do the
sources actually say about the order", which is the question asked when two
accounts disagree. The default counts both, since an inferred constraint is an
honest record too. The edge set is built in one place, so the filter is one
branch there.

Every returned timepoint now carries `kind`; `earliest` and `latest` where
they could be derived; `contested` and `temporal_contradiction_id` where they
apply; and for an occurrence, `recurrence_id`, `occurrence_start` and
`materialised_id` (null until something materialises it). The response keeps
`reference_time` and gains `truncated` per rule.

`propose_timepoints` on `store_decomposition` is **not** changed. It proposes
dates only. "Before" in prose is narrative order far more often than
chronology, and a detector that proposed constraints would fill the graph with
assertions no source made. `TIMELINE_VISUALISATION.md` §7.3 refuses the same
kind of guessing for dates.

### 4.7 `add_timepoint` (changed, write)

Validation per §2.1, and the response names the derived `kind` so a caller
sees what it created. Two additions: `recurrence_id` with `occurrence_start`
as an alternative to dates, which materialises an occurrence idempotently
(§3.5); and the cycle check of §3.1, which a dated point can now trigger. The
response carries any contradiction the call opened.

### 4.8 `create_timelink` (changed, write)

Parameters unchanged. The response gains the point's `kind`, its bounds where
derived, and its `contested` flag, so a caller that has just dated a fact can
see what it dated it to without a second call. Linking to a contested point is
allowed without comment: the fact's relation to the point is not what is in
dispute.

### 4.9 `reflect` (changed, read)

A new `temporal_contradictions` key beside `contradictions`, listing the open,
unheld ones per timeline with the kind, the edges, the constraints, their
sources and when it was found. Cheap: one read of each timeline record, and
the contradictions are stored rather than recomputed.

### 4.10 `search` (changed, read)

A returned node linked by `TIMELINK` to a contested point carries
`date_contested: true` and the contradiction id. This is a separate flag from
`contested`, which says the claim itself is disputed: an agent reading "the
treasury was empty at the coronation" needs to know when the coronation's
place in time is in dispute, and must not take that for doubt about the
treasury. The cost is one read of the relevant timeline records per call, and
only for returned nodes that have a timelink, which most do not.

---

## 5. What the dashboard does with this

Pointers into `TIMELINE_VISUALISATION.md` rather than new design. Three things
change and one deliberately does not.

- **The tray empties.** §12.6's undated tray holds points with no coordinate. A
  partly-dated point now has a bounded position, so it leaves the tray and is
  drawn with §13.1's **hatched band**, which is already the mark for "vague
  label, resolved", spanning `earliest` to `latest` with the label written on
  it verbatim. §13.2's rule 3 still holds: resolution adds a position and never
  replaces the words. What stays in the tray: points nothing constrains,
  contested points, and points from a different timeline's clock (§12.6's
  cross-clock chips).
- **Contested needs a mark.** Red is the contradiction colour in both panels,
  and a contested point is a contradiction. The mark has to read as "the order
  here is disputed" rather than "this date is wrong", since §3.2 keeps the
  date.
- **Recurrence beads are already designed.** §13.1's beads on a dotted spine
  were drawn for one node with several validity intervals; the same drawing
  reads correctly for the occurrences of a rule, with the spine meaning "same
  rule, nothing asserted in between". A materialised occurrence is a full
  mark; the rest stay beads.
- **Record order does not change.** `reorder_timepoints` still sorts dated
  points by start and appends vague ones in the order they were added, which
  is the contract §12.6 depends on. Bounds are a read-time answer and the
  dashboard does the placing. Writing a derived order into the record would be
  the fabrication §2.1 refuses, by another route.

---

## 6. Tests worth naming

Failing test first, storage additions through the parameterised `storage`
fixture, and parity on both backends, as the rest of the storage layer is
tested.

- **Parity and round-trip.** A timeline carrying constraints, contradictions
  and recurrences round-trips identically on both backends; `write_batch_tx`
  upserts it and rolls the upsert back, extending the existing timeline
  rollback tests. The bundle round-trip in `tests/pipelines/test_transfer.py`
  gets the new lists in its hand-built graph: that test exists because what an
  export silently drops is exactly what the tools do not routinely write, so
  every new list has to be in the fixture.
- **The cycle detector.** A two-constraint cycle. A longer one, so an
  implementation that only checks the pair just inserted fails. One closed by a
  date-derived edge. One closed by `add_timepoint` adding a dated point, the
  case that is easy to miss. A self-loop refused. Disputed constraints
  excluded from the next detection. Two overlapping dated points producing no
  edge either way.
- **Bounds.** Tightening across a chain. An interval predecessor contributing
  its `end` and an interval successor its `start`. A one-sided bound. §3.3's
  crossed bound opening a `crossed_bounds` contradiction that names the point
  and both paths' constraints. A point nothing constrains staying unbounded.
  `basis="stated"` dropping a bound that only an inferred constraint produced.
- **Enumeration.** The cap fires and `truncated` says so. Nearest mode does no
  enumeration, asserted against a daily rule anchored a century before the
  target, so an enumerate-then-sort implementation fails on time rather than
  on output. A cancelled occurrence omitted; a moved one returned at its new
  date with its original index. Bounds clipping at both ends.
- **Materialisation idempotence.** The same occurrence twice gives one id. A
  start the rule does not produce is refused, as is a cancelled occurrence. A
  moved occurrence materialises at `moved_to` and is still found by its
  original `occurrence_start`.
- **The verdicts.** Retire returns the other constraints to live, except one
  also held by a second open contradiction. Split creates a point, re-points
  the named constraints, retires and rewrites the links of the named nodes,
  leaves the rest on the original, and closes the contradiction. Hold stops
  the nomination, keeps the point contested, and is cleared by a new
  constraint or dated point touching the contradiction, and by nothing else.
  Merge re-points constraints, moves every link, re-runs both checks, and is
  refused when a constraint orders the two points. All are append-only; none
  edits a stored constraint.
- **`date_contested` on search.** A fact linked to a contested point carries
  it; a fact whose claim is contradicted carries `contested` and not this; a
  fact with no timelink carries neither.
- **Validation and derivation.** `end` without `start` refused; neither date
  nor label refused; a supplied `kind` refused; a record written in the old
  shape (a fixture built without the validator) reads back as the kind its
  fields imply.
- **`reference_time`.** The next occurrence is answered in a fictional
  timeline's era, and `next_after` overrides it.

---

## 7. Build order

Three stages. Each ships alone and each is worth shipping alone.

**(a) Kind, validation, and the range query.** The `kind` field and its
derivation, the two tightened refusals, `kind` on every response. In the same
stage, fix `get_in_range` in `pipelines/timeline/functions.py`: today it
returns intervals that started before the window *after* the ones inside it,
so the result is not in chronological order, and it scans every earlier point
linearly to find them. Both are fixable without changing the interface. This
stage ships as a better query surface with no new concepts. **Roughly one to
two days.**

**(b) Constraints, contradictions, bounds, verdicts, nomination.** The largest
stage and the one that pays for the work: `OrderingConstraint`,
`TemporalContradiction` of both kinds, the cycle and crossing checks on both
write paths, the closure and bounds computation with the `basis` filter,
`order_timepoints`, `resolve_temporal_contradiction` with its three verdicts
and the `TIMELINK` retirement the split needs, `merge_timepoints`, the
`query_timeline` ordering modes, the `reflect` nomination, and
`date_contested` on `search`. Storage parity and the bundle test move with it.
**Roughly a week and a half.**

**(c) Recurrence.** `Recurrence` with both rule kinds, exceptions, bounds,
enumeration with its caps, materialisation through `add_timepoint`,
`add_recurrence`, `end_recurrence`, `record_recurrence_exception`, and
`reference_time` in the query path. Plus one dependency change, below.
**Roughly three to four days.**

Stage (c) depends on (a) for `kind` and on nothing in (b). Stage (b) depends
on (a) only for the refusals it can then assume.

### `python-dateutil`

It is in the lock file and it is **not** a dependency of Epimemer. Version
2.9.0.post0 is in the working venv, pulled in three ways, all optional:
`aiobotocore` and `botocore` under the `s3` extra, and `matplotlib` under
`notebooks` by way of `petritype[examples]`. A plain `pip install epimemer`
gets none of them.

So `CalendarRule` requires adding it to the core dependencies in
`pyproject.toml`. That is cheap: it is pure Python with `six` as its only
requirement. Relying on the transitive copy would make calendar rules work on
a developer's machine and fail on a user's.

---

## 8. Questions the first draft raised, and their answers

The first draft ended with eight open questions. All were settled on
2026-09-16 and folded into the sections above; this list keeps the reasoning
for someone wondering why the design is shaped as it is.

1. **Crossed bounds are a contradiction, not just a flag.** A flag on a query
   result is seen only by whoever runs that query; a recorded contradiction is
   nominated by `reflect` and answered by a judge. The cost is one extra check
   per write (§3.3).
2. **A split moves the facts the judge names, by retiring their links.** Leaving
   every fact on the original point asserts a false date; adding a second link
   asserts two. A retirement kept with its reason fixes both without opening a
   general "unlink" door (§3.4).
3. **No occurrence number.** For a calendar rule the number needs a walk that
   has to be capped, so it would be missing exactly where most queries land.
   The start the rule produces is a complete identity (§2.4).
4. **No calendar flag.** Dates are Gregorian `datetime` values and fiction in
   a real-shaped calendar is the supported case. A flag would not make an
   invented calendar representable; *Timelines on other clocks* in
   `PROPOSED_FEATURES.md` is where that goes (opening section).
5. **Last-writer-wins stays.** One server has one client today; the version
   check belongs with live sharing (§2.5).
6. **A stated-only filter is offered.** It is one branch where the edge set is
   built, and it matches what validity queries already do (§4.6).
7. **Timepoints can merge.** Extraction makes one point per phrase, so
   duplicates of one event will be common, and this graph merges records of
   one thing everywhere else (§3.4).
8. **Search says when a fact's date is disputed**, under its own name, so doubt
   about the date is never read as doubt about the claim (§4.10).

One verdict came out of the discussion that no question asked for: **hold**,
for a contradiction nothing on hand can settle, cleared by new evidence and by
nothing else (§3.4).
