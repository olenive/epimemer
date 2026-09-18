"""Stated order between timepoints: the graph, the checks, and the verdicts.

Every test here is over pure functions, so a `Timeline` is built by hand and no
backend is involved. The tool-level behaviour lives in
`tests/mcp/test_timeline_ordering.py`.
"""

from datetime import UTC, datetime

from epimemer.core.temporal import IntervalBasis
from epimemer.core.types import (
    OrderingConstraint,
    Timeline,
    Timepoint,
    disputed_constraint_ids,
)
from epimemer.pipelines.timeline.ordering import (
    add_constraints,
    bounds_for,
    build_graph,
    clear_holds,
    contested_points,
    date_derived_edges,
    find_any_cycle,
    hold,
    live_constraints,
    merge_timepoints,
    points_after,
    points_before,
    points_between,
    retire_constraint,
    run_checks,
    self_ordering_refusal,
    split_timepoint,
)

AT = datetime(2026, 9, 17, tzinfo=UTC)


def _dt(year: int, month: int = 1, day: int = 1) -> datetime:
    return datetime(year, month, day, tzinfo=UTC)


def _constraint(earlier: str, later: str, *, source: str = "doc-1", basis: str = "stated"):
    return OrderingConstraint(
        earlier_id=earlier,
        later_id=later,
        source_id=source,
        basis=IntervalBasis(basis),
    )


def _timeline(points, constraints=()) -> Timeline:
    return Timeline(name="the parish", timepoints=list(points), constraints=list(constraints))


def _order(timeline, *pairs, source="doc-1", basis="stated"):
    return add_constraints(
        timeline,
        list(pairs),
        source_id=source,
        basis=IntervalBasis(basis),
        because=None,
        judge=None,
        at=AT,
    )


class TestDateDerivedEdges:
    """Two dated points order each other only where the dates settle it."""

    def test_an_instant_before_another_gives_one_edge(self):
        points = [
            Timepoint(id="fire", start=_dt(1897)),
            Timepoint(id="flood", start=_dt(1899)),
        ]
        edges = date_derived_edges(points)
        assert [(e.earlier_id, e.later_id) for e in edges] == [("fire", "flood")]

    def test_overlapping_intervals_give_no_edge_either_way(self):
        points = [
            Timepoint(id="a", start=_dt(1890), end=_dt(1910), label="the long war"),
            Timepoint(id="b", start=_dt(1900), end=_dt(1920), label="the longer peace"),
        ]
        assert date_derived_edges(points) == []

    def test_two_points_at_the_same_instant_order_nothing(self):
        """Equal dates settle nothing, and must not order each other both ways."""
        points = [
            Timepoint(id="a", start=_dt(1897)),
            Timepoint(id="b", start=_dt(1897)),
        ]
        assert date_derived_edges(points) == []

    def test_an_interval_ending_where_the_next_begins_still_orders(self):
        points = [
            Timepoint(id="a", start=_dt(1890), end=_dt(1900), label="before"),
            Timepoint(id="b", start=_dt(1900), end=_dt(1910), label="after"),
        ]
        edges = date_derived_edges(points)
        assert [(e.earlier_id, e.later_id) for e in edges] == [("a", "b")]

    def test_a_date_derived_edge_names_no_constraint(self):
        points = [Timepoint(id="a", start=_dt(1897)), Timepoint(id="b", start=_dt(1899))]
        assert date_derived_edges(points)[0].constraint_id is None


