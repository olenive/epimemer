# Timelines: dated points, stated order, recurrence

A design, built apart from replacing a recurrence rule that was wrong (§2.4).

This document records the decisions that shape timelines and the reasons for
them; what a caller sees is [docs/TIMELINES.md](../docs/TIMELINES.md). The
models are in `epimemer/core/types.py` and the reasoning is pure functions in
`epimemer/pipelines/timeline/`.

A timepoint's `start` and `end` are what a source stated, the same rule
`epimemer/core/temporal.py` keeps for validity: a date the system computed is
never written into the same field as a date a source gave.

Dates are Gregorian `datetime` values, years 1 to 9999. Geological and
astronomical spans, sub-microsecond events, and invented calendars are not
representable today; *Timelines on other clocks* in `PROPOSED_FEATURES.md`
sketches how they would be. One rule follows from that: nothing here may
assume the coordinate is a date where a number would do, so that the later
change is a type swap and not a rewrite. Ordering, bounds and periodic
recurrence are all arithmetic on coordinates; only `CalendarRule` is about
months and weekdays.

---

## 2. One container, three capabilities

There is one `Timeline`, not separate precise, vague and cyclical types.
Precise versus vague is a property of a point, not of the timeline it sits on:
one chronology holds both "14 July 1789" and "during the Terror", and someone
asking what happened in 1793 wants both. So the three are three capabilities
of one timeline: queries over dated points, ordering constraints between
points, and recurrence rules stored beside the points.

### 2.1 `Timepoint` has a derived `kind`

The kind is derived from the fields every time it is read and never supplied
by a caller. `kind` is a property rather than a stored field, so a record
reads back as the kind its fields imply, whenever it was written, with no
migration.

### 2.2 `OrderingConstraint`

An ordering constraint is an assertion by one source that one point came before
another: "this source says A came before B". It is the same shape of thing as a
validity interval, which is also an assertion by one source.

**Disputed is derived, never stored.** A constraint is disputed while an open
contradiction lists it (`disputed_constraint_ids`, §3.2). A stored flag would
be a second copy that every resolution would have to remember to clear.

### 2.3 `TemporalContradiction`

A set of constraints that cannot all hold, recorded when a write creates it.
Two kinds: a **cycle** in the ordering (§3.1), and **crossed bounds**, where a
point's computed `earliest` is later than its `latest` (§3.3).

`resolution` is derived from `resolutions`: the verdict that closed the
contradiction, or `None` while it is open. A hold is a verdict that leaves it
open, so a contradiction can carry several verdicts over its life.

### 2.4 `Recurrence`

Two rule kinds:

- **`PeriodicRule(anchor: datetime, period: timedelta)`.** Occurrence `n` is
  `anchor + n * period`, where `n` is an integer and may be negative. This
  works on any timeline, including an invented one: "every third day from the
  founding" needs arithmetic and no calendar.
- **`CalendarRule(rrule: str)`.** An RFC 5545 recurrence rule, the format
  calendar software uses, enumerated with `dateutil.rrule`. "The second
  Tuesday of every month" is a statement about the Gregorian calendar, which
  is the only calendar timepoints have (see the opening of this document), so
  no flag distinguishes timelines that may use it: every timeline may. The
  string is parsed when the rule is built, so a mistyped rule is refused at
  write time.

`python-dateutil` is a core dependency in `pyproject.toml` for this reason.
The packages that would bring it in transitively are all optional extras, so
relying on them would have made calendar rules work on a developer's machine
and fail on a plain install.

An occurrence is identified within its rule by **`occurrence_start`**, the
start the rule produces for it. That is unique within one rule and needs no
counting. There is no occurrence number: for a calendar rule the number would
need a walk from the rule's first occurrence, which is cheap near it and not
cheap a century later, so a number that had to be capped would be missing
exactly where most queries land. "The twelfth market" is a label a fact can
carry in its own words if a source says it.

**A rule carries exceptions.** A rule with no way to say "the 1943 service was
cancelled" forces a caller to choose between recording something false and
abandoning the rule to write every occurrence by hand. The second is worse,
because the recurrence is then lost.

