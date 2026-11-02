"""``pythonw.exe -I -m arcilogger_core.desktop``: the desktop status window (Wave 5 contract §5,
decisions I1-I5).

* The logon task ``\\ARCILOGGER\\Desktop\\StatusWindow`` starts it for members of
  Administrators with the highest rights, so it reads ``state\\`` without a UAC prompt.
* The Start-menu shortcut "ARCILOGGER Durum" starts it by hand. Without administrator rights it
  starts itself again through UAC; when that is declined it goes on with what it may read
  (services, API, disk).
* One window per session: a second start shows the first one (through an elevated copy when
  started without rights: Windows lets only an elevated process restore an elevated window).

``--print`` writes one look as text (``python.exe``, a console; no Tk needed), ``--self-test``
builds the window off-screen (exit 0 or 1), ``--config`` reads another configuration file
(development), ``--no-elevate`` never asks UAC.
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from arcilogger_core.desktop import status, winapi

#: One window per logon session.
MUTEX: Final = "Local\\ARCILOGGER-Durum"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m arcilogger_core.desktop",
        description="ARCILOGGER Durum: masaüstü durum penceresi [desktop status window]",
    )
    parser.add_argument("--config", type=Path, help="arcilogger.toml (default: the installation's)")
    parser.add_argument("--print", action="store_true", help="print one look as text and exit")
    parser.add_argument("--self-test", action="store_true", help="build the window off-screen")
    parser.add_argument("--no-elevate", action="store_true", help="never ask for elevation (UAC)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.self_test:
        from arcilogger_core.desktop import window

        return window.self_test()
    site = status.load_site(args.config)
    probe = status.Probe(services=winapi.service_states)
    if args.print:
        with contextlib.suppress(AttributeError, ValueError):  # a Windows console's code page
            sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        print(status.render_text(status.collect(site, probe)))
        return 0
    elevate = not args.no_elevate and not winapi.is_elevated()
    again = ["-I", "-m", "arcilogger_core.desktop", "--no-elevate"]
    if args.config is not None:
        again += ["--config", str(args.config)]
    guard = winapi.single_instance(MUTEX)
    if guard is None:
        # The open window runs elevated (the logon task); Windows lets only an elevated process
        # restore it, so a start without rights hands that to an elevated copy.
        if not (elevate and winapi.relaunch_elevated(again)):
            winapi.bring_to_front(status.TITLE)
        return 0
    if elevate:
        winapi.release(guard)
        if winapi.relaunch_elevated(again):
            return 0
        guard = winapi.single_instance(MUTEX)  # declined: go on without the data folder
        if guard is None:
            return 0
    from arcilogger_core.desktop import window

    winapi.dpi_aware()
    return window.run(lambda: status.collect(site, probe))


if __name__ == "__main__":
    raise SystemExit(main())