class TestTheCycleCheck:
    def test_a_self_loop_is_refused_rather_than_recorded(self):
        assert self_ordering_refusal("tp-1", "tp-1") is not None
        assert self_ordering_refusal("tp-1", "tp-2") is None

        timeline = _timeline([Timepoint(id="tp-1", label="the fire")])
        timeline, report = _order(timeline, ("tp-1", "tp-1"))
        assert report.outcomes[0].refused is not None
        assert timeline.constraints == []
        assert timeline.temporal_contradictions == []

    def test_two_constraints_that_disagree_open_a_cycle(self):
        timeline = _timeline(
            [Timepoint(id="fire", label="the fire"), Timepoint(id="flood", label="the flood")]
        )
        timeline, _ = _order(timeline, ("fire", "flood"), source="doc-1")
        timeline, report = _order(timeline, ("flood", "fire"), source="doc-2")

        assert len(report.opened) == 1
        assert report.opened[0].kind == "cycle"
        assert set(report.opened[0].constraint_ids) == {c.id for c in timeline.constraints}
        assert set(report.opened[0].sources) == {"doc-1", "doc-2"}

    def test_a_longer_loop_is_found_too(self):
        """An implementation that only checks the pair just inserted fails here."""
        timeline = _timeline([Timepoint(id=f"p{n}", label=f"p{n}") for n in range(1, 4)])
        timeline, _ = _order(timeline, ("p1", "p2"))
        timeline, _ = _order(timeline, ("p2", "p3"))
        timeline, report = _order(timeline, ("p3", "p1"))

        assert len(report.opened) == 1
        assert len(report.opened[0].constraint_ids) == 3

    def test_a_loop_closed_by_the_dates_themselves_is_found(self):
        timeline = _timeline(
            [
                Timepoint(id="fire", start=_dt(1897)),
                Timepoint(id="flood", start=_dt(1899)),
            ]
        )
        timeline, report = _order(timeline, ("flood", "fire"))

        assert len(report.opened) == 1
        loop = report.opened[0]
        assert loop.kind == "cycle"
        # One step is the source's assertion, the other is the dates'.
        assert [edge.constraint_id is None for edge in loop.edges].count(True) == 1

    def test_a_loop_closed_by_a_new_dated_point_is_found(self):
        """The case that is easy to miss: the date arrives after the constraints."""
        timeline = _timeline(
            [
                Timepoint(id="fire", start=_dt(1899)),
                Timepoint(id="flood", label="the flood"),
            ]
        )
        timeline, report = _order(timeline, ("flood", "fire"))
        assert report.opened == []

        # Dating the flood after the fire closes the loop.
        timeline = timeline.model_copy(
            update={
                "timepoints": [
                    point.model_copy(update={"start": _dt(1901)}) if point.id == "flood" else point
                    for point in timeline.timepoints
                ]
            }
        )
        timeline, opened = run_checks(timeline, at=AT)
        assert len(opened) == 1
        assert opened[0].kind == "cycle"

    def test_a_disputed_constraint_is_left_out_of_the_next_check(self):
        timeline = _timeline(
            [Timepoint(id="fire", label="the fire"), Timepoint(id="flood", label="the flood")]
        )
        timeline, _ = _order(timeline, ("fire", "flood"), source="doc-1")
        timeline, _ = _order(timeline, ("flood", "fire"), source="doc-2")
        assert len(timeline.temporal_contradictions) == 1

        # Running the checks again must not find the same loop a second time.
        timeline, opened = run_checks(timeline, at=AT)
        assert opened == []
        assert live_constraints(timeline) == []
        assert disputed_constraint_ids(timeline.temporal_contradictions) == {
            c.id for c in timeline.constraints
        }

    def test_an_order_a_source_repeats_is_recorded_once(self):
        timeline = _timeline(
            [Timepoint(id="fire", label="the fire"), Timepoint(id="flood", label="the flood")]
        )
        timeline, first = _order(timeline, ("fire", "flood"))
        timeline, second = _order(timeline, ("fire", "flood"))

        assert first.outcomes[0].created is True
        assert second.outcomes[0].created is False
        assert second.outcomes[0].constraint_id == first.outcomes[0].constraint_id
        assert len(timeline.constraints) == 1

    def test_two_sources_disagreeing_keeps_both_constraints(self):
        timeline = _timeline(
            [Timepoint(id="fire", label="the fire"), Timepoint(id="flood", label="the flood")]
        )
        timeline, _ = _order(timeline, ("fire", "flood"), source="doc-1")
        timeline, _ = _order(timeline, ("flood", "fire"), source="doc-2")
        assert len(timeline.constraints) == 2
        assert all(c.retired is None for c in timeline.constraints)

    def test_a_graph_with_no_loop_finds_none(self):
        timeline = _timeline([Timepoint(id=f"p{n}", label=f"p{n}") for n in range(1, 5)])
        timeline, _ = _order(timeline, ("p1", "p2"), ("p2", "p3"), ("p1", "p4"))
        assert find_any_cycle(build_graph(timeline)) is None


