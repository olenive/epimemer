# Security

## Reporting a vulnerability

Use GitHub's private vulnerability reporting: open the repository's
**Security** tab and choose **Report a vulnerability**. That reaches the
maintainer without a public issue, and GitHub tracks the report through to a
fix and an advisory. Please do not open a public issue for a security problem.

The latest release is the supported version.

## What this server trusts

Epimemer is an MCP server that talks to one client over stdio. It trusts that
client completely: every tool an agent can call is available to whatever
process launched the server, and the tools deliberately cannot do the things
reserved for a person — approving a judge, renaming one, requiring one — which
live in the `epimemer` CLI and in the client's elicitation prompts instead.

Storage credentials are the deployer's. The `root`/`root` defaults are
SurrealDB's local-development credentials; anything reachable from another
machine needs `EPIMEMER_SURREALDB_USER` and `EPIMEMER_SURREALDB_PASS` set.

The visualization hub binds `127.0.0.1` by default and, on that bind, keeps
whole retrieval records, including query text and response payloads. Pointing
`EPIMEMER_VIZ_HOST` at a non-loopback address switches sessions to mirroring
structural metadata only; the hub has no authentication of its own, so an
address other users can reach is an address other users can read.
