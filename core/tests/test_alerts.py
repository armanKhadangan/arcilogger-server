"""``raise_alert``: one complete JSON line per alert, also after a torn tail left by a power cut."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from arcilogger_core.alerts import raise_alert, read_alerts


def test_alerts_are_appended_one_per_line(tmp_path: Path) -> None:
    path = tmp_path / "state" / "alerts.jsonl"
    raise_alert(path, "R20-spool", "critical", "Ham log yazılamıyor", {"source": "fortigate"})
    raise_alert(path, "R20-clock", "high", "Saat sapması 1500 ms")
    data = path.read_bytes()
    assert data.count(b"\n") == 2
    assert data.endswith(b"\n")
    assert not data.startswith(b"\n")  # an empty file gets no leading line break
    first, second = read_alerts(path)
    assert (first["rule"], first["details"], first["message"]) == (
        "R20-spool",
        {"source": "fortigate"},
        "Ham log yazılamıyor",
    )
    assert (second["rule"], second["details"]) == ("R20-clock", {})


@pytest.mark.parametrize(
    "tail",
    [
        b"\x00" * 40,  # the size grew but the data never reached the disk
        b'{"details": {"day": "2026-10-1',  # half a line
    ],
)
def test_an_alert_after_a_torn_tail_starts_on_a_fresh_line(tmp_path: Path, tail: bytes) -> None:
    path = tmp_path / "alerts.jsonl"
    raise_alert(path, "R20-seal", "high", "before", {"day": "2026-10-18"})
    with path.open("ab") as handle:
        handle.write(tail)
    raise_alert(path, "R20-spool-torn", "medium", "after", {"source": "fortigate"})
    lines = path.read_bytes().split(b"\n")
    assert lines[1] == tail  # the fragment is a line of its own, unchanged
    assert json.loads(lines[2])["message"] == "after"
    assert lines[3] == b""
