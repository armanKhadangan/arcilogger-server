"""The section rules of the browser administration (ADR-0014; contract 2.3, 2.6, 2.12)."""

from __future__ import annotations

import hashlib
import io
import zipfile
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import NameOID
from hypothesis import given
from hypothesis import strategies as st

from arcilogger_core.admin import messages, rules
from arcilogger_core.admin.contract import (
    FALLBACK_POLICY,
    AdminPolicy,
    AdminRequest,
    SetOp,
    UploadKind,
    new_id,
)
from arcilogger_core.admin.rules import (
    EDITABLE,
    RuleContext,
    SectionCheck,
    UploadInfo,
    check_request,
    conflicts,
    locked_paths,
    validate_section,
)
from arcilogger_core.config import Config, parse_config
from arcilogger_core.execpolicy import TrustAnchors
from arcilogger_core.timeutil import SITE_TZ
from arcilogger_core.tsa import zamane_releases
from arcilogger_core.tsa.zamane_releases import ZamaneRelease

NOW = datetime(2026, 10, 25, 10, 15, tzinfo=SITE_TZ)
POLICY = FALLBACK_POLICY.model_copy(update={"enabled": True})

BASE_TOML = """\
site_name = "Örnek A.Ş."
data_dir = "{data}"

[tsa]
provider = "mock"

[clock]
ntp_server = "172.20.10.1"
fallback_server = "time.ume.tubitak.gov.tr"

[[sources]]
name = "core-r1"
type = "mikrotik"
address = "172.20.10.1"

[[sources]]
name = "aps"
type = "generic"
address = "172.20.20.0/24"
expected = false
"""


@pytest.fixture
def root(tmp_path: Path) -> Path:
    folder = tmp_path / "ProgramData"
    folder.mkdir()
    (folder / "arcilogger.toml").write_text(
        BASE_TOML.format(data=(tmp_path / "data").as_posix()), encoding="utf-8"
    )
    return folder


@pytest.fixture
def config(root: Path) -> Config:
    path = root / "arcilogger.toml"
    return parse_config(path.read_bytes(), path)


def context(root: Path, **changes: Any) -> RuleContext:
    values: dict[str, Any] = {
        "policy": POLICY,
        "anchors": TrustAnchors(root, POLICY),
        "stored_secrets": frozenset(),
        "now": NOW,
        "local_addresses": ("172.20.10.5",),
        **changes,
    }
    return RuleContext(**values)


def codes(check: SectionCheck) -> list[str]:
    return [problem.code for problem in check.errors]


def warnings(check: SectionCheck) -> list[str]:
    return [problem.code for problem in check.warnings]


# -- the allow-list --------------------------------------------------------------------------------


def test_every_editable_path_is_a_configuration_path_or_a_browser_secret() -> None:
    paths = set(rules._leaf_paths(Config)) | set(rules.SECTION_SECRETS["tsa"])
    paths |= {"smtp.password", "replica.password", "sources"}
    assert set(EDITABLE) <= paths
    assert {"data_dir", "secrets_dir"}.isdisjoint(EDITABLE)


def test_every_other_path_is_locked_with_its_reason() -> None:
    locked = {item.path: item.reason for item in locked_paths()}
    assert locked["data_dir"] == "security"
    assert locked["secrets_dir"] == "security"
    assert locked["database.host"] == "security"
    assert locked["database.password"] == "security"
    assert locked["usage.pseudonym_key"] == "security"
    assert locked["backup.pg_dump"] == "security"
    assert locked["api.port"] == "service"
    assert locked["worker.interval_seconds"] == "service"
    assert locked["collector.unknown_senders"] == "collector"
    assert locked["seal.quiet_seconds"] == "evidence"
    assert locked["tsa.digest"] == "expert"
    assert locked["tsa.roots_dir"] == "expert"
    assert locked["clock.ntp_port"] == "expert"
    assert set(locked).isdisjoint(EDITABLE)


@given(
    st.from_regex(r"\A[a-z_]{1,12}(\.[a-z0-9][a-z0-9_-]{0,12}){0,2}\Z").filter(
        lambda path: path not in EDITABLE
    )
)
def test_any_path_outside_the_allow_list_is_locked(path: str) -> None:
    root = Path("/srv/arci")
    config = Config.model_validate({"data_dir": "/srv/data", "secrets_dir": str(root)})
    for section in rules.SECTIONS:
        check = rules._run(
            section,
            config,
            [SetOp(op="set", path=path, expect=None, value="x")],
            RuleContext(
                policy=POLICY,
                anchors=TrustAnchors(root, POLICY),
                stored_secrets=frozenset(),
                now=NOW,
            ),
            test_to=None,
            probe=True,
        )
        assert codes(check) == ["config_key_locked"]
        assert check.edits == ()


def test_a_path_of_another_section_is_locked(root: Path, config: Config) -> None:
    check = validate_section("smtp", config, {"site_name": "x"}, context(root))
    assert check.ops == ()  # a form body names only its own keys
    request = AdminRequest.model_validate(
        {
            "id": "a" * 32, "instance": "b" * 32,
            "issued_at": NOW, "expires_at": NOW.replace(minute=20),
            "kind": "config.apply", "section": "smtp",
            "actor": {"user_id": 3, "username": "ayse", "session": "s", "ip": "172.20.10.37"},
            "ops": [{"op": "set", "path": "site_name", "expect": "Örnek A.Ş.", "value": "x"}],
        }
    )  # fmt: skip
    assert codes(check_request(request, config, context(root))) == ["config_key_locked"]


def test_every_code_the_agent_can_report_has_both_texts() -> None:
    for code in (
        "site_name_required", "host_invalid", "clock_servers_required", "config_invalid",
        "config_key_locked", "retention_below_minimum", "source_overlap", "source_limit",
        "secret_rebind_required", "tsa_would_break", "tsa_not_ready", "java_not_detected",
        "root_exists", "zamane_archive_invalid", "replica_target_invalid", "admin_disabled",
    ):  # fmt: skip
        turkish, english = messages.texts(code)
        assert turkish
        assert english
        assert turkish != english


def test_http_statuses_follow_the_catalogue() -> None:
    assert rules.Problem("source_not_found").http_status == 404
    assert rules.Problem("source_overlap").http_status == 409
    assert rules.Problem("tsa_would_break").http_status == 409
    assert rules.Problem("config_unsupported_format").http_status == 409
    assert rules.Problem("site_name_required").http_status == 422


# -- values, etags, conflicts ----------------------------------------------------------------------


def test_section_values_and_etags(root: Path, config: Config) -> None:
    values = rules.section_values("general", config, frozenset())
    assert values == {
        "site_name": "Örnek A.Ş.",
        "clock.ntp_server": "172.20.10.1",
        "clock.fallback_server": "time.ume.tubitak.gov.tr",
        "clock.alert_offset_ms": 1000,
    }
    tsa = rules.section_values("tsa", config, frozenset({"kamusm.customer"}))
    assert tsa["kamusm.customer"] == "set"
    assert tsa["kamusm.password"] == "unset"
    assert "kamusm.password" not in rules.section_values("general", config, frozenset())
    etag = rules.etag("general", config, frozenset())
    assert etag.startswith("general:")
    assert len(etag) == len("general:") + 64
    changed = config.model_copy(update={"site_name": "Başka"})
    assert rules.etag("general", changed, frozenset()) != etag
    assert rules.etag("tsa", config, frozenset()) != rules.etag(
        "tsa", config, frozenset({"kamusm.password"})
    )
    sources = rules.section_values("sources", config, frozenset())["sources"]
    assert isinstance(sources, list)
    assert isinstance(sources[0], dict)
    assert sources[0]["name"] == "core-r1"


def test_conflicts_name_the_changed_paths(config: Config) -> None:
    ops = [
        SetOp(op="set", path="site_name", expect="Eski", value="Yeni"),
        SetOp(op="set", path="clock.ntp_server", expect="172.20.10.1", value="172.20.10.2"),
        SetOp(op="set", path="data_dir", expect="anything", value="E:/x"),  # locked: no conflict
    ]
    assert conflicts(ops, config, frozenset()) == ["site_name"]


