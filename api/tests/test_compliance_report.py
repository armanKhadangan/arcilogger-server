"""The Uyum Durumu Raporu (Wave 4 contract 2.3.4): every card in both languages and in the order
of the page, the tones as text, no claim of conformity, the site, the time and the account that
exported it — read back from the PDF with the text extraction of the other report tests
(:mod:`arcilogger_api.pdftext`: the text a viewer searches and copies).

The router is not mounted before W4-F (contract D2): this module mounts it on the test app
unless the path already exists (Implementation note 3).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.app import API_PREFIX
from arcilogger_api.audit import record_audit
from arcilogger_api.compliance import routes
from arcilogger_api.compliance.cards import CONFIG_BACKUP_CONFIRM
from arcilogger_api.compliance.models import ComplianceStatus, ComplianceTone
from arcilogger_api.compliance.pdf import render_report
from arcilogger_api.pdftext import normalized, pdf_text
from arcilogger_api.reports.texts import FOOTER_NOTE_EN, FOOTER_NOTE_TR
from arcilogger_api.testing import EXAMPLE_SITE_NAME, LoggedIn
from arcilogger_core.fmt import format_datetime
from arcilogger_core.timeutil import SITE_TZ
from arcilogger_db.models import Alert, Source

Login = Callable[..., LoggedIn]
API = f"{API_PREFIX}/compliance"
REPORT = f"{API}/report.pdf"
#: Wednesday 28 October 2026, 10:05:30 site time.
NOW = datetime(2026, 10, 28, 10, 5, 30, tzinfo=SITE_TZ).astimezone(UTC)
TONES: dict[ComplianceTone, str] = {
    "ok": "[TAMAM] / [OK]",
    "warning": "[DİKKAT] / [ATTENTION]",
    "error": "[SORUN] / [PROBLEM]",
    "neutral": "[BİLGİ YOK] / [NO DATA]",
}
#: ``COMPLIANCE_CLAIMS`` of ``tests/test_docs_consistency.py``, the claim list of
#: ``web/src/locales/locales.test.ts`` and the two words of contract 0.1 item 7.
CLAIMS = (
    "yasal olarak uyumlu",
    "kanuna uygundur",
    "mevzuata uygundur",
    "legally compliant",
    "fully compliant",
    "yasal geçerli olan tek yol",
    "kopyalanamaz",
    "kırılamaz",
    "tamper-proof",
    "tamper proof",
    "uyumlu",
    "conforms",
)


@pytest.fixture
def app(app: FastAPI) -> FastAPI:
    """The test app with the compliance routes (W4-F mounts them; until then this module)."""
    if not any(getattr(route, "path", None) == API for route in app.routes):
        app.include_router(routes.router, prefix=API_PREFIX)
    return app


@pytest.fixture(autouse=True)
def frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes, "utc_now", lambda: NOW)


@pytest.fixture
def site(app_session_factory: sessionmaker[Session]) -> sessionmaker[Session]:
    """A site with a silent source (card 1 alerted) and an open backup alert (card 10)."""
    with app_session_factory() as session:
        session.add(Source(name="core-r1", type="mikrotik", last_seen=None))
        session.add_all(
            [
                Alert(
                    rule="R15",
                    severity="high",
                    state="open",
                    title="Log kaynağı sessiz: core-r1",
                    time=datetime(2026, 10, 28, 8, 15, tzinfo=SITE_TZ),
                    details={},
                    dedup_key="R15:core-r1",
                ),
                Alert(
                    rule="R20-backup",
                    severity="medium",
                    state="acknowledged",
                    title="Veritabanı yedeği başarısız",
                    time=datetime(2026, 10, 28, 1, 41, tzinfo=SITE_TZ),
                    details={},
                    dedup_key="R20-backup",
                ),
            ]
        )
        session.commit()
    return app_session_factory


def export(client: TestClient) -> tuple[ComplianceStatus, str]:
    """The cards of the page and the text of the report, both at :data:`NOW`."""
    status = ComplianceStatus.model_validate(client.get(API).json())
    response = client.get(REPORT)
    assert response.status_code == 200
    return status, normalized(pdf_text(response.content))


def heading(card: Any) -> str:
    return f"{card.number}. {card.title_tr} / {card.title_en}"


def test_report_lists_every_card_in_both_languages(
    client: TestClient, login_as: Login, site: sessionmaker[Session]
) -> None:
    login_as(client, "admin")
    status, text = export(client)
    assert "Uyum Durumu Raporu" in text
    assert "Compliance status report" in text
    tones = [card.tone for card in status.cards]
    ok = tones.count("ok")
    assert f"10 denetimden {ok} tanesi tamam / {ok} of 10 checks OK" in text
    assert normalized(status.notice_tr) in text
    assert normalized(status.notice_en) in text
    positions = [text.index(heading(card)) for card in status.cards]
    assert positions == sorted(positions)  # the order of the page
    assert len(status.cards) == 10
    for index, card in enumerate(status.cards):
        end = positions[index + 1] if index + 1 < len(positions) else len(text)
        section = text[positions[index] : end]
        assert TONES[card.tone] in section, card.id
        assert normalized(card.reason_tr) in section, card.id
        assert normalized(card.reason_en) in section, card.id
        assert card.facts, card.id
        for fact in card.facts:
            # the facts as a two-column table: Turkish label, Turkish value
            assert f"{normalized(fact.label_tr)} {normalized(fact.value_tr)}" in section, (
                card.id,
                fact.key,
            )
        if card.verify_on_site_tr is not None and card.verify_on_site_en is not None:
            assert "Sahada doğrulayın / Verify on site" in section, card.id
            assert normalized(card.verify_on_site_tr) in section, card.id
            assert normalized(card.verify_on_site_en) in section, card.id
        assert f"Denetlendi / Checked: {format_datetime(card.checked_at)}" in section, card.id
    # the active alerts as "rule, severity, opened"
    by_id = {card.id: card for card in status.cards}
    sources = text[text.index(heading(by_id["sources"])) : text.index(heading(by_id["chain"]))]
    assert "Açık uyarılar / Active alerts R15, Yüksek / High, 28.10.2026 08:15:00" in sources
    backup = text[text.index(heading(by_id["backup"])) :]
    assert "R20-backup, Orta / Medium, 28.10.2026 01:41:00" in backup
    assert "R20-backup" not in text[: text.index(heading(by_id["backup"]))]
    assert any(card.verify_on_site_tr for card in status.cards)
    # every page: the neutral note and "Sayfa n / N"
    pages = text.count(FOOTER_NOTE_TR)
    assert pages >= 2
    assert text.count(FOOTER_NOTE_EN) == pages
    for page in range(1, pages + 1):
        assert f"Sayfa {page} / {pages}" in text


def test_report_shows_tones_as_text(
    client: TestClient, login_as: Login, site: sessionmaker[Session]
) -> None:
    login_as(client, "admin")
    status, text = export(client)
    for card in status.cards:  # the computed tones
        assert f"{heading(card)} {TONES[card.tone]}" in text, card.id
    # every tone, each under its own card
    cycle: list[ComplianceTone] = ["ok", "warning", "error", "neutral"]
    toned = status.model_copy(
        update={
            "cards": [
                card.model_copy(update={"tone": cycle[index % 4]})
                for index, card in enumerate(status.cards)
            ]
        }
    )
    text = normalized(
        pdf_text(render_report(toned, display_name="Deniz Örnek", username="deniz").pdf)
    )
    for card in toned.cards:
        assert f"{heading(card)} {TONES[card.tone]}" in text, card.id
    assert text.count("[TAMAM] / [OK]") == 3
    assert text.count("[DİKKAT] / [ATTENTION]") == 3
    assert text.count("[SORUN] / [PROBLEM]") == 2
    assert text.count("[BİLGİ YOK] / [NO DATA]") == 2


def _fold(value: str) -> str:
    """Lower case with the dotless and dotted i folded and white space collapsed
    (``web/src/locales/locales.test.ts``)."""
    lowered = value.lower().replace("i̇", "i").replace("ı", "i")
    return " ".join(lowered.split())


def test_report_claims_no_conformity(
    client: TestClient, login_as: Login, site: sessionmaker[Session]
) -> None:
    login_as(client, "admin")
    status, text = export(client)
    texts = [text]
    # and with every tone and every reason the cards gave
    for tone in ("ok", "warning", "error", "neutral"):
        toned = status.model_copy(
            update={"cards": [card.model_copy(update={"tone": tone}) for card in status.cards]}
        )
        report = render_report(toned, display_name="Deniz Örnek", username="deniz")
        texts.append(normalized(pdf_text(report.pdf)))
    for found in texts:
        folded = _fold(found)
        assert [claim for claim in CLAIMS if _fold(claim) in folded] == []
        assert "5651 yükümlülüklerini desteklemek için tasarlanmıştır" in found
        assert "designed to support 5651 obligations" in found
        assert "uygunluk beyanı değildir" in found
        assert "neither a legal assessment nor a statement of conformity" in found


def test_report_names_site_time_and_exporter(
    app: FastAPI, login_as: Login, site: sessionmaker[Session]
) -> None:
    with TestClient(app) as other:
        confirmer = login_as(other, "admin", display_name="Ece Örnek")
    with site() as session:  # card 10 shows when the copy was confirmed, never by whom
        record_audit(
            session,
            actor=_Actor(confirmer.user_id, confirmer.username),
            action=CONFIG_BACKUP_CONFIRM,
            details={"config_sha256": "0" * 64},
        )
        session.commit()
    with TestClient(app) as client:
        exporter = login_as(client, "admin", display_name="Deniz Örnek")
        response = client.get(REPORT)
    text = normalized(pdf_text(response.content))
    expected = [
        "Uyum Durumu Raporu",
        "Compliance status report",
        f"Kurum / Site: {EXAMPLE_SITE_NAME}",
        "Oluşturulma / Generated: 28.10.2026 10:05:30 (Europe/Istanbul)",
        f"Oluşturan / Exported by: Deniz Örnek ({exporter.username})",
        "denetimden",
        "ARCILOGGER, 5651 yükümlülüklerini",
        "1. Log kaynakları veri gönderiyor",
    ]
    positions = [text.index(part) for part in expected]
    assert positions == sorted(positions)
    assert "Ece Örnek" not in text
    assert confirmer.username not in text
    # the page header carries the site, the footer the generation time
    assert text.count(EXAMPLE_SITE_NAME) >= 2
    assert "Oluşturulma 28.10.2026 10:05:30" in text

    # a site whose name is not set
    status = ComplianceStatus.model_validate(client.get(API).json())
    unnamed = status.model_copy(update={"site_name": None})
    text = normalized(
        pdf_text(render_report(unnamed, display_name="Deniz Örnek", username="deniz").pdf)
    )
    assert "Kurum / Site: —" in text
    assert EXAMPLE_SITE_NAME not in text
    assert "Oluşturan / Exported by: Deniz Örnek (deniz)" in text


class _Actor:
    def __init__(self, id_: int, username: str) -> None:
        self.id = id_
        self.username = username
