import marimo

__generated_with = "0.21.1"
app = marimo.App(width="medium")


@app.cell
def _(mo):
    mo.md("""
    # Timelines and metacontexts — *when* a claim held, and *which world* it is about

    Two things that look similar and answer opposite questions. Both are hard to
    read from the code alone, which is why they share a notebook.

    **A metacontext is a frame: which world the claim is about.** Saint
    Petersburg being in Russia and a novel's Petersburg being haunted are not a
    contradiction, because they are asserted in different frames. Every ingested
    node states one — absence is *no information*, not base reality — and a node
    stating no frame shares a frame with nothing: never compared, never merged,
    returned by no scoped search. `the-real` is the conventional id for
    real-world claims and has no special status in the code.

    **A validity interval is a period: when the claim held.** Saint Petersburg
    was Petrograd was Leningrad was Saint Petersburg, and every one of those was
    true of its own period. Intervals live on the `sourced_from` edge, per
    source, so two documents disagreeing about dates keeps both accounts rather
    than averaging them. Endpoints distinguish **unknown** from **unbounded**,
    which is the distinction the whole comparison rests on.

    The rule for telling them apart: a frame answers *would this hold in every
    other world here?*, an interval answers *when?*
    """)
    return


@app.cell
def _():
    import marimo as mo
    from epimemer.core.temporal import (
        PreciseInstant,
        UnboundedInstant,
        UnknownInstant,
        ValidityInterval,
        compare_intervals,
    )
    from epimemer.core.types import (
        ClaimKind,
        EdgeType,
        Fact,
        Metacontext,
        NodeEdge,
        Timeline,
    )
    from epimemer.pipelines.frames import frames_for, shared_frame_set
    from epimemer.storage.memory import InMemoryStorage
    from datetime import datetime, timezone

    return (
        ClaimKind,
        EdgeType,
        Fact,
        InMemoryStorage,
        Metacontext,
        NodeEdge,
        PreciseInstant,
        Timeline,
        UnboundedInstant,
        UnknownInstant,
        ValidityInterval,
        compare_intervals,
        datetime,
        frames_for,
        mo,
        shared_frame_set,
        timezone,
    )


@app.cell
def _(mo):
    mo.md("## 1. Frames — two claims about one city, in two worlds")
    return


@app.cell
async def _(ClaimKind, EdgeType, Fact, InMemoryStorage, Metacontext, NodeEdge, mo):
    store = InMemoryStorage()

    _the_real = Metacontext(id="the-real", content="The Real",
                            description="Claims about the real world.")
    _the_novel = Metacontext(id="petersburg-novel", content="Bely's *Petersburg*",
                             description="What is true inside the novel.")
    for _frame in (_the_real, _the_novel):
        await store.store_metacontext(_frame)

    real_fact = Fact(content="Saint Petersburg is a city in Russia.",
                     source_id="gazetteer", claim_kind=ClaimKind.STATE)
    novel_fact = Fact(content="Petersburg is a shadow cast by a bureaucratic mind.",
                      source_id="bely-1913", claim_kind=ClaimKind.STATE)
    unframed_fact = Fact(content="Petersburg has canals.",
                         source_id="unknown", claim_kind=ClaimKind.STATE)

    for _fact in (real_fact, novel_fact, unframed_fact):
        await store.store_node(_fact)
    # The third node is deliberately left with no frame edge.
    for _fact, _frame_id in ((real_fact, "the-real"), (novel_fact, "petersburg-novel")):
        await store.store_edge(NodeEdge(
            src_id=_fact.id, dst_id=_frame_id, type=EdgeType.HAS_METACONTEXT,
        ))

    mo.md(
        "Three facts stored: one framed `the-real`, one framed as the novel's "
        "world, and one **stating no frame at all** — the state a graph written "
        "before frames were required is full of, and the one `epimemer frames "
        "declare` exists to end."
    )
    return novel_fact, real_fact, store, unframed_fact


@app.cell
async def _(frames_for, mo, novel_fact, real_fact, shared_frame_set, store, unframed_fact):
    _ids = [real_fact.id, novel_fact.id, unframed_fact.id]
    _frames = await frames_for(_ids, store)

    _rows = [
        ("real-world fact", real_fact.id),
        ("in-novel fact", novel_fact.id),
        ("unframed fact", unframed_fact.id),
    ]
    _lines = ["| node | frames it states |", "|---|---|"]
    for _label, _node_id in _rows:
        _held = sorted(_frames[_node_id]) or ["*(none)*"]
        _lines.append(f"| {_label} | {', '.join(_held)} |")

    _pair_real_novel = await shared_frame_set([real_fact.id, novel_fact.id], store)
    _pair_unframed = await shared_frame_set([real_fact.id, unframed_fact.id], store)

    mo.md(
        "\n".join(_lines)
        + "\n\n### What that decides\n\n"
        f"- Merging the real and in-novel facts: **{'allowed' if _pair_real_novel else 'refused'}** "
        "— they do not stand in the same set of frames, and a merged node would "
        "assert in one world what was only ever claimed in another.\n"
        f"- Merging the real and unframed facts: **{'allowed' if _pair_unframed else 'refused'}** "
        "— the unframed node speaks for no world, so there is no set to agree on.\n\n"
        "*Two perspectives disagreeing about one world are also never nominated "
        "as a contradiction, because the sweep skips pairs sharing no frame. "
        "Where the disagreement is the point, `record_contradiction` takes it "
        "and marks it cross-frame.*"
    )
    return