# -- general ---------------------------------------------------------------------------------------


def test_a_site_name_change(root: Path, config: Config) -> None:
    check = validate_section("general", config, {"site_name": "  Örnek Holding  "}, context(root))
    assert check.ok
    assert check.ops == (
        {"op": "set", "path": "site_name", "expect": "Örnek A.Ş.", "value": "Örnek Holding"},
    )
    assert check.changes == {"site_name": {"old": "Örnek A.Ş.", "new": "Örnek Holding"}}
    assert not check.reauth
    assert set(check.effects) == {"reload.api", "reload.worker"}
    assert check.probes == ()
    assert check.candidate is not None
    assert check.candidate.site_name == "Örnek Holding"
    assert not check.guarded
    assert check.alert is None
    assert not check.system_sync


def test_nothing_changed_gives_no_ops(root: Path, config: Config) -> None:
    check = validate_section("general", config, {"site_name": "Örnek A.Ş."}, context(root))
    assert check.ok
    assert check.ops == ()
    assert check.edits == ()


@pytest.mark.parametrize(
    ("body", "code", "field"),
    [
        ({"site_name": ""}, "site_name_required", "site_name"),
        ({"site_name": "   "}, "site_name_required", "site_name"),
        ({"site_name": "iki\nsatır"}, "config_invalid", "site_name"),
        ({"site_name": "biçim\u200bkarakteri"}, "config_invalid", "site_name"),
        ({"site_name": "x" * 201}, "config_invalid", "site_name"),
        ({"site_name": 7}, "config_invalid", "site_name"),
        ({"clock": {"ntp_server": "10.0.3"}}, "host_invalid", "clock.ntp_server"),
        ({"clock": {"ntp_server": "256.1.1.1"}}, "host_invalid", "clock.ntp_server"),
        ({"clock": {"ntp_server": "kötü host"}}, "host_invalid", "clock.ntp_server"),
        ({"clock": {"fallback_server": "-x"}}, "host_invalid", "clock.fallback_server"),
        ({"clock": {"ntp_server": "", "fallback_server": ""}}, "clock_servers_required",
         "clock.fallback_server"),
        ({"clock": {"alert_offset_ms": 0}}, "config_invalid", "clock.alert_offset_ms"),
        ({"clock": {"alert_offset_ms": 60_001}}, "config_invalid", "clock.alert_offset_ms"),
        ({"clock": "172.20.10.2"}, "config_invalid", "clock"),
    ],
)  # fmt: skip
def test_general_refusals(
    root: Path, config: Config, body: Mapping[str, Any], code: str, field: str
) -> None:
    check = validate_section("general", config, body, context(root))
    assert [(p.code, p.field) for p in check.errors] == [(code, field)]
    assert check.edits == ()
    assert check.candidate is None
    submitted = [v for v in body.values() if isinstance(v, str) and v.strip()]
    assert all(v not in str(p.details) for p in check.errors for v in submitted)  # no echo


def test_a_clock_change_needs_reauthentication_and_is_probed(root: Path, config: Config) -> None:
    body = {"clock": {"ntp_server": "172.20.10.2", "fallback_server": "time.ume.tubitak.gov.tr"}}
    check = validate_section("general", config, body, context(root))
    assert check.ok
    assert check.reauth
    assert check.probes == (rules.Probe("sntp", "172.20.10.2"),)
    assert {"system.time", "task.next_run:ClockCheck"} <= set(check.effects)
    assert check.system_sync
    no_probe = validate_section("general", config, {**body, "probe": False}, context(root))
    assert no_probe.probes == check.probes  # the agent skips them; they are still named


def test_an_empty_local_source_warns_and_strict_mode_skips_the_probe(
    root: Path, config: Config
) -> None:
    body = {"clock": {"ntp_server": "", "fallback_server": "pool.ornek.example"}}
    check = validate_section("general", config, body, context(root, strict_outbound=True))
    assert check.ok
    assert warnings(check) == ["ntp_server_empty", "probe_skipped"]
    assert check.probes == ()
    assert "system.firewall" in check.effects


def test_ipv4_addresses_and_host_names_are_valid_time_servers() -> None:
    for host in ("172.20.10.1", "time.ume.tubitak.gov.tr", "ntp", "a-b.c"):
        assert rules.host_ok(host)
    for host in ("10.0.3", "1.2.3.4.5", "999.1.1.1", "x" * 254, "a_b", "-a"):
        assert not rules.host_ok(host)


def test_the_policy_bounds_nothing_of_general(root: Path, config: Config) -> None:
    tight = AdminPolicy(enabled=True, written_at=NOW, written_by="test", max_sources=1)
    check = validate_section("general", config, {"site_name": "X"}, context(root, policy=tight))
    assert check.ok


def signed(section: str, ops: list[dict[str, Any]], **options: Any) -> AdminRequest:
    """A verified request as the agent checks it."""
    return AdminRequest.model_validate(
        {
            "id": "a" * 32, "instance": "b" * 32,
            "issued_at": NOW, "expires_at": NOW.replace(minute=20),
            "kind": "config.apply", "section": section,
            "actor": {"user_id": 3, "username": "ayse", "session": "s", "ip": "172.20.10.37"},
            "ops": ops, "options": options,
        }
    )  # fmt: skip


def with_tsa(root: Path, block: str) -> Config:
    """The configuration of ``root`` with its ``[tsa]`` table replaced by ``block``."""
    path = root / "arcilogger.toml"
    text = path.read_text(encoding="utf-8").replace('[tsa]\nprovider = "mock"\n', block)
    path.write_text(text, encoding="utf-8")
    return parse_config(path.read_bytes(), path)


ZAMANE_TEST = '[tsa]\nprovider = "zamane"\n'
ZAMANE_PRODUCTION = '[tsa]\nprovider = "zamane"\nurl = "http://zd.kamusm.gov.tr"\n'


def root_certificate(
    *,
    ca: bool = True,
    self_signed: bool = True,
    expired: bool = False,
    curve: ec.EllipticCurve | None = None,
) -> bytes:
    """A fictional root certificate (PEM) for the upload checks."""
    key = ec.generate_private_key(curve or ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Örnek Test Kök Sertifikası")])
    issuer = name if self_signed else x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Başka")])
    signer = key if self_signed else ec.generate_private_key(ec.SECP256R1())
    start = NOW - timedelta(days=800 if expired else 1)
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(start + timedelta(days=365 if expired else 3650))
    )
    if ca:
        builder = builder.add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
    return builder.sign(signer, hashes.SHA256()).public_bytes(Encoding.PEM)


def der_sha256(pem: bytes) -> str:
    return hashlib.sha256(
        x509.load_pem_x509_certificate(pem).public_bytes(Encoding.DER)
    ).hexdigest()


def client_jar(content: bytes = b"\xca\xfe\xba\xbe") -> bytes:
    """A Zamane client stand-in: a jar (a ZIP with a manifest)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n")
        archive.writestr("ornek/Client.class", content)
    return buffer.getvalue()


def distribution(jar: bytes, name: str = "tss-client-console-3.1.33.jar") -> bytes:
    """A distribution ``.zip`` with the client in a folder."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("zamane/BENIOKU.txt", "Örnek dağıtım\n")
        archive.writestr(f"zamane/{name}", jar)
    return buffer.getvalue()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def uploaded(tmp_path: Path, kind: UploadKind, data: bytes) -> UploadInfo:
    upload_id = new_id()
    path = tmp_path / f"upload-{upload_id}"
    path.write_bytes(data)
    return rules.inspect_upload(upload_id, kind, path, now=NOW)


# -- tsa: the server, the threshold, Java ----------------------------------------------------------


