"""The agent guidance, read from the files that ship in ``epimemer_prompts``.

Two files, two channels. ``RULES.md`` is the server's MCP ``instructions``
string: short, sent to every client on connect, so the rules that must hold on
every call are in every context without anyone copying them. ``DEFAULT.md`` is
the full guide, served as the MCP prompt ``guide`` for a client to pull when
the work calls for it. The files are the single source; nothing here restates
them.
"""

from importlib.resources import files


def guidance(name: str) -> str:
    """The text of one guidance file, by its name in ``epimemer_prompts``."""
    return (files("epimemer_prompts") / name).read_text(encoding="utf-8")


def rules() -> str:
    """What must hold on every call: the server ``instructions``."""
    return guidance("RULES.md")


def guide() -> str:
    """The full guide to using the tools well: the ``guide`` prompt."""
    return guidance("DEFAULT.md")