class TestBounds:
    def test_a_chain_tightens_the_bounds(self):
        timeline = _timeline(
            [
                Timepoint(id="start", start=_dt(1890)),
                Timepoint(id="mid", start=_dt(1895)),
                Timepoint(id="vague", label="the reconstruction"),
                Timepoint(id="end", start=_dt(1910)),
            ]
        )
        timeline, _ = _order(timeline, ("mid", "vague"), ("vague", "end"))

        bounds = bounds_for(build_graph(timeline), "vague")
        assert bounds.earliest == _dt(1895)
        assert bounds.latest == _dt(1910)

    def test_an_interval_gives_its_end_behind_and_its_start_ahead(self):
        timeline = _timeline(
            [
                Timepoint(id="war", start=_dt(1890), end=_dt(1900), label="the war"),
                Timepoint(id="vague", label="the rebuilding"),
                Timepoint(id="boom", start=_dt(1910), end=_dt(1920), label="the boom"),
            ]
        )
        timeline, _ = _order(timeline, ("war", "vague"), ("vague", "boom"))

        bounds = bounds_for(build_graph(timeline), "vague")
        assert bounds.earliest == _dt(1900)
        assert bounds.latest == _dt(1910)

    def test_a_one_sided_bound_is_a_real_answer(self):
        timeline = _timeline(
            [
                Timepoint(id="vague", label="the founding"),
                Timepoint(id="fire", start=_dt(1897)),
            ]
        )
        timeline, _ = _order(timeline, ("vague", "fire"))

        bounds = bounds_for(build_graph(timeline), "vague")
        assert bounds.earliest is None
        assert bounds.latest == _dt(1897)

    def test_a_point_nothing_constrains_stays_unbounded(self):
        timeline = _timeline(
            [Timepoint(id="vague", label="the founding"), Timepoint(id="fire", start=_dt(1897))]
        )
        bounds = bounds_for(build_graph(timeline), "vague")
        assert bounds.earliest is None and bounds.latest is None

    def test_stated_only_drops_a_bound_only_an_inference_produced(self):
        timeline = _timeline(
            [
                Timepoint(id="war", start=_dt(1890)),
                Timepoint(id="vague", label="the rebuilding"),
                Timepoint(id="boom", start=_dt(1910)),
            ]
        )
        timeline, _ = _order(timeline, ("war", "vague"), basis="stated")
        timeline, _ = _order(timeline, ("vague", "boom"), basis="inferred", source="doc-2")

        both = bounds_for(build_graph(timeline, basis="all"), "vague")
        assert both.earliest == _dt(1890) and both.latest == _dt(1910)

        stated = bounds_for(build_graph(timeline, basis="stated"), "vague")
        assert stated.earliest == _dt(1890)
        assert stated.latest is None

    def test_crossed_bounds_open_a_contradiction_naming_both_paths(self):
        """Two overlapping intervals squeeze a point with no loop to find."""
        timeline = _timeline(
            [
                Timepoint(id="a", start=_dt(1890), end=_dt(1910), label="the long war"),
                Timepoint(id="p", label="the treaty"),
                Timepoint(id="b", start=_dt(1900), end=_dt(1920), label="the long peace"),
            ]
        )
        timeline, report = _order(timeline, ("a", "p"), ("p", "b"))

        assert len(report.opened) == 1
        crossing = report.opened[0]
        assert crossing.kind == "crossed_bounds"
        assert crossing.point_id == "p"
        assert set(crossing.constraint_ids) == {c.id for c in timeline.constraints}
        assert contested_points(timeline) == {"p": crossing.id}

    def test_a_crossed_bound_does_not_contest_the_points_that_squeezed_it(self):
        timeline = _timeline(
            [
                Timepoint(id="a", start=_dt(1890), end=_dt(1910), label="the long war"),
                Timepoint(id="p", label="the treaty"),
                Timepoint(id="b", start=_dt(1900), end=_dt(1920), label="the long peace"),
            ]
        )
        timeline, _ = _order(timeline, ("a", "p"), ("p", "b"))
        assert "a" not in contested_points(timeline)
        assert "b" not in contested_points(timeline)