**A rule never retires through supersession.** `VALIDITY_DESIGN.md` §3.3
gives the reason: a recurrence rule such as "Christmas is 24 to 26
December, annually" never stops being true, so it has no lifecycle. Two things
can happen to a rule instead, both append-only:

- A rule that stops applying gets a `bound_changes` entry with a `because`,
  and the most recent entry is in force.
- A rule that was wrong would be replaced by a new rule, the old one kept with
  a note saying which rule replaced it. **This is not built**: `Recurrence`
  has no field for the note and no tool writes one, so a wrong rule today can
  only have its end moved.

### 2.5 Storage: one record, written whole

`Timeline` holds three lists beside `timepoints`: `constraints`,
`temporal_contradictions` and `recurrences`. None of them is a graph node or a
separate table.

**The whole-record write, and what it costs.** A timeline is one row on every
backend, written whole, so adding a timepoint rewrites every timepoint,
constraint and recurrence on the timeline. At the sizes this is designed for,
a few hundred points and a few hundred constraints, that is roughly 60 KB of
points and 125 KB of constraints, so one write moves a couple of hundred
kilobytes. That is fine for a tool call. It would not be fine at ten thousand
points, where each added point would rewrite megabytes. The change to make
when that day comes is a `timepoint` table keyed by `timeline_id`, and the
trigger for making it is a measurement.

Two things come free with one record. It is atomic, so a constraint and the
contradiction it caused are never seen apart. And the graph bundle format needs
no version bump: `timelines.jsonl` embeds the whole record, adding fields to a
section is defined as not a format change (`GRAPH_BUNDLES.md` §5), and the
lists ride along.

One thing does not come free: two writers changing one record at once. That
is what the version on the record is for (§2.6).

### 2.6 Concurrent writers and the version

Several servers can open one graph, on a served SurrealDB or on Postgres, so
two people can change one timeline at the same moment. Every timeline tool
reads the record, changes it and writes it back whole. Without a check the
second write replaces the first, and a constraint one person recorded vanishes
under the other's write with nothing to say it existed.

`Timeline.version` counts the writes. A write carries the version its timeline
was read at and lands only if the store still holds that version, storing one
more. Otherwise the backend raises `TimelineConflict` (`storage/protocol.py`)
and writes nothing. A timeline never stored is at version 0, and so is a record
written before the field existed, so neither needs a migration. Every timeline
write applies the check inside its transaction, so a refused decision moves no
link. `write_verbatim_tx` is the one exception: it restores the version a
bundle carries.

Each backend makes the check part of the write, which is what makes it hold
across servers: on Postgres an `UPDATE ... WHERE version = ?` that waited on
another server's row lock tests its `WHERE` again against the row that server
committed, and on SurrealDB a batch that fails at commit because another server
wrote the record first is reported as the same conflict.

What a caller does with the refusal depends on whether it decided anything:

- The tools that record a judge's decision or place a mark refuse in their
  usual shape (`refused`, or the error `add_timepoint` and
  `set_reference_time` give for a missing timeline). The refusal says
  the timeline changed since it was read, and to read it again with
  `query_timeline` and retry. They do not retry on their own, because what
  they decided may rest on what changed: a contradiction already answered, a
  point already merged.
- `store_decomposition` proposes its timepoints again, up to three times. Its
  proposal decides nothing about the points already there: it is a function
  of the text and the timeline, and the timeline is the only shared part of
  its batch.

Two servers ingesting into a graph that has no `Extracted` timeline yet each
create one, with ids of their own. That gives two timelines of one name rather
than a lost write, and the version cannot see it.

---

## 3. Semantics

### 3.1 The cycle check, and why dates take part

When a constraint is inserted, the timeline checks whether `later_id` already
reaches `earlier_id`. The graph it searches has two kinds of edge:

- **Live constraints**: `earlier_id → later_id` for every constraint that is
  not disputed.
- **Date-derived edges**, computed from the points themselves. Two dated points
  get an edge when their dates settle the order beyond doubt: A's last moment
  is at or before B's first moment, and B's last moment is not at or before
  A's first. A point's last moment is `end` for an interval and `start` for an
  instant; its first moment is `start`. Two dated points whose intervals
  overlap get no edge in either direction, and the second half of the rule
  gives the same answer for two instants at the same moment, so neither pair
  imposes anything. This is the same discipline as `compare_intervals` in
  `core/temporal.py`: an answer only where the answer is certain.

