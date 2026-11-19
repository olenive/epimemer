#!/usr/bin/env python
"""Ask OSV and GitHub whether anything installed here has a published advisory.

**This exists because Dependabot cannot see Python in this repository.** Its
`pip` ecosystem covers pip, pipenv, pip-compile and poetry, and the dependency
graph reads `requirements.txt` and `pipfile.lock`. Nothing in that list
understands `uv.lock`, so no alert fires and no pull request is opened, whatever
is in the resolved environment. Measured once, on 2026-09-07: 19 of 132
installed packages carried an advisory, one of them critical, and nothing had
said so.

**OSV alone misses what a project publishes on its own repository.** A
maintainer publishes an advisory on the project's GitHub security page, and OSV
imports it only after GitHub has reviewed it into the global advisory database,
which can take weeks or never happen. So the script also reads the security
page of every GitHub repository named in installed metadata (`Project-URL` and
`Home-page`). On 2026-11-19 OSV reported nothing while five of those pages
carried ten advisories against installed versions, one of them critical. A package
whose metadata names no GitHub repository is checked against OSV alone, and the
report names it.

**What it cannot read goes to a person.** A repository advisory names a package
and a version range, both typed by the maintainer. One that names no package is
attributed when exactly one installed package comes from that repository, and
is otherwise listed for a person to read, as is a range that will not parse,
unless the installed version is at or past every patched version, which the
report notes. Nothing is skipped: missing information is not an assurance.

**It reads the environment rather than the lock file.** A lock file says what
should be installed and a resolved environment says what is, and the second is
what an advisory is about. It also means the answer covers transitive packages
without this script having to resolve anything: `aiohttp` arrives through
`surrealdb` and `cryptography` through FastMCP's auth stack, and neither is
named in `pyproject.toml`.

**A finding is not a verdict.** An advisory says a version is affected, not that
this code reaches the affected path: of those 19, the two that mattered were
`starlette`, whose `StaticFiles` serves the visualization hub, and `aiohttp`,
which every graph operation goes through. The rest sat behind FastMCP features
this server does not configure, or behind the `notebooks` extra. So the output
names the package and the severity and stops there, and a person decides.

    GITHUB_TOKEN=$(gh auth token) uv run python scripts/audit_dependencies.py

The token is needed because GitHub allows 60 unauthenticated requests an hour
and there are more than a hundred repositories to read; any token will do, since
the advisories are public. `--skip-github` checks OSV alone and the report says
so.

Three exit codes, because the caller has to tell two failures apart: `0` clean,
`1` something is affected, `2` the check itself could not run. A workflow that
files a finding as an issue must not file an unreachable service, a rate limit,
a missing token or a defect in this script as one, so every error is `2`.
`--fail-on` raises the bar where a stream of low-severity findings would
otherwise train the habit of ignoring it.
"""

import argparse
import importlib.metadata as metadata
import json
import os
import re
import sys
import traceback
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime

try:
    from packaging.specifiers import InvalidSpecifier, SpecifierSet
    from packaging.utils import canonicalize_name
    from packaging.version import InvalidVersion, Version
except ImportError as error:
    # Python exits 1 on an uncaught exception, and 1 is the code for a finding.
    print(f"could not complete the check: {error}", file=sys.stderr)
    sys.exit(2)

OSV_BATCH_URL = "https://api.osv.dev/v1/querybatch"
OSV_QUERY_URL = "https://api.osv.dev/v1/query"
GITHUB_API = "https://api.github.com"

# Ascending, so a threshold is a comparison rather than a lookup table. OSV
# reports these under `database_specific.severity`, and an advisory carrying
# none sorts as `UNKNOWN` rather than being dropped: a missing severity is
# missing information, not an assurance.
SEVERITIES = ("UNKNOWN", "LOW", "MODERATE", "HIGH", "CRITICAL")

# First path segments on github.com that are GitHub's own pages, so a link such
# as a `Funding` entry pointing at `github.com/sponsors/someone` names no
# repository. `.github` is an organisation's settings repository, which holds
# no package.
_GITHUB_PAGES = frozenset(
    "about advisories apps collections enterprise events explore features issues login "
    "marketplace notifications orgs pricing pulls search security settings site sponsors "
    "topics trending users".split()
)
_OWNER = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*")
_REPO = re.compile(r"[A-Za-z0-9._-]+")
_NEXT_LINK = re.compile(r'<([^>]+)>\s*;\s*rel="next"')

