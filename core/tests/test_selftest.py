"""``arcilogger-core selftest`` (0.4.0 P3): one row per check, each ok, warn/fail and skip; it
changes nothing and no stored secret appears in its output."""

from __future__ import annotations

import json
import ssl
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import IPv4Address
from pathlib import Path
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from arcilogger_core import selftest
from arcilogger_core.admin.testing import SmtpState, SmtpTestServer
from arcilogger_core.cli import main
from arcilogger_core.clockcheck import ClockError, NtpResult
from arcilogger_core.secretstore import SMTP_PASSWORD, TURKTRUST_PASSWORD, SecretStore
from arcilogger_core.selftest import DatabaseAnswer, Probes, Row
from arcilogger_core.smtp import SmtpSendError

#: A stored value that must never reach a row (the e-mail server echoes it back).
PASSWORD = "Ornek-Posta-Parola-27"  # smtplib sends ASCII only
HEAD = {"revision": ["0042_head"], "head": ["0042_head"], "up_to_date": True}
TURKTRUST = 'provider = "turktrust"\nurl = "https://zd.turktrust.com.tr/"'


def write_config(root: Path, *, tsa: str = 'provider = "mock"', extra: str = "") -> Path:
    path = root / "arcilogger.toml"
    path.write_text(
        f'data_dir = "{(root / "data").as_posix()}"\n'
        'site_name = "Örnek A.Ş."\n\n'
        f"[tsa]\n{tsa}\n\n"
        '[clock]\nntp_server = "172.20.10.1"\n\n'
        f"{extra}",
        encoding="utf-8",
    )
    if not (root / "secret.key").exists():
        SecretStore.create_key(root / "secret.key")
    return path


def calm(**overrides: Any) -> Probes:
    """Probes that answer as a healthy installation does."""
    values: dict[str, Any] = {
        "database": lambda _path: DatabaseAnswer(0, HEAD, ""),
        "http": lambda _url, _timeout, _proxy: 401,
        "tcp": lambda _host, _port, _timeout: None,
        "smtp": lambda _config, _password: None,
        "ntp": lambda _server, _port: NtpResult(offset_s=0.012, delay_s=0.003, stratum=2),
    }
    return Probes(**{**values, **overrides})


def by_id(rows: list[Row]) -> dict[str, Row]:
    return {row.id: row for row in rows}


def test_a_healthy_mock_installation_is_ok_and_skips_what_it_does_not_use(
    tmp_path: Path,
) -> None:
    rows = selftest.run(write_config(tmp_path), calm())
    assert [row.id for row in rows] == list(selftest.ROWS)
    found = {row.id: row.status for row in rows}
    assert found == {
        "config": "ok",
        "secrets": "ok",
        "database": "ok",
        "roots": "skip",  # the mock makes its test root on its first stamp
        "tsa.reach": "skip",
        "smtp": "skip",
        "clock": "ok",
    }
    assert by_id(rows)["clock"].message_tr == "Saat farkı 12 ms (172.20.10.1; sınır 1000 ms)."
    assert selftest.exit_code(rows) == 0


def test_a_configuration_that_does_not_load_fails_and_skips_the_rest(tmp_path: Path) -> None:
    broken = tmp_path / "arcilogger.toml"
    broken.write_text("[tsa\n", encoding="utf-8")
    rows = selftest.run(broken, calm())
    assert (rows[0].id, rows[0].status) == ("config", "fail")
    assert rows[0].message_en.startswith("The configuration does not load: ")
    assert {row.status for row in rows[1:]} == {"skip"}
    assert selftest.exit_code(rows) == 2


def test_the_secret_store(tmp_path: Path) -> None:
    config = write_config(tmp_path, tsa=TURKTRUST)
    rows = by_id(selftest.run(config, calm()))
    assert rows["secrets"].status == "warn"  # the browser's wizard asks for them later
    assert rows["secrets"].message_en == (
        "Not stored: turktrust.username, turktrust.password. "
        "The setup wizard in the browser asks for them."
    )
    (tmp_path / "secret.key").unlink()
    rows = by_id(selftest.run(config, calm()))
    assert rows["secrets"].status == "fail"
    assert "secret key not found" in rows["secrets"].message_en


def test_the_database_row(tmp_path: Path) -> None:
    config = write_config(tmp_path)

    def database(answer: DatabaseAnswer | None) -> Row:
        return by_id(selftest.run(config, calm(database=lambda _path: answer)))["database"]

    behind = {**HEAD, "revision": ["0041_before"], "up_to_date": False}
    assert database(DatabaseAnswer(1, behind, "")).status == "fail"
    assert "arcilogger-db upgrade" in database(DatabaseAnswer(1, behind, "")).message_en
    down = database(DatabaseAnswer(3, None, "veritabanına bağlanılamadı: connection refused"))
    assert (down.status, down.message_tr) == (
        "fail",
        "Veritabanı: veritabanına bağlanılamadı: connection refused",
    )
    assert database(None).status == "skip"  # no database package in this installation
    # warnings of `arcilogger-db check` that are not the schema keep the row ok
    assert database(DatabaseAnswer(1, {**HEAD, "warnings": ["partitions"]}, "")).status == "ok"