Dates take part because the interesting contradiction is the one that crosses
them. A source asserting "the flood came before the fire" when the fire is
dated 1897 and the flood 1899 is a disagreement worth holding, and a check over
constraints alone would not see it.

**The check runs on two tools, not one.** `order_timepoints` can close a cycle
by adding a constraint. `add_timepoint` can close one by adding a *dated*
point, because a new date creates date-derived edges against every other dated
point, and one of those can complete a loop with existing constraints. If the
second case were missed, a contradiction would go unrecorded while the system
appeared to be checking for it.

A constraint whose `earlier_id` equals its `later_id` is refused outright
rather than recorded as a one-edge cycle. A point cannot precede itself; that
is a malformed request, not a disagreement between sources. So is a pair
naming a point the timeline does not hold, or one a merge retired. Each pair
is refused on its own, and the rest of the batch is recorded.

### 3.2 Nothing is refused, and what `disputed` excludes

When a cycle closes, it is recorded as a `TemporalContradiction` with the
constraint set, the sources and the time, and every constraint on it is
disputed from then on. The insert still succeeds. The graph holds the
disagreement; it does not decide it.

A disputed constraint is excluded from exactly three things:

1. the transitive closure that `between`, `before` and `after` walk;
2. the bounds computation (§3.3);
3. the cycle check itself, so the next check runs against the live set and a
   resolved cycle is not detected again.

It is excluded from nothing else. It is still stored, still returned by reads,
and still carries its source and judge, because what the sources said is the
record, and dropping half of it would make the contradiction unreadable.

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
is deleted and no constraint's stored content is edited: re-pointing a
constraint at another point retires it and writes a replacement, and the
retirement's `superseded_by` names the replacement. What each verdict does is
in `docs/TIMELINES.md` §4.

**A split moves the facts the judge names.** Each `TIMELINK` from a named
node to the original point is **retired**, kept with `superseded_by` naming
the new link and the verdict as the reason, and a new link to the new point is
written. Nodes the judge does not name stay on the original. Facts are moved
one by one, by a decision, because a fact's date is part of what it claims and
nothing in this graph changes a claim's evidence without someone deciding it.
This is the one place a `TIMELINK` can be retired, apart from a merge; there
is no general tool for detaching a fact from its date.

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

### 3.5 Occurrences: computed, capped, materialised on demand

*Range mode* enumerates inside the asked window only. The window is clipped
to the rule's bounds and entered in one step, a division for a `PeriodicRule`
and one `after()` for a `CalendarRule`, and enumeration steps forward from
there until the window ends or the cap fires. Nothing walks a rule from its
beginning. The window is read against the start the rule gave, so an
occurrence moved out of the window still comes back, at its new date, and one
moved into it from outside does not: the window is over the rule, and a move
is a fact about one of its occurrences.

*Nearest mode* enumerates nothing. For a periodic rule the nearest occurrence
to a target is one division; for a calendar rule it is one `before()` and one
`after()` call. The obvious implementation of "nearest" is "enumerate, then
sort", which works on every small example and hangs on a daily rule anchored
a century back, so a test pins exactly that case.

**Materialisation** turns one occurrence into a real timepoint so that it can
be linked, ordered and disputed like any other. It happens on demand, through
one route: `add_timepoint` with `recurrence_id` and `occurrence_start` in
place of dates. That tool's job is already "put a point on this timeline"; a
second way of naming the point is better than giving three tools an
alternative addressing mode.

A fact linked to the rule rather than to one occurrence holds at every
occurrence, and its `TIMELINK` carries the rule id in place of a point id.
Every reader of `TIMELINK` metadata skips such a link, because there is no
point to move, contest or draw.

### 3.6 `reference_time` and the meaning of "next"

"The next occurrence" means the next one after the **timeline's** reference
time, resolved the way `TIMELINE_VISUALISATION.md` §6.4 resolves it. It has to
be resolved once per call and passed down, not read again inside the
enumerator, or a long call could see two different values of "now".