# A bound on pagination, so a response that keeps pointing at another page
# cannot hold the check forever. A hundred advisories a page makes this far
# more than any repository publishes.
_MAX_PAGES = 50


def installed_packages() -> list[tuple[str, str]]:
    """Every distribution in this environment, as (name, version)."""
    found = {
        (dist.metadata["Name"], dist.version)
        for dist in metadata.distributions()
        if dist.metadata["Name"] and dist.version
    }
    return sorted(found, key=lambda pair: pair[0].lower())


def github_repository(url: str) -> str | None:
    """The `owner/repo` a URL points into, lower-cased, or None for any other URL."""
    url = url.strip().removeprefix("git+")
    try:
        parsed = urllib.parse.urlsplit(url)
        host = parsed.hostname
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or host not in ("github.com", "www.github.com"):
        return None
    segments = [segment for segment in parsed.path.split("/") if segment]
    if len(segments) < 2:
        return None
    owner, repo = segments[0], segments[1].removesuffix(".git")
    if owner.lower() in _GITHUB_PAGES or repo.lower() == ".github":
        return None
    if not _OWNER.fullmatch(owner) or not _REPO.fullmatch(repo) or repo in (".", ".."):
        return None
    return f"{owner}/{repo}".lower()


def metadata_urls(project_urls: Iterable[str], home_page: str | None) -> list[str]:
    """The URLs in a distribution's metadata: `Project-URL` is `label, url`."""
    urls = [entry.split(",", 1)[-1].strip() for entry in project_urls]
    return [*urls, home_page] if home_page else urls


def repositories_of(urls: Iterable[str]) -> set[str]:
    """Every GitHub repository these URLs point into."""
    return {repo for url in urls if (repo := github_repository(url))}


def installed_repositories() -> dict[str, set[str]]:
    """For each distribution in this environment, the GitHub repositories its metadata names."""
    found: dict[str, set[str]] = {}
    for dist in metadata.distributions():
        name = dist.metadata["Name"]
        if not name:
            continue
        urls = metadata_urls(dist.metadata.get_all("Project-URL") or [], dist.metadata["Home-page"])
        found.setdefault(name, set()).update(repositories_of(urls))
    return found


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


def _github_get(url: str, token: str | None, timeout: float) -> tuple[object, str | None]:
    """One page of a GitHub listing, and the URL of the next page if there is one."""
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "dependency-advisory-audit",
        },
    )
    if token:
        # Unredirected, so a redirect cannot carry the token to another host.
        request.add_unredirected_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        link = _NEXT_LINK.search(response.headers.get("Link") or "")
        return json.load(response), link.group(1) if link else None


def _refusal(repo: str, error: urllib.error.HTTPError) -> OSError:
    """A GitHub error as a message saying whether it was the rate limit."""
    remaining = error.headers.get("x-ratelimit-remaining") if error.headers else None
    if error.code in (403, 429) and remaining == "0":
        reset = error.headers.get("x-ratelimit-reset", "")
        when = (
            datetime.fromtimestamp(int(reset), UTC).strftime("%H:%M UTC")
            if reset.isdigit()
            else "an unknown time"
        )
        return OSError(f"GitHub rate limit reached reading {repo}; it resets at {when}")
    return OSError(f"GitHub refused {repo}: HTTP {error.code} {error.reason}")


def repository_advisories(
    repo: str, token: str | None, *, timeout: float = 30.0
) -> list[dict] | None:
    """Every published advisory on `repo`'s security page; None if GitHub has no such repo."""
    url = f"{GITHUB_API}/repos/{repo}/security-advisories?state=published&per_page=100"
    advisories: list[dict] = []
    for _ in range(_MAX_PAGES):
        try:
            page, url = _github_get(url, token, timeout)
        except urllib.error.HTTPError as error:
            # A 404 is a stale URL in some package's metadata, which no run of
            # this check can fix; the report names the repository instead.
            if error.code == 404 and not advisories:
                return None
            raise _refusal(repo, error) from error
        if not isinstance(page, list):
            raise ValueError(f"GitHub returned {type(page).__name__} for {repo}, not a list")
        advisories.extend(page)
        if url is None:
            return advisories
        if not url.startswith(f"{GITHUB_API}/"):
            raise ValueError(f"GitHub pointed the next page of {repo} at {url}")
    raise ValueError(f"{repo} has more than {_MAX_PAGES} pages of advisories")


