# Timelines: dated points, stated order, recurrence

The decisions that shape timelines and the reasons for them; what a caller
sees is [docs/TIMELINES.md](../docs/TIMELINES.md). The models are in
`epimemer/core/types.py` and the reasoning is pure functions in
`epimemer/pipelines/timeline/`.

A timepoint's `start` and `end` are what a source stated, the rule
`epimemer/core/temporal.py` keeps for validity: a computed date is never
written into a field that holds a date a source gave.

Dates are Gregorian `datetime` values, years 1 to 9999; *Timelines on other
clocks* in `PROPOSED_FEATURES.md` sketches the rest. So nothing here may
assume the coordinate is a date where a number would do, which keeps that
later change a type swap. Ordering, bounds and periodic recurrence are
arithmetic on coordinates; only `CalendarRule` is about months and weekdays.

---

## 2. One container, three capabilities

There is one `Timeline`, because precise versus vague is a property of a
point: one chronology holds "14 July 1789" and "during the Terror", and a
question about 1793 wants both. Its three capabilities are dated points,
ordering constraints between points, and recurrence rules.

### 2.1 `Timepoint` has a derived `kind`

`kind` is a property computed from the fields, never supplied or stored, so a
record of any age reads back as the kind its fields imply.

### 2.2 `OrderingConstraint`

An assertion by one source that one point came before another, the same shape
as a validity interval. **Disputed is derived, never stored**: a constraint is
disputed while an open contradiction lists it (`disputed_constraint_ids`),
because a stored flag is a copy every resolution would have to clear.

### 2.3 `TemporalContradiction`

A set of constraints that cannot all hold, recorded when a write creates it:
a `cycle` (§3.1) or `crossed_bounds` (§3.3). `resolution` is derived from the
append-only `resolutions`; a hold is a verdict that leaves it open.

### 2.4 `Recurrence`

- **`PeriodicRule(anchor, period)`**: occurrence `n` is `anchor + n * period`
  for any integer `n`, so it works on any timeline without a calendar.
- **`CalendarRule(rrule)`**: an RFC 5545 rule enumerated with
  `dateutil.rrule`, allowed on every timeline because Gregorian is the only
  calendar timepoints have. It is parsed when built, so a mistyped rule is
  refused at write. `python-dateutil` is a core dependency, because the
  packages that would bring it transitively are optional extras.

An occurrence is identified by **`occurrence_start`**, the start its rule
produces. There is no occurrence number, because for a calendar rule it needs
a walk from the first occurrence, which is expensive exactly where most
queries land.

**A rule carries exceptions**, so a cancelled occurrence never forces a choice
between recording something false and abandoning the rule.

**A rule never retires through supersession**, because it never stops being
true (`VALIDITY_DESIGN.md` §3.3). A rule that stops applying gets an
append-only `bound_changes` entry with a `because`, and the newest is in
force. Replacing a rule that was wrong is not built: `Recurrence` has no field
for a replacement note and no tool writes one, so a wrong rule can only have
its end moved.

### 2.5 Storage: one record, written whole

`Timeline` holds `constraints`, `temporal_contradictions` and `recurrences`
beside `timepoints`, none of them a node or a table. A timeline is one row on
every backend, written whole: atomic, so a constraint and the contradiction it
caused are never seen apart, and carried whole in `timelines.jsonl` with no
bundle format change (`GRAPH_BUNDLES.md` §5). Each write rewrites the whole
timeline, which suits the few hundred points and constraints it is designed
for; past that, the change is a `timepoint` table keyed by `timeline_id`, made
when a measurement calls for it.

### 2.6 Concurrent writers and the version

Several servers can open one graph (served SurrealDB, Postgres), and every
timeline tool reads, changes and writes the record whole, so without a check
a second write silently drops the first's change.

`Timeline.version` counts writes. A write carries the version it read and lands
only if the store still holds it; otherwise the backend raises
`TimelineConflict` (`storage/protocol.py`) and writes nothing. A timeline
never stored, or written before the field existed, is version 0. The check is
inside each write's transaction, so a refused decision moves no link, and each
backend makes it part of the write so it holds across servers (a re-tested
`UPDATE ... WHERE version = ?` on Postgres, a commit failure on SurrealDB).
`write_verbatim_tx` is the one exception: it restores a bundle's version.