def test_the_server_is_one_of_the_two_kamu_sm_servers(root: Path, config: Config) -> None:
    assert codes(validate_section("tsa", config, {"server": "mock"}, context(root))) == [
        "tsa_mock_refused"
    ]
    body = {"server": "http://tsa.ornek.example"}
    assert codes(validate_section("tsa", config, body, context(root))) == ["tsa_server_invalid"]
    check = validate_section("tsa", config, {"server": "test"}, context(root))
    assert check.ok
    assert check.ops == (
        {"op": "set", "path": "tsa.provider", "expect": "mock", "value": "zamane"},
    )
    assert check.reauth
    assert {"reload.api", "reload.worker", "task.next_run:Seal"} <= set(check.effects)
    custom = signed(
        "tsa",
        [{"op": "set", "path": "tsa.url", "expect": "http://tzd.kamusm.gov.tr",
          "value": "http://tsa.ornek.example"}],
    )  # fmt: skip
    zamane = with_tsa(root, ZAMANE_TEST)
    assert codes(check_request(custom, zamane, context(root))) == ["tsa_server_invalid"]
    back = signed(
        "tsa", [{"op": "set", "path": "tsa.provider", "expect": "zamane", "value": "mock"}]
    )
    assert codes(check_request(back, zamane, context(root))) == ["tsa_mock_refused"]


def test_strict_outbound_mode_syncs_the_firewall_for_a_new_server(
    root: Path, config: Config
) -> None:
    open_mode = validate_section("tsa", config, {"server": "test"}, context(root))
    assert "system.firewall" not in open_mode.effects
    strict = validate_section(
        "tsa", config, {"server": "test"}, context(root, strict_outbound=True)
    )
    assert "system.firewall" in strict.effects
    assert strict.system_sync


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"credit_alert_threshold": -1}, "tsa.credit_alert_threshold"),
        ({"credit_alert_threshold": 1_000_001}, "tsa.credit_alert_threshold"),
        ({"credit_alert_threshold": "500"}, "tsa.credit_alert_threshold"),
        ({"timeout_seconds": 4}, "tsa.timeout_seconds"),
        ({"timeout_seconds": 601}, "tsa.timeout_seconds"),
    ],
)
def test_tsa_numbers_out_of_range(
    root: Path, config: Config, body: Mapping[str, Any], field: str
) -> None:
    check = validate_section("tsa", config, body, context(root))
    assert [(p.code, p.field) for p in check.errors] == [("config_invalid", field)]


def test_the_credit_threshold_needs_no_reauthentication(root: Path, config: Config) -> None:
    check = validate_section("tsa", config, {"credit_alert_threshold": 200}, context(root))
    assert check.ok
    assert not check.reauth
    assert check.changes == {"tsa.credit_alert_threshold": {"old": 500, "new": 200}}


JRE = "C:/Program Files/Eclipse Adoptium/jre-8.0.412.8-hotspot/bin/java.exe"


def test_java_only_from_the_detected_candidates(root: Path, config: Config) -> None:
    refused = validate_section("tsa", config, {"java": JRE}, context(root))
    assert [(p.code, p.field) for p in refused.errors] == [("java_not_detected", "tsa.java")]
    detected = context(root, java_candidates=(JRE,))
    check = validate_section("tsa", config, {"java": JRE}, detected)
    assert check.ok
    assert check.ops == ({"op": "set", "path": "tsa.java", "expect": ["java"], "value": [JRE]},)
    assert check.reauth
    extra = validate_section("tsa", config, {"java": [JRE, "-Xmx1g"]}, detected)
    assert codes(extra) == ["java_not_detected"]


# -- tsa: credentials ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"customer": "12 345"},
        {"customer": ""},
        {"customer": "1" * 65},
        {"password": "x" * 129},
        {"password": "satır\nsonu"},
        {"password": 12345},
    ],
)
def test_credentials_that_cannot_be_stored(
    root: Path, config: Config, body: Mapping[str, Any]
) -> None:
    check = validate_section("tsa", config, body, context(root))
    assert codes(check) == ["secret_invalid"]
    assert check.secret_changes == {}
    assert all(str(value) not in repr(check.errors) for value in body.values() if value)


def test_credentials_are_tokens_and_never_shown(root: Path, config: Config) -> None:
    body = {"customer": "4812", "password": "kamu-parola-örnek"}
    check = validate_section(
        "tsa", config, body, context(root, encrypt=lambda v: f"gAAAAAtoken{len(v)}")
    )
    assert check.ok
    assert [op["op"] for op in check.ops] == ["secret_set", "secret_set"]
    assert [op["token"] for op in check.ops] == ["gAAAAAtoken4", "gAAAAAtoken17"]
    assert check.changes["kamusm.password"] == {
        "secret": True,
        "old": "unset",
        "new": "set",
        "changed": True,
    }
    assert "kamu-parola" not in repr(check.changes) + repr(check.ops)
    assert check.reauth
    assert check.alert == "high"
    dry = validate_section("tsa", config, body, context(root))  # no encrypt: a dry run
    assert {op["token"] for op in dry.ops} == {rules.UNENCRYPTED}


def test_clearing_the_credentials(root: Path, config: Config) -> None:
    stored = frozenset({"kamusm.customer", "kamusm.password"})
    check = validate_section(
        "tsa", config, {"clear_credentials": True}, context(root, stored_secrets=stored)
    )
    assert check.ok
    assert [(op["op"], op["name"]) for op in check.ops] == [
        ("secret_delete", "kamusm.customer"),
        ("secret_delete", "kamusm.password"),
    ]
    assert check.secret_changes == {"kamusm.customer": None, "kamusm.password": None}
    both = {"clear_credentials": True, "password": "yeni"}
    assert codes(validate_section("tsa", config, both, context(root, stored_secrets=stored))) == [
        "config_invalid"
    ]


# -- tsa: readiness, do-not-break, the credit probe ------------------------------------------------


@pytest.fixture
def ready(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Config, RuleContext]:
    """A Zamane installation on the test server that has everything production needs."""
    monkeypatch.setenv("PATH", str(tmp_path / "no-java-here"))
    programs = tmp_path / "Program Files"
    java = programs / "Java" / "jre1.8.0_412" / "bin" / "java"
    java.parent.mkdir(parents=True)
    java.write_bytes(b"#!/bin/sh\n")
    client = client_jar()
    jar = root / "zamane" / "tss-client-console-3.1.33.jar"
    jar.parent.mkdir()
    jar.write_bytes(client)
    (root / "zamane" / "zamane-jar.sha256").write_text(
        f"{sha256(client)}  {jar.name}\n", encoding="utf-8"
    )
    pem = root_certificate()
    policy = POLICY.model_copy(update={"executable_roots": (str(programs),)})
    config = with_tsa(
        root, f'{ZAMANE_TEST}java = ["{java.as_posix()}"]\nzamane_jar = "{jar.as_posix()}"\n'
    )
    return config, context(
        root,
        policy=policy,
        anchors=TrustAnchors(root, policy),
        stored_secrets=frozenset({"kamusm.customer", "kamusm.password"}),
        installed_roots=frozenset({der_sha256(pem)}),
    )


def test_production_needs_credentials_a_client_java_and_a_root(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "no-java-here"))
    config = with_tsa(root, ZAMANE_TEST)
    check = validate_section("tsa", config, {"server": "production"}, context(root))
    assert codes(check) == ["tsa_not_ready"]
    assert check.errors[0].details == {"missing": ["credentials", "zamane_jar", "java", "roots"]}
    assert check.http_status == 409
    assert check.edits == ()


def test_a_ready_installation_switches_to_production(
    ready: tuple[Config, RuleContext], root: Path
) -> None:
    config, ready_context = ready
    check = validate_section("tsa", config, {"server": "production"}, ready_context)
    assert check.ok, check.errors
    assert check.ops == (
        {"op": "set", "path": "tsa.url", "expect": "http://tzd.kamusm.gov.tr",
         "value": "http://zd.kamusm.gov.tr"},
    )  # fmt: skip
    assert warnings(check) == ["legal_roots_empty", "production_not_tested"]
    assert check.probes == (rules.Probe("credit", None),)
    assert check.reauth
    tested = replace(ready_context, production_tested=True)
    assert warnings(validate_section("tsa", config, {"server": "production"}, tested)) == [
        "legal_roots_empty"
    ]


