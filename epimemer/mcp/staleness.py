"""Whether this server still runs the Epimemer that is installed.

A server process outlives upgrades of the package underneath it. It keeps
running the code it loaded when it started, while the files on disk, and the
tool schemas a reconnecting client would be handed, belong to the new version.
Every tool response says so while the two differ, because the agent reading
it is the one who can ask the user to reconnect.

The version the server started on is read once, at startup, and carried as a
value; the installed version is read from disk on each call, by
`read_installed_version`.
"""

import importlib.metadata

# What the server reports for a version it could not read: the `serverInfo` of
# the handshake and the opening of every judge prompt carry this text.
UNKNOWN_VERSION = "unknown"

# The `kind` of the entry this module adds to a response's `warnings`.
STALE_SERVER = "stale_server"

STALE_SERVER_WARNING = (
    "This server is running Epimemer {running} and the installed package is now "
    "{installed}. Ask the user to reconnect the MCP client, which restarts the "
    "server on the installed version. After the reconnect, load the tool schemas "
    "again: the ones you hold describe the old version."
)


def read_installed_version(distribution: str = "epimemer") -> str | None:
    """The version of `distribution` installed on disk now, or None.

    None on any failure, because this runs inside every tool call and an
    upgrade can be half done when it does: the metadata directory can be gone,
    or present with its `METADATA` file not yet written. Not knowing the
    version means no warning, never a failed tool.
    """
    try:
        version = importlib.metadata.version(distribution)
    except Exception:
        return None
    return version if isinstance(version, str) and version else None


def stale_server_warning(running: str | None, installed: str | None) -> str | None:
    """The warning text when the server runs another version than is installed.

    None when the two are equal, and when either is unknown: a server run from
    a source tree with nothing installed has no version to compare.
    """
    unknown = (None, "", UNKNOWN_VERSION)
    if running in unknown or installed in unknown or running == installed:
        return None
    return STALE_SERVER_WARNING.format(running=running, installed=installed)


def stale_server_entry(running: str | None, installed: str | None) -> dict | None:
    """The warning as one entry of a response's `warnings`, or None.

    The shape every entry there has: a `kind`, a `message`, the `subjects` it
    is about and structured `detail`.
    """
    message = stale_server_warning(running, installed)
    if message is None:
        return None
    return {
        "kind": STALE_SERVER,
        "message": message,
        "subjects": [],
        "detail": {"running": running, "installed": installed},
    }


def with_warning(response: dict, entry: dict | None) -> dict:
    """`response` with `entry` added to its `warnings`, a list made where it has none.

    A new dict; the one passed in is left as it was. `notify_user` is the
    tool's own answer and stays untouched.
    """
    if entry is None:
        return response
    return {**response, "warnings": [*(response.get("warnings") or []), entry]}