def query_github(
    repos: Iterable[str], token: str | None, *, timeout: float = 30.0
) -> tuple[dict[str, list[dict]], list[str]]:
    """Each repository's advisories, and the repositories GitHub says do not exist."""
    found: dict[str, list[dict]] = {}
    missing: list[str] = []
    for repo in sorted(set(repos)):
        advisories = repository_advisories(repo, token, timeout=timeout)
        if advisories is None:
            missing.append(repo)
        else:
            found[repo] = advisories
    return found, missing


def github_severity(advisory: dict) -> str:
    """GitHub's severity in OSV's words, where GitHub's `medium` is `MODERATE`."""
    raw = str(advisory.get("severity") or "").upper()
    raw = "MODERATE" if raw == "MEDIUM" else raw
    return raw if raw in SEVERITIES else "UNKNOWN"


def _versions(texts: Iterable[str]) -> list[Version] | None:
    """These as versions, or None if any is not one."""
    try:
        return [Version(text) for text in texts]
    except InvalidVersion:
        return None


def github_range(text: str | None) -> Callable[[Version], bool] | None:
    """GitHub's version range as a test of a version, or None if it will not parse.

    GitHub's syntax is PEP 440's except for an exact version, which it writes
    `= 1.2.3`, so `>= 2.13.0, < 2.14.2` reads as written. Some maintainers write
    a bare list of the affected versions instead, `1.6.5, 1.6.6`, which reads as
    those versions exactly. An empty range will not parse, rather than becoming
    an empty specifier set, which would match every version.
    """
    clauses = [clause.strip() for clause in (text or "").split(",") if clause.strip()]
    if not clauses:
        return None
    exact = _versions(clauses)
    if exact is not None:
        return lambda version: version in exact
    pep440 = ["==" + c[1:] if c.startswith("=") and c[1:2] != "=" else c for c in clauses]
    try:
        specifier = SpecifierSet(",".join(pep440))
    except InvalidSpecifier:
        return None
    return lambda version: specifier.contains(version, prereleases=True)


def _patched(entry: dict) -> list[str]:
    """The fixing versions, kept by GitHub as free text: `1.8.2,v1.7.4`, `1.84.2 and 1.85.1`."""
    text = str(entry.get("patched_versions") or "")
    return sorted({fix.strip() for fix in re.split(r",|\band\b", text) if fix.strip()})


def _verdict(version: str, affects: Callable[[Version], bool] | None, patched: list[str]) -> str:
    """`affected` or `clear` where the range decides.

    Where it cannot, `patched` if this version is at or past every fixing
    version, which is what a fix means, and otherwise `unknown`.
    """
    installed = _versions([version])
    if installed is None:
        return "unknown"
    if affects is not None:
        return "affected" if affects(installed[0]) else "clear"
    fixes = _versions(patched)
    return "patched" if fixes and installed[0] >= max(fixes) else "unknown"


