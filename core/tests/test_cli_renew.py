"""``arcilogger-core renew [--dry-run] [--json]`` (contract 2.1.3) and ``state/renewal.json``.

The command runs on a real configuration file; only the clock (and, where a test needs a
certificate of its own, the provider) is replaced, so the times of :mod:`renewal_world` hold.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization

from arcilogger_core import cli as cli_module
from arcilogger_core.cli import App, main
from arcilogger_core.execpolicy import ExecutableRefusedError
from arcilogger_core.renewal import (
    STATE_SCHEMA,
    RenewalBusyError,
    one_run,
    renew,
    renewal_status,
    write_state,
)
from arcilogger_core.secretstore import SecretStoreError
from arcilogger_core.tsa import TsaExecutableError
from arcilogger_core.tsa.base import TimestampProvider, TsaConfigurationError
from arcilogger_core.tsa.mock import MockFailure

from .renewal_world import CNA, RenewalWorld, day_token_path, make_world

DAY_TOKEN = day_token_path()
FAR = CNA + timedelta(days=1825)
RECORD = "renewals/2031-06-23-1.json"

CONFIG = """
data_dir = "{data}"
site_name = "Örnek A.Ş."

[[sources]]
name = "fortigate"
type = "fortigate"
address = "172.20.0.1"

[seal]
quiet_seconds = 0

[tsa]
{tsa}
legal_root_fingerprints = ["{fingerprint}"]
"""


@dataclass
class Cli:
    rw: RenewalWorld
    config: Path
    roots: Path
    monkeypatch: pytest.MonkeyPatch

    def configure(self, tsa: str) -> None:
        self.config.write_text(
            CONFIG.format(
                data=self.rw.layout.root.as_posix(),
                tsa=tsa,
                fingerprint=self.rw.legal.fingerprint,
            ),
            encoding="utf-8",
        )

    def run(self, *args: str, now: datetime, provider: TimestampProvider | None = None) -> int:
        """``renew`` at ``now``; with ``provider`` in place of the configured one."""
        self.rw.set_now(now)
        self.monkeypatch.setattr("arcilogger_core.cli.utc_now", lambda: now)
        if provider is not None:
            self.monkeypatch.setattr(App, "provider", lambda app: provider)
        return main(["--config", str(self.config), "renew", *args])

    def state(self) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(
            (self.rw.layout.state / "renewal.json").read_text(encoding="utf-8")
        )
        return data

    def renewal_files(self) -> list[str]:
        folder = self.rw.layout.renewals_dir
        return sorted(path.name for path in folder.iterdir()) if folder.is_dir() else []


@pytest.fixture
def cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Cli:
    """A sealed day (its certificate expiring at CNA) and a configuration with the mock
    provider and the legal CA's root pinned and listed."""
    rw = make_world(tmp_path)
    roots = tmp_path / "roots"
    roots.mkdir()
    (roots / "legal.pem").write_bytes(rw.legal.root.public_bytes(serialization.Encoding.PEM))
    cli = Cli(rw, tmp_path / "arcilogger.toml", roots, monkeypatch)
    cli.configure(f'provider = "mock"\nroots_dir = "{roots.as_posix()}"')
    return cli