def test_production_back_to_test_needs_a_confirmation(root: Path) -> None:
    config = with_tsa(root, ZAMANE_PRODUCTION)
    unconfirmed = validate_section("tsa", config, {"server": "test"}, context(root))
    assert codes(unconfirmed) == ["tsa_confirm_required"]
    check = validate_section("tsa", config, {"server": "test", "confirm": True}, context(root))
    assert check.ok
    assert warnings(check) == ["production_stamps_stop"]
    assert check.alert == "high"


def preflight_of(config: Config, stored: frozenset[str]) -> list[str]:
    """A stand-in for ``seal_preflight``: credentials are all it looks at."""
    if config.tsa.provider != "zamane":
        return []
    return [] if {"kamusm.customer", "kamusm.password"} <= stored else ["credentials"]


def test_a_change_that_would_stop_a_working_sealer_is_refused(root: Path) -> None:
    config = with_tsa(root, ZAMANE_TEST)
    stored = frozenset({"kamusm.customer", "kamusm.password"})
    working = context(root, stored_secrets=stored, preflight=preflight_of)
    check = validate_section("tsa", config, {"clear_credentials": True}, working)
    assert codes(check) == ["tsa_would_break"]
    assert check.errors[0].details == {"causes": ["credentials"]}
    assert check.http_status == 409
    assert check.secret_changes == {}
    # A sealer that does not work now cannot be broken: the change is allowed.
    broken = context(root, stored_secrets=frozenset({"kamusm.customer"}), preflight=preflight_of)
    assert validate_section("tsa", config, {"customer": "4812"}, broken).ok


def test_new_credentials_are_probed_with_the_credit_query(
    ready: tuple[Config, RuleContext],
) -> None:
    config, ready_context = ready
    check = validate_section("tsa", config, {"password": "yeni-parola"}, ready_context)
    assert check.ok
    assert check.probes == (rules.Probe("credit", None),)
    untrusted = replace(
        ready_context, anchors=TrustAnchors(ready_context.anchors.root, POLICY)
    )  # Java is no longer in a trusted folder
    skipped = validate_section("tsa", config, {"password": "yeni-parola"}, untrusted)
    assert skipped.ok
    assert skipped.probes == ()
    assert warnings(skipped) == ["probe_skipped"]


# -- tsa: roots and legal fingerprints -------------------------------------------------------------


def test_a_root_is_installed_and_listed_as_legal(
    root: Path, config: Config, tmp_path: Path
) -> None:
    pem = root_certificate()
    info = uploaded(tmp_path, "kamusm_root", pem)
    fingerprint = der_sha256(pem)
    assert info.root is not None
    assert info.root.certificate is not None
    assert info.root.certificate.sha256 == fingerprint
    body = {"root": {"upload_id": info.id, "legal": True, "confirmed": True}}
    check = validate_section("tsa", config, body, context(root, uploads={info.id: info}))
    assert check.ok, check.errors
    assert check.ops == (
        {"op": "root_add", "upload": info.id, "sha256": info.sha256, "size": info.size,
         "legal": True},
    )  # fmt: skip
    assert [item.target for item in check.installs] == [
        root / "kamusm-roots" / f"{fingerprint}.pem"
    ]
    assert check.installs[0].data.startswith(b"-----BEGIN CERTIFICATE-----")
    assert check.candidate is not None
    assert check.candidate.tsa.legal_root_fingerprints == (fingerprint,)
    assert check.candidate.tsa.roots_dir == root / "kamusm-roots"
    assert check.changes[f"tsa.roots.{fingerprint}"] == {"old": None, "new": "installed"}
    assert check.reauth
    assert check.alert == "high"


def test_root_refusals(root: Path, config: Config, tmp_path: Path) -> None:
    pem = root_certificate()
    info = uploaded(tmp_path, "kamusm_root", pem)
    uploads = {info.id: info}
    again = context(root, uploads=uploads, installed_roots=frozenset({der_sha256(pem)}))
    body: dict[str, Any] = {"root": {"upload_id": info.id}}
    assert codes(validate_section("tsa", config, body, again)) == ["root_exists"]
    unconfirmed = {"root": {"upload_id": info.id, "legal": True}}
    assert codes(validate_section("tsa", config, unconfirmed, context(root, uploads=uploads))) == [
        "legal_root_confirm_required"
    ]
    assert codes(validate_section("tsa", config, {"root": {"upload_id": "x"}}, context(root))) == [
        "upload_not_found"
    ]
    for bad in (
        root_certificate(ca=False),
        root_certificate(self_signed=False),
        root_certificate(curve=ec.SECP521R1()),
        pem + root_certificate(),
        b"not a certificate",
    ):
        item = uploaded(tmp_path, "kamusm_root", bad)
        refused = validate_section(
            "tsa", config, {"root": {"upload_id": item.id}}, context(root, uploads={item.id: item})
        )
        assert [(p.code, p.field) for p in refused.errors] == [
            ("root_certificate_invalid", "tsa.roots_dir")
        ]


def test_an_expired_root_is_accepted_with_a_warning(
    root: Path, config: Config, tmp_path: Path
) -> None:
    item = uploaded(tmp_path, "kamusm_root", root_certificate(expired=True))
    check = validate_section(
        "tsa", config, {"root": {"upload_id": item.id}}, context(root, uploads={item.id: item})
    )
    assert check.ok
    assert warnings(check) == ["root_expired"]


def test_roots_are_added_only_to_the_installation_folder(root: Path, tmp_path: Path) -> None:
    config = with_tsa(root, f'[tsa]\nprovider = "mock"\nroots_dir = "{tmp_path.as_posix()}/x"\n')
    item = uploaded(tmp_path, "kamusm_root", root_certificate())
    check = validate_section(
        "tsa", config, {"root": {"upload_id": item.id}}, context(root, uploads={item.id: item})
    )
    assert [(p.code, p.field) for p in check.errors] == [("config_key_locked", "tsa.roots_dir")]


def test_a_legal_fingerprint_names_an_installed_root(root: Path, config: Config) -> None:
    fingerprint = "a" * 64
    body = {"legal_root": {"sha256": fingerprint.upper(), "confirmed": True}}
    assert codes(validate_section("tsa", config, body, context(root))) == ["root_not_installed"]
    installed = context(root, installed_roots=frozenset({fingerprint}))
    check = validate_section("tsa", config, body, installed)
    assert check.ok
    assert check.ops == ({"op": "legal_root_add", "sha256": fingerprint},)
    assert check.candidate is not None
    assert check.candidate.tsa.legal_root_fingerprints == (fingerprint,)
    assert codes(validate_section("tsa", config, {"legal_root": {"sha256": "x"}}, installed)) == [
        "root_not_installed"
    ]
    unconfirmed = {"legal_root": {"sha256": fingerprint}}
    assert codes(validate_section("tsa", config, unconfirmed, installed)) == [
        "legal_root_confirm_required"
    ]


# -- tsa: the Zamane client ------------------------------------------------------------------------


