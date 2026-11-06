"""Measure the sealed-file formats before choosing their defaults (Wave 5 contract H5).

For each raw spool file given (load-generator data, e.g. 1 M and 5 M lines) and each format
(gzip -9; xz presets 6 and 9 on 1 and 2 threads) it seals the file with the product's own writer
(:func:`arcilogger_core.sealedfile.write`) in a child process and reports:

- the ratio (compressed / raw) and the sealing time;
- the child's peak memory (resident set size);
- the time to show one line from the middle of the day: from the start of the file for gzip
  without checkpoints (the first view), from its own stream for xz;
- the sealing time of a 5 GB day extrapolated from the measured speed, against half of the Seal
  task's limit (PT2H, so 3 600 s).

With ``--views`` it measures instead what one cited line costs the raw-line view
(``arcilogger_api.rawlines.read_line``, the code ``/raw`` runs) on a day sealed as gzip -9 and as
xz 6: the first ("cold") view of the middle and of the last line of the file, and a later view of
another line once the file is known ("warm"). For xz the cold view counts the line feeds of the
streams before the line (so the line number is checked); a design that counted them on every
view instead of keeping the counts would pay the cold cost on each view.

Usage: ``uv run python tools/bench/seal_compression.py [--views] SPOOL.log [SPOOL.log ...]``.
The sealed files are written next to the inputs and removed. Numbers depend on the machine; the
pull request and docs/DATA-MODEL.md record the run they quote.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

from arcilogger_api import rawlines
from arcilogger_core import sealedfile
from arcilogger_core.layout import Layout

FORMATS = (
    ("gzip -9", {"gzip_level": 9}, ".gz"),
    ("xz 6, 1 thread", {"xz_preset": 6, "xz_threads": 1}, ".xz"),
    ("xz 6, 2 threads", {"xz_preset": 6, "xz_threads": 2}, ".xz"),
    ("xz 9, 1 thread", {"xz_preset": 9, "xz_threads": 1}, ".xz"),
    ("xz 9, 2 threads", {"xz_preset": 9, "xz_threads": 2}, ".xz"),
)
FIVE_GB = 5 * 1000**3
HALF_LIMIT_SECONDS = 3600

CHILD = """
import json, resource, sys, time
from pathlib import Path
from arcilogger_core import sealedfile
source, target, options = Path(sys.argv[1]), Path(sys.argv[2]), json.loads(sys.argv[3])
started = time.perf_counter()
sealedfile.write(source, target, **options)
seal = time.perf_counter() - started
middle = source.stat().st_size // 2
started = time.perf_counter()
reader = sealedfile.chunks_from(target, middle)
line = b""
for piece in reader:
    line += piece
    if b"\\n" in line:
        break
reader.close()
view = time.perf_counter() - started
peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
print(json.dumps({"seal": seal, "view": view, "peak": peak, "bytes": target.stat().st_size}))
"""


def measure(source: Path) -> list[dict[str, object]]:
    raw = source.stat().st_size
    with source.open("rb") as handle:
        lines = sum(chunk.count(b"\n") for chunk in iter(lambda: handle.read(1 << 20), b""))
    rows: list[dict[str, object]] = []
    for name, options, suffix in FORMATS:
        target = source.with_name(source.stem + ".bench.log" + suffix)
        started = time.perf_counter()
        result = subprocess.run(  # noqa: S603 - this Python, this script's own child code
            [sys.executable, "-c", CHILD, str(source), str(target), json.dumps(options)],
            check=True,
            capture_output=True,
            text=True,
        )
        wall = time.perf_counter() - started
        target.unlink()
        data = json.loads(result.stdout)
        seconds = float(data["seal"])
        rows.append(
            {
                "input": source.name,
                "lines": lines,
                "raw_bytes": raw,
                "format": name,
                "compressed_bytes": data["bytes"],
                "ratio": round(int(data["bytes"]) / raw, 4),
                "seal_seconds": round(seconds, 1),
                "mb_per_second": round(raw / seconds / 1e6, 1),
                "peak_rss_mb": round(int(data["peak"]) / 1e6, 1),
                "middle_line_seconds": round(float(data["view"]), 3),
                "five_gb_seconds": round(FIVE_GB / (raw / seconds)),
                "within_half_limit": FIVE_GB / (raw / seconds) <= HALF_LIMIT_SECONDS,
                "wall_seconds": round(wall, 1),
            }
        )
        print(json.dumps(rows[-1]), flush=True)
    return rows


def _line_starts(source: Path, numbers: list[int]) -> dict[int, int]:
    """The byte offsets of the given (1-based) line numbers of a plain file."""
    found: dict[int, int] = {}
    wanted = sorted(numbers)
    line, position = 1, 0
    with source.open("rb") as handle:
        for raw in handle:
            if wanted and line == wanted[0]:
                found[wanted.pop(0)] = position
            line += 1
            position += len(raw)
    return found


def measure_views(source: Path) -> list[dict[str, object]]:
    with source.open("rb") as handle:
        lines = sum(chunk.count(b"\n") for chunk in iter(lambda: handle.read(1 << 20), b""))
    middle, late, near = lines // 2, lines - 1, lines // 2 + 1000
    starts = _line_starts(source, [middle, late, near, near + 7])
    day = date(2026, 10, 12)
    root = source.with_name(source.stem + ".views")
    layout = Layout(root)
    rows: list[dict[str, object]] = []
    for name, compression, options in (
        ("gzip -9", "gzip", {"gzip_level": 9}),
        ("xz 6, 2 threads", "xz", {"xz_preset": 6, "xz_threads": 2}),
    ):
        target = layout.vault_day(day) / sealedfile.log_name("bench", compression)  # type: ignore[arg-type]
        target.parent.mkdir(parents=True, exist_ok=True)
        sealedfile.write(source, target, **options)

        def view(number: int) -> float:
            started = time.perf_counter()
            rawlines.read_line(layout, "bench", day, number, starts[number])
            return time.perf_counter() - started

        timings: dict[str, float] = {}
        rawlines.VAULT_INDEX.clear()
        timings["cold_middle"] = view(middle)
        timings["warm_near_middle"] = view(near)
        rawlines.VAULT_INDEX.clear()
        timings["cold_last"] = view(late)
        timings["warm_after_last"] = view(near + 7)
        rows.append(
            {
                "input": source.name,
                "lines": lines,
                "format": name,
                **{key: round(value, 3) for key, value in timings.items()},
            }
        )
        print(json.dumps(rows[-1]), flush=True)
        target.unlink()
    shutil.rmtree(root)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("spool", nargs="+", type=Path, help="raw spool files to seal")
    parser.add_argument("--views", action="store_true", help="measure the raw-line view instead")
    args = parser.parse_args(argv)
    for source in args.spool:
        if args.views:
            measure_views(source)
        else:
            measure(source)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