class TestOrderingQueries:
    def _chain(self):
        timeline = _timeline(
            [
                Timepoint(id="p1", start=_dt(1890)),
                Timepoint(id="p2", label="the middle"),
                Timepoint(id="p3", start=_dt(1910)),
                Timepoint(id="away", label="unconnected"),
            ]
        )
        timeline, _ = _order(timeline, ("p1", "p2"), ("p2", "p3"))
        return timeline

    def test_after_walks_the_closure(self):
        graph = build_graph(self._chain())
        assert set(points_after(graph, "p1")) == {"p2", "p3"}

    def test_before_walks_it_the_other_way(self):
        graph = build_graph(self._chain())
        assert set(points_before(graph, "p3")) == {"p1", "p2"}

    def test_between_is_the_intersection_and_excludes_what_neither_reaches(self):
        graph = build_graph(self._chain())
        assert points_between(graph, "p1", "p3") == ["p2"]

    def test_a_merged_point_takes_no_part(self):
        timeline = self._chain()
        timeline, _ = merge_timepoints(
            timeline,
            survivor_id="p2",
            merged_id="away",
            because="one moment under two names",
            at=AT,
        )
        graph = build_graph(timeline)
        assert "away" not in graph.points


class TestRetireVerdict:
    def _cycle(self):
        timeline = _timeline(
            [Timepoint(id="fire", label="the fire"), Timepoint(id="flood", label="the flood")]
        )
        timeline, _ = _order(timeline, ("fire", "flood"), source="doc-1")
        timeline, _ = _order(timeline, ("flood", "fire"), source="doc-2")
        return timeline, timeline.temporal_contradictions[0]

    def test_retiring_one_returns_the_other_to_live(self):
        timeline, contradiction = self._cycle()
        doomed, survivor = contradiction.constraint_ids

        timeline, report = retire_constraint(
            timeline,
            contradiction_id=contradiction.id,
            constraint_id=doomed,
            because="the source was misread: it narrates, it does not order",
            at=AT,
        )
        assert report.refused is None
        assert report.returned_to_live == [survivor]
        assert [c.id for c in live_constraints(timeline)] == [survivor]
        assert timeline.temporal_contradictions[0].is_open is False
        assert contested_points(timeline) == {}

    def test_the_retired_constraint_is_kept_with_its_reason(self):
        timeline, contradiction = self._cycle()
        doomed = contradiction.constraint_ids[0]
        before = next(c for c in timeline.constraints if c.id == doomed)

        timeline, _ = retire_constraint(
            timeline,
            contradiction_id=contradiction.id,
            constraint_id=doomed,
            because="this source is unreliable on dates",
            at=AT,
        )
        after = next(c for c in timeline.constraints if c.id == doomed)
        assert after.retired is not None
        assert after.retired.because == "this source is unreliable on dates"
        assert after.retired.contradiction_id == contradiction.id
        # Nothing else about the stored constraint moved.
        assert (after.earlier_id, after.later_id, after.source_id, after.basis) == (
            before.earlier_id,
            before.later_id,
            before.source_id,
            before.basis,
        )

    def test_a_constraint_a_second_contradiction_holds_stays_disputed(self):
        timeline = _timeline([Timepoint(id=f"p{n}", label=f"p{n}") for n in range(1, 4)])
        # One constraint takes part in two different loops.
        timeline, _ = _order(timeline, ("p1", "p2"), source="doc-1")
        timeline, _ = _order(timeline, ("p2", "p1"), source="doc-2")
        shared = timeline.temporal_contradictions[0].constraint_ids[0]
        # The shared constraint is disputed, so a second loop has to be built
        # from it directly rather than through the live graph.
        second = timeline.temporal_contradictions[0].model_copy(
            update={"id": "second", "constraint_ids": [shared]}
        )
        timeline = timeline.model_copy(
            update={"temporal_contradictions": [*timeline.temporal_contradictions, second]}
        )
        first = timeline.temporal_contradictions[0]
        other = next(cid for cid in first.constraint_ids if cid != shared)

        timeline, report = retire_constraint(
            timeline,
            contradiction_id=first.id,
            constraint_id=other,
            because="misread",
            at=AT,
        )
        assert report.returned_to_live == []
        assert report.still_disputed == {shared: "second"}

    def test_a_contradiction_already_answered_is_refused(self):
        timeline, contradiction = self._cycle()
        doomed = contradiction.constraint_ids[0]
        timeline, _ = retire_constraint(
            timeline,
            contradiction_id=contradiction.id,
            constraint_id=doomed,
            because="misread",
            at=AT,
        )
        _, report = retire_constraint(
            timeline,
            contradiction_id=contradiction.id,
            constraint_id=doomed,
            because="again",
            at=AT,
        )
        assert report.refused is not None

    def test_a_constraint_outside_the_contradiction_is_refused(self):
        timeline, contradiction = self._cycle()
        _, report = retire_constraint(
            timeline,
            contradiction_id=contradiction.id,
            constraint_id="not-a-constraint",
            because="misread",
            at=AT,
        )
        assert report.refused is not None