def test_an_unknown_client_needs_its_published_sha256(
    root: Path, config: Config, tmp_path: Path
) -> None:
    jar = client_jar()
    item = uploaded(tmp_path, "zamane_client", jar)
    uploads = context(root, uploads={item.id: item})
    assert codes(validate_section("tsa", config, {"zamane": {"upload_id": item.id}}, uploads)) == [
        "zamane_sha256_required"
    ]
    wrong = {"zamane": {"upload_id": item.id, "expected_sha256": "0" * 64}}
    assert codes(validate_section("tsa", config, wrong, uploads)) == ["zamane_sha256_mismatch"]
    body = {"zamane": {"upload_id": item.id, "expected_sha256": f"  {sha256(jar).upper()} "}}
    check = validate_section("tsa", config, body, uploads)
    assert check.ok, check.errors
    assert check.ops == (
        {"op": "zamane_stage", "upload": item.id, "sha256": sha256(jar), "size": len(jar),
         "jar_sha256": sha256(jar), "release": None},
    )  # fmt: skip
    assert check.jar_hold == sha256(jar)
    assert check.stage_jar == (item.id, sha256(jar))
    assert warnings(check) == ["unknown_jar_held"]
    assert "jar.promotion" in check.effects
    approved = (root / "zamane" / "approved" / f"{sha256(jar)}.jar").as_posix()
    assert check.changes["tsa.zamane_jar"] == {"old": None, "new": approved}
    assert check.candidate is not None
    assert check.candidate.tsa.zamane_jar is None  # set once the client is approved
    assert check.reauth
    assert check.alert == "high"


