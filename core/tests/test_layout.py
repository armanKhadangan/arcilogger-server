"""Data directory layout."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from arcilogger_core.layout import Layout


def test_paths(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    day = date(2026, 10, 12)
    assert (
        layout.spool_file("fortigate", day) == tmp_path / "spool" / "fortigate" / "2026-10-12.log"
    )
    assert layout.manifest(day) == tmp_path / "vault/2026/10/12/manifest-2026-10-12.json"
    assert layout.token(day).name == "manifest-2026-10-12.json.zd"
    assert layout.seal_state(day) == tmp_path / "state" / "seal" / "2026-10-12.json"


def test_spool_discovery_ignores_audit_and_junk(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    for source, name in [
        ("fortigate", "2026-10-11.log"),
        ("core-r1", "2026-10-12.log"),
        ("fortigate", "2026-10-12.log"),
        ("fortigate", "notes.log"),
        ("_audit", "2026-10-09.log"),
    ]:
        path = layout.spool / source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
    assert layout.spool_days() == [date(2026, 10, 11), date(2026, 10, 12)]
    assert [s for s, _ in layout.spool_files_for(date(2026, 10, 12))] == ["core-r1", "fortigate"]
    assert Layout(tmp_path / "missing").spool_days() == []
    assert Layout(tmp_path / "missing").spool_files_for(date(2026, 10, 12)) == []