def test_the_database_check_runs_the_database_command(tmp_path: Path) -> None:
    """The real child process: without a reachable server it fails with a cause."""
    config = write_config(
        tmp_path, extra='[database]\nhost = "127.0.0.1"\nport = 9\nconnect_timeout_seconds = 2\n'
    )
    answer = selftest.run_database_check(config)
    assert answer is not None
    assert answer.exit_code != 0
    assert answer.error


def test_the_roots_row(tmp_path: Path) -> None:
    empty = tmp_path / "roots"
    empty.mkdir()
    config = write_config(
        tmp_path,
        tsa=f'provider = "turktrust"\nurl = "https://zd.turktrust.com.tr/"\n'
        f'roots_dir = "{empty.as_posix()}"',
    )
    rows = by_id(selftest.run(config, calm()))
    assert rows["roots"].status == "fail"
    shipped = Path(__file__).parents[2] / "deploy" / "windows" / "roots"
    config = write_config(
        tmp_path,
        tsa=f'provider = "turktrust"\nurl = "https://zd.turktrust.com.tr/"\n'
        f'roots_dir = "{shipped.as_posix()}"',
    )
    rows = by_id(selftest.run(config, calm()))
    assert rows["roots"].status == "ok", rows["roots"]  # the product's H7 root


def test_the_time_stamp_server_row(tmp_path: Path) -> None:
    seen: list[tuple[str, int]] = []
    config = write_config(tmp_path, tsa='provider = "zamane"\nurl = "http://tzd.kamusm.gov.tr"')

    def tcp(host: str, port: int, _timeout: float) -> None:
        seen.append((host, port))

    rows = by_id(selftest.run(config, calm(tcp=tcp)))
    assert rows["tsa.reach"].status == "ok"
    assert seen == [("tzd.kamusm.gov.tr", 80)]

    def unreachable(*_: object) -> None:
        raise OSError("timed out")

    rows = by_id(selftest.run(config, calm(tcp=unreachable)))
    assert rows["tsa.reach"].status == "warn"
    assert rows["tsa.reach"].message_en == (
        "The time-stamp server cannot be reached (tzd.kamusm.gov.tr): timed out"
    )


def test_turktrust_is_reached_when_it_answers_401(tmp_path: Path) -> None:
    config = write_config(
        tmp_path, tsa='provider = "turktrust"\nurl = "https://zd.turktrust.com.tr/"'
    )
    rows = by_id(selftest.run(config, calm(http=lambda *_: 401)))
    assert rows["tsa.reach"].status == "ok"
    assert "HTTP 401" in rows["tsa.reach"].message_en


# -- a real HTTPS server that answers 401 to a request without credentials (S6) -----------------


