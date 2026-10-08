"""The one canonical rendering of a timestamp a backend compares as text.

Shared by the SurrealDB and SQLite backends, which both store timestamps as
ISO-8601 strings in indexed fields and compare them there. Two backends each
owning a copy of this would be two places for the shape to drift apart.
"""

from datetime import UTC, datetime


def iso_micros(at: datetime) -> str:
    """A timestamp as a backend stores and compares it: UTC, ISO-8601, always to
    the microsecond, always ending in `Z`.

    Timestamps stored as text compare chronologically only while every rendering
    has the same shape. Pydantic omits the fractional part when it is exactly
    zero, so a row written on a whole second renders `…:41Z` while a bound
    renders `…:41.500000Z`, and since `"Z" > "."` the earlier row sorts *after*
    the later bound. Rare, and wrong. So every writer of a compared timestamp
    and every bound compared against one goes through here, which removes the
    case rather than narrowing it.

    A naive datetime is taken to be UTC, which is what every writer in this
    project means by one.
    """
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return at.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
