"""``GET /api/v1/legal-status`` (Wave 5 contract 2.3.1): every reason with its level, text, count,
time and link, the provider labels, the order of the reasons, every role, the 60 s cache, no
audit entry, no data about log subjects and no licence gate.

The state files of the neighbour packages are written as their writers write them: the
re-stamping report with the per-token fields of 2.1.2, the revocation list index as the CrlFetch
task of 2.2 does, and the scheduled verification with the day-check text of 2.6."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api import deps
from arcilogger_api.legalstatus import routes, texts
from arcilogger_api.legalstatus.compute import compute_legal_status, late_unstamped_days
from arcilogger_api.legalstatus.models import LegalReason, LegalStatus
from arcilogger_api.legalstatus.options import legal_options
from arcilogger_api.testing import LoggedIn
from arcilogger_core.config import KAMUSM_PRODUCTION_HOST, KAMUSM_TEST_HOST, Config
from arcilogger_core.layout import Layout
from arcilogger_core.license.testing import base_only_status, override_status
from arcilogger_core.seal import Sealer
from arcilogger_core.timeutil import isoformat_site
from arcilogger_db.models import AuditLog, Lease, Person
from arcilogger_worker.alerts.system import CRL_INDEX_SCHEMA
from arcilogger_worker.vaulttesting import SealedVault, at_site, make_vault

Login = Callable[..., LoggedIn]
API = "/api/v1/legal-status"
#: 2 November 2026, 09:00 site time.
NOW = at_site(date(2026, 11, 2), 9, 0)
FINGERPRINT = "ab" * 32
ISSUER_SHA = "a1" * 32
TSA_SUBJECT = "CN=Örnek Test TSA 1,O=Örnek A.Ş.,C=TR"
CONTRACT = Path(__file__).resolve().parents[2] / "design" / "wave-5-contract.md"
CLAIMS = re.compile(r"uyumlu|uyumu|garanti|complian|guarantee", re.IGNORECASE)


def write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def tsa_config(config: Config, **tsa: Any) -> Config:
    """``config`` with other ``[tsa]`` values (not validated: a provider or a Wave 5 key that
    the configuration does not know yet reads as W5-T will give it)."""
    return config.model_copy(update={"tsa": config.tsa.model_copy(update=tsa)})


def legal(config: Config, **tsa: Any) -> Config:
    """The Kamu SM production server with a legal root fingerprint: no reason of its own."""
    values = {
        "provider": "zamane",
        "url": f"https://{KAMUSM_PRODUCTION_HOST}",
        "legal_root_fingerprints": (FINGERPRINT,),
        **tsa,
    }
    return tsa_config(config, **values)


@dataclass
class Site:
    """The app's data directory and the configuration ``GET /legal-status`` sees."""

    app: FastAPI
    config: Config
    vaults: list[SealedVault] = field(default_factory=list)

    @property
    def layout(self) -> Layout:
        return Layout(self.config.data_dir)

    def use(self, config: Config) -> Config:
        self.config = config
        self.app.dependency_overrides[deps.get_config] = lambda: config
        return config

    def vault(self) -> SealedVault:
        """29 October sealed, 30 October written and never stamped (57 hours after its end at
        ``NOW``), 31 October and 1 November closed without data, not yet late."""
        sealed = make_vault(self.config.data_dir, tsa_now=at_site(date(2026, 9, 29)))
        self.vaults.append(sealed)
        sealed.seal_days([date(2026, 10, 29)])
        sealed.write_day(date(2026, 10, 30))
        return sealed

    def renewals(self, *tokens: dict[str, Any]) -> None:
        totals = dict.fromkeys(("not_due", "due", "covered", "late", "uncovered"), 0)
        for entry in tokens:
            totals[entry["status"]] += 1
        write(self.layout.state / "renewal.json", {"tokens": list(tokens), "totals": totals})

    def revoked(self, subject: str = TSA_SUBJECT) -> None:
        error = (
            f"A time-stamp certificate is on a revocation list: {subject}. Contact the provider; "
            "legal review is recommended."
        )
        check = {"day": "2026-10-29", "ok": False, "errors": [error], "warnings": []}
        write(
            self.layout.state / "verify" / "last-1.json",
            {"at": isoformat_site(NOW - timedelta(hours=6), "seconds"), "checks": [check]},
        )

    def crl_index(self, this_update: datetime) -> None:
        entry = {
            "file": f"{ISSUER_SHA[:12]}-{this_update:%Y%m%dT%H%M%S}Z-89abcdef.crl",
            "issuer": "CN=Örnek Test TSA Kök,O=Örnek A.Ş.,C=TR",
            "issuer_sha256": ISSUER_SHA,
            "this_update": this_update.astimezone(UTC).isoformat(),
            "next_update": None,
            "sha256": "c3" * 32,
            "url": "http://192.0.2.10/crl/root.crl",
            "fetched_at": isoformat_site(NOW - timedelta(hours=6), "seconds"),
        }
        failure = {
            "url": "http://198.51.100.7/ca.crl",
            "at": isoformat_site(NOW - timedelta(hours=6), "seconds"),
            "error": "unreachable",
        }
        write(
            self.layout.state / "crl" / "index.json",
            {"schema": CRL_INDEX_SCHEMA, "lists": [entry], "failures": [failure]},
        )