def _json(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(capsys.readouterr().out)
    return data


def _statuses(data: dict[str, Any]) -> dict[str, str]:
    return {token["path"]: token["status"] for token in data["tokens"]}


# -- exit codes -----------------------------------------------------------------------------------


def test_nothing_due_exits_0(cli: Cli, capsys: pytest.CaptureFixture[str]) -> None:
    now = CNA - timedelta(days=181)
    assert cli.run(now=now) == 0
    out = capsys.readouterr().out
    assert "Yeniden damgalanması gereken damga yok [no time stamp is due" in out
    assert "Durum [status]: not_due 1, due 0, covered 0, late 0, uncovered 0" in out
    assert cli.renewal_files() == []
    state = cli.state()
    assert _statuses(state) == {DAY_TOKEN: "not_due"}
    assert state["last_run"] == {
        "at": "2031-04-03T00:00:00Z",
        "outcome": "not_needed",
        "exit_code": 0,
        "record": None,
        "error": None,
    }
    assert state["failures_in_a_row"] == 0


def test_renewed_exits_0(cli: Cli, capsys: pytest.CaptureFixture[str]) -> None:
    now = CNA - timedelta(days=100)
    cli.rw.set_now(now)
    assert cli.run(now=now, provider=cli.rw.legal_provider(FAR)) == 0
    out = capsys.readouterr().out
    assert f"Yenileme kaydı yazıldı [renewal record written]: {RECORD} (1 damga" in out
    assert "UYARI" not in out
    assert cli.renewal_files() == ["2031-06-23-1.json", "2031-06-23-1.json.zd"]
    state = cli.state()
    # from the status after the run, not before (fixed34: report_after)
    assert _statuses(state) == {DAY_TOKEN: "covered", f"{RECORD}.zd": "not_due"}
    assert state["last_run"]["outcome"] == "renewed"
    assert state["last_run"]["record"] == RECORD


def test_a_test_provider_leaves_uncovered_and_exits_1(
    cli: Cli, capsys: pytest.CaptureFixture[str]
) -> None:
    """The mock provider writes a ``reason: "test"`` record that never counts (D5): the expired
    day stays uncovered, so the run exits 1."""
    now = CNA + timedelta(days=1)
    cli.rw.set_now(now)
    assert cli.run(now=now, provider=cli.rw.mock_provider(FAR)) == 1
    out = capsys.readouterr().out
    assert "(reason: test)" in out
    assert "1 damganın sertifikası yeniden damgalanmadan sona erdi" in out
    assert f"  {DAY_TOKEN}" in out
    state = cli.state()
    assert _statuses(state) == {DAY_TOKEN: "uncovered"}
    assert state["last_run"]["exit_code"] == 1
    record = json.loads((cli.rw.layout.vault / state["last_run"]["record"]).read_bytes())
    assert record["reason"] == "test"


def test_no_provider_credentials_exits_2(cli: Cli, capsys: pytest.CaptureFixture[str]) -> None:
    jar = cli.config.parent / "tss.jar"
    jar.write_bytes(b"PK")
    cli.configure(
        f'provider = "zamane"\nzamane_jar = "{jar.as_posix()}"\n'
        f'roots_dir = "{cli.roots.as_posix()}"'
    )
    assert cli.run("--json", now=CNA - timedelta(days=100)) == 2
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert (data["outcome"], data["exit_code"]) == ("refused", 2)
    assert data["error"]["code"] == "credentials"
    assert data["error"]["message_tr"].startswith(
        "Yapılandırma yeniden damgalamaya izin vermiyor (credentials): "
    )
    assert data["error"]["message_en"].startswith("The configuration refuses re-stamping")
    assert "Hata [error]: Yapılandırma" in captured.err
    assert cli.renewal_files() == []
    # the roots are known: the statuses are computed afresh
    state = cli.state()
    assert _statuses(state) == {DAY_TOKEN: "due"}
    assert (state["last_run"]["outcome"], state["last_run"]["error"]) == ("refused", "credentials")
    assert state["failures_in_a_row"] == 1


def test_no_pinned_root_exits_2_and_keeps_the_last_statuses(
    cli: Cli, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.run(now=CNA - timedelta(days=181)) == 0
    capsys.readouterr()
    before = cli.state()
    empty = cli.config.parent / "empty-roots"
    empty.mkdir()
    cli.configure(f'provider = "rfc3161"\nurl = "http://192.0.2.10"\nroots_dir = "{empty}"')
    assert cli.run("--json", now=CNA - timedelta(days=100)) == 2
    data = _json(capsys)
    assert (data["error"]["code"], data["report"]) == ("roots", None)
    state = cli.state()
    # without a root nothing can be verified: the last known statuses stay, with their time
    assert (state["tokens"], state["at"]) == (before["tokens"], before["at"])
    assert state["last_run"]["outcome"] == "refused"
    assert state["last_run"]["at"] == "2031-06-23T00:00:00Z"


def test_no_root_and_no_earlier_state_leaves_the_statuses_unknown(
    cli: Cli, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without a token list W5-C's reader says "unknown", never "nothing is due"."""
    empty = cli.config.parent / "empty-roots"
    empty.mkdir()
    cli.configure(f'provider = "rfc3161"\nurl = "http://192.0.2.10"\nroots_dir = "{empty}"')
    assert cli.run(now=CNA) == 2
    assert "(roots)" in capsys.readouterr().err
    state = cli.state()
    assert "tokens" not in state
    assert state["schema"] == STATE_SCHEMA


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (MockFailure.UNREACHABLE, "tsa_unavailable"),
        (MockFailure.NO_CREDIT, "tsa_no_credit"),
        (MockFailure.REJECTION, "tsa_rejected"),
        (MockFailure.BAD_SIGNATURE, "renewal_stamp_invalid"),
    ],
)
def test_a_failed_stamp_exits_3(
    cli: Cli, capsys: pytest.CaptureFixture[str], failure: MockFailure, code: str
) -> None:
    now = CNA - timedelta(days=100)
    cli.rw.set_now(now)
    provider = cli.rw.legal_provider(FAR)
    cli.rw.legal_tsa.failure = failure
    assert cli.run("--json", now=now, provider=provider) == 3
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert (data["outcome"], data["record"], data["error"]["code"]) == ("failed", None, code)
    assert data["due"] == [DAY_TOKEN]
    assert captured.err.startswith("Hata [error]: ")
    assert cli.renewal_files() == []
    state = cli.state()
    assert _statuses(state) == {DAY_TOKEN: "due"}
    assert state["last_run"] == {
        "at": "2031-06-23T00:00:00Z",
        "outcome": "failed",
        "exit_code": 3,
        "record": None,
        "error": code,
    }


def test_a_stamp_by_an_unpinned_root_exits_3(cli: Cli, capsys: pytest.CaptureFixture[str]) -> None:
    """The new token does not verify (its root is not pinned): no record, exit 3."""
    (cli.roots / "legal.pem").unlink()
    now = CNA - timedelta(days=100)
    cli.rw.set_now(now)
    assert cli.run("--json", now=now, provider=cli.rw.legal_provider(FAR)) == 3
    data = _json(capsys)
    assert data["error"]["code"] == "renewal_stamp_invalid"
    assert data["error"]["message_tr"].startswith("Yenileme kaydının zaman damgası doğrulanamadı")
    assert cli.renewal_files() == []


def test_a_file_error_exits_3(
    cli: Cli, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def full(*args: Any, **kwargs: Any) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("arcilogger_core.renewal.atomic_write_bytes", full)
    now = CNA - timedelta(days=100)
    cli.rw.set_now(now)
    assert cli.run("--json", now=now, provider=cli.rw.legal_provider(FAR)) == 3
    data = _json(capsys)
    assert data["error"]["code"] == "io_error"
    # the state file cannot be written either (the same function): reported, still exit 3
    assert data["state_written"] is False


def test_another_run_holding_the_lock_exits_3_and_writes_no_state(
    cli: Cli, capsys: pytest.CaptureFixture[str]
) -> None:
    now = CNA - timedelta(days=100)
    cli.rw.set_now(now)
    with one_run(cli.rw.layout):
        assert cli.run("--json", now=now, provider=cli.rw.legal_provider(FAR)) == 3
    data = _json(capsys)
    assert (data["outcome"], data["error"]["code"], data["state_written"]) == (
        "busy",
        "renewal_busy",
        False,
    )
    assert not (cli.rw.layout.state / "renewal.json").exists()


def test_failures_in_a_row_counts_and_resets(cli: Cli, capsys: pytest.CaptureFixture[str]) -> None:
    now = CNA - timedelta(days=100)
    cli.rw.set_now(now)
    provider = cli.rw.legal_provider(FAR)
    cli.rw.legal_tsa.failure = MockFailure.UNREACHABLE
    assert cli.run(now=now, provider=provider) == 3
    assert cli.state()["failures_in_a_row"] == 1
    assert cli.run(now=now + timedelta(days=1), provider=provider) == 3
    assert cli.state()["failures_in_a_row"] == 2
    # a dry run is not a run of the task: it keeps the count and the last run
    assert cli.run("--dry-run", now=now + timedelta(days=1, hours=1)) == 0
    state = cli.state()
    assert (state["failures_in_a_row"], state["last_run"]["outcome"]) == (2, "failed")
    assert state["at"] == "2031-06-24T01:00:00Z"
    cli.rw.legal_tsa.failure = MockFailure.NONE
    assert cli.run(now=now + timedelta(days=2), provider=provider) == 0
    state = cli.state()
    assert (state["failures_in_a_row"], state["last_run"]["outcome"]) == (0, "renewed")
    capsys.readouterr()


# -- --dry-run and --json --------------------------------------------------------------------------


def test_dry_run_takes_no_stamp_and_writes_no_record(
    cli: Cli, capsys: pytest.CaptureFixture[str]
) -> None:
    now = CNA - timedelta(days=100)

    class NoStamp:
        name = "zamane"
        legal_grade = True

        def stamp(self, file_path: Path) -> Any:
            raise AssertionError("a dry run takes no stamp")

    assert cli.run("--dry-run", now=now, provider=NoStamp()) == 0  # type: ignore[arg-type]
    out = capsys.readouterr().out
    assert "Deneme: damga alınmadı, kayıt yazılmadı [dry run: no stamp taken" in out
    assert f"Yeniden damgalanacak damgalar [stamps to re-stamp] (1):\n  {DAY_TOKEN}" in out
    assert cli.renewal_files() == []
    state = cli.state()
    assert _statuses(state) == {DAY_TOKEN: "due"}
    assert "last_run" not in state
    # an expired, uncovered token: 1, by the rule of a real run
    assert cli.run("--dry-run", "--json", now=CNA + timedelta(days=1)) == 1
    data = _json(capsys)
    assert (data["outcome"], data["dry_run"], data["due"]) == ("dry_run", True, [DAY_TOKEN])
    assert cli.renewal_files() == []


def test_dry_run_needs_no_provider_credentials(
    cli: Cli, capsys: pytest.CaptureFixture[str]
) -> None:
    jar = cli.config.parent / "tss.jar"
    jar.write_bytes(b"PK")
    cli.configure(
        f'provider = "zamane"\nzamane_jar = "{jar.as_posix()}"\n'
        f'roots_dir = "{cli.roots.as_posix()}"'
    )
    assert cli.run("--dry-run", now=CNA - timedelta(days=100)) == 0
    assert "Durum [status]: not_due 0, due 1" in capsys.readouterr().out


def test_json_output_shape(cli: Cli, capsys: pytest.CaptureFixture[str]) -> None:
    now = CNA - timedelta(days=100)
    cli.rw.set_now(now)
    assert cli.run("--json", now=now, provider=cli.rw.legal_provider(FAR)) == 0
    data = _json(capsys)
    assert list(data) == [
        "outcome",
        "exit_code",
        "dry_run",
        "record",
        "reason",
        "due",
        "renewed",
        "discarded",
        "error",
        "state_written",
        "report",
    ]
    assert data | {"report": None} == {
        "outcome": "renewed",
        "exit_code": 0,
        "dry_run": False,
        "record": RECORD,
        "reason": "due",
        "due": [DAY_TOKEN],
        "renewed": [DAY_TOKEN],
        "discarded": [],
        "error": None,
        "state_written": True,
        "report": None,
    }
    report = data["report"]
    assert list(report) == ["tokens", "totals", "unreadable", "mismatched", "notes"]
    day = report["tokens"][0]
    assert day == {
        "path": DAY_TOKEN,
        "kind": "day",
        "cert_not_after": "2031-10-01T00:00:00Z",
        "status": "covered",
        "covered_by": RECORD,
        "renewed_at": day["renewed_at"],
    }
    assert datetime.fromisoformat(day["renewed_at"]) == now
    assert report["totals"] == {"not_due": 1, "due": 0, "covered": 1, "late": 0, "uncovered": 0}
    # the state file holds the same report
    state = cli.state()
    assert {key: state[key] for key in report} == report


def test_the_state_file_shape(cli: Cli, capsys: pytest.CaptureFixture[str]) -> None:
    now = CNA - timedelta(days=100)
    cli.rw.set_now(now)
    assert cli.run(now=now, provider=cli.rw.legal_provider(FAR)) == 0
    capsys.readouterr()
    state = cli.state()
    assert list(state) == [
        "schema",
        "at",
        "renew_before_days",
        "tokens",
        "totals",
        "unreadable",
        "mismatched",
        "notes",
        "last_run",
        "failures_in_a_row",
    ]
    assert (state["schema"], state["at"], state["renew_before_days"]) == (
        "arcilogger.renewal-status/1",
        "2031-06-23T00:00:00Z",
        180,
    )
    report = renewal_status(cli.rw.layout, cli.rw.world.config, cli.rw.roots, now)
    assert state["tokens"] == report.as_json()["tokens"]
    assert not list(cli.rw.layout.state.glob(".renewal.json*.tmp"))


# -- the lock, the refusals and the state write ---------------------------------------------------


def test_the_exit_code_follows_the_status_after_the_run(
    cli: Cli, capsys: pytest.CaptureFixture[str]
) -> None:
    """An expired day renewed by a legal provider is ``uncovered`` before the run and ``late``
    after it: the run exits 0, judged from the status after the run (fixed34)."""
    now = CNA + timedelta(days=1)
    cli.rw.set_now(now)
    assert cli.run("--json", now=now, provider=cli.rw.legal_provider(FAR)) == 0
    data = _json(capsys)
    assert (data["outcome"], data["exit_code"], data["due"]) == ("renewed", 0, [DAY_TOKEN])
    state = cli.state()
    assert _statuses(state)[DAY_TOKEN] == "late"
    assert state["totals"]["uncovered"] == 0
    assert state["last_run"]["exit_code"] == 0


def test_a_state_file_that_cannot_be_written_alone_exits_3(
    cli: Cli, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The record is written, only ``state/renewal.json`` fails: still exit 3."""

    def full(*args: Any, **kwargs: Any) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(cli_module, "write_state", full)
    now = CNA - timedelta(days=100)
    cli.rw.set_now(now)
    assert cli.run("--json", now=now, provider=cli.rw.legal_provider(FAR)) == 3
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert (data["outcome"], data["exit_code"], data["error"]) == ("renewed", 3, None)
    assert data["state_written"] is False
    assert "state/renewal.json yazılamadı [cannot be written]" in captured.err
    assert cli.renewal_files() == ["2031-06-23-1.json", "2031-06-23-1.json.zd"]


@pytest.mark.parametrize(
    ("error", "cause"),
    [
        (TsaConfigurationError("Java not found"), "provider"),
        (TsaExecutableError(ExecutableRefusedError("tsa.zamane_jar", "not found")), "executable"),
    ],
)
def test_a_provider_that_cannot_run_at_stamp_time_is_refused(
    cli: Cli, capsys: pytest.CaptureFixture[str], error: TsaConfigurationError, cause: str
) -> None:
    """A configuration error found only while stamping (no Java, no client) needs an
    administrator, not a retry: exit 2, as when the provider cannot be made."""

    class CannotRun:
        name = "zamane"
        legal_grade = True

        def stamp(self, file_path: Path) -> Any:
            raise error

    now = CNA - timedelta(days=100)
    assert cli.run("--json", now=now, provider=CannotRun()) == 2  # type: ignore[arg-type]
    data = _json(capsys)
    assert (data["outcome"], data["exit_code"], data["error"]["code"]) == ("refused", 2, cause)
    assert cli.renewal_files() == []
    state = cli.state()
    assert _statuses(state) == {DAY_TOKEN: "due"}
    assert (state["last_run"]["outcome"], state["last_run"]["exit_code"]) == ("refused", 2)
    assert (state["last_run"]["error"], state["failures_in_a_row"]) == (cause, 1)


@pytest.mark.parametrize(
    ("error", "cause"),
    [
        (SecretStoreError("the secret store cannot be read"), "secret-store"),
        (TsaExecutableError(ExecutableRefusedError("tsa.java", "not allowed")), "executable"),
        (TsaConfigurationError("unknown provider"), "provider"),
    ],
)
def test_each_refusal_names_its_cause(
    cli: Cli,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    cause: str,
) -> None:
    def refuse(app: App) -> Any:
        raise error

    monkeypatch.setattr(App, "provider", refuse)
    assert cli.run("--json", now=CNA - timedelta(days=100)) == 2
    data = _json(capsys)
    assert (data["outcome"], data["error"]["code"]) == ("refused", cause)
    assert cli.state()["last_run"]["error"] == cause


def test_no_zamane_jar_is_refused(cli: Cli, capsys: pytest.CaptureFixture[str]) -> None:
    cli.configure(f'provider = "zamane"\nroots_dir = "{cli.roots.as_posix()}"')
    assert cli.run("--json", now=CNA - timedelta(days=100)) == 2
    assert _json(capsys)["error"]["code"] == "zamane-jar"


def test_an_unreadable_pinned_root_is_refused(cli: Cli, capsys: pytest.CaptureFixture[str]) -> None:
    """A damaged certificate in ``tsa.roots_dir`` is a configuration refusal, recorded in the
    state file, never a traceback."""
    (cli.roots / "damaged.pem").write_text(
        "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n", encoding="ascii"
    )
    cli.configure(f'provider = "rfc3161"\nurl = "http://192.0.2.10"\nroots_dir = "{cli.roots}"')
    assert cli.run("--json", now=CNA - timedelta(days=100)) == 2
    data = _json(capsys)
    assert (data["outcome"], data["error"]["code"], data["report"]) == ("refused", "roots", None)
    assert cli.state()["last_run"]["error"] == "roots"


def test_a_dry_run_without_roots_exits_2_and_writes_nothing(
    cli: Cli, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = cli.config.parent / "empty-roots"
    empty.mkdir()
    cli.configure(f'provider = "rfc3161"\nurl = "http://192.0.2.10"\nroots_dir = "{empty}"')
    assert cli.run("--dry-run", "--json", now=CNA) == 2
    data = _json(capsys)
    assert (data["outcome"], data["dry_run"], data["state_written"]) == ("refused", True, False)
    assert not (cli.rw.layout.state / "renewal.json").exists()


def test_a_dry_run_while_another_run_holds_the_lock_writes_nothing(
    cli: Cli, capsys: pytest.CaptureFixture[str]
) -> None:
    """A dry run never writes over the file of a run in progress (busy, exit 3)."""
    now = CNA - timedelta(days=100)
    cli.rw.set_now(now)
    cli.rw.legal_tsa.failure = MockFailure.UNREACHABLE
    assert cli.run(now=now, provider=cli.rw.legal_provider(FAR)) == 3
    capsys.readouterr()
    before = (cli.rw.layout.state / "renewal.json").read_bytes()
    with one_run(cli.rw.layout):
        assert cli.run("--dry-run", "--json", now=now + timedelta(hours=1)) == 3
    data = _json(capsys)
    assert (data["outcome"], data["error"]["code"], data["state_written"]) == (
        "busy",
        "renewal_busy",
        False,
    )
    assert (cli.rw.layout.state / "renewal.json").read_bytes() == before


@pytest.mark.parametrize("kind", ["renewed", "not_needed", "failed", "refused", "dry_run"])
def test_the_state_file_is_written_under_the_lock(
    cli: Cli, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """Every outcome reads and writes ``state/renewal.json`` while it holds
    ``state/renewal.lock``."""
    held: list[bool] = []

    def no_secrets(app: App) -> Any:
        raise SecretStoreError("the secret store cannot be read")

    def checked(layout: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            with one_run(layout):
                held.append(False)
        except RenewalBusyError:
            held.append(True)
        return write_state(layout, *args, **kwargs)

    monkeypatch.setattr(cli_module, "write_state", checked)
    now = CNA - timedelta(days=100) if kind != "not_needed" else CNA - timedelta(days=181)
    cli.rw.set_now(now)
    provider: TimestampProvider | None = cli.rw.legal_provider(FAR)
    if kind == "failed":
        cli.rw.legal_tsa.failure = MockFailure.UNREACHABLE
    if kind == "refused":
        monkeypatch.setattr(App, "provider", no_secrets)
        provider = None
    args = ["--dry-run"] if kind == "dry_run" else []
    cli.run(*args, "--json", now=now, provider=provider)
    assert _json(capsys)["outcome"] == kind
    assert held == [True]


def test_the_state_file_is_replaced_atomically(
    cli: Cli, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A write that fails before the rename leaves the previous file as it was."""
    now = CNA - timedelta(days=181)
    assert cli.run(now=now) == 0
    capsys.readouterr()
    path = cli.rw.layout.state / "renewal.json"
    before = path.read_bytes()

    def broken(self: Path, target: Any) -> Path:
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(Path, "replace", broken)
    with pytest.raises(OSError, match="Input/output error"):
        write_state(
            cli.rw.layout, None, at=now + timedelta(days=1), renew_before=180, last_run=None
        )
    monkeypatch.undo()
    assert path.read_bytes() == before


def test_only_the_command_writes_the_state_file(cli: Cli) -> None:
    """``renewal_status`` and ``renew`` themselves never write ``state/renewal.json``: only
    the ``Renew`` task's command does (§2.1.2, W5-A-2)."""
    now = CNA - timedelta(days=100)
    cli.rw.set_now(now)
    renewal_status(cli.rw.layout, cli.rw.world.config, cli.rw.roots, now)
    renew(cli.rw.layout, cli.rw.world.config, cli.rw.legal_provider(FAR), cli.rw.roots, now)
    assert cli.renewal_files() == ["2031-06-23-1.json", "2031-06-23-1.json.zd"]
    assert not (cli.rw.layout.state / "renewal.json").exists()