class TestSplitVerdict:
    def _cycle(self):
        timeline = _timeline(
            [Timepoint(id="fire", label="the fire"), Timepoint(id="flood", label="the flood")]
        )
        timeline, _ = _order(timeline, ("fire", "flood"), source="doc-1")
        timeline, _ = _order(timeline, ("flood", "fire"), source="doc-2")
        return timeline, timeline.temporal_contradictions[0]

    def test_a_split_makes_a_point_and_repoints_the_named_constraints(self):
        timeline, contradiction = self._cycle()
        moving = contradiction.constraint_ids[0]

        timeline, report = split_timepoint(
            timeline,
            contradiction_id=contradiction.id,
            point_id="fire",
            constraints_to_move=[moving],
            nodes_to_move=["fact-1"],
            because="two fires, and each source saw a different one",
            at=AT,
        )
        assert report.refused is None
        fresh = next(tp for tp in timeline.timepoints if tp.id == report.new_timepoint_id)
        assert fresh.label == "the fire"
        assert fresh.split_from == "fire"
        # A second occurrence is not the same moment, so no date travels.
        assert fresh.start is None and fresh.end is None

        retired = next(c for c in timeline.constraints if c.id == moving)
        assert retired.retired is not None
        replacement = next(c for c in timeline.constraints if c.id == retired.retired.superseded_by)
        assert fresh.id in (replacement.earlier_id, replacement.later_id)
        assert timeline.temporal_contradictions[0].is_open is False

    def test_a_split_names_the_links_to_move_and_leaves_the_rest(self):
        timeline, contradiction = self._cycle()
        _, report = split_timepoint(
            timeline,
            contradiction_id=contradiction.id,
            point_id="fire",
            constraints_to_move=[contradiction.constraint_ids[0]],
            nodes_to_move=["fact-1", "fact-2"],
            because="two fires",
            at=AT,
        )
        assert [(m.node_id, m.from_point_id) for m in report.link_moves] == [
            ("fact-1", "fire"),
            ("fact-2", "fire"),
        ]

    def test_a_point_with_no_label_cannot_be_split(self):
        """Dates are never copied onto a split, so a bare date leaves nothing to carry."""
        timeline = _timeline(
            [Timepoint(id="fire", start=_dt(1897)), Timepoint(id="flood", start=_dt(1899))]
        )
        timeline, _ = _order(timeline, ("flood", "fire"), source="doc-2")
        contradiction = timeline.temporal_contradictions[0]

        _, report = split_timepoint(
            timeline,
            contradiction_id=contradiction.id,
            point_id="fire",
            constraints_to_move=contradiction.constraint_ids,
            because="two fires",
            at=AT,
        )
        assert report.refused is not None
        assert "no label" in report.refused

    def test_a_constraint_that_does_not_name_the_point_is_refused(self):
        timeline, contradiction = self._cycle()
        timeline = timeline.model_copy(
            update={
                "timepoints": [
                    *timeline.timepoints,
                    Timepoint(id="storm", label="the storm"),
                    Timepoint(id="calm", label="the calm"),
                ],
                "constraints": [
                    *timeline.constraints,
                    _constraint("storm", "calm", source="doc-3"),
                ],
            }
        )
        stray = timeline.constraints[-1]
        _, report = split_timepoint(
            timeline,
            contradiction_id=contradiction.id,
            point_id="fire",
            constraints_to_move=[stray.id],
            because="two fires",
            at=AT,
        )
        assert report.refused is not None
        assert "cannot move" in report.refused