def token(day: str, status: str, not_after: datetime) -> dict[str, Any]:
    year, month, number = day.split("-")
    return {
        "path": f"{year}/{month}/{number}/manifest-{day}.json.zd",
        "kind": "day",
        "cert_not_after": not_after.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": status,
        "covered_by": "renewals/2026-10-20-1.json" if status in ("covered", "late") else None,
    }


@pytest.fixture
def site(app: FastAPI, api_config: Config, monkeypatch: pytest.MonkeyPatch) -> Iterator[Site]:
    monkeypatch.setattr(routes, "utc_now", lambda: NOW)
    found = Site(app, api_config)
    yield found
    for vault in found.vaults:
        vault.unlock_all()


@pytest.fixture
def admin(client: TestClient, login_as: Login) -> TestClient:
    login_as(client, "admin")
    return client


def get(client: TestClient) -> dict[str, Any]:
    response = client.get(API)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def codes(body: dict[str, Any]) -> list[str]:
    return [reason["code"] for reason in body["reasons"]]


def audit_count(factory: sessionmaker[Session]) -> int:
    with factory() as session:
        return session.scalar(select(func.count()).select_from(AuditLog)) or 0


# -- the reasons ----------------------------------------------------------------------------------


def test_legal_status_ok_with_a_legal_provider_and_nothing_open(
    site: Site, admin: TestClient
) -> None:
    site.use(legal(site.config))
    assert get(admin) == {
        "level": "ok",
        "provider": "zamane",
        "provider_label": "Kamu SM",
        "reasons": [],
        "checked_at": "2026-11-02T09:00:00+03:00",
    }


def _provider_not_legal(site: Site) -> None:
    site.use(site.config)  # the test configuration: the mock TSA


def _credentials_in_clear(site: Site) -> None:
    site.use(legal(site.config, provider="turktrust", url="http://198.51.100.20/"))


def _legal_root_missing(site: Site) -> None:
    site.use(legal(site.config, legal_root_fingerprints=()))


def _unstamped_days(site: Site) -> None:
    site.vault()
    site.use(legal(site.config))


def _renewal_due(site: Site) -> None:
    site.use(legal(site.config))
    site.renewals(
        token("2026-10-01", "due", datetime(2027, 3, 1, tzinfo=UTC)),
        token("2026-10-02", "due", datetime(2027, 2, 1, tzinfo=UTC)),
        token("2026-10-03", "not_due", datetime(2028, 2, 1, tzinfo=UTC)),
        token("2026-09-01", "covered", datetime(2026, 12, 1, tzinfo=UTC)),
        token("2026-08-01", "late", datetime(2026, 10, 1, tzinfo=UTC)),
    )


def _renewal_uncovered(site: Site) -> None:
    site.use(legal(site.config))
    site.renewals(token("2026-04-01", "uncovered", datetime(2026, 10, 30, tzinfo=UTC)))


def _certificate_revoked(site: Site) -> None:
    site.use(legal(site.config))
    site.revoked()


def _crl_stale(site: Site) -> None:
    site.use(legal(site.config))
    site.crl_index(NOW - timedelta(days=8))