def _name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def _tls(folder: Path) -> tuple[Path, ssl.SSLContext]:
    now = datetime.now(UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = (
        x509.CertificateBuilder()
        .subject_name(_name("Örnek TLS CA (TEST)"))
        .issuer_name(_name("Örnek TLS CA (TEST)"))
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    host = (
        x509.CertificateBuilder()
        .subject_name(_name("localhost"))
        .issuer_name(ca.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(IPv4Address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    (folder / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (folder / "host.pem").write_bytes(host.public_bytes(serialization.Encoding.PEM))
    (folder / "host.key").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(folder / "host.pem", folder / "host.key")
    return folder / "ca.pem", context


@pytest.fixture
def https_401(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    ca, context = _tls(tmp_path)
    monkeypatch.setenv("SSL_CERT_FILE", str(ca))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            assert self.headers.get("Authorization") is None  # the check sends no credentials
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="TSA"')
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"https://localhost:{server.server_address[1]}/"
    server.shutdown()
    server.server_close()


def test_an_https_answer_of_401_counts_as_reached(https_401: str) -> None:
    assert selftest.http_status(https_401, 5.0, "") == 401


def test_no_https_answer_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(OSError, match=r".+"):  # nobody listens on the discard port
        selftest.http_status("https://127.0.0.1:9/", 2.0, "")


# -- the e-mail server --------------------------------------------------------------------------


@pytest.fixture
def smtp_server() -> Iterator[SmtpState]:
    with SmtpTestServer() as state:
        state.credentials = ("arcilogger@ornek.example", PASSWORD)
        yield state


def smtp_config(root: Path, state: SmtpState, password: str | None) -> Path:
    config = write_config(
        root,
        extra=(
            '[smtp]\nenabled = true\nhost = "127.0.0.1"\n'
            f'port = {state.port}\nsecurity = "none"\n'
            'username = "arcilogger@ornek.example"\n'
            'from_address = "arcilogger@ornek.example"\ntimeout_seconds = 5\n'
        ),
    )
    if password is not None:
        SecretStore(root / "secret.key", root / "secrets.json").set(SMTP_PASSWORD, password)
    return config


def test_the_mail_server_is_signed_in_to_and_nothing_is_sent(
    tmp_path: Path, smtp_server: SmtpState
) -> None:
    rows = by_id(selftest.run(smtp_config(tmp_path, smtp_server, PASSWORD), Probes(
        database=lambda _path: DatabaseAnswer(0, HEAD, ""),
        ntp=lambda _server, _port: NtpResult(offset_s=0.0, delay_s=0.0, stratum=2),
    )))  # fmt: skip
    assert rows["smtp"].status == "ok", rows["smtp"]
    assert rows["smtp"].message_en.endswith("nothing was sent.")
    assert smtp_server.received == []  # no DATA


def test_a_refused_sign_in_warns_with_the_answer_and_without_the_password(
    tmp_path: Path, smtp_server: SmtpState
) -> None:
    smtp_server.credentials = ("arcilogger@ornek.example", "another")
    smtp_server.echo = PASSWORD  # a server that repeats what it was sent
    config = smtp_config(tmp_path, smtp_server, PASSWORD)
    rows = selftest.run(config, Probes(database=lambda _path: DatabaseAnswer(0, HEAD, "")))
    smtp = by_id(rows)["smtp"]
    assert smtp.status == "warn"
    assert "535" in smtp.message_en
    output = json.dumps(selftest.as_json(rows), ensure_ascii=False) + selftest.render(rows)
    assert PASSWORD not in output


def test_a_known_refusal_gets_its_explanation(tmp_path: Path) -> None:
    config = write_config(
        tmp_path,
        extra=(
            '[smtp]\nenabled = true\nhost = "smtp.gmail.com"\nport = 587\n'
            'username = "bt@ornek.example"\nfrom_address = "bt@ornek.example"\n'
        ),
    )
    SecretStore(tmp_path / "secret.key", tmp_path / "secrets.json").set(SMTP_PASSWORD, PASSWORD)

    def refused(_config: object, _password: object) -> None:
        raise SmtpSendError(
            "SMTPAuthenticationError: 535 5.7.8 Username and Password not accepted.",
            permanent=False,
        )

    smtp = by_id(selftest.run(config, calm(smtp=refused)))["smtp"]
    assert smtp.status == "warn"
    assert "535 5.7.8" in smtp.message_tr
    assert "uygulama şifresi" in smtp.message_tr
    assert "app password" in smtp.message_en


def test_the_clock_row(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    far = by_id(selftest.run(config, calm(ntp=lambda *_: NtpResult(2.5, 0.0, 2))))["clock"]
    assert (far.status, far.message_en) == ("warn", "Clock: 172.20.10.1: 2500 ms > 1000 ms")

    def silent(server: str, _port: int) -> NtpResult:
        raise ClockError(f"{server}: timed out")

    silent_row = by_id(selftest.run(config, calm(ntp=silent)))["clock"]
    assert silent_row.status == "warn"
    assert "172.20.10.1: timed out" in silent_row.message_en


def test_no_stored_secret_appears_in_any_output(tmp_path: Path) -> None:
    config = write_config(
        tmp_path, tsa='provider = "turktrust"\nurl = "https://zd.turktrust.com.tr/"'
    )
    store = SecretStore(tmp_path / "secret.key", tmp_path / "secrets.json")
    store.set(TURKTRUST_PASSWORD, PASSWORD)
    store.set("turktrust.username", "ornek-kullanici")

    def leaky(*_: object) -> int:
        raise OSError(f"proxy said: {PASSWORD}")

    rows = selftest.run(config, calm(http=leaky))
    output = json.dumps(selftest.as_json(rows), ensure_ascii=False) + selftest.render(rows)
    assert PASSWORD not in output
    assert "***" in by_id(rows)["tsa.reach"].message_en


def test_the_command_prints_json_and_exits_with_the_worst_status(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    config = write_config(tmp_path)
    monkeypatch.setattr(selftest, "Probes", lambda: calm())
    assert main(["--config", str(config), "selftest", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert [row["id"] for row in report["rows"]] == list(selftest.ROWS)
    assert set(report["rows"][0]) == {"id", "status", "message_tr", "message_en"}
    monkeypatch.setattr(selftest, "Probes", lambda: calm(tcp=None, ntp=_far))
    assert main(["--config", str(config), "selftest"]) == 1
    out = capsys.readouterr().out
    assert out.startswith("ARCILOGGER denetimi [self-test]")
    assert "UYARI [WARN]" in out
    broken = tmp_path / "broken.toml"
    broken.write_text("[tsa\n", encoding="utf-8")
    assert main(["--config", str(broken), "selftest"]) == 2


def _far(_server: str, _port: int) -> NtpResult:
    return NtpResult(offset_s=3.0, delay_s=0.0, stratum=2)


def test_nothing_is_written(tmp_path: Path) -> None:
    """No clock sample, alert, mock root or stamp: the folders are as they were."""
    shipped = Path(__file__).parents[2] / "deploy" / "windows" / "roots"
    for tsa in ('provider = "mock"', f'{TURKTRUST}\nroots_dir = "{shipped.as_posix()}"'):
        config = write_config(tmp_path, tsa=tsa)
        before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
        selftest.run(config, calm())
        assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before
