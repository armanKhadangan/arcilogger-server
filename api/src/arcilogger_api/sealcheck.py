"""Verify one sealed day for the API, in a child process with a time limit.

"Doğrula" on the sealing page (``POST /api/v1/seals/{day}/verify``) runs the same day check
as the scheduled ``arcilogger-core verify`` (:func:`arcilogger_core.chain.verify_range`:
canonical manifest, time stamp against the pinned roots, every file's SHA-256, the link to the
previous day; ``deep`` also decompresses and re-hashes the raw data). A deep check of a busy day
reads gigabytes, so it runs in a separate Python process that is **killed** when the time limit
is reached — a runaway check can never keep a core of the server busy after the request ended.

The child only reads: it never writes ``state/verify`` (the API service account may not,
ADR-0007); the API stores the result in ``seal_verifications``. Only this module's
``main`` runs in the child, and it imports nothing but ``arcilogger_core``.

Root certificates are loaded **read-only**: the pinned roots of ``tsa.roots_dir`` and — only
while ``tsa.provider`` is ``mock`` — the mock TSA's test root (``state/mock-tsa/root.pem``),
exactly the set :func:`arcilogger_core.tsa.trusted_roots` trusts, without creating the mock
TSA's files. A leftover ``state/mock-tsa`` (its private keys lie next to the root) is never
trusted once the site uses a real provider.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Final

from cryptography import x509

from arcilogger_core.chain import verify_range
from arcilogger_core.layout import Layout
from arcilogger_core.tsa.verify import load_certificates, load_roots_dir

#: Longest on-demand verification (seconds) before the child process is killed.
VERIFY_TIMEOUT_SECONDS: Final = 300.0
MOCK_ROOT: Final = ("mock-tsa", "root.pem")


def read_roots(layout: Layout, roots_dir: Path | None, *, provider: str) -> list[x509.Certificate]:
    """The trusted roots, read-only (empty list when none are configured). ``provider`` is
    ``tsa.provider``: the mock TSA's test root counts only for ``mock``."""
    roots: list[x509.Certificate] = []
    if roots_dir is not None and roots_dir.is_dir():
        roots += load_roots_dir(roots_dir)
    mock_root = layout.state.joinpath(*MOCK_ROOT)
    if provider == "mock" and mock_root.is_file():
        roots += load_certificates([mock_root])
    return roots


def check_day(
    layout: Layout, day: date, roots: Sequence[x509.Certificate], *, deep: bool
) -> dict[str, Any]:
    """The core day check as plain JSON (``DayCheck.as_json()`` plus ``deep``)."""
    (check,) = (c for c in verify_range(layout, [day], roots, deep=deep) if c.day == day)
    return {**check.as_json(), "deep": deep}


@dataclass(frozen=True)
class CheckResult:
    #: ``ok`` / ``failed`` (the day does not verify) / ``timeout`` / ``error`` (no result).
    outcome: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0


def command(
    layout: Layout, roots_dir: Path | None, day: date, *, deep: bool, provider: str
) -> list[str]:
    args = [sys.executable, "-I", "-m", __name__, "--data-dir", str(layout.root)]
    args += ["--day", day.isoformat(), "--provider", provider]
    if roots_dir is not None:
        args += ["--roots-dir", str(roots_dir)]
    if deep:
        args.append("--deep")
    return args


def run_check(
    layout: Layout,
    roots_dir: Path | None,
    day: date,
    *,
    deep: bool,
    provider: str,
    timeout: float = VERIFY_TIMEOUT_SECONDS,
) -> CheckResult:
    """Run :func:`main` in a child process; kill it after ``timeout`` seconds."""
    started = time.monotonic()

    def elapsed() -> int:
        return int((time.monotonic() - started) * 1000)

    try:
        completed = subprocess.run(  # noqa: S603 - fixed interpreter and module, validated args
            command(layout, roots_dir, day, deep=deep, provider=provider),
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            "timeout",
            errors=[f"time limit of {int(timeout)} s exceeded; the check was stopped"],
            duration_ms=elapsed(),
        )
    except OSError as exc:  # e.g. the service account may not start python.exe
        reason = exc.strerror or type(exc).__name__
        return CheckResult(
            "error",
            errors=[f"the verification process could not start: {reason}"],
            duration_ms=elapsed(),
        )
    try:
        data = json.loads(completed.stdout)
        if not isinstance(data, dict) or completed.returncode not in (0, 1):
            raise ValueError("unexpected output")
    except ValueError:
        tail = completed.stderr.decode("utf-8", "replace").strip().splitlines()[-1:]
        return CheckResult(
            "error",
            errors=[f"the verification process failed (exit {completed.returncode}): {tail}"],
            duration_ms=elapsed(),
        )
    return CheckResult(
        "ok" if data.get("ok") else "failed",
        errors=[str(e) for e in data.get("errors") or []],
        warnings=[str(w) for w in data.get("warnings") or []],
        details={key: data.get(key) for key in ("serial", "gen_time", "root")},
        duration_ms=elapsed(),
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Child process: print the day check as JSON; exit 0 verified, 1 not, 2 unusable."""
    parser = argparse.ArgumentParser(prog="arcilogger_api.sealcheck")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--day", type=date.fromisoformat, required=True)
    parser.add_argument("--roots-dir", type=Path)
    parser.add_argument("--provider", default="zamane")  # anything but "mock": no test root
    parser.add_argument("--deep", action="store_true")
    args = parser.parse_args(argv)
    layout = Layout(args.data_dir)
    roots = read_roots(layout, args.roots_dir, provider=args.provider)
    if not roots:
        print(json.dumps({"ok": False, "errors": ["no trusted root certificates"]}))
        return 1
    result = check_day(layout, args.day, roots, deep=args.deep)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