def github_findings(
    packages: Sequence[tuple[str, str]],
    repos_by_package: Mapping[str, Iterable[str]],
    advisories_by_repo: Mapping[str, Iterable[dict]],
) -> tuple[dict[str, dict], list[dict], list[dict]]:
    """What these repository advisories say about the installed packages.

    Three answers: the affected packages; the advisories a person has to read;
    and notes on ranges that will not parse, where the installed version is at
    or past every fix. A named package is matched wherever it is installed,
    whichever repository published the advisory. An entry naming no package
    belongs to the one installed package from that repository; when several
    come from it, a person decides, unless the range clears them all.
    """
    installed = {canonicalize_name(name): (name, version) for name, version in packages}
    from_repo: dict[str, set[str]] = {}
    for name, repos in repos_by_package.items():
        for repo in repos:
            if canonicalize_name(name) in installed:
                from_repo.setdefault(repo, set()).add(canonicalize_name(name))

    findings: dict[str, dict] = {}
    unresolved: list[dict] = []
    notes: list[dict] = []

    def keep(into: list[dict], **item: str) -> None:
        if item not in into:
            into.append(item)

    for repo, advisories in sorted(advisories_by_repo.items()):
        for advisory in advisories:
            if advisory.get("withdrawn_at"):
                continue
            ghsa = advisory["ghsa_id"]
            severity = github_severity(advisory)
            for entry in advisory.get("vulnerabilities") or []:
                package = entry.get("package") or {}
                if package.get("ecosystem") not in (None, "", "pip"):
                    continue
                if package.get("name"):
                    candidates = {canonicalize_name(package["name"])} & installed.keys()
                else:
                    candidates = from_repo.get(repo, set())
                stated = entry.get("vulnerable_version_range")
                shown = repr(stated) if stated else "missing"
                affects = github_range(stated)
                patched = _patched(entry)
                verdicts = {
                    installed[c]: _verdict(installed[c][1], affects, patched)
                    for c in sorted(candidates)
                }

                for (name, version), verdict in verdicts.items():
                    if verdict == "patched":
                        reason = f"range {shown}, {name} {version}, patched {', '.join(patched)}"
                        keep(notes, id=ghsa, repo=repo, reason=reason)
                if len(verdicts) > 1:
                    still_open = [
                        f"{name} {version}"
                        for (name, version), verdict in verdicts.items()
                        if verdict in ("affected", "unknown")
                    ]
                    if still_open:
                        reason = f"names no package; range {shown} could apply to " + ", ".join(
                            still_open
                        )
                        keep(unresolved, id=ghsa, severity=severity, repo=repo, reason=reason)
                    continue
                for (name, version), verdict in verdicts.items():
                    if verdict == "affected":
                        found = {"version": version, "ids": [ghsa], "severity": severity}
                        findings = merge_findings(findings, {name: {**found, "fixed": patched}})
                    elif verdict == "unknown":
                        reason = (
                            f"range {shown} does not parse ({name} {version})"
                            if affects is None
                            else f"{name} {version} is not a PEP 440 version"
                        )
                        keep(unresolved, id=ghsa, severity=severity, repo=repo, reason=reason)
    return findings, unresolved, notes


def merge_findings(*sources: Mapping[str, dict]) -> dict[str, dict]:
    """One finding per package: every id, the worst severity, every fixing version.

    An advisory that has reached OSV carries the same GHSA id there, so the
    union lists it once.
    """
    merged: dict[str, dict] = {}
    for source in sources:
        for name, found in source.items():
            prior = merged.get(name, {"ids": [], "severity": "UNKNOWN", "fixed": []})
            merged[name] = {
                "version": found["version"],
                "ids": sorted({*prior["ids"], *found["ids"]}),
                "severity": max(prior["severity"], found["severity"], key=SEVERITIES.index),
                "fixed": sorted({*prior["fixed"], *found["fixed"]}),
            }
    return merged


def _coverage(github: dict | None) -> list[str]:
    if github is None:
        return ["GitHub repository advisories were not checked (--skip-github): OSV alone."]
    unchecked = github["unchecked"]
    read = f"GitHub: {github['repositories']} repositories read"
    if unchecked:
        names = ", ".join(unchecked)
        lines = [f"{read}; {len(unchecked)} packages name none, so OSV alone checked: {names}."]
    else:
        lines = [f"{read}; every package names one."]
    if github["missing"]:
        lines.append("Named in package metadata but not on GitHub: " + ", ".join(github["missing"]))
    return lines


