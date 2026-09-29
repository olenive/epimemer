"""The seat a judge is confirmed in: completeness, comparison, and the bound."""

from datetime import UTC, datetime, timedelta

from epimemer.core.seat import (
    JudgeConfirmation,
    Seat,
    ask_reason,
    carried_confirmation,
    seat_changes,
    seat_is_complete,
    with_confirmation,
)

AT = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)

SEAT = Seat(
    session_id="conv-1",
    client_name="claude-code",
    client_version="2.1.0",
    model="claude-fable-5-1",
)


def _confirmation(seat: Seat = SEAT, agent_id: str = "critic", at: datetime = AT):
    return JudgeConfirmation(
        session_id=seat.session_id,
        agent_id=agent_id,
        client_name=seat.client_name,
        client_version=seat.client_version,
        model=seat.model,
        confirmed_at=at,
    )


class TestSeatIsComplete:
    def test_conversation_and_model_make_a_complete_seat(self):
        assert seat_is_complete(SEAT)

    def test_the_client_name_and_version_are_not_required(self):
        # Client info is optional in the MCP handshake, and Claude Code sends
        # none, so a seat without it still carries a confirmation.
        unnamed = SEAT.model_copy(update={"client_name": None, "client_version": None})
        assert seat_is_complete(unnamed)

    def test_a_missing_session_or_model_is_incomplete(self):
        for field in ("session_id", "model"):
            assert not seat_is_complete(SEAT.model_copy(update={field: None})), field

    def test_an_empty_seat_is_incomplete(self):
        assert not seat_is_complete(Seat())


class TestSeatChanges:
    def test_the_same_seat_has_no_changes(self):
        assert seat_changes(SEAT, SEAT.model_copy()) == []

    def test_a_model_switch_is_named_with_both_models(self):
        now = SEAT.model_copy(update={"model": "claude-opus-5-5"})
        assert seat_changes(SEAT, now) == [
            "the model was claude-fable-5-1 and is now claude-opus-5-5"
        ]

    def test_known_to_unknown_is_a_change(self):
        now = SEAT.model_copy(update={"model": None})
        assert seat_changes(SEAT, now) == ["the model was claude-fable-5-1 and is now unknown"]

    def test_unknown_to_known_is_a_change(self):
        then = SEAT.model_copy(update={"model": None})
        assert seat_changes(then, SEAT) == ["the model was unknown and is now claude-fable-5-1"]

    def test_every_differing_field_is_named_in_reading_order(self):
        now = Seat(session_id="conv-2", client_name="other", client_version="1", model="m")
        changes = seat_changes(SEAT, now)
        assert [c.split(" was ")[0] for c in changes] == [
            "the conversation",
            "the client",
            "the client version",
            "the model",
        ]


class TestCarriedConfirmation:
    def test_a_matching_seat_carries_the_confirmation(self):
        stored = _confirmation()
        assert carried_confirmation([stored], "critic", SEAT) == stored

    def test_another_judge_in_the_same_seat_does_not_carry(self):
        assert carried_confirmation([_confirmation()], "editor", SEAT) is None

    def test_a_changed_model_does_not_carry(self):
        now = SEAT.model_copy(update={"model": "claude-opus-5-5"})
        assert carried_confirmation([_confirmation()], "critic", now) is None

    def test_an_incomplete_seat_never_carries(self):
        unknown_model = SEAT.model_copy(update={"model": None})
        assert carried_confirmation([_confirmation()], "critic", unknown_model) is None


class TestAskReason:
    def test_no_session_id(self):
        assert "no client session id" in ask_reason([], "critic", Seat())

    def test_model_unknown_points_at_the_file_and_the_hook(self):
        reason = ask_reason(
            [],
            "critic",
            SEAT.model_copy(update={"model": None}),
            client_state_file="/tmp/state/conv-1.json",
        )
        assert "model is unknown" in reason
        assert "/tmp/state/conv-1.json" in reason
        assert "INTEGRATION.md" in reason

    def test_first_claim_in_this_seat(self):
        assert "first claim for this judge in this seat" in ask_reason([], "critic", SEAT)

    def test_a_changed_field_is_named_from_and_to(self):
        now = SEAT.model_copy(update={"model": "claude-opus-5-5"})
        reason = ask_reason([_confirmation()], "critic", now)
        assert "the model was claude-fable-5-1 and is now claude-opus-5-5" in reason


class TestWithConfirmation:
    def test_the_same_seat_is_replaced_rather_than_repeated(self):
        first = _confirmation(at=AT)
        again = _confirmation(at=AT + timedelta(days=1))
        assert with_confirmation([first], again) == [again]

    def test_the_list_is_bounded_by_count_keeping_the_newest(self):
        many = [
            _confirmation(
                seat=SEAT.model_copy(update={"session_id": f"conv-{i}"}),
                at=AT + timedelta(minutes=i),
            )
            for i in range(5)
        ]
        newest = _confirmation(
            seat=SEAT.model_copy(update={"session_id": "conv-new"}), at=AT + timedelta(days=1)
        )
        kept = with_confirmation(many, newest, keep=3)
        assert [c.session_id for c in kept] == ["conv-3", "conv-4", "conv-new"]

    def test_ordered_by_time_not_by_text(self):
        """Offsets that render differently still order by the instant."""
        early = _confirmation(
            seat=SEAT.model_copy(update={"session_id": "a"}),
            at=datetime(2026, 9, 28, 13, 0, tzinfo=UTC) + timedelta(hours=0),
        )
        late_in_utc_but_lower_text = JudgeConfirmation(
            **{
                **_confirmation(seat=SEAT.model_copy(update={"session_id": "b"})).model_dump(),
                "confirmed_at": datetime.fromisoformat("2026-09-28T10:00:00-05:00"),
            }
        )
        kept = with_confirmation([late_in_utc_but_lower_text], early)
        assert [c.session_id for c in kept] == ["a", "b"]