REASONS: dict[str, tuple[Callable[[Site], None], dict[str, Any]]] = {
    "provider_not_legal": (
        _provider_not_legal,
        {
            "level": "danger",
            "message_tr": "Zaman damgaları deneme ya da test sağlayıcısından alınıyor; bu "
            "damgaların yasal geçerliliği yoktur. Ayarlar → Zaman damgası'ndan Kamu SM üretim "
            "sunucusunu seçin.",
            "message_en": "Time stamps come from a trial or test provider and have no legal "
            "validity. Choose the Kamu SM production server under Settings → Time stamping.",
            "since": None,
            "count": None,
            "link": "settings-tsa",
        },
    ),
    "credentials_in_clear": (
        _credentials_in_clear,
        {
            "level": "warning",
            "message_tr": "TÜRKTRUST bağlantısı şifresiz HTTP ile yapılıyor; kullanıcı adı ve "
            "parola ağda açık metin olarak gider. Mümkünse HTTPS'e dönün.",
            "message_en": "The TÜRKTRUST connection uses unencrypted HTTP; the username and "
            "password cross the network in clear text. Switch back to HTTPS if you can.",
            "since": None,
            "count": None,
            "link": "settings-tsa",
        },
    ),
    "legal_root_missing": (
        _legal_root_missing,
        {
            "level": "danger",
            "message_tr": "Kök sertifika parmak izi tanımlı değil; hiçbir gün yasal nitelikli "
            "gösterilemez.",
            "message_en": "No legal root fingerprint is set; no day can be shown as legal grade.",
            "since": None,
            "count": None,
            "link": "settings-tsa",
        },
    ),
    "unstamped_days": (
        _unstamped_days,
        {
            "level": "danger",
            "message_tr": "1 kapanmış gün 48 saatten uzun süredir damgalanmadı.",
            "message_en": "1 closed day(s) have not been time-stamped for more than 48 hours.",
            # the end of 30 October plus 48 hours
            "since": "2026-11-02T00:00:00+03:00",
            "count": 1,
            "link": "sealing",
        },
    ),
    "renewal_due": (
        _renewal_due,
        {
            "level": "warning",
            "message_tr": "2 damganın sertifikası 180 gün içinde sona eriyor; yeniden damgalama "
            "bekleniyor.",
            "message_en": "2 stamp certificate(s) expire within 180 days; re-stamping is due.",
            # 180 days before the first certificate expires (1 February 2027 00:00 UTC)
            "since": "2026-08-05T03:00:00+03:00",
            "count": 2,
            "link": "sealing",
        },
    ),
    "renewal_uncovered": (
        _renewal_uncovered,
        {
            "level": "warning",
            "message_tr": "1 damganın sertifikası yeniden damgalanmadan sona erdi; damgalar "
            "geçerliliğini korur, ancak uzun süreli kanıt için yeniden damgalama önerilir.",
            "message_en": "1 stamp certificate(s) expired without re-stamping; the stamps stay "
            "valid, but re-stamping is recommended for long-term evidence.",
            "since": "2026-10-30T03:00:00+03:00",
            "count": 1,
            "link": "sealing",
        },
    ),
    "certificate_revoked": (
        _certificate_revoked,
        {
            "level": "danger",
            "message_tr": "Zaman damgası sertifikası iptal listesinde: Örnek Test TSA 1. Zaman "
            "damgası sağlayıcısıyla görüşün; hukuki inceleme önerilir.",
            "message_en": "A time-stamp certificate is on a revocation list: Örnek Test TSA 1. "
            "Contact the provider; legal review is recommended.",
            "since": None,
            "count": None,
            "link": "sealing",
        },
    ),
    "crl_stale": (
        _crl_stale,
        {
            "level": "warning",
            "message_tr": "7 günden yeni doğrulanmış bir iptal listesi (SİL) yok.",
            "message_en": "No verified revocation list newer than 7 days.",
            # the list's thisUpdate plus seven days
            "since": isoformat_site(NOW - timedelta(days=1), "seconds"),
            "count": None,
            "link": "settings-tsa",
        },
    ),
}


@pytest.mark.parametrize("code", sorted(REASONS))
def test_legal_status_each_reason(site: Site, admin: TestClient, code: str) -> None:
    setup, expected = REASONS[code]
    setup(site)
    body = get(admin)
    found = [reason for reason in body["reasons"] if reason["code"] == code]
    assert found == [{"code": code, **expected}]
    if code == "credentials_in_clear":  # turktrust is no legal-grade claim before W5-T
        assert codes(body) == ["provider_not_legal", code]
    else:
        assert (body["level"], codes(body)) == (expected["level"], [code])


