"""Release guard: RELEASE_DATE must be the release day of the tagged commit.

``RELEASE_DATE`` in ``core/src/arcilogger_core/__init__.py`` drives the maintenance rule of the
licence (licensing contract 5.1, 9.2): a version released after a licence's ``maintenance_until``
runs with 5651 Temel only. Nothing in the code changes it, so a release that forgets to bump it
carries an old date and every licence whose maintenance ended since then still covers it. The
release job of ``.github/workflows/installer.yml`` runs this script for a ``v*`` tag and stops
the release when ``RELEASE_DATE`` is

* more than :data:`MAX_AGE_DAYS` days before the day of the tagged commit (it was not bumped), or
* after that day (a release cannot be dated in the future).

The day of the commit is its committer date in the site time zone, Europe/Istanbul, the zone the
product counts days in. Türkiye has kept UTC+03:00 all year since 2016-09-07 and every release is
later than that, so a fixed offset is used: Windows Python has no time zone database without the
``tzdata`` package, and this script needs nothing but the standard library and git.

Usage (from the repository root)::

    python tools/release/check_release_date.py [--commit-date ISO-8601] [--tag v1.2.3]

Without ``--commit-date`` the committer date of ``HEAD`` is read with ``git log``. Exit codes:
0 the date is plausible, 1 it is not (a GitHub Actions ``::error::`` line says why), 2 usage or
read error.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).resolve().parents[2]
CORE_INIT: Final = ROOT / "core" / "src" / "arcilogger_core" / "__init__.py"
#: The line Build-ArciBundle reads (tests/test_versions.py pins the same format).
RELEASE_DATE_LINE: Final = re.compile(r'(?m)^RELEASE_DATE: Final = "(?P<d>\d{4}-\d{2}-\d{2})"')
VERSION_LINE: Final = re.compile(r'(?m)^__version__\s*=\s*"(?P<v>[^"]+)"')
#: Europe/Istanbul since 2016-09-07 (no daylight saving time).
SITE_OFFSET: Final = timezone(timedelta(hours=3))
#: How much older than the tagged commit RELEASE_DATE may be: room for a release branch that is
#: tested for a while before it is tagged, small against a maintenance year.
MAX_AGE_DAYS: Final = 14
CHECKLIST: Final = "docs/adr/0007-windows-installer.md, section A1"


class GuardError(Exception):
    """The input cannot be read (exit code 2)."""


def read_release_date(text: str) -> date:
    """``RELEASE_DATE`` of ``core/src/arcilogger_core/__init__.py`` (exactly one line)."""
    found = RELEASE_DATE_LINE.findall(text)
    if len(found) != 1:
        raise GuardError(
            f'expected one line RELEASE_DATE: Final = "YYYY-MM-DD", found {len(found)}'
        )
    try:
        return date.fromisoformat(found[0])
    except ValueError as exc:
        raise GuardError(f"RELEASE_DATE {found[0]!r} is not a date: {exc}") from exc


def read_version(text: str) -> str | None:
    match = VERSION_LINE.search(text)
    return match.group("v") if match else None


def commit_day(committed: str) -> date:
    """The site day (Europe/Istanbul) of an ISO-8601 time with offset (``git log --format=%cI``)."""
    try:
        moment = datetime.fromisoformat(committed.strip())
    except ValueError as exc:
        raise GuardError(f"commit date {committed!r} is not ISO 8601: {exc}") from exc
    if moment.tzinfo is None:
        raise GuardError(f"commit date {committed!r} has no UTC offset")
    return moment.astimezone(SITE_OFFSET).date()


def check(release: date, committed: date, *, max_age_days: int = MAX_AGE_DAYS) -> str | None:
    """Why ``release`` cannot be the release day of a commit made on ``committed``; ``None``
    when it can (from ``max_age_days`` days before the commit up to the commit's day)."""
    if release > committed:
        days = (release - committed).days
        return (
            f"RELEASE_DATE {release.isoformat()} is {days} day(s) after the tagged commit "
            f"({committed.isoformat()}, Europe/Istanbul): a release cannot be dated in the future. "
            "Set RELEASE_DATE to the day of the release commit."
        )
    age = (committed - release).days
    if age > max_age_days:
        return (
            f"RELEASE_DATE {release.isoformat()} is {age} days before the tagged commit "
            f"({committed.isoformat()}, Europe/Istanbul), more than {max_age_days}: it was not "
            "bumped for this release, so licences whose maintenance ended since then would still "
            "cover it. Bump __version__ and RELEASE_DATE together in "
            f"core/src/arcilogger_core/__init__.py and tag that commit ({CHECKLIST})."
        )
    return None


def head_commit_date(repository: Path) -> str:
    """The committer date of ``HEAD`` (ISO 8601 with offset)."""
    git = shutil.which("git")
    if git is None:
        raise GuardError("git is not on PATH; pass --commit-date")
    try:
        result = subprocess.run(  # noqa: S603 - git found on PATH, fixed arguments
            [git, "log", "-1", "--format=%cI", "HEAD"],
            cwd=repository,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except OSError as exc:
        raise GuardError(f"git cannot be run: {exc}") from exc
    if result.returncode != 0 or not result.stdout.strip():
        raise GuardError(f"git log failed ({result.returncode}): {result.stderr.strip()}")
    return result.stdout.strip()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--init", type=Path, default=CORE_INIT, help="arcilogger_core/__init__.py")
    parser.add_argument("--commit-date", help="ISO 8601 with offset (default: HEAD's, from git)")
    parser.add_argument("--tag", default="", help="the tag, for the messages (e.g. v1.2.3)")
    parser.add_argument("--max-age-days", type=int, default=MAX_AGE_DAYS)
    args = parser.parse_args(argv)
    try:
        text = args.init.read_text(encoding="utf-8")
        release = read_release_date(text)
        committed_at = args.commit_date or head_commit_date(ROOT)
        committed = commit_day(committed_at)
    except (GuardError, OSError) as exc:
        print(f"::error::Release date guard: {exc}")
        return 2
    version = read_version(text) or "?"
    where = f"{args.tag} " if args.tag else ""
    problem = check(release, committed, max_age_days=args.max_age_days)
    if problem is not None:
        print(f"::error::Release {where}(__version__ {version}): {problem}")
        return 1
    print(
        f"Release {where}(__version__ {version}): RELEASE_DATE {release.isoformat()}, tagged "
        f"commit {committed_at} = {committed.isoformat()} Europe/Istanbul: ok."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