def test_a_known_release_in_a_distribution_zip(
    root: Path, config: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    jar = client_jar(b"known")
    archive = distribution(jar)
    monkeypatch.setattr(zamane_releases, "KNOWN_RELEASES", {sha256(jar): ZamaneRelease("3.1.33")})
    item = uploaded(tmp_path, "zamane_client", archive)
    assert item.zamane is not None
    assert (item.zamane.kind, item.zamane.jar_name) == ("zip", "tss-client-console-3.1.33.jar")
    check = validate_section(
        "tsa", config, {"zamane": {"upload_id": item.id}}, context(root, uploads={item.id: item})
    )
    assert check.ok
    assert check.ops[0]["release"] == "3.1.33"
    assert check.ops[0]["sha256"] == sha256(archive)
    assert check.ops[0]["jar_sha256"] == sha256(jar)
    assert check.jar_hold == sha256(jar)  # promoted by ApplySystem, then applied
    assert warnings(check) == []
    # Unknown, the SHA-256 a user copies may be the archive's as well as the jar's.
    monkeypatch.setattr(zamane_releases, "KNOWN_RELEASES", {})
    unknown = uploaded(tmp_path, "zamane_client", archive)
    for expected in (sha256(archive), sha256(jar)):
        body = {"zamane": {"upload_id": unknown.id, "expected_sha256": expected}}
        check = validate_section("tsa", config, body, context(root, uploads={unknown.id: unknown}))
        assert check.ok
        assert warnings(check) == ["unknown_jar_held"]


def test_an_approved_client_is_applied_at_once(root: Path, config: Config, tmp_path: Path) -> None:
    jar = client_jar(b"approved")
    item = uploaded(tmp_path, "zamane_client", jar)
    approved = context(root, uploads={item.id: item}, approved_jars=frozenset({sha256(jar)}))
    body = {"zamane": {"upload_id": item.id, "expected_sha256": sha256(jar)}}
    check = validate_section("tsa", config, body, approved)
    assert check.ok
    assert check.jar_hold is None
    assert check.candidate is not None
    assert check.candidate.tsa.zamane_jar == root / "zamane" / "approved" / f"{sha256(jar)}.jar"


def test_a_file_that_is_not_a_client_is_refused(root: Path, config: Config, tmp_path: Path) -> None:
    for data in (b"not a zip", distribution(b"no manifest"), distribution(client_jar(), "x.jar")):
        item = uploaded(tmp_path, "zamane_client", data)
        check = validate_section(
            "tsa",
            config,
            {"zamane": {"upload_id": item.id}},
            context(root, uploads={item.id: item}),
        )
        assert [(p.code, p.field) for p in check.errors] == [
            ("zamane_archive_invalid", "tsa.zamane_jar")
        ]


def test_the_agent_refuses_a_client_op_that_does_not_match_its_upload(
    root: Path, config: Config, tmp_path: Path
) -> None:
    jar = client_jar()
    item = uploaded(tmp_path, "zamane_client", jar)
    op = {"op": "zamane_stage", "upload": item.id, "sha256": sha256(jar), "size": len(jar),
          "jar_sha256": "f" * 64, "release": None}  # fmt: skip
    check = check_request(signed("tsa", [op]), config, context(root, uploads={item.id: item}))
    assert codes(check) == ["payload_mismatch"]
    resized = {**op, "jar_sha256": sha256(jar), "size": len(jar) + 1}
    check = check_request(signed("tsa", [resized]), config, context(root, uploads={item.id: item}))
    assert codes(check) == ["payload_mismatch"]


# -- sources ---------------------------------------------------------------------------------------


SOURCE = {"name": "fw-01", "type": "fortigate", "address": "172.20.10.22"}


def test_a_source_is_added(root: Path, config: Config) -> None:
    check = validate_section("sources", config, {"source": SOURCE}, context(root))
    assert check.ok, check.errors
    added = {**SOURCE, "heartbeat_minutes": 15, "expected": True, "transport": "any",
             "enabled": True}  # fmt: skip
    assert check.ops == ({"op": "source_add", "source": added},)
    assert check.changes == {"sources.fw-01": {"old": None, "new": added}}
    assert set(check.effects) == {
        "reload.collector",
        "reload.ingest",
        "reload.api",
        "system.firewall",
    }
    assert check.system_sync
    assert not check.reauth
    assert check.candidate is not None
    assert [s.name for s in check.candidate.sources] == ["core-r1", "aps", "fw-01"]


@pytest.mark.parametrize(
    ("address", "code"),
    [
        ("127.0.0.1", "source_reserved_address"),
        ("224.0.0.5", "source_reserved_address"),
        ("169.254.10.1", "source_reserved_address"),
        ("0.0.0.0", "source_reserved_address"),  # noqa: S104
        ("255.255.255.255", "source_reserved_address"),
        ("240.0.0.1", "source_reserved_address"),
        ("172.20.10.5", "source_own_address"),
        ("172.20.10.5/32", "source_own_address"),
        ("8.8.8.8", "source_network_not_allowed"),
        ("100.64.0.1", "source_network_not_allowed"),
        ("172.16.0.0/15", "source_prefix_too_short"),
        ("10.0.0.0/8", "source_prefix_too_short"),
        ("172.20.10.1", "source_address_taken"),
        ("172.20.10.1/32", "source_address_taken"),
        ("172.20.20.0/24", "source_address_taken"),
        ("172.20.20.128/25", "source_overlap"),
        ("172.20.0.0/16", "source_overlap"),
        ("fd00::1", "source_ipv6_unsupported"),
        ("2001:db8::/64", "source_ipv6_unsupported"),
        ("172.20.10", "source_invalid"),
    ],
)
def test_source_addresses_the_browser_may_not_add(
    root: Path, config: Config, address: str, code: str
) -> None:
    check = validate_section("sources", config, {"source": {**SOURCE, "address": address}},
                             context(root))  # fmt: skip
    assert [(p.code, p.field) for p in check.errors] == [(code, "sources.fw-01.address")]
    assert check.edits == ()


@pytest.mark.parametrize("address", ["172.21.0.0/16", "172.20.20.9", "192.168.1.0/24", "10.1.2.3"])
def test_source_addresses_the_browser_may_add(root: Path, config: Config, address: str) -> None:
    check = validate_section(
        "sources", config, {"source": {**SOURCE, "address": address}}, context(root)
    )
    assert check.ok, check.errors


def test_the_policy_bounds_the_source_networks_and_their_number(root: Path, config: Config) -> None:
    narrow = POLICY.model_copy(update={"source_networks": ("172.20.10.0/24",), "min_prefix_v4": 24})
    outside = {"source": {**SOURCE, "address": "172.20.30.1"}}
    assert codes(validate_section("sources", config, outside, context(root, policy=narrow))) == [
        "source_network_not_allowed"
    ]
    full = POLICY.model_copy(update={"max_sources": 2})
    assert codes(
        validate_section("sources", config, {"source": SOURCE}, context(root, policy=full))
    ) == ["source_limit"]


@pytest.mark.parametrize(
    ("body", "code", "field"),
    [
        ({"source": {**SOURCE, "name": "aps"}}, "source_name_taken", "sources.aps.name"),
        ({"source": {**SOURCE, "name": "unknown-x"}}, "source_invalid", "sources.unknown-x.name"),
        ({"source": {**SOURCE, "name": "Büyük"}}, "source_invalid", "sources"),
        ({"source": {**SOURCE, "type": "cisco"}}, "source_invalid", "sources.fw-01.type"),
        ({"source": {**SOURCE, "heartbeat_minutes": 0}}, "source_invalid",
         "sources.fw-01.heartbeat_minutes"),
        ({"source": {**SOURCE, "transport": "quic"}}, "source_invalid", "sources.fw-01.transport"),
        ({"source": "fw-01"}, "source_invalid", "sources"),
        ({"name": "nope", "source": SOURCE}, "source_not_found", "sources"),
        ({"name": "core-r1", "source": {**SOURCE, "name": "core-r2"}},
         "source_rename_not_allowed", "sources.core-r1.name"),
        ({"name": "core-r1", "remove": True, "confirm": "core"}, "source_confirm_mismatch",
         "sources.core-r1"),
    ],
)  # fmt: skip
def test_source_refusals(
    root: Path, config: Config, body: Mapping[str, Any], code: str, field: str
) -> None:
    check = validate_section("sources", config, body, context(root))
    assert [(p.code, p.field) for p in check.errors] == [(code, field)]


def test_a_source_is_edited_in_place(root: Path, config: Config) -> None:
    edited = {"type": "mikrotik", "address": "172.20.10.1", "heartbeat_minutes": 30}
    check = validate_section("sources", config, {"name": "core-r1", "source": edited},
                             context(root))  # fmt: skip
    assert check.ok, check.errors
    assert check.ops[0]["op"] == "source_update"
    expect = check.ops[0]["expect"]
    assert isinstance(expect, dict)
    assert expect["heartbeat_minutes"] == 15
    assert not check.reauth
    assert check.candidate is not None
    assert check.candidate.sources[0].heartbeat_minutes == 30
    same = {"type": "mikrotik", "address": "172.20.10.1"}
    assert validate_section("sources", config, {"name": "core-r1", "source": same},
                            context(root)).ops == ()  # fmt: skip


def test_disabling_or_removing_a_source_needs_reauthentication(root: Path, config: Config) -> None:
    off = {"type": "mikrotik", "address": "172.20.10.1", "enabled": False}
    disabled = validate_section("sources", config, {"name": "core-r1", "source": off},
                                context(root))  # fmt: skip
    assert disabled.ok
    assert disabled.reauth
    assert disabled.alert == "high"
    assert warnings(disabled) == ["source_disable_gaps"]  # an expected source
    removed = validate_section(
        "sources", config, {"name": "aps", "remove": True, "confirm": "aps"}, context(root)
    )
    assert removed.ok
    assert removed.reauth
    assert removed.alert == "high"
    assert warnings(removed) == []  # not expected: no gaps
    assert removed.changes["sources.aps"]["new"] is None
    assert removed.candidate is not None
    assert [s.name for s in removed.candidate.sources] == ["core-r1"]


def test_one_source_per_request(root: Path, config: Config) -> None:
    ops = [
        {"op": "source_add", "source": SOURCE},
        {"op": "source_add", "source": {**SOURCE, "name": "fw-02", "address": "172.20.10.23"}},
    ]
    assert codes(check_request(signed("sources", ops), config, context(root))) == ["source_invalid"]


def with_tables(root: Path, extra: str) -> Config:
    """The configuration of ``root`` with ``extra`` tables appended."""
    path = root / "arcilogger.toml"
    path.write_text(path.read_text(encoding="utf-8") + "\n" + extra, encoding="utf-8")
    return parse_config(path.read_bytes(), path)


SMTP_ON = """\
[smtp]
enabled = true
host = "smtp.ornek.example"
port = 587
security = "starttls"
username = "arcilogger"
from_address = "arcilogger@ornek.example"
"""

SMTP_BODY = {
    "enabled": True,
    "host": "smtp.ornek.example",
    "port": 587,
    "security": "starttls",
    "username": "arcilogger",
    "from_address": "arcilogger@ornek.example",
    "password": "smtp-parola",
    "test_to": "bt@ornek.example",
}

REPLICA_ON = """\
[replica]
enabled = true
target = '\\\\NAS-01\\arcilogger-worm'
user = 'NAS-01\\arcireplica'
"""


# -- smtp ------------------------------------------------------------------------------------------


def test_smtp_is_switched_on_with_a_trial_email(root: Path, config: Config) -> None:
    check = validate_section("smtp", config, SMTP_BODY, context(root))
    assert check.ok, check.errors
    assert check.probes == (rules.Probe("smtp", None),)
    assert check.options == {"test_to": "bt@ornek.example"}
    assert check.reauth
    assert "reload.worker" in check.effects
    assert check.changes["smtp.password"]["new"] == "set"
    assert check.candidate is not None
    assert check.candidate.smtp.host == "smtp.ornek.example"
    no_recipient = {key: value for key, value in SMTP_BODY.items() if key != "test_to"}
    assert [(p.code, p.field) for p in validate_section("smtp", config, no_recipient,
                                                        context(root)).errors] == [
        ("smtp_test_recipient_required", "test_to")
    ]  # fmt: skip
    assert validate_section("smtp", config, {**no_recipient, "probe": False}, context(root)).ok


def test_the_trial_recipient_is_one_the_api_allows(root: Path, config: Config) -> None:
    allowed = context(root, test_recipients=frozenset({"BT@ornek.example"}))
    assert validate_section("smtp", config, SMTP_BODY, allowed).ok  # case-insensitive
    other = {**SMTP_BODY, "test_to": "baska@ornek.example"}
    assert codes(validate_section("smtp", config, other, allowed)) == [
        "test_email_recipient_not_allowed"
    ]
    invalid = validate_section(
        "smtp", config, {**SMTP_BODY, "test_to": "not an address"}, context(root)
    )
    assert [(p.code, p.field) for p in invalid.errors] == [("config_invalid", "test_to")]


@pytest.mark.parametrize(
    ("body", "code", "field"),
    [
        ({"host": "kötü host"}, "config_invalid", "smtp.host"),
        ({"host": "10.0.3"}, "config_invalid", "smtp.host"),
        ({"port": 0}, "config_invalid", "smtp.port"),
        ({"port": 65536}, "config_invalid", "smtp.port"),
        ({"security": "tls"}, "config_invalid", "smtp.security"),
        ({"from_address": "arcilogger"}, "config_invalid", "smtp.from_address"),
        ({"timeout_seconds": 301}, "config_invalid", "smtp.timeout_seconds"),
        ({"enabled": "yes"}, "config_invalid", "smtp.enabled"),
        ({"username": "satır\nsonu"}, "config_invalid", "smtp.username"),
    ],
)  # fmt: skip
def test_smtp_values_out_of_range(
    root: Path, config: Config, body: Mapping[str, Any], code: str, field: str
) -> None:
    check = validate_section("smtp", config, body, context(root))
    assert [(p.code, p.field) for p in check.errors] == [(code, field)]


def test_an_enabled_smtp_needs_its_host_and_sender(root: Path, config: Config) -> None:
    check = validate_section("smtp", config, {"enabled": True, "test_to": "bt@ornek.example"},
                             context(root))  # fmt: skip
    assert [(p.code, p.field) for p in check.errors] == [
        ("config_invalid", "smtp.host"),
        ("config_invalid", "smtp.from_address"),
    ]


def test_a_password_is_never_sent_in_plain_text_to_another_host(root: Path, config: Config) -> None:
    plain = {**SMTP_BODY, "security": "none", "port": 25}
    assert [(p.code, p.field) for p in validate_section("smtp", config, plain,
                                                        context(root)).errors] == [
        ("smtp_plaintext_auth_refused", "smtp.security")
    ]  # fmt: skip
    local = {**plain, "host": "localhost"}
    assert validate_section("smtp", config, local, context(root)).ok
    anonymous = {key: value for key, value in plain.items() if key not in ("username", "password")}
    check = validate_section("smtp", config, anonymous, context(root))
    assert check.ok
    assert warnings(check) == ["smtp_plaintext"]


def test_a_new_endpoint_needs_the_password_again(root: Path) -> None:
    config = with_tables(root, SMTP_ON)
    stored = context(root, stored_secrets=frozenset({"smtp.password"}))
    for change in ({"host": "smtp2.ornek.example"}, {"port": 465, "security": "ssl"},
                   {"username": "baska"}):  # fmt: skip
        body = {**change, "test_to": "bt@ornek.example"}
        check = validate_section("smtp", config, body, stored)
        assert [(p.code, p.field) for p in check.errors] == [
            ("secret_rebind_required", "smtp.password")
        ], change
        assert validate_section("smtp", config, {**body, "password": "yeni"}, stored).ok
    sender = {"from_address": "uyari@ornek.example", "test_to": "bt@ornek.example"}
    check = validate_section("smtp", config, sender, stored)
    assert check.ok  # the server and the account stay: the stored password still fits
    assert check.probes == (rules.Probe("smtp", None),)


def test_a_user_name_needs_a_stored_password(root: Path, config: Config) -> None:
    body = {key: value for key, value in SMTP_BODY.items() if key != "password"}
    assert [(p.code, p.field) for p in validate_section("smtp", config, body,
                                                        context(root)).errors] == [
        ("smtp_password_required", "smtp.password")
    ]  # fmt: skip


def test_removing_the_user_name_deletes_the_password(root: Path) -> None:
    config = with_tables(root, SMTP_ON)
    stored = context(root, stored_secrets=frozenset({"smtp.password"}))
    check = validate_section(
        "smtp", config, {"username": "", "test_to": "bt@ornek.example"}, stored
    )
    assert check.ok, check.errors
    assert [op["op"] for op in check.ops] == ["set", "secret_delete"]
    assert check.secret_changes == {"smtp.password": None}


def test_switching_smtp_off_warns(root: Path) -> None:
    config = with_tables(root, SMTP_ON)
    stored = context(root, stored_secrets=frozenset({"smtp.password"}))
    check = validate_section("smtp", config, {"enabled": False}, stored)
    assert check.ok
    assert warnings(check) == ["smtp_disabled"]
    assert check.alert == "medium"
    assert check.probes == ()


def test_strict_outbound_mode_syncs_the_firewall_for_a_new_smtp_host(root: Path) -> None:
    config = with_tables(root, SMTP_ON)
    strict = context(root, stored_secrets=frozenset({"smtp.password"}), strict_outbound=True)
    body = {"host": "smtp2.ornek.example", "password": "yeni", "test_to": "bt@ornek.example"}
    check = validate_section("smtp", config, body, strict)
    assert check.ok
    assert "system.firewall" in check.effects


def test_a_company_ca_file_is_installed_by_its_hash(
    root: Path, config: Config, tmp_path: Path
) -> None:
    bundle = root_certificate() + root_certificate()
    item = uploaded(tmp_path, "smtp_ca", bundle)
    body = {**SMTP_BODY, "ca": {"upload_id": item.id}}
    check = validate_section("smtp", config, body, context(root, uploads={item.id: item}))
    assert check.ok, check.errors
    target = root / "smtp-ca" / f"{sha256(bundle)}.pem"
    assert [(i.target, i.data) for i in check.installs] == [(target, bundle)]
    assert check.candidate is not None
    assert check.candidate.smtp.ca_file == target
    bad = uploaded(tmp_path, "smtp_ca", b"not a certificate file")
    refused = validate_section(
        "smtp", config, {**SMTP_BODY, "ca": {"upload_id": bad.id}},
        context(root, uploads={bad.id: bad}),
    )  # fmt: skip
    assert [(p.code, p.field) for p in refused.errors] == [
        ("root_certificate_invalid", "smtp.ca_file")
    ]


def test_clearing_the_ca_file(root: Path) -> None:
    config = with_tables(root, SMTP_ON + f'ca_file = "{(root / "smtp-ca" / "x.pem").as_posix()}"\n')
    stored = context(root, stored_secrets=frozenset({"smtp.password"}))
    check = validate_section("smtp", config, {"clear_ca": True, "test_to": "bt@ornek.example"},
                             stored)  # fmt: skip
    assert check.ok, check.errors
    assert check.ops[0]["op"] == "smtp_ca_clear"
    assert check.candidate is not None
    assert check.candidate.smtp.ca_file is None
    assert check.probes == (rules.Probe("smtp", None),)


# -- replica ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "target",
    [
        "\\\\NAS-01\\arcilogger-worm",
        "\\\\nas.ornek.example\\arci$\\gunluk",
        "\\\\172.20.10.40\\worm\\a\\b\\",
        "//NAS-01/arcilogger-worm",
    ],
)
def test_replica_targets_that_are_shares(target: str) -> None:
    assert rules.unc_ok(target)
    assert rules.unc_host(target) == target.replace("/", "\\")[2:].split("\\")[0].lower()


