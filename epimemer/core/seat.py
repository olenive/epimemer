"""The seat a judge is confirmed in (REVIEW_MODE.md §2.6).

The seat a claim is made from: the conversation it is made in, the client that
carries it, and the model behind it. A judge is confirmed in a seat; change any
part and it is a different seat, and the user is asked again.

Everything here is pure. Reading the model off disk lives in
`epimemer.client_state`, and remembering confirmations lives on the storage
backend, beside the approved-id list.
"""

from collections.abc import Sequence
from datetime import datetime

from pydantic import BaseModel

# How many confirmations a graph keeps. A bound on the list's size, not an
# expiry: nothing is dropped for being old, only for being the oldest once
# there are more than this many, and a dropped seat simply asks again.
JUDGE_CONFIRMATIONS_KEPT = 500


class Seat(BaseModel):
    """Where a claim is made from. Any field may be unknown, and is then None.

    `session_id` is the client's conversation, `client_name` and
    `client_version` come from the MCP handshake when the client sends them,
    and `model` is what a client hook recorded for this conversation. The
    server cannot see the model itself, which is why it needs the hook.
    """

    session_id: str | None = None
    client_name: str | None = None
    client_version: str | None = None
    model: str | None = None


class JudgeConfirmation(BaseModel):
    """The user confirmed this judge in this seat, at `confirmed_at`.

    Persisted per graph, so a confirmation outlives the server process that
    asked for it: a reconnect restarts the server, and nothing about who is
    behind the name changed.
    """

    session_id: str
    agent_id: str
    client_name: str | None = None
    client_version: str | None = None
    model: str
    confirmed_at: datetime


# The fields a seat is compared on, in the order a person reads them, with the
# words a refusal or a picker uses for each.
_SEAT_FIELDS: tuple[tuple[str, str], ...] = (
    ("session_id", "the conversation"),
    ("client_name", "the client"),
    ("client_version", "the client version"),
    ("model", "the model"),
)


def seat_is_complete(seat: Seat) -> bool:
    """Whether the seat is known well enough to carry a confirmation.

    The conversation and the model. The client's name and version are part of
    the seat and count when compared, but neither is required: client info is
    optional in the MCP handshake on the current protocol, and Claude Code
    sends none, so requiring it would leave the seat incomplete on the very
    client the hook exists for.
    """
    return bool(seat.session_id and seat.model)


def _shown(value: str | None) -> str:
    return value if value else "unknown"


def seat_changes(then: Seat, now: Seat) -> list[str]:
    """What differs between two seats, one plain sentence per field.

    Unknown counts as a value: a model that was known and is now unknown is a
    change, and so is the reverse, because either way nobody can say the same
    model is behind the name.
    """
    return [
        f"{label} was {_shown(getattr(then, field))} and is now {_shown(getattr(now, field))}"
        for field, label in _SEAT_FIELDS
        if getattr(then, field) != getattr(now, field)
    ]


def confirmation_seat(confirmation: JudgeConfirmation) -> Seat:
    """The seat a confirmation was given in."""
    return Seat(
        session_id=confirmation.session_id,
        client_name=confirmation.client_name,
        client_version=confirmation.client_version,
        model=confirmation.model,
    )


def carried_confirmation(
    confirmations: Sequence[JudgeConfirmation], agent_id: str, seat: Seat
) -> JudgeConfirmation | None:
    """The confirmation this judge already has in exactly this seat, if any.

    Only a complete seat can carry one, so an incomplete seat always asks.
    """
    if not seat_is_complete(seat):
        return None
    matches = [c for c in confirmations if c.agent_id == agent_id and confirmation_seat(c) == seat]
    return min(matches, key=lambda c: c.confirmed_at) if matches else None


def ask_reason(
    confirmations: Sequence[JudgeConfirmation],
    agent_id: str,
    seat: Seat,
    *,
    client_state_file: str | None = None,
) -> str:
    """Why the user is being asked, in a sentence they can act on.

    Shown in the picker and returned as `asked_because`, so a person asked
    after a reconnect can see whether that was expected or whether the seat
    really changed.
    """
    if not seat.session_id:
        return (
            "the client did not say which conversation this is (no client "
            "session id), so a confirmation cannot be carried over"
        )
    if not seat.model:
        where = f" at {client_state_file}" if client_state_file else ""
        return (
            f"the model is unknown: there is no client state for this "
            f"session{where}. The client hook in INTEGRATION.md records it"
        )
    same_conversation = sorted(
        (c for c in confirmations if c.agent_id == agent_id and c.session_id == seat.session_id),
        key=lambda c: c.confirmed_at,
    )
    if not same_conversation:
        return "this is the first claim for this judge in this seat"
    changes = seat_changes(confirmation_seat(same_conversation[-1]), seat)
    return "; ".join(changes) + " since this judge was last confirmed"


def with_confirmation(
    confirmations: Sequence[JudgeConfirmation],
    confirmation: JudgeConfirmation,
    *,
    keep: int = JUDGE_CONFIRMATIONS_KEPT,
) -> list[JudgeConfirmation]:
    """The list with this confirmation added, newest last, at most `keep` long.

    A confirmation of the same judge in the same seat replaces the older one,
    so the list holds one entry per judge per seat. Ordered by the datetime
    itself, never by its text.
    """
    others = [
        c
        for c in confirmations
        if not (
            c.agent_id == confirmation.agent_id
            and confirmation_seat(c) == confirmation_seat(confirmation)
        )
    ]
    ordered = sorted([*others, confirmation], key=lambda c: c.confirmed_at)
    return ordered[-keep:] if keep > 0 else []