- **Tools that record a decision or place a mark refuse** in their usual shape
  and say to re-read with `query_timeline` and retry. They never retry
  themselves, because what they decided may rest on what changed.
- **`store_decomposition` re-proposes its timepoints up to three times**,
  because its proposal is a function of the text and the timeline and decides
  nothing about existing points.

Two servers ingesting into a graph with no `Extracted` timeline yet each create
one, giving two timelines of one name that the version cannot see.

---

## 3. Semantics

### 3.1 The cycle check, and why dates take part

Inserting a constraint checks whether `later_id` already reaches `earlier_id`
over live (undisputed) constraints plus **date-derived edges**. Two dated
points get an edge only when their dates settle the order: A's last moment is
at or before B's first, and B's last is not at or before A's first (a point's
last moment is `end` for an interval and `start` for an instant). Overlapping
points and coincident instants get none, the discipline of
`compare_intervals`.

Dates take part because the contradiction worth holding is a source saying
"the flood came before the fire" when the dates say otherwise. So the check
runs on `order_timepoints` and on `add_timepoint`, since a new dated point can
close a loop with existing constraints.

A constraint from a point to itself, or naming a point the timeline does not
hold or a merge retired, is refused as malformed, per pair, and the rest of the
batch is recorded.

### 3.2 Nothing is refused, and what `disputed` excludes

A closed cycle is recorded as a `TemporalContradiction` and its constraints are
disputed; the insert still succeeds, because the graph holds the disagreement
rather than deciding it. A disputed constraint is excluded from exactly three
things: the closure `between`, `before` and `after` walk; the bounds (§3.3);
and the cycle check, so a resolved cycle is not detected again. It is still
stored and returned with its source and judge. A constraint is live again when
no open contradiction lists it.

### 3.3 Bounds for a vague point

Over the live graph, `earliest` is the latest last moment among dated points
that reach `P`, and `latest` is the earliest first moment among dated points
`P` reaches. **Bounds can cross without a cycle**: overlapping dated points
have no edge between them, so `A = [1890, 1910]`, `B = [1900, 1920]`, `A → P`,
`P → B` gives `earliest = 1910`, `latest = 1900`. So every write that adds a
constraint or a dated point recomputes bounds for every vague point, and a
crossing opens a `crossed_bounds` contradiction naming the point and the
constraints on both paths, which are disputed.

### 3.4 Verdicts: retire, split, hold, and the merge beside them

Verdicts are append-only; nothing is deleted and no constraint's content is
edited. Re-pointing a constraint retires it with `superseded_by` naming the
replacement. `docs/TIMELINES.md` §4 has what each verdict does.

**A split moves only the facts the judge names**: each named node's `TIMELINK`
to the original point is retired with `superseded_by` and a new link written,
because a fact's date is part of its claim and moves only by a decision. Split
and merge are the only ways a `TIMELINK` is retired.

Verdicts go through `resolve_temporal_contradiction` rather than
`apply_reflection`, because a constraint is not a node, a timeline write does
not belong in `apply_reflection`'s node-and-edge batch, and the verdict is
usually reached from a `contested` mark on `query_timeline`, outside any
reflect.

### 3.5 Occurrences: computed, capped, materialised on demand

*Range mode* enumerates only inside the window, clipped to the rule's bounds
and entered in one step (a division, or one `after()`), never walked from the
rule's beginning. The window is read against the start the rule gave, so a
moved occurrence comes back at its new date only if its original start is in
the window.

*Nearest mode* enumerates nothing: one division, or one `before()` and one
`after()`. Enumerate-then-sort hangs on a daily rule anchored a century back,
and a test pins that case.

**Materialisation** turns one occurrence into a real timepoint through one
route, `add_timepoint` with `recurrence_id` and `occurrence_start` in place of
dates, rather than a second addressing mode on several tools. A fact linked to
the rule itself holds at every occurrence; its `TIMELINK` carries the rule id,
and every reader of `TIMELINK` metadata skips it.

### 3.6 `reference_time` and the meaning of "next"

"Next" is after the timeline's reference time, resolved as
`TIMELINE_VISUALISATION.md` §6.4 describes, once per call and passed down, so a
long call never sees two values of "now".
