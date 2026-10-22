"""What the sealer has finished, read from its state and manifest files."""

from __future__ import annotations

import json
from datetime import date, timedelta

from arcilogger_ingest.sealed import RECHECK_SECONDS, SealIndex
from arcilogger_ingest.testing import DAY, World, dhcp_message, fortigate_message


def test_sealed_day_files_match_the_spool_they_replaced(world: World) -> None:
    world.append("fortigate", DAY, (fortigate_message(i) for i in range(5)))
    world.append("core-r1", DAY, (dhcp_message(i) for i in range(3)))
    sizes = {s: world.layout.spool_file(s, DAY).stat().st_size for s in ("fortigate", "core-r1")}
    index = SealIndex(world.layout)
    assert index.files(DAY) is None
    assert not index.is_sealed(DAY)
    world.seal(DAY)
    index = SealIndex(world.layout)
    files = index.files(DAY)
    assert files is not None
    assert set(files) == {"fortigate", "core-r1"}
    for source, entry in files.items():
        assert entry.bytes_uncompressed == sizes[source]
        assert entry.path.is_file()
        assert entry.path.name == f"{source}.log.gz"
        assert entry.day == DAY
    assert (files["fortigate"].lines, files["core-r1"].lines) == (5, 3)
    assert index.entry(DAY, "fortigate") == files["fortigate"]
    assert index.entry(DAY, "switch-99") is None
    assert index.sealed_days(DAY - timedelta(days=5)) == [DAY]
    assert index.sealed_days(DAY + timedelta(days=1)) == []


def test_an_unsealed_day_is_looked_up_again_only_after_a_pause(world: World) -> None:
    now = [100.0]
    index = SealIndex(world.layout, monotonic=lambda: now[0])
    state = world.layout.seal_state(DAY)
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"day": DAY.isoformat(), "status": "pending"}), "utf-8")
    assert index.files(DAY) is None
    # sealed meanwhile, but the cached "not sealed" answer holds for RECHECK_SECONDS
    world.append("fortigate", DAY, [fortigate_message(0)])
    world.seal(DAY)
    assert index.files(DAY) is None
    now[0] += RECHECK_SECONDS
    assert index.files(DAY) is not None


def test_missing_or_damaged_state_files_mean_not_sealed(world: World) -> None:
    index = SealIndex(world.layout)
    assert index.sealed_days(date(2020, 1, 1)) == []  # no state folder at all
    folder = world.layout.state / "seal"
    folder.mkdir(parents=True)
    (folder / "2026-10-10.json").write_text("{not json", "utf-8")
    (folder / "2026-10-11.json").write_text('{"status": "sealed"}', "utf-8")  # no manifest
    (folder / "notes.json").write_text("{}", "utf-8")
    (folder / "2026-10-09.json").write_text("[1, 2]", "utf-8")
    assert index.sealed_days(date(2020, 1, 1)) == []