def test_legal_status_levels_are_the_worst_reason(site: Site, admin: TestClient) -> None:
    site.use(legal(site.config))
    site.renewals(token("2026-10-01", "due", datetime(2027, 3, 1, tzinfo=UTC)))
    site.crl_index(NOW - timedelta(days=8))
    body = get(admin)
    assert (body["level"], codes(body)) == ("warning", ["crl_stale", "renewal_due"])

    site.app.state.arci_legal_status.invalidate()
    site.use(legal(site.config, legal_root_fingerprints=()))
    body = get(admin)
    assert (body["level"], codes(body)) == (
        "danger",
        ["legal_root_missing", "crl_stale", "renewal_due"],
    )


def test_legal_status_reasons_sorted_danger_first_then_by_code(
    site: Site, admin: TestClient
) -> None:
    site.vault()
    site.use(site.config)  # the mock TSA: provider_not_legal
    site.renewals(
        token("2026-10-01", "due", datetime(2027, 3, 1, tzinfo=UTC)),
        token("2026-04-01", "uncovered", datetime(2026, 10, 30, tzinfo=UTC)),
    )
    site.revoked()
    site.crl_index(NOW - timedelta(days=30))
    body = get(admin)
    assert body["level"] == "danger"
    assert codes(body) == [
        "certificate_revoked",
        "provider_not_legal",
        "unstamped_days",
        "crl_stale",
        "renewal_due",
        "renewal_uncovered",
    ]
    levels = [reason["level"] for reason in body["reasons"]]
    assert levels == ["danger"] * 3 + ["warning"] * 3


@pytest.mark.parametrize(
    ("tsa", "label"),
    [
        ({}, "Deneme (mock)"),
        ({"provider": "rfc3161", "url": "http://192.0.2.30/tsa"}, "Test sunucusu"),
        ({"provider": "zamane", "url": f"http://{KAMUSM_TEST_HOST}"}, "Test sunucusu"),
        ({"provider": "zamane", "url": f"https://{KAMUSM_PRODUCTION_HOST}"}, "Kamu SM"),
        # no legal-grade claim for TÜRKTRUST before the configuration of W5-T
        ({"provider": "turktrust", "url": "https://198.51.100.20/"}, "Test sunucusu"),
    ],
)
def test_legal_status_provider_labels(
    site: Site, admin: TestClient, tsa: dict[str, Any], label: str
) -> None:
    config = site.use(tsa_config(site.config, **tsa))
    body = get(admin)
    assert (body["provider"], body["provider_label"]) == (str(config.tsa.provider), label)
    assert ("provider_not_legal" in codes(body)) is (label != "Kamu SM")


def test_provider_label_of_a_legal_grade_turktrust_claim() -> None:
    """W5-T's ``legal_grade`` for TÜRKTRUST production names it (patched claim until then)."""
    assert texts.provider_label("turktrust", legal_grade=True) == "TÜRKTRUST"
    assert texts.provider_label("zamane", legal_grade=True) == "Kamu SM"
    assert texts.provider_label("rfc3161", legal_grade=True) == "Test sunucusu"
    assert texts.provider_label("mock", legal_grade=True) == "Deneme (mock)"


@pytest.mark.parametrize(
    ("url", "allow_plain_http", "in_clear"),
    [
        ("http://198.51.100.20/", False, True),
        ("https://198.51.100.20/", False, False),
        ("https://198.51.100.20/", True, True),  # tsa.allow_plain_http (D12)
    ],
)
def test_legal_status_credentials_in_clear_for_turktrust_over_http(
    site: Site, admin: TestClient, url: str, allow_plain_http: bool, in_clear: bool
) -> None:
    site.use(
        tsa_config(site.config, provider="turktrust", url=url, allow_plain_http=allow_plain_http)
    )
    assert ("credentials_in_clear" in codes(get(admin))) is in_clear


def test_credentials_in_clear_only_for_turktrust(site: Site, admin: TestClient) -> None:
    site.use(tsa_config(site.config, provider="rfc3161", url="http://192.0.2.30/tsa"))
    assert codes(get(admin)) == ["provider_not_legal"]


