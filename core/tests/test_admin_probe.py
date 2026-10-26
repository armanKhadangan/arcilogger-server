"""Connection tests with candidate values, run in a child process (ADR-0014; contract 2.6, 2.1).

The child gets the request, secrets included, on its standard input and never on its command
line; a hanging child is killed at its time limit; its output is read capped and every secret
is replaced by ``***`` before a message is kept.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from arcilogger_core.admin import probe
from arcilogger_core.admin.probe import (
    MAX_MESSAGE,
    ChildProber,
    ProbeResult,
    probe_credit,
    probe_sntp,
    redact,
    run_probe,
)
from arcilogger_core.clockcheck import NTP_EPOCH_OFFSET
from arcilogger_core.config import TsaConfig

FAKE_JAVA = Path(__file__).parent / "fake_zamane" / "fake_java.py"
SECRET = "gizli-parola-örnek"

posix_only = pytest.mark.skipif(os.name == "nt", reason="a #! script stands in for Python")


@pytest.fixture
def ntp_server() -> Iterator[int]:
    """An SNTP server on 127.0.0.1 whose clock runs one second ahead; yields its port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(0.2)
    stop = threading.Event()

    def ntp(seconds: float) -> bytes:
        return struct.pack(
            "!II", int(seconds) + NTP_EPOCH_OFFSET, int((seconds % 1) * 2**32) & 0xFFFFFFFF
        )

    def serve() -> None:
        while not stop.is_set():
            try:
                data, peer = sock.recvfrom(512)
            except OSError:
                continue
            now = time.time() + 1.0
            sock.sendto(bytes([0x24, 2, 6, 0xEC]) + b"\x00" * 20 + data[40:48] + ntp(now) * 2, peer)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield sock.getsockname()[1]
    stop.set()
    thread.join()
    sock.close()


def fake_python(tmp_path: Path, body: str) -> str:
    """An executable that ChildProber starts instead of Python (it gets the same arguments)."""
    script = tmp_path / "fake-python"
    script.write_text(f"#!{sys.executable}\nimport json, sys, time\n{body}\n", encoding="utf-8")
    script.chmod(0o755)
    return str(script)


# -- the probes ------------------------------------------------------------------------------------


def test_the_sntp_probe_runs_in_a_child(ntp_server: int) -> None:
    result = ChildProber().run("sntp", {"server": "127.0.0.1", "port": ntp_server}, {})
    assert result.ok, result.message
    assert result.code is None
    assert result.message.startswith("127.0.0.1: offset ")


def test_a_silent_time_server_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(probe.TIMEOUTS, "sntp", 0.2)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as silent:
        silent.bind(("127.0.0.1", 0))
        result = probe_sntp({"server": "127.0.0.1", "port": silent.getsockname()[1]})
    assert (result.ok, result.code) == (False, "ntp_unreachable")


def credit_params(tmp_path: Path) -> dict[str, object]:
    jar = tmp_path / "tss-client-console-3.1.33.jar"
    jar.write_bytes(b"PK fake")
    return {
        "java": [sys.executable, str(FAKE_JAVA)],
        "jar": str(jar),
        "url": "http://tzd.kamusm.gov.tr",
        "port": 80,
        "timeout_seconds": 20,
        "verify_flag": "-CC",
        "credit_regex": TsaConfig().credit_regex,
    }


def test_the_credit_query_with_the_candidate_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_ZAMANE_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("FAKE_ZAMANE_MODE", "ok")
    secrets = {"kamusm.customer": "4812", "kamusm.password": SECRET}
    params = credit_params(tmp_path)
    assert probe_credit(params, secrets) == ProbeResult(True, None, "credits: 9634")
    child = ChildProber().run("credit", params, secrets)  # type: ignore[arg-type]
    assert child == ProbeResult(True, None, "credits: 9634")
    monkeypatch.setenv("FAKE_ZAMANE_MODE", "credit_garbled")
    failed = ChildProber().run("credit", params, secrets)  # type: ignore[arg-type]
    assert (failed.ok, failed.code) == (False, "credit_query_failed")
    assert SECRET not in failed.message


def test_an_unknown_probe_kind_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown probe"):
        run_probe("telnet", {}, {})