class TestHoldVerdict:
    def _cycle(self):
        timeline = _timeline(
            [Timepoint(id="fire", label="the fire"), Timepoint(id="flood", label="the flood")]
        )
        timeline, _ = _order(timeline, ("fire", "flood"), source="doc-1")
        timeline, _ = _order(timeline, ("flood", "fire"), source="doc-2")
        return timeline, timeline.temporal_contradictions[0]

    def test_a_hold_leaves_the_contradiction_open_and_the_point_contested(self):
        timeline, contradiction = self._cycle()
        timeline, report = hold(
            timeline,
            contradiction_id=contradiction.id,
            because="the two accounts are equally good and nothing settles it",
            at=AT,
        )
        assert report.refused is None
        held = timeline.temporal_contradictions[0]
        assert held.held is True
        assert held.is_open is True
        assert held.resolution is None
        assert set(contested_points(timeline)) == {"fire", "flood"}

    def test_a_new_constraint_touching_it_clears_the_hold(self):
        timeline, contradiction = self._cycle()
        timeline = timeline.model_copy(
            update={"timepoints": [*timeline.timepoints, Timepoint(id="storm", label="the storm")]}
        )
        timeline, _ = hold(
            timeline, contradiction_id=contradiction.id, because="nothing settles it", at=AT
        )
        timeline, _ = _order(timeline, ("storm", "fire"), source="doc-3")
        assert timeline.temporal_contradictions[0].held is False

    def test_a_constraint_touching_nothing_in_it_leaves_the_hold_alone(self):
        timeline, contradiction = self._cycle()
        timeline = timeline.model_copy(
            update={
                "timepoints": [
                    *timeline.timepoints,
                    Timepoint(id="x", label="x"),
                    Timepoint(id="y", label="y"),
                ]
            }
        )
        timeline, _ = hold(
            timeline, contradiction_id=contradiction.id, because="nothing settles it", at=AT
        )
        timeline, _ = _order(timeline, ("x", "y"), source="doc-3")
        assert timeline.temporal_contradictions[0].held is True

    def test_a_dated_point_touching_it_clears_the_hold(self):
        timeline, contradiction = self._cycle()
        timeline, _ = hold(
            timeline, contradiction_id=contradiction.id, because="nothing settles it", at=AT
        )
        timeline = clear_holds(timeline, ["fire"])
        assert timeline.temporal_contradictions[0].held is False

    def test_a_hold_is_appended_rather_than_replacing_the_history(self):
        timeline, contradiction = self._cycle()
        timeline, _ = hold(
            timeline, contradiction_id=contradiction.id, because="nothing settles it", at=AT
        )
        timeline, _ = retire_constraint(
            timeline,
            contradiction_id=contradiction.id,
            constraint_id=contradiction.constraint_ids[0],
            because="new evidence: the source was misread",
            at=AT,
        )
        closed = timeline.temporal_contradictions[0]
        assert [entry.verdict for entry in closed.resolutions] == [
            "hold",
            "retire_constraint",
        ]
        assert closed.resolution.verdict == "retire_constraint"