@app.cell
def _():
    def describe_instant(instant):
        """One phrase per endpoint state, which is the whole point of the type."""
        match instant.instant_kind:
            case "precise":
                return instant.at.strftime("%Y")
            case "named":
                return f"*{instant.label}*"
            case "unbounded":
                return "**unbounded** — no end exists"
            case _:
                return "**unknown** — nobody has said"

    return (describe_instant,)


@app.cell
def _(mo):
    mo.md("## 2. Intervals — one city, three names, three periods")
    return


@app.cell
def _(mo):
    endpoint = mo.ui.dropdown(
        options=["unknown — nobody has said", "unbounded — no end exists"],
        value="unknown — nobody has said",
        label="How the last period ends",
    )
    endpoint
    return (endpoint,)


@app.cell
def _(
    PreciseInstant,
    Timeline,
    UnboundedInstant,
    UnknownInstant,
    ValidityInterval,
    datetime,
    describe_instant,
    endpoint,
    mo,
    timezone,
):
    gregorian = Timeline(id="gregorian", name="Gregorian calendar")

    def _at(year):
        return PreciseInstant(at=datetime(year, 1, 1, tzinfo=timezone.utc))

    _open_end = (
        UnknownInstant() if endpoint.value.startswith("unknown") else UnboundedInstant()
    )

    petersburg = ValidityInterval(
        start=_at(1703), end=_at(1914), timeline_id=gregorian.id, basis="stated",
    )
    petrograd = ValidityInterval(
        start=_at(1914), end=_at(1924), timeline_id=gregorian.id, basis="stated",
    )
    leningrad = ValidityInterval(
        start=_at(1924), end=_at(1991), timeline_id=gregorian.id, basis="stated",
    )
    petersburg_again = ValidityInterval(
        start=_at(1991), end=_open_end, timeline_id=gregorian.id, basis="stated",
    )

    intervals = [
        ("Saint Petersburg", petersburg),
        ("Petrograd", petrograd),
        ("Leningrad", leningrad),
        ("Saint Petersburg (again)", petersburg_again),
    ]

    _lines = ["| the city was called | from | until |", "|---|---|---|"]
    for _name, _iv in intervals:
        _lines.append(f"| {_name} | {describe_instant(_iv.start)} | {describe_instant(_iv.end)} |")
    mo.md("\n".join(_lines))
    return intervals, petersburg, petersburg_again


@app.cell
def _(
    PreciseInstant,
    ValidityInterval,
    compare_intervals,
    datetime,
    intervals,
    mo,
    petersburg,
    petersburg_again,
    timezone,
):
    _first_vs_last = compare_intervals(petersburg, petersburg_again)
    _adjacent = compare_intervals(intervals[0][1], intervals[1][1])

    # A period that starts after the open one does. This is where the two open
    # endpoint states diverge, and the earlier comparisons are where they do
    # **not** — 1703–1914 sits wholly before 1991 whatever happens after 1991,
    # so how the last period ends cannot change that answer.
    _future = ValidityInterval(
        start=PreciseInstant(at=datetime(2050, 1, 1, tzinfo=timezone.utc)),
        end=PreciseInstant(at=datetime(2060, 1, 1, tzinfo=timezone.utc)),
        timeline_id="gregorian", basis="stated",
    )
    _open_vs_future = compare_intervals(petersburg_again, _future)

    mo.md(
        "### Comparison concludes only what cannot be otherwise\n\n"
        f"- 1703–1914 against 1991–now: **{_first_vs_last.value}**\n"
        f"- 1703–1914 against 1914–1924: **{_adjacent.value}**\n"
        f"- **1991–now against 2050–2060: `{_open_vs_future.value}`**\n\n"
        "**Switch the dropdown and only the third answer moves.** That is the "
        "whole distinction: *unbounded* settles the comparison, because every "
        "moment of a period with no end falls after 2050; *unknown* settles "
        "nothing, because nobody has said whether the period reaches that far. "
        "Collapsing the two into one null is the mistake this type exists to "
        "prevent — it would turn *we do not know* into *no*.\n\n"
        "The first two answers do **not** move, and that is worth as much: "
        "1703–1914 sits wholly before 1991 whatever happens afterwards, so how "
        "the last period ends cannot change them. An open endpoint only "
        "withholds where it is actually load-bearing.\n\n"
        "*The same four values run everywhere. Retrieval answers a valid-time "
        "question in buckets — provably valid, or unknown — rather than as a "
        "filter, because a filter turns missing metadata into a silent false "
        "negative.*"
    )
    return


if __name__ == "__main__":
    app.run()