def test_legal_options_read_the_wave_5_keys_with_their_defaults(api_config: Config) -> None:
    options = legal_options(api_config)
    assert (options.renew_before_days, options.crl_max_age_days) == (180, 7)
    assert (options.allow_plain_http, options.late_after_hours) == (False, 48)
    changed = legal_options(
        tsa_config(api_config, renew_before_days=90, crl_max_age_days=3, allow_plain_http=True)
    )
    assert (changed.renew_before_days, changed.crl_max_age_days) == (90, 3)
    assert changed.allow_plain_http is True


def test_legal_status_reads_the_configured_ages(site: Site, admin: TestClient) -> None:
    site.use(legal(site.config, renew_before_days=90, crl_max_age_days=30))
    site.renewals(token("2026-10-01", "due", datetime(2027, 1, 1, tzinfo=UTC)))
    site.crl_index(NOW - timedelta(days=8))  # fresh for 30 days
    (reason,) = get(admin)["reasons"]
    assert (
        reason["message_en"] == "1 stamp certificate(s) expire within 90 days; re-stamping is due."
    )
    assert reason["since"] == isoformat_site(datetime(2026, 10, 3, tzinfo=UTC), "seconds")


def test_late_unstamped_days_only_after_late_after_hours(site: Site) -> None:
    site.vault()
    config = site.config
    day_end = datetime(2026, 10, 31, tzinfo=UTC) - timedelta(hours=3)  # 31.10. 00:00 site
    assert late_unstamped_days(site.layout, config, day_end + timedelta(hours=47)) == []
    assert late_unstamped_days(site.layout, config, day_end + timedelta(hours=49)) == [
        date(2026, 10, 30)
    ]
    # a later day is late too after its own 48 hours; a stamped day never is
    assert late_unstamped_days(site.layout, config, day_end + timedelta(hours=73)) == [
        date(2026, 10, 30),
        date(2026, 10, 31),
    ]