class TestMergeVerdict:
    def _two_names(self):
        return _timeline(
            [
                Timepoint(id="launch", label="the launch"),
                Timepoint(id="golive", label="the go-live"),
                Timepoint(id="review", start=_dt(2024, 6)),
            ]
        )

    def test_a_merge_repoints_the_constraints_and_retires_the_point(self):
        timeline = self._two_names()
        timeline, _ = _order(timeline, ("golive", "review"))
        moving = timeline.constraints[0].id

        timeline, report = merge_timepoints(
            timeline,
            survivor_id="launch",
            merged_id="golive",
            linked_node_ids=["fact-1"],
            because="two documents, one event",
            at=AT,
        )
        assert report.refused is None
        assert report.moved_constraint_ids == [moving]
        merged = next(tp for tp in timeline.timepoints if tp.id == "golive")
        assert merged.merged_into == "launch"

        retired = next(c for c in timeline.constraints if c.id == moving)
        replacement = next(c for c in timeline.constraints if c.id == retired.retired.superseded_by)
        assert (replacement.earlier_id, replacement.later_id) == ("launch", "review")
        assert [(m.node_id, m.to_point_id) for m in report.link_moves] == [("fact-1", "launch")]

    def test_a_merge_is_refused_when_a_constraint_orders_the_two(self):
        timeline = self._two_names()
        timeline, _ = _order(timeline, ("launch", "golive"))

        _, report = merge_timepoints(
            timeline,
            survivor_id="launch",
            merged_id="golive",
            because="two documents, one event",
            at=AT,
        )
        assert report.refused is not None
        assert timeline.constraints[0].source_id in report.refused

    def test_a_merge_runs_the_checks_again(self):
        """Combining two points can create a loop neither had alone."""
        timeline = _timeline(
            [
                Timepoint(id="a", label="a"),
                Timepoint(id="b", label="b"),
                Timepoint(id="c", label="c"),
            ]
        )
        timeline, _ = _order(timeline, ("a", "b"), source="doc-1")
        timeline, _ = _order(timeline, ("b", "c"), source="doc-2")

        timeline, report = merge_timepoints(
            timeline,
            survivor_id="a",
            merged_id="c",
            because="a and c are one moment",
            at=AT,
        )
        assert report.refused is None
        assert [found.kind for found in report.opened] == ["cycle"]

    def test_a_point_cannot_be_merged_into_itself(self):
        _, report = merge_timepoints(
            self._two_names(),
            survivor_id="launch",
            merged_id="launch",
            because="nonsense",
            at=AT,
        )
        assert report.refused is not None


class TestAppendOnly:
    def test_no_verdict_edits_a_stored_constraint(self):
        timeline = _timeline(
            [
                Timepoint(id="launch", label="the launch"),
                Timepoint(id="golive", label="the go-live"),
                Timepoint(id="review", start=_dt(2024, 6)),
            ]
        )
        timeline, _ = _order(timeline, ("golive", "review"))
        before = {
            c.id: c.model_dump(mode="json", exclude={"retired"}) for c in timeline.constraints
        }

        timeline, _ = merge_timepoints(
            timeline,
            survivor_id="launch",
            merged_id="golive",
            because="one event",
            at=AT,
        )
        after = {c.id: c.model_dump(mode="json", exclude={"retired"}) for c in timeline.constraints}
        for constraint_id, stored in before.items():
            assert after[constraint_id] == stored