# -- the child process -----------------------------------------------------------------------------


@posix_only
def test_secrets_go_on_standard_input_and_never_on_the_command_line(tmp_path: Path) -> None:
    record = tmp_path / "record.json"
    python = fake_python(
        tmp_path,
        f"data = sys.stdin.read()\n"
        f"open({str(record)!r}, 'w').write(json.dumps({{'argv': sys.argv, 'stdin': data}}))\n"
        f"print(json.dumps({{'ok': True, 'code': None, 'message': 'ok'}}))",
    )
    result = ChildProber(python).run(
        "replica", {"target": "//NAS-01/worm"}, {"replica.password": SECRET}
    )
    assert result == ProbeResult(True, None, "ok")
    seen = json.loads(record.read_text())
    assert seen["argv"][1:] == ["-I", "-m", "arcilogger_core.admin.probe"]
    assert SECRET not in " ".join(seen["argv"])
    request = json.loads(seen["stdin"])
    assert request == {
        "kind": "replica",
        "params": {"target": "//NAS-01/worm"},
        "secrets": {"replica.password": SECRET},
    }


@posix_only
def test_a_secret_the_child_echoes_is_replaced(tmp_path: Path) -> None:
    message = f"refused: user arcireplica password {SECRET}"
    python = fake_python(
        tmp_path,
        "sys.stdin.read()\n"
        f"print(json.dumps({{'ok': False, 'code': 'x', 'message': {message!r}}}))",
    )
    result = ChildProber(python).run("replica", {}, {"replica.password": SECRET})
    assert (result.ok, result.code) == (False, "replica_test_failed")  # the parent's code
    assert result.message == "refused: user arcireplica password ***"


@posix_only
def test_a_hanging_child_is_killed_at_its_limit(tmp_path: Path) -> None:
    python = fake_python(tmp_path, "time.sleep(30)")
    started = time.monotonic()
    result = ChildProber(python, timeouts={"smtp": 0.2}, margin=0.3).run("smtp", {}, {})
    assert time.monotonic() - started < 10
    assert (result.ok, result.code) == (False, "smtp_test_failed")
    assert result.message.startswith("no answer within")


@posix_only
def test_the_childs_output_is_read_capped(tmp_path: Path) -> None:
    python = fake_python(
        tmp_path,
        "sys.stdin.read()\nsys.stdout.write('x' * (1024 * 1024))\n"
        "print(json.dumps({'ok': True, 'code': None, 'message': 'late'}))",
    )
    result = ChildProber(python).run("sntp", {}, {})
    assert (result.ok, result.code) == (False, "ntp_unreachable")
    assert len(result.message) <= MAX_MESSAGE


@posix_only
def test_a_crashing_child_reports_its_exit_code_without_secrets(tmp_path: Path) -> None:
    python = fake_python(
        tmp_path,
        f"sys.stdin.read()\nsys.stderr.write('Traceback: ' + {SECRET!r} + chr(10) * 3)\n"
        "sys.exit(3)",
    )
    result = ChildProber(python).run("credit", {}, {"kamusm.password": SECRET})
    assert (result.ok, result.code) == (False, "credit_query_failed")
    assert result.message.startswith("exit code 3")
    assert SECRET not in result.message
    assert "***" in result.message


def test_the_child_answers_bad_input_with_a_failure_line() -> None:
    for data in (b"not json", b'{"kind": "telnet", "params": {}}', b"[]"):
        completed = subprocess.run(
            [sys.executable, "-I", "-m", "arcilogger_core.admin.probe"],
            input=data, capture_output=True, check=False, timeout=60,
        )  # fmt: skip
        assert completed.returncode == 1
        answer = json.loads(completed.stdout.decode().strip().splitlines()[-1])
        assert answer["ok"] is False
        assert "Traceback" not in completed.stdout.decode()


def test_redact_replaces_every_secret_and_caps_the_text() -> None:
    secrets = {"a": "uzun-parola", "b": "ab", "c": ""}  # too short to replace safely: kept
    assert redact("x uzun-parola y uzun-parola ab", secrets) == "x *** y *** ab"
    assert len(redact("y" * 10_000, {})) == MAX_MESSAGE
