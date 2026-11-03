"""Sealed files in both formats (Wave 5 W5-H): seekable xz, one reader, old ``.gz`` days kept."""

from __future__ import annotations

import gzip
import hashlib
import json
import lzma
import os
import random
import shutil
import subprocess
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from arcilogger_core import sealedfile
from arcilogger_core.chain import verify_range
from arcilogger_core.config import SealConfig
from arcilogger_core.seal import compress_deterministic
from arcilogger_core.sealedfile import SealedFileError


@pytest.fixture
def small_streams(monkeypatch: pytest.MonkeyPatch) -> int:
    """1 000-byte streams, so a small file has many of them."""
    monkeypatch.setattr(sealedfile, "STREAM_BYTES", 1000)
    return 1000


def _content(lines: int = 600, torn: bool = True) -> bytes:
    rnd = random.Random(5651)  # noqa: S311 - test data, not security
    body = b"".join(f"<14> line {n} {rnd.random()}\n".encode() for n in range(lines))
    return body + (b"torn last line" if torn else b"")


def _seal(tmp_path: Path, data: bytes, name: str, **options: Any) -> Path:
    source = tmp_path / f"{name}.spool"
    source.write_bytes(data)
    target = tmp_path / name
    sealedfile.write(source, target, **options)
    return target


def test_xz_round_trip_and_the_line_rule(tmp_path: Path, small_streams: int) -> None:
    data = _content()
    target = _seal(tmp_path, data, "fw.log.xz")
    assert sealedfile.digest(target) == (hashlib.sha256(data).hexdigest(), data.count(b"\n") + 1)
    assert lzma.decompress(target.read_bytes()) == data  # any xz tool reads the concatenation
    with sealedfile.open_sequential(target) as handle:
        assert list(handle) == data.splitlines(keepends=True)


def test_xz_bytes_do_not_depend_on_the_thread_count(tmp_path: Path, small_streams: int) -> None:
    data = _content()
    outputs = {
        threads: _seal(tmp_path, data, f"t{threads}.log.xz", xz_threads=threads).read_bytes()
        for threads in (1, 2, 4)
    }
    assert outputs[1] == outputs[2] == outputs[4]
    assert _seal(tmp_path, data, "again.log.xz", xz_threads=1).read_bytes() == outputs[1]


