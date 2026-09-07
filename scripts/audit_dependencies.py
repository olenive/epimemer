#!/usr/bin/env python
"""Ask OSV whether anything installed here has a published advisory.

**This exists because Dependabot cannot see Python in this repository.** Its
`pip` ecosystem covers pip, pipenv, pip-compile and poetry, and the dependency
graph reads `requirements.txt` and `pipfile.lock`. Nothing in that list
understands `uv.lock`, so no alert fires and no pull request is opened, whatever
is in the resolved environment. Measured once, on 2026-09-07: 19 of 132
installed packages carried an advisory, one of them critical, and nothing had
said so.

**It reads the environment rather than the lock file.** A lock file says what
should be installed and a resolved environment says what is, and the second is
what an advisory is about. It also means the answer covers transitive packages
without this script having to resolve anything: `aiohttp` arrives through
`surrealdb` and `cryptography` through FastMCP's auth stack, and neither is
named in `pyproject.toml`.

**A finding is not a verdict.** OSV says a version is affected, not that this
code reaches the affected path: of those 19, the two that mattered were
`starlette`, whose `StaticFiles` serves the visualization hub, and `aiohttp`,
which every graph operation goes through. The rest sat behind FastMCP features
this server does not configure, or behind the `notebooks` extra. So the output
names the package and the severity and stops there, and a person decides.

    uv run python scripts/audit_dependencies.py

Three exit codes, because the caller has to tell two failures apart: `0` clean,
`1` something is affected, `2` the check itself could not run. A workflow that
files a finding as an issue must not file an unreachable OSV as one.
`--fail-on` raises the bar where a stream of low-severity findings would
otherwise train the habit of ignoring it.
"""

import argparse
import importlib.metadata as metadata
import json
import sys
import urllib.request
from collections.abc import Iterable, Sequence

OSV_BATCH_URL = "https://api.osv.dev/v1/querybatch"
OSV_QUERY_URL = "https://api.osv.dev/v1/query"

# Ascending, so a threshold is a comparison rather than a lookup table. OSV
# reports these under `database_specific.severity`, and an advisory carrying
# none sorts as `UNKNOWN` rather than being dropped: a missing severity is
# missing information, not an assurance.
SEVERITIES = ("UNKNOWN", "LOW", "MODERATE", "HIGH", "CRITICAL")


def installed_packages() -> list[tuple[str, str]]:
    """Every distribution in this environment, as (name, version)."""
    found = {
        (dist.metadata["Name"], dist.version)
        for dist in metadata.distributions()
        if dist.metadata["Name"] and dist.version
    }
    return sorted(found, key=lambda pair: pair[0].lower())


def _post(url: str, payload: dict, timeout: float) -> dict:
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def severity_of(vulns: Iterable[dict]) -> str:
    """The worst severity among these advisories."""
    ranks = [
        SEVERITIES.index(sev)
        for vuln in vulns
        if (sev := (vuln.get("database_specific") or {}).get("severity", "UNKNOWN")) in SEVERITIES
    ]
    return SEVERITIES[max(ranks)] if ranks else "UNKNOWN"


def fixed_versions(name: str, vulns: Iterable[dict]) -> list[str]:
    """Every version these advisories name as fixing them, for this package."""
    fixes = {
        event["fixed"]
        for vuln in vulns
        for affected in vuln.get("affected", [])
        if (affected.get("package") or {}).get("name", "").lower() == name.lower()
        for entry in affected.get("ranges", [])
        for event in entry.get("events", [])
        if "fixed" in event
    }
    return sorted(fixes)


def query_osv(packages: Sequence[tuple[str, str]], *, timeout: float = 120.0) -> dict[str, dict]:
    """For each affected package, its advisory ids, worst severity and fixes.

    Two calls per finding rather than one for everything: `querybatch` returns
    ids alone, and the severity and the fixing version live on the full record.
    Only the affected packages are fetched in full, which on a clean run is
    none.
    """
    queries = [
        {"package": {"name": name, "ecosystem": "PyPI"}, "version": version}
        for name, version in packages
    ]
    results = _post(OSV_BATCH_URL, {"queries": queries}, timeout)["results"]

    findings: dict[str, dict] = {}
    for (name, version), result in zip(packages, results, strict=True):
        if not result.get("vulns"):
            continue
        query = {"package": {"name": name, "ecosystem": "PyPI"}, "version": version}
        full = _post(OSV_QUERY_URL, query, timeout).get("vulns", [])
        findings[name] = {
            "version": version,
            "ids": sorted(vuln["id"] for vuln in full),
            "severity": severity_of(full),
            "fixed": fixed_versions(name, full),
        }
    return findings


def report(findings: dict[str, dict], checked: int, threshold: str) -> str:
    if not findings:
        return f"{checked} packages checked, none with a published advisory."
    lines = [f"{checked} packages checked, {len(findings)} with a published advisory.", ""]
    order = {name: SEVERITIES.index(f["severity"]) for name, f in findings.items()}
    for name in sorted(findings, key=lambda n: (-order[n], n.lower())):
        found = findings[name]
        at_or_above = order[name] >= SEVERITIES.index(threshold)
        mark = "!" if at_or_above else " "
        fixed = ", ".join(found["fixed"]) or "no fixed version published"
        lines.append(f"{mark} {found['severity']:8} {name} {found['version']}  fixed in: {fixed}")
        lines.append(f"           {', '.join(found['ids'])}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--fail-on",
        choices=[s.lower() for s in SEVERITIES],
        default="unknown",
        help=(
            "exit non-zero only at or above this severity (default: any advisory, "
            "including one OSV records no severity for)"
        ),
    )
    args = parser.parse_args(argv)
    threshold = args.fail_on.upper()

    packages = installed_packages()
    try:
        findings = query_osv(packages)
    except (OSError, ValueError, KeyError) as error:
        # Distinct from a finding: an unreachable or changed OSV says nothing
        # about this environment, and reporting it as a vulnerability would
        # spend the caller's attention on the wrong thing.
        print(f"could not complete the check: {error}", file=sys.stderr)
        return 2
    print(report(findings, len(packages), threshold))

    worst = max(
        (SEVERITIES.index(f["severity"]) for f in findings.values()),
        default=-1,
    )
    return 1 if worst >= SEVERITIES.index(threshold) else 0


if __name__ == "__main__":
    sys.exit(main())