@pytest.mark.parametrize(
    "target",
    [
        "D:\\replica",
        "\\\\NAS-01",
        "\\\\NAS-01\\",
        "\\\\?\\C:\\replica",
        "\\\\.\\pipe\\x",
        "\\\\?\\UNC\\NAS-01\\worm",
        "\\\\NAS-01\\worm\\..\\yukari",
        "\\\\NAS-01\\worm\\.\\x",
        "\\\\NAS-01\\worm\\a\\\\b",
        "\\\\NAS-01\\worm\\son.",
        "\\\\NAS-01\\worm\\x:y",
        "\\\\NAS-01\\wo rm",
        "\\\\kötü host\\worm",
        "\\\\10.0.3\\worm",
        "\\\\NAS-01\\worm\\a*b",
        "\\\\NAS-01\\worm\\satır\nsonu",
        "\\\\NAS-01\\" + "x" * 81,
        "\\\\NAS-01\\worm\\" + "a\\" * 130,
    ],
)
def test_replica_targets_that_are_refused(target: str) -> None:
    assert not rules.unc_ok(target)


@given(st.text(alphabet="\\/.:?*<>|$ aN0-é", max_size=40))
def test_any_accepted_target_is_a_plain_share_path(target: str) -> None:
    if rules.unc_ok(target):
        text = target.replace("/", "\\")
        assert text.startswith("\\\\")
        assert ".." not in text.split("\\")
        assert not text.startswith(("\\\\?\\", "\\\\.\\"))
        assert ":" not in text


def test_replica_users(root: Path, config: Config) -> None:
    for user in ("NAS-01\\arcireplica", "ORNEK\\yedek $", "arcireplica@ornek.example"):
        assert rules.replica_user_ok(user)
    for user in ("arcireplica", "\\arci", "NAS-01\\", "a@b", "x" * 257):
        assert not rules.replica_user_ok(user)
    body = {"enabled": True, "target": "\\\\NAS-01\\worm", "user": "arcireplica", "password": "p"}
    assert [(p.code, p.field) for p in validate_section("replica", config, body,
                                                        context(root)).errors] == [
        ("replica_user_invalid", "replica.user")
    ]  # fmt: skip


def test_a_replica_is_switched_on_and_probed(root: Path, config: Config) -> None:
    body = {"enabled": True, "target": " \\\\NAS-01\\arcilogger-worm ", "user": "NAS-01\\arci",
            "password": "replika-parola"}  # fmt: skip
    check = validate_section("replica", config, body, context(root))
    assert check.ok, check.errors
    assert check.probes == (rules.Probe("replica", "nas-01"),)
    assert check.reauth
    assert not check.guarded
    assert {"task.next_run:Replica", "reload.worker"} <= set(check.effects)
    assert codes(validate_section("replica", config, {"enabled": True}, context(root))) == [
        "config_invalid"
    ]
    target = {"enabled": True, "target": "D:\\replica"}
    assert [(p.code, p.field) for p in validate_section("replica", config, target,
                                                        context(root)).errors] == [
        ("replica_target_invalid", "replica.target")
    ]  # fmt: skip