def report(
    findings: dict[str, dict], checked: int, threshold: str, github: dict | None = None
) -> str:
    """The findings worst first, then what a person has to read.

    `github` is None when GitHub was not asked, and otherwise holds how many
    repositories were read (`repositories`), the packages left to OSV alone
    (`unchecked`), the repositories GitHub does not have (`missing`), the
    advisories it could not pin to a package or version (`unresolved`), and the
    unparseable ranges read as fixed (`notes`).
    """
    unresolved = github["unresolved"] if github else []
    notes = github["notes"] if github else []
    if not findings and not unresolved:
        head = f"{checked} packages checked, none with a published advisory."
    else:
        head = f"{checked} packages checked, {len(findings)} with a published advisory"
        head += f"; {len(unresolved)} for a person to read (below)." if unresolved else "."
    lines = [head, *_coverage(github)]
    bar = SEVERITIES.index(threshold)

    order = {name: SEVERITIES.index(f["severity"]) for name, f in findings.items()}
    if findings:
        lines.append("")
    for name in sorted(findings, key=lambda n: (-order[n], n.lower())):
        found = findings[name]
        mark = "!" if order[name] >= bar else " "
        fixed = ", ".join(found["fixed"]) or "no fixed version published"
        lines.append(f"{mark} {found['severity']:8} {name} {found['version']}  fixed in: {fixed}")
        lines.append(f"           {', '.join(found['ids'])}")

    if unresolved:
        lines += ["", "For a person to read:"]
    for item in sorted(unresolved, key=lambda u: (-SEVERITIES.index(u["severity"]), u["id"])):
        mark = "!" if SEVERITIES.index(item["severity"]) >= bar else " "
        lines.append(
            f"{mark} {item['severity']:8} {item['id']} in {item['repo']}: {item['reason']}"
        )

    if notes:
        lines += ["", "Ranges that do not parse, read as fixed: the version is past every patch."]
    for item in sorted(notes, key=lambda n: n["id"]):
        lines.append(f"  {item['id']} in {item['repo']}: {item['reason']}")
    return "\n".join(lines)


def _audit(threshold: str, token: str | None, skip_github: bool) -> int:
    if token is None and not skip_github:
        print(
            "could not complete the check: GITHUB_TOKEN is not set, and GitHub allows too few "
            "unauthenticated requests to read every repository. Set it (any token will do, "
            "for example GITHUB_TOKEN=$(gh auth token)), or pass --skip-github to check OSV alone.",
            file=sys.stderr,
        )
        return 2

    packages = installed_packages()
    findings = query_osv(packages)
    github = None
    if not skip_github:
        repos_by_package = installed_repositories()
        repos = {repo for found in repos_by_package.values() for repo in found}
        advisories, missing = query_github(repos, token)
        from_github, unresolved, notes = github_findings(packages, repos_by_package, advisories)
        findings = merge_findings(findings, from_github)
        github = {
            "repositories": len(advisories),
            "unchecked": sorted(
                (
                    name
                    for name, _ in packages
                    if not set(repos_by_package.get(name, ())) - {*missing}
                ),
                key=str.lower,
            ),
            "missing": missing,
            "unresolved": unresolved,
            "notes": notes,
        }
    print(report(findings, len(packages), threshold, github))

    severities = [f["severity"] for f in findings.values()]
    severities += [u["severity"] for u in (github["unresolved"] if github else [])]
    worst = max((SEVERITIES.index(s) for s in severities), default=-1)
    return 1 if worst >= SEVERITIES.index(threshold) else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--fail-on",
        choices=[s.lower() for s in SEVERITIES],
        default="unknown",
        help=(
            "exit non-zero only at or above this severity (default: any advisory, "
            "including one with no severity recorded)"
        ),
    )
    parser.add_argument(
        "--skip-github",
        action="store_true",
        help="check OSV alone, without each repository's own advisories (needs no GITHUB_TOKEN)",
    )
    args = parser.parse_args(argv)
    token = os.environ.get("GITHUB_TOKEN") or None
    try:
        return _audit(args.fail_on.upper(), token, args.skip_github)
    except (OSError, ValueError, KeyError) as error:
        # Distinct from a finding: an unreachable or changed service says
        # nothing about this environment, and reporting it as a vulnerability
        # would spend the caller's attention on the wrong thing.
        print(f"could not complete the check: {error}", file=sys.stderr)
        return 2
    except Exception:
        # A defect here, and for the same reason not a finding.
        traceback.print_exc()
        print("could not complete the check: the error above is a defect in it", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