def test_xz_stream_boundaries(tmp_path: Path, small_streams: int) -> None:
    data = _content()
    target = _seal(tmp_path, data, "fw.log.xz")
    streams = sealedfile.xz_streams(target)
    assert len(streams) == -(-len(data) // small_streams)
    assert [s.uncompressed_size for s in streams[:-1]] == [small_streams] * (len(streams) - 1)
    assert sum(s.uncompressed_size for s in streams) == len(data)
    raw = target.read_bytes()
    position = 0
    for number, stream in enumerate(streams):
        assert stream.compressed_start == position
        assert stream.uncompressed_start == number * small_streams
        piece = raw[stream.compressed_start : stream.compressed_start + stream.compressed_size]
        assert piece.startswith(b"\xfd7zXZ\x00")
        assert piece.endswith(b"YZ")
        start = stream.uncompressed_start
        assert lzma.decompress(piece) == data[start : start + stream.uncompressed_size]
        position += stream.compressed_size
    assert position == len(raw)  # no stream padding, no sidecar


def test_reading_from_an_offset_decompresses_from_its_stream_only(
    tmp_path: Path, small_streams: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = _content()
    target = _seal(tmp_path, data, "fw.log.xz")
    streams = sealedfile.xz_streams(target)
    opened: list[int] = []
    real = sealedfile.xz_stream_chunks

    def counting(raw: Any, stream: sealedfile.XzStream) -> Any:
        opened.append(stream.uncompressed_start)
        return real(raw, stream)

    monkeypatch.setattr(sealedfile, "xz_stream_chunks", counting)
    offset = 4321
    reader = sealedfile.chunks_from(target, offset)
    first = next(reader)
    reader.close()
    assert data[offset:].startswith(first)
    assert opened == [streams[sealedfile.stream_of(streams, offset)].uncompressed_start]
    for offset in (0, 999, 1000, len(data) - 1, len(data)):
        assert b"".join(sealedfile.chunks_from(target, offset)) == data[offset:]


@pytest.mark.parametrize("name", ["fw.log.gz", "fw.log.xz"])
def test_both_formats_read_alike(tmp_path: Path, small_streams: int, name: str) -> None:
    data = _content()
    target = _seal(tmp_path, data, name)
    assert sealedfile.format_of(target) == ("gzip" if name.endswith(".gz") else "xz")
    assert sealedfile.digest(target)[0] == hashlib.sha256(data).hexdigest()
    assert b"".join(sealedfile.chunks_from(target, 77)) == data[77:]
    with target.open("rb") as raw, sealedfile.open_sequential(target, raw, offset=5) as handle:
        assert handle.read() == data[5:]
        assert not raw.closed  # a caller's handle stays open


def test_gzip_stays_gzip_n(tmp_path: Path) -> None:
    target = _seal(tmp_path, b"a\nb\n", "fw.log.gz", gzip_level=6)
    raw = target.read_bytes()
    assert raw[:2] == b"\x1f\x8b"
    assert raw[3] & 0x08 == 0  # no file name
    assert raw[4:8] == b"\0\0\0\0"  # mtime 0
    assert gzip.decompress(raw) == b"a\nb\n"


def test_empty_input_gives_a_valid_file(tmp_path: Path) -> None:
    for name in ("e.log.gz", "e.log.xz"):
        target = _seal(tmp_path, b"", name)
        empty = hashlib.sha256(b"").hexdigest()
        assert sealedfile.digest(target) == (empty, 0)
    assert len(sealedfile.xz_streams(tmp_path / "e.log.xz")) == 1


def test_every_damaged_byte_of_an_xz_file_is_found(tmp_path: Path, small_streams: int) -> None:
    data = _content(200)
    good = _seal(tmp_path, data, "fw.log.xz").read_bytes()
    victim = tmp_path / "bad.log.xz"
    for position in range(0, len(good), 37):
        damaged = bytearray(good)
        damaged[position] ^= 0x55
        victim.write_bytes(damaged)
        with pytest.raises(SealedFileError) as raised:
            sealedfile.digest(victim)
        assert raised.value.code == "damaged"
    victim.write_bytes(good[:-5])
    with pytest.raises(SealedFileError):
        sealedfile.xz_streams(victim)
    victim.write_bytes(good + b"\0\0\0\0")  # stream padding is not part of the format
    with pytest.raises(SealedFileError):
        sealedfile.digest(victim)


def test_an_empty_xz_file_is_damage(tmp_path: Path) -> None:
    """The writer never leaves a zero-byte file, so one is damage, not an empty day."""
    empty = tmp_path / "fw.log.xz"
    empty.write_bytes(b"")
    with pytest.raises(SealedFileError) as raised:
        sealedfile.digest(empty)
    assert raised.value.code == "damaged"
    with pytest.raises(SealedFileError):
        sealedfile.xz_streams(empty)


def test_a_damaged_gzip_file_is_reported_as_damage(tmp_path: Path) -> None:
    victim = tmp_path / "bad.log.gz"
    victim.write_bytes(b"\x1f\x8b\x08\x00garbage-not-deflate" * 10)
    with pytest.raises(SealedFileError) as raised:
        sealedfile.digest(victim)
    assert raised.value.code == "damaged"


def test_names_and_formats(tmp_path: Path) -> None:
    assert sealedfile.log_name("fw", "xz") == "fw.log" + sealedfile.SUFFIXES["xz"]
    assert sealedfile.audit_name("gzip") == "audit.jsonl" + sealedfile.SUFFIXES["gzip"]
    xz = tmp_path / sealedfile.log_name("fw", "xz")
    assert sealedfile.other_formats(xz) == [tmp_path / sealedfile.log_name("fw", "gzip")]
    with pytest.raises(SealedFileError):
        sealedfile.format_of(tmp_path / "fw.log")


def test_a_source_sealed_in_both_formats_is_ambiguous(world: Any) -> None:
    day = date(2026, 10, 10)
    folder = world.layout.vault_day(day)
    folder.mkdir(parents=True)
    assert sealedfile.find_log(world.layout, day, "fw") is None
    (folder / sealedfile.log_name("fw", "gzip")).write_bytes(b"")
    assert sealedfile.find_log(world.layout, day, "fw") == folder / sealedfile.log_name(
        "fw", "gzip"
    )
    (folder / sealedfile.log_name("fw", "xz")).write_bytes(b"")
    with pytest.raises(SealedFileError) as raised:
        sealedfile.find_log(world.layout, day, "fw")
    assert raised.value.code == "sealed_file_ambiguous"
    (folder / sealedfile.audit_name("xz")).write_bytes(b"")
    assert sealedfile.find_audit(world.layout, day) == folder / sealedfile.audit_name("xz")


def test_a_retry_in_the_other_format_removes_the_first_attempt(tmp_path: Path) -> None:
    source = tmp_path / "spool.log"
    source.write_bytes(b"x\n")
    gz = tmp_path / sealedfile.log_name("fw", "gzip")
    compress_deterministic(source, gz, SealConfig(compression="gzip"))
    (tmp_path / (sealedfile.log_name("fw", "gzip") + ".part")).write_bytes(b"left")
    xz = tmp_path / sealedfile.log_name("fw", "xz")
    compress_deterministic(source, xz, SealConfig())
    assert sorted(p.name for p in tmp_path.iterdir()) == ["fw.log" + ".xz", "spool.log"]


def test_sealing_with_xz_keeps_old_gzip_days_and_verifies(world: Any, days: list[date]) -> None:
    """Days sealed as gzip stay byte- and time-identical; new days are xz; the chain verifies."""
    world.reconfigure(seal={"quiet_seconds": 0, "compression": "gzip", "gzip_level": 6})
    for day in days[:2]:
        world.busy_day(day)
    world.set_now(days[1] + timedelta(days=1))
    assert [o.status for o in world.sealer().seal_due()] == ["sealed", "sealed"]
    before = {
        path: (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
        for day in days[:2]
        for path in world.layout.vault_day(day).iterdir()
    }
    world.reconfigure(seal={"quiet_seconds": 0, "compression": "xz", "xz_threads": 2})
    world.busy_day(days[2])
    world.set_now(days[2] + timedelta(days=1))
    assert [o.status for o in world.sealer().seal_due()] == ["sealed"]
    after = {
        path: (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
        for path in before
    }
    assert after == before
    manifest = json.loads(world.layout.manifest(days[2]).read_bytes())
    for entry in manifest["files"]:
        assert sealedfile.format_of(entry["path"]) == "xz"
        spool_digest = entry["sha256_uncompressed"]
        assert sealedfile.digest(world.layout.vault / entry["path"])[0] == spool_digest
    assert sealedfile.format_of(manifest["audit_log"]["path"]) == "xz"
    checks = verify_range(world.layout, days, [world.tsa.root_certificate], deep=True)
    assert [c.errors for c in checks] == [[], [], []]


def test_a_deeply_nested_day_state_is_read_as_damaged(world: Any) -> None:
    """``state/seal/<day>.json`` that makes the JSON parser recurse is skipped like any damage."""
    folder = world.layout.state / "seal"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "2026-10-10.json").write_text("[" * 100_000 + "]" * 100_000, encoding="utf-8")
    (folder / "2026-10-11.json").write_text(json.dumps({"status": "sealed"}), encoding="utf-8")
    assert world.sealer()._day_states() == {date(2026, 10, 11): {"status": "sealed"}}


def _seven_zip() -> str | None:
    """7-Zip (H6): the Windows runner's ``7z.exe`` when installed, else ``7z`` on the path."""
    for variable in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(variable)
        if base and (Path(base) / "7-Zip" / "7z.exe").is_file():
            return str(Path(base) / "7-Zip" / "7z.exe")
    return shutil.which("7z") or shutil.which("7za")


def test_seven_zip_decompresses_a_multi_stream_file_to_the_same_bytes(
    tmp_path: Path, small_streams: int
) -> None:
    """Sahada doğrula X1 (H6): an auditor's 7-Zip reads every stream, not only the first."""
    tool = _seven_zip()
    if tool is None:
        pytest.skip("7-Zip not installed (the Windows CI runner runs this when it has 7z.exe)")
    data = _content()
    target = _seal(tmp_path, data, "fw.log.xz")
    assert len(sealedfile.xz_streams(target)) > 1
    out = subprocess.run([tool, "e", "-so", str(target)], capture_output=True, check=True).stdout
    assert hashlib.sha256(out).hexdigest() == hashlib.sha256(data).hexdigest()
