"""The administration channel's names, texts, paths and file formats (ADR-0014; contract 2.4,
2.10, 2.12, 2.14)."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from arcilogger_core.admin import contract, messages
from arcilogger_core.admin.contract import (
    AdminLayout,
    AdminPolicy,
    AdminRequest,
    AgentStatus,
    DocumentError,
    JarsFile,
    JournalLine,
    RequestStatus,
    SystemJournalLine,
    SystemStatus,
    TaskResult,
    UploadMeta,
    VersionIndex,
    b64url_decode,
    b64url_encode,
    dump_document,
    dump_line,
    heartbeat_state,
    parse_document,
)
from arcilogger_core.config import Config
from arcilogger_core.layout import Layout
from arcilogger_core.secretstore import (
    DATABASE_PASSWORD,
    KAMUSM_CUSTOMER,
    KAMUSM_PASSWORD,
    REPLICA_PASSWORD,
    SMTP_PASSWORD,
    USAGE_PSEUDONYM_KEY,
)
from arcilogger_core.timeutil import SITE_TZ

NOW = datetime(2026, 10, 25, 10, 15, tzinfo=SITE_TZ)
SHA_A = "a1" * 32
SHA_B = "b2" * 32


def document(model: type[contract._Model], value: Any) -> Any:
    return parse_document(model, json.dumps(value, ensure_ascii=False).encode("utf-8"))


# -- names and texts ------------------------------------------------------------------------------


def test_every_state_has_both_labels_and_the_terminal_ones_are_states() -> None:
    assert set(messages.STATE_LABELS) == set(contract.STATES)
    assert contract.TERMINAL_STATES.issubset(contract.STATES)
    assert {"queued", "applied", "pending", "dispatched"}.isdisjoint(contract.TERMINAL_STATES)
    assert len(contract.STATES) == 20
    assert len(contract.STEPS) == 20


@pytest.mark.parametrize("catalogue", [messages.MESSAGES, messages.WARNINGS, messages.STATE_LABELS])
def test_every_text_is_given_in_turkish_and_english(catalogue: Any) -> None:
    for code, (turkish, english) in catalogue.items():
        assert turkish.strip(), code
        assert english.strip(), code
        assert turkish != english, code
        assert "uyumlu" not in turkish.lower(), "no compliance claim"
        assert "complian" not in english.lower(), "no compliance claim"


def test_codes_and_warnings_are_distinct_and_cover_the_channel() -> None:
    assert set(messages.MESSAGES).isdisjoint(messages.WARNINGS)
    for code in (
        "signature_invalid",
        "schema_invalid",
        "request_expired",
        "request_replayed",
        "installation_mismatch",
        "payload_mismatch",
        "reauth_missing",
        "executable_refused",
        "task_timeout",
    ):
        assert messages.texts(code) == messages.MESSAGES[code]
    assert messages.texts("change_held") == messages.WARNINGS["change_held"]
    with pytest.raises(KeyError):
        messages.texts("config_conflict")  # an HTTP error of the API, not of the channel


def test_the_browser_secrets_are_the_stored_names_and_never_the_others() -> None:
    assert set(contract.BROWSER_SECRETS) == {
        KAMUSM_CUSTOMER,
        KAMUSM_PASSWORD,
        SMTP_PASSWORD,
        REPLICA_PASSWORD,
    }
    assert {DATABASE_PASSWORD, USAGE_PSEUDONYM_KEY}.isdisjoint(contract.BROWSER_SECRETS)


def test_kinds_and_tasks() -> None:
    assert set(contract.ACTION_TASKS) == {
        k for k in contract.REQUEST_KINDS if k.startswith("action.")
    }
    assert set(contract.ACTION_TASKS.values()) == set(contract.TASK_KINDS)
    assert set(contract.ADMIN_REQUEST_KINDS) == {
        *contract.REQUEST_KINDS,
        "worker.test_email",
        "worker.lease_rebuild",
    }
    assert [contract.task_queue(kind) for kind in contract.TASK_KINDS] == [
        "seal",
        "tasks",
        "tasks",
        "tasks",
    ]
    assert set(get_args(contract.Section)) == {
        "general",
        "tsa",
        "smtp",
        "replica",
        "retention",
        "backup",
        "sources",
    }


def test_event_ids() -> None:
    assert contract.EVENT_SOURCE == "ARCILOGGER-Admin"
    assert sorted(int(event) for event in contract.Event) == [
        4100, 4101, 4102, 4103, 4104, 4105, 4106, 4107, 4110, 4111, 4120, 4121, 4199,
    ]  # fmt: skip


def test_heartbeat_thresholds() -> None:
    assert heartbeat_state(None, NOW) == "missing"
    assert heartbeat_state(NOW - timedelta(seconds=30), NOW) == "ok"
    assert heartbeat_state(NOW - timedelta(seconds=31), NOW) == "stale"
    assert heartbeat_state(NOW - timedelta(seconds=120), NOW) == "stale"
    assert heartbeat_state(NOW - timedelta(seconds=121), NOW) == "missing"


# -- paths ----------------------------------------------------------------------------------------


def test_the_admin_folder_follows_the_secret_store(tmp_path: Path) -> None:
    config = Config(data_dir=tmp_path / "data", secrets_dir=tmp_path / "pd")
    admin = AdminLayout(config.admin_dir)
    root = tmp_path / "pd" / "admin"
    rid = "6f1c1a2b3c4d5e6f708192a3b4c5d6e7"
    found = [
        admin.policy,
        admin.keys,
        admin.request_file(rid),
        admin.upload_data(rid),
        admin.upload_meta(rid),
        admin.work_dir(rid),
        admin.pending_file(rid),
        admin.txn,
        admin.ledger,
        admin.version_dir(17),
        admin.versions_index,
        admin.agent_status,
        admin.request_status(rid),
        admin.secret_meta,
        admin.alerts,
        admin.journal_file(date(2026, 10, 25)),
        admin.system_status,
        admin.jars,
        admin.system_journal,
        admin.system_alerts,
    ]
    assert [path.relative_to(root).as_posix() for path in found] == [
        "policy.json",
        "keys",
        f"inbox/{rid}.req",
        f"inbox/uploads/{rid}.bin",
        f"inbox/uploads/{rid}.json",
        f"work/{rid}",
        f"pending/{rid}.json",
        "state/txn.json",
        "state/ledger.json",
        "versions/17",
        "versions/index.json",
        "status/agent.json",
        f"status/{rid}.json",
        "status/secret-meta.json",
        "status/alerts.jsonl",
        "journal/2026-10-25.jsonl",
        "system/status.json",
        "system/jars.json",
        "system/journal.jsonl",
        "system/alerts.jsonl",
    ]


@pytest.mark.parametrize("bad", ["..", "../../secret.key", "ABCDEF" * 6, "", "a" * 31])
def test_paths_are_built_from_ids_only(tmp_path: Path, bad: str) -> None:
    admin = AdminLayout(tmp_path)
    for build in (admin.request_file, admin.work_dir, admin.request_status, admin.upload_data):
        with pytest.raises(ValueError, match="not an id"):
            build(bad)
    with pytest.raises(ValueError, match="start at 1"):
        admin.version_dir(0)


def test_request_folders_of_the_data_root(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    requests = tmp_path / "state" / "requests"
    assert layout.requests == requests
    assert layout.request_dir("seal") == requests / "seal"
    assert layout.request_dir("tasks") == requests / "tasks"
    assert layout.request_results == requests / "results"
    assert layout.request_seen == requests / "seen"
    assert layout.ingest_status == tmp_path / "state" / "ingest-status.json"
    assert layout.worker_status == tmp_path / "state" / "worker-status.json"


# -- strict documents -----------------------------------------------------------------------------


def policy_sample() -> dict[str, Any]:
    """The example of contract 2.4.1 (with a real hash)."""
    return {
        "schema": "arcilogger.admin-policy/1",
        "enabled": True,
        "source_networks": ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"],
        "min_prefix_v4": 16,
        "max_sources": 500,
        "hold_hours": 0,
        "jar_hold_hours": 24,
        "executable_roots": ["C:\\Program Files", "C:\\Program Files (x86)"],
        "trusted_executables": [
            {
                "path": "D:\\Tools\\java\\bin\\java.exe",
                "sha256": SHA_A,
                "argv": ["D:\\Tools\\java\\bin\\java.exe", "-Xmx256m"],
            }
        ],
        "tsa_test_per_day": 3,
        "written_at": "2026-10-25T10:00:00+03:00",
        "written_by": "installer",
    }


def test_the_policy_example_parses_and_round_trips() -> None:
    policy = document(AdminPolicy, policy_sample())
    assert policy.trusted_executables[0].argv == ("D:\\Tools\\java\\bin\\java.exe", "-Xmx256m")
    assert parse_document(AdminPolicy, dump_document(policy)) == policy
    minimal = document(
        AdminPolicy,
        {"schema": contract.POLICY_SCHEMA, "enabled": False, "written_at": NOW.isoformat(),
         "written_by": "installer"},
    )  # fmt: skip
    assert (minimal.hold_hours, minimal.jar_hold_hours, minimal.min_prefix_v4) == (0, 24, 16)
    assert minimal.source_networks == ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")


@pytest.mark.parametrize(
    "changes",
    [
        {"hold_hours": 169},
        {"jar_hold_hours": 0},
        {"min_prefix_v4": 7},
        {"max_sources": 0},
        {"tsa_test_per_day": 21},
        {"source_networks": ["10.0.0.1/8"]},
        {"source_networks": ["2001:db8::/32"]},
        {"enabled": "true"},
        {"written_at": "2026-10-25T10:00:00"},
        {"surprise": 1},
    ],
    ids=lambda changes: next(iter(changes)),
)
def test_an_invalid_policy_is_refused(changes: dict[str, Any]) -> None:
    with pytest.raises(DocumentError):
        document(AdminPolicy, {**policy_sample(), **changes})


@pytest.mark.parametrize(
    "data",
    [
        b'{"schema": "arcilogger.admin-policy/1", "enabled": true, "enabled": false}',
        b'{"schema": "arcilogger.admin-policy/1", "enabled": true, "hold_hours": 1.0}',
        b'{"schema": "arcilogger.admin-policy/1", "enabled": true, "hold_hours": NaN}',
        b'{"enabled": true, "written_at": "2026-10-25T10:00:00+03:00", "written_by": "x"}',
        b'{"schema": "arcilogger.admin-policy/1", "enabled": 1}',
        b"\xef\xbb\xbf{}",
        b"[]",
    ],
    ids=["duplicate", "float", "nan", "no-schema", "int-as-bool", "bom", "array"],
)
def test_documents_are_parsed_strictly(data: bytes) -> None:
    with pytest.raises(DocumentError):
        parse_document(AdminPolicy, data)


def test_base64url_is_decoded_strictly() -> None:
    assert b64url_decode(b64url_encode(b"\x00\xff" * 16), 32) == b"\x00\xff" * 16
    for bad in ("AA==", "A+/A", "AAAAA", "A B", "AB"):  # padding, alphabet, length, bits
        with pytest.raises(ValueError, match="base64url"):
            b64url_decode(bad)
    with pytest.raises(ValueError, match="32 bytes"):
        b64url_decode(b64url_encode(b"x" * 31), 32)


def test_request_shapes() -> None:
    actor = {"user_id": 3, "username": "ayse", "session": "3f9c0a1b2c4d", "ip": "172.20.10.37"}
    base = {
        "id": "6f1c" * 8,
        "instance": "5e0c" * 8,
        "issued_at": NOW,
        "expires_at": NOW + timedelta(minutes=10),
        "actor": actor,
    }
    set_op = {"op": "set", "path": "site_name", "expect": "", "value": "Örnek A.Ş."}
    assert AdminRequest.model_validate({**base, "kind": "config.apply", "section": "general",
                                        "ops": [set_op]})  # fmt: skip
    for bad in (
        {"kind": "config.apply", "ops": [set_op]},  # no section
        {"kind": "config.apply", "section": "general"},  # no op
        {"kind": "config.revert"},  # no target
        {"kind": "action.seal"},  # no day
        {"kind": "action.credit", "options": {"day": "2026-10-24"}},
        {"kind": "action.credit", "section": "tsa"},
        {"kind": "action.credit", "ops": [set_op]},
        {"kind": "action.credit", "expires_at": NOW + timedelta(minutes=11)},
        {"kind": "action.credit", "actor": {**actor, "ip": "not-an-address"}},
    ):
        with pytest.raises(ValidationError):
            AdminRequest.model_validate({**base, **bad})


def test_task_results_match_their_kind_and_code() -> None:
    base = {
        "schema": contract.TASK_RESULT_SCHEMA,
        "id": "0a" * 16,
        "started_at": "2026-10-25T10:15:00+03:00",
        "finished_at": "2026-10-25T10:15:42+03:00",
    }
    credit = {"credits": 1234, "threshold": 500, "below_threshold": False}
    assert document(TaskResult, {**base, "kind": "credit", "ok": True, "result": credit})
    tsa = {
        "provider": "zamane", "legal_grade_claimed": False, "legal_grade": False,
        "root_sha256": SHA_A, "root": "CN=Örnek Kök", "verified": True, "errors": [],
        "warnings": [], "gen_time": "2026-10-25T07:15:41+00:00", "serial": "4660",
        "tsa": "CN=Örnek TSA", "tsa_cert_not_after": "2030-01-01T00:00:00+00:00",
        "container": "TimeStampResp",
    }  # fmt: skip
    assert document(TaskResult, {**base, "kind": "tsa_test", "ok": True, "result": tsa})
    failed = {**base, "kind": "seal", "ok": False, "code": "seal_failed"}
    assert document(TaskResult, failed).code == "seal_failed"
    for bad in (
        {**base, "kind": "seal", "ok": True, "result": credit},  # a credit result for a seal
        {**base, "kind": "seal", "ok": False},  # failed without a code
        {**base, "kind": "credit", "ok": True, "code": "credit_query_failed"},
        {**base, "kind": "seal", "ok": False, "code": "no_such_code"},
        {**base, "kind": "tsa_test", "ok": True, "result": {**tsa, "token": "C:\\x.zd"}},
    ):
        with pytest.raises(DocumentError):
            document(TaskResult, bad)


def test_the_agent_heartbeat_example_parses() -> None:
    heartbeat = {
        "schema": "arcilogger.admin-agent/1",
        "version": "0.1.0",
        "pid": 4242,
        "started_at": "2026-10-25T09:00:00+03:00",
        "heartbeat_at": "2026-10-25T10:15:00+03:00",
        "state": "idle",
        "current_request": None,
        "queue": 0,
        "pending": [{"id": "6f1c" * 8, "effective_at": "2026-10-26T10:15:00+03:00"}],
        "policy_sha256": SHA_A,
        "key_ids": ["api-3f0c9e1a"],
        "java_candidates": ["C:/Program Files/Eclipse Adoptium/jre-8.0.432.6-hotspot/bin/java.exe"],
        "local_addresses": ["172.20.10.5"],
        "seal_ready": {"ok": True, "causes": []},
        "system_task": {"ok": True, "checked_at": "2026-10-25T09:01:00+03:00", "error": None},
    }
    status = document(AgentStatus, heartbeat)
    assert parse_document(AgentStatus, dump_document(status)) == status
    with pytest.raises(DocumentError):
        document(AgentStatus, {**heartbeat, "state": "sleeping"})


def test_a_request_status_carries_codes_only() -> None:
    status = {
        "id": "6f1c" * 8,
        "kind": "config.apply",
        "section": "smtp",
        "state": "failed",
        "steps": [
            {"step": "probe", "state": "done", "at": "2026-10-25T10:15:03+03:00", "code": None},
            {"step": "write_config", "state": "failed", "code": "write_failed"},
            {"step": "rollback", "state": "done"},
        ],
        "version": {"number": 17, "from": SHA_A, "to": SHA_B},
        "error": {"code": "write_failed"},
        "warnings": [{"code": "smtp_plaintext"}],
        "rolled_back": True,
        "updated_at": "2026-10-25T10:15:04+03:00",
    }
    parsed = document(RequestStatus, status)
    assert parsed.version is not None
    assert (parsed.version.from_, parsed.version.to) == (SHA_A, SHA_B)
    assert json.loads(dump_document(parsed))["version"] == {
        "number": 17,
        "from": SHA_A,
        "to": SHA_B,
    }
    for bad in (
        {**status, "error": {"code": "smtp_plaintext"}},  # a warning is not an outcome
        {**status, "warnings": [{"code": "write_failed"}]},
        {**status, "error": {"code": "write_failed", "message_tr": "…"}},  # texts: the API's
        {**status, "state": "done"},
        {**status, "steps": [{"step": "reload.printer", "state": "done"}]},
    ):
        with pytest.raises(DocumentError):
            document(RequestStatus, bad)


def journal_sample() -> dict[str, Any]:
    """The example of contract 2.4.9."""
    return {
        "v": 1,
        "line": "1c" * 16,
        "request": "6f1c" * 8,
        "state": "applied",
        "at": "2026-10-25T10:15:05+03:00",
        "kind": "config.apply",
        "section": "smtp",
        "requested_by": {"user_id": 3, "username": "ayse"},
        "version": 17,
        "config_sha256": {"from": SHA_A, "to": SHA_B},
        "changes": {
            "smtp.host": {"old": "mail.ornek.example", "new": "smtp.ornek.example"},
            "smtp.password": {"secret": True, "old": "set", "new": "set", "changed": True},
        },
        "secrets": [{"name": "smtp.password", "action": "set", "result": "stored"}],
        "code": None,
        "rolled_back": False,
        "result": None,
    }


def test_the_journal_example_parses_and_never_holds_a_secret_value() -> None:
    line = document(JournalLine, journal_sample())
    assert dump_line(line).endswith(b"\n")
    assert dump_line(line).count(b"\n") == 1
    assert parse_document(JournalLine, dump_line(line)) == line
    leaking = journal_sample()
    leaking["changes"]["smtp.password"] = {"old": "eski-parola", "new": "yeni-parola"}
    with pytest.raises(DocumentError, match="secret"):
        document(JournalLine, leaking)
    with pytest.raises(DocumentError):
        document(JournalLine, {**journal_sample(), "v": True})
    with pytest.raises(DocumentError):
        document(JournalLine, {**journal_sample(), "state": "active"})


def test_the_remaining_formats_parse() -> None:
    upload = {
        "schema": "arcilogger.admin-upload/1",
        "id": "0b" * 16,
        "kind": "kamusm_root",
        "sha256": SHA_A,
        "size": 1500,
        "created_at": "2026-10-25T10:14:00+03:00",
        "user_id": 3,
    }
    assert document(UploadMeta, upload)
    with pytest.raises(DocumentError, match="at most"):
        document(UploadMeta, {**upload, "size": 64 * 1024 + 1})
    assert document(UploadMeta, {**upload, "kind": "zamane_client", "size": 25 * 1024 * 1024})
    versions = {
        "schema": "arcilogger.admin-versions/1",
        "versions": [
            {"n": 17, "at": "2026-10-25T10:15:04+03:00", "request": "6f1c" * 8, "actor": "ayse",
             "paths": ["smtp.host", "smtp.password"], "toml_sha256": SHA_A,
             "secrets_sha256": SHA_B, "has_secrets": True},
        ],
    }  # fmt: skip
    assert document(VersionIndex, versions).versions[0].n == 17
    system = {
        "schema": "arcilogger.admin-system/1",
        "at": "2026-10-25T10:16:00+03:00",
        "ok": False,
        "code": "system_sync_failed",
        "applied_config_sha256": SHA_A,
        "collector_ack_sha256": None,
        "firewall": {"managed": True, "strict_outbound": False,
                     "syslog_addresses": ["172.20.10.1", "172.20.20.0/24"], "outbound_hosts": []},
        "time": {"peers": "172.20.10.1,0x8 time.ume.tubitak.gov.tr,0xa", "changed_at": None},
        "jars": {"staged": 1, "promoted": 0},
    }  # fmt: skip
    assert document(SystemStatus, system).code == "system_sync_failed"
    jars: dict[str, Any] = {
        "schema": "arcilogger.admin-jars/1",
        "staged": [{"sha256": SHA_A, "first_seen": "2026-10-25T10:16:00+03:00"}],
        "approved": [{"sha256": SHA_B, "path": "C:\\ProgramData\\ARCILOGGER\\zamane\\approved\\"
                      f"{SHA_B}.jar", "approved_at": "2026-10-26T10:16:00+03:00",
                      "approved_by": "console:bt-yonetici"}],
    }  # fmt: skip
    assert document(JarsFile, jars).approved[0].approved_by == "console:bt-yonetici"
    with pytest.raises(DocumentError):
        document(JarsFile, {**jars, "approved": [{**jars["approved"][0], "approved_by": "api"}]})
    firewall = {
        "v": 1,
        "line": "2d" * 16,
        "at": "2026-10-25T10:16:00+03:00",
        "change": "firewall",
        "firewall": {"syslog_addresses_count": 2, "outbound_rules": 0},
    }
    assert document(SystemJournalLine, firewall)
    with pytest.raises(DocumentError, match="exactly"):
        document(SystemJournalLine, {**firewall, "change": "time"})