def test_a_new_share_host_or_user_needs_the_password_again(root: Path) -> None:
    config = with_tables(root, REPLICA_ON)
    stored = context(root, stored_secrets=frozenset({"replica.password"}))
    folder = validate_section(
        "replica", config, {"target": "\\\\NAS-01\\arcilogger-worm\\yeni"}, stored
    )
    assert folder.ok  # the same host: the stored password still fits
    for change in ({"target": "\\\\NAS-02\\arcilogger-worm"}, {"user": "NAS-01\\baska"}):
        check = validate_section("replica", config, change, stored)
        assert [(p.code, p.field) for p in check.errors] == [
            ("secret_rebind_required", "replica.password")
        ], change
        assert validate_section("replica", config, {**change, "password": "yeni"}, stored).ok
    no_password = validate_section("replica", config, {"user": "NAS-01\\baska"}, context(root))
    assert codes(no_password) == ["replica_password_required"]


def test_turning_a_replica_off_or_moving_it_is_guarded(root: Path) -> None:
    config = with_tables(root, REPLICA_ON)
    stored = context(root, stored_secrets=frozenset({"replica.password"}))
    off = validate_section("replica", config, {"enabled": False}, stored)
    assert off.ok
    assert off.guarded
    assert off.alert == "high"
    assert off.probes == ()
    moved = validate_section("replica", config, {"target": "\\\\NAS-01\\baska"}, stored)
    assert moved.guarded
    assert moved.probes == (rules.Probe("replica", "nas-01"),)
    policy = POLICY.model_copy(update={"hold_hours": 4})
    assert off.effective_at(policy, NOW) == NOW + timedelta(hours=4)
    assert off.effective_at(POLICY, NOW) is None  # no hold configured


def test_strict_outbound_mode_tests_a_new_share_after_the_firewall(root: Path) -> None:
    config = with_tables(root, REPLICA_ON)
    strict = context(root, stored_secrets=frozenset({"replica.password"}), strict_outbound=True)
    body = {"target": "\\\\NAS-02\\worm", "password": "yeni"}
    check = validate_section("replica", config, body, strict)
    assert check.ok
    assert check.probes == ()
    assert warnings(check) == ["probe_skipped"]
    assert check.followups == ("replica_test",)
    assert "system.firewall" in check.effects


# -- retention -------------------------------------------------------------------------------------


@given(st.integers(min_value=-(10**6), max_value=729))
def test_every_retention_below_two_years_is_refused(days: int) -> None:
    config = Config.model_validate({"data_dir": "/srv/data", "secrets_dir": "/srv/arci"})
    check = rules._run(
        "retention",
        config,
        [SetOp(op="set", path="retention.days", expect=config.retention.days, value=days)],
        RuleContext(
            policy=POLICY,
            anchors=TrustAnchors(Path("/srv/arci"), POLICY),
            stored_secrets=frozenset(),
            now=NOW,
        ),
        test_to=None,
        probe=True,
    )
    assert codes(check) == ["retention_below_minimum"]


@pytest.mark.parametrize("days", [36_501, True, "800", 800.0])
def test_retention_values_that_are_not_days(root: Path, config: Config, days: Any) -> None:
    check = validate_section("retention", config, {"days": days}, context(root))
    assert [(p.code, p.field) for p in check.errors] == [("config_invalid", "retention.days")]


def preview(days: int) -> Any:
    return lambda config: rules.RetentionPreview(
        cutoff=NOW.date(), days_to_delete=days, oldest=None, newest=None
    )


def test_switching_retention_on_needs_the_typed_number_of_days(root: Path, config: Config) -> None:
    preview_context = context(root, retention_preview=preview(12))
    body: dict[str, Any] = {"enabled": True, "days": 760}
    assert [(p.code, p.field) for p in validate_section("retention", config, body,
                                                        preview_context).errors] == [
        ("retention_confirm_mismatch", "retention.days")
    ]  # fmt: skip
    wrong = validate_section("retention", config, {**body, "confirm_delete_days": 11},
                             preview_context)  # fmt: skip
    assert codes(wrong) == ["retention_confirm_mismatch"]
    check = validate_section("retention", config, {**body, "confirm_delete_days": 12},
                             preview_context)  # fmt: skip
    assert check.ok
    assert check.guarded
    assert check.alert == "high"
    assert check.reauth
    assert {"task.next_run:Retention", "reload.api"} <= set(check.effects)


def test_lowering_retention_is_guarded_and_raising_it_is_not(root: Path) -> None:
    config = with_tables(root, "[retention]\nenabled = true\ndays = 1000\n")
    lower = validate_section("retention", config, {"days": 800, "confirm_delete_days": 0},
                             context(root, retention_preview=preview(0)))  # fmt: skip
    assert lower.ok
    assert lower.guarded
    higher = validate_section("retention", config, {"days": 1200}, context(root))
    assert higher.ok
    assert not higher.guarded
    assert higher.alert is None
    off = validate_section("retention", config, {"enabled": False}, context(root))
    assert off.ok
    assert not off.guarded


# -- backup ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"keep_daily": 0}, "backup.keep_daily"),
        ({"keep_daily": 3661}, "backup.keep_daily"),
        ({"keep_weekly": -1}, "backup.keep_weekly"),
        ({"keep_monthly": 1201}, "backup.keep_monthly"),
        ({"min_free_gb": 100_001}, "backup.min_free_gb"),
        ({"enabled": 1}, "backup.enabled"),
    ],
)
def test_backup_values_out_of_range(
    root: Path, config: Config, body: Mapping[str, Any], field: str
) -> None:
    check = validate_section("backup", config, body, context(root))
    assert [(p.code, p.field) for p in check.errors] == [("config_invalid", field)]


def test_backup_changes(root: Path, config: Config) -> None:
    keep = validate_section("backup", config, {"keep_daily": 30, "keep_weekly": 10}, context(root))
    assert keep.ok
    assert keep.reauth
    assert set(keep.effects) == {"task.next_run:Backup"}
    off = validate_section("backup", config, {"enabled": False}, context(root))
    assert off.ok
    assert warnings(off) == ["backup_disabled"]
    assert off.alert == "medium"


# -- re-authentication and guarded changes (contract 2.3) ------------------------------------------


@pytest.mark.parametrize(
    ("section", "body", "reauth", "guarded"),
    [
        ("general", {"site_name": "Örnek Holding"}, False, False),
        ("general", {"clock": {"ntp_server": "172.20.10.2"}}, True, False),
        ("tsa", {"credit_alert_threshold": 100}, False, False),
        ("tsa", {"server": "test"}, True, False),
        ("smtp", {"enabled": True, "host": "localhost", "security": "none",
                  "from_address": "arcilogger@ornek.example", "probe": False}, True, False),
        ("replica", {"enabled": True, "target": "\\\\NAS-01\\worm", "probe": False}, True, False),
        ("retention", {"days": 900}, True, False),
        ("retention", {"enabled": True}, True, True),
        ("backup", {"keep_daily": 10}, True, False),
        ("sources", {"source": {"name": "fw-01", "type": "fortigate",
                                "address": "172.20.10.22"}}, False, False),
        ("sources", {"name": "aps", "remove": True, "confirm": "aps"}, True, False),
    ],
)  # fmt: skip
def test_which_changes_need_reauthentication_or_a_hold(
    root: Path,
    config: Config,
    section: Any,
    body: Mapping[str, Any],
    reauth: bool,
    guarded: bool,
) -> None:
    check = validate_section(section, config, body, context(root))
    assert check.ok, check.errors
    assert (check.reauth, check.guarded) == (reauth, guarded)