def test_legal_status_unknown_spool_gives_no_unstamped_reason(
    site: Site, admin: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    site.vault()
    site.use(legal(site.config))

    def denied(self: Sealer) -> list[date]:
        raise PermissionError("spool: access denied")  # the API service's rights (ADR-0007)

    monkeypatch.setattr(Sealer, "closed_unsealed_days", denied)
    assert late_unstamped_days(site.layout, site.config, NOW) is None
    assert get(admin)["reasons"] == []


def test_legal_status_corrupt_state_files_never_fail_the_request(
    site: Site, admin: TestClient
) -> None:
    site.use(legal(site.config))
    state = site.layout.state
    (state / "crl").mkdir(parents=True)
    (state / "renewal.json").write_text('{"tokens": [', encoding="utf-8")
    (state / "crl" / "index.json").write_text('{"schema": "x"}', encoding="utf-8")
    (state / "verify").mkdir()
    (state / "verify" / "last-1.json").write_text("[", encoding="utf-8")
    body = get(admin)
    assert (body["level"], body["reasons"]) == ("ok", [])


# -- who may read it ------------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["admin", "auditor", "hr", "viewer"])
def test_legal_status_for_every_role(
    site: Site, client: TestClient, login_as: Login, role: str
) -> None:
    site.use(site.config)
    site.renewals(token("2026-10-01", "due", datetime(2027, 3, 1, tzinfo=UTC)))
    login_as(client, role)
    expected = compute_legal_status(site.layout, site.config, NOW).model_dump(mode="json")
    assert get(client) == expected
    assert codes(expected) == ["provider_not_legal", "renewal_due"]


def test_legal_status_needs_a_session(site: Site, client: TestClient) -> None:
    response = client.get(API)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"


def test_legal_status_password_change_required(
    site: Site, client: TestClient, login_as: Login
) -> None:
    login_as(client, "viewer", must_change_password=True)
    response = client.get(API)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "password_change_required"


def test_legal_status_is_not_audited(
    site: Site, admin: TestClient, app_session_factory: sessionmaker[Session]
) -> None:
    before = audit_count(app_session_factory)
    for _ in range(3):
        get(admin)
    site.app.state.arci_legal_status.invalidate()  # a computed answer is not audited either
    get(admin)
    assert audit_count(app_session_factory) == before


def test_legal_status_carries_no_subject_data(
    site: Site, admin: TestClient, app_session_factory: sessionmaker[Session]
) -> None:
    with app_session_factory() as session:
        session.add(Person(name="Deneme Kişi Örnek"))
        session.add(
            Lease(
                ip="172.20.1.23",
                mac="00:00:5e:00:53:01",
                hostname="ornek-dizustu",
                start=NOW - timedelta(hours=2),
                confidence="high",
                source_table="mt_dhcp",
            )
        )
        session.commit()
    _unstamped_days(site)
    _renewal_due(site)
    site.use(tsa_config(site.config, provider="turktrust", url="http://198.51.100.20/"))
    site.revoked()
    site.crl_index(NOW - timedelta(days=8))
    response = admin.get(API)
    assert response.status_code == 200
    for value in ("172.20.1.23", "00:00:5e:00:53:01", "00:00:5E:00:53:01", "Deneme Kişi"):
        assert value not in response.text
    assert "ornek-dizustu" not in response.text
    body = response.json()
    assert (
        set(body)
        == set(LegalStatus.model_fields)
        == {
            "level",
            "provider",
            "provider_label",
            "reasons",
            "checked_at",
        }
    )
    assert len(body["reasons"]) == 6
    for reason in body["reasons"]:
        assert (
            set(reason)
            == set(LegalReason.model_fields)
            == {
                "code",
                "level",
                "message_tr",
                "message_en",
                "since",
                "count",
                "link",
            }
        )


def test_legal_status_is_cached_for_60_seconds(site: Site, admin: TestClient) -> None:
    site.use(legal(site.config))
    first = get(admin)
    cache = site.app.state.arci_legal_status
    assert cache.ttl == routes.CACHE_SECONDS == 60.0
    clock = [1000.0]
    cache.invalidate()
    cache.clock = lambda: clock[0]
    assert get(admin) == first
    site.renewals(token("2026-10-01", "due", datetime(2027, 3, 1, tzinfo=UTC)))
    clock[0] += 59.0
    assert get(admin) == first  # the kept answer, its checked_at too
    clock[0] += 2.0
    assert codes(get(admin)) == ["renewal_due"]


def test_legal_status_is_not_gated_by_the_licence(site: Site, admin: TestClient) -> None:
    site.use(site.config)
    with override_status(base_only_status()):  # 5651 Temel only: every paid module locked
        assert codes(get(admin)) == ["provider_not_legal"]


# -- texts ----------------------------------------------------------------------------------------


def _contract_texts() -> dict[str, tuple[str, str]]:
    """The rows of contract 2.6: code → (TR, EN)."""
    section = CONTRACT.read_text(encoding="utf-8").split("### 2.6", 1)[1].split("\n### ", 1)[0]
    rows = {}
    for line in section.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 3 or cells[0] in ("Code", "---"):
            continue
        name = re.match(r"`([a-z_-]+)`", cells[0])
        rows[name[1] if name else cells[0]] = (cells[1], cells[2])
    return rows


def test_legal_texts_are_the_contract_texts() -> None:
    contract = _contract_texts()
    ours: dict[str, tuple[str, str]] = {str(code): text for code, text in texts.REASONS.items()}
    ours |= {**texts.RENEWAL_DAY, "credit, no credit query": texts.NO_CREDIT_QUERY}
    assert ours == contract
    # C13: "Zaman damgası sağlayıcısıyla görüşün", no documentation reference in the API texts
    assert "sağlayıcısıyla görüşün" in texts.REASONS["certificate_revoked"][0]
    assert not any("ZAMAN-DAMGASI" in text for pair in ours.values() for text in pair)


def test_legal_texts_never_claim_compliance() -> None:
    every = [
        *texts.REASONS.values(),
        *texts.RENEWAL_DAY.values(),
        texts.NO_CREDIT_QUERY,
        (*texts.LEGAL_PROVIDER_LABELS.values(), texts.MOCK_LABEL, texts.TEST_LABEL),
    ]
    for pair in every:
        for text in pair:
            assert not CLAIMS.search(text), text


@pytest.mark.parametrize(
    ("subject", "name"),
    [
        (TSA_SUBJECT, "Örnek Test TSA 1"),
        ("O=Örnek A.Ş.,C=TR", "O=Örnek A.Ş.,C=TR"),  # no common name: the whole subject
        ("not a name", "not a name"),
    ],
)
def test_certificate_name_is_the_common_name(subject: str, name: str) -> None:
    assert texts.certificate_name(subject) == name
