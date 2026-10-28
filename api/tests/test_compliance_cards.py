"""The ten cards of Uyum Durumu (Wave 4 contract 2.3.2-2.3.3): every condition of every card's
table against the files and rows the evidence tasks and services write, the evaluation order,
the texts in both languages and what the cards never show.

Cards 2 and 3 read a vault sealed by the real sealing code (``arcilogger_worker.vaulttesting``);
cards 4, 5 and 9 only ask whether a day's manifest and time stamp are in the vault, so their
tests place those files (and the deletion records, seal and replica state) themselves.
Verification state files are named with the CLI's own expression (Implementation note 7).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.admin_channel import Channel
from arcilogger_api.audit import record_audit
from arcilogger_api.compliance import cards, texts
from arcilogger_api.compliance.cards import (
    CONFIG_BACKUP_CONFIRM,
    CardContext,
    compute_card,
    compute_status,
)
from arcilogger_api.compliance.models import ComplianceCard, ComplianceCardId, ComplianceStatus
from arcilogger_api.routers.dashboard import clock_box
from arcilogger_api.sealinfo import VaultView
from arcilogger_api.testing import EXAMPLE_SITE_NAME
from arcilogger_core.admin.testing import Installed
from arcilogger_core.config import Config, ReplicaConfig, RetentionConfig, TsaConfig
from arcilogger_core.fmt import format_date, format_datetime
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import SITE_TZ, isoformat_site, site_day
from arcilogger_db.kinds import kind_of
from arcilogger_db.models import Alert, FgWeb, JobRun, SealVerification, SearchCount, Source
from arcilogger_db.settings import set_setting
from arcilogger_worker.compliance_filter import FilterEvidence
from arcilogger_worker.vaulttesting import SealedVault, at_site, make_vault

#: The cards' "now": Wednesday 28 October 2026, 10:00 site time (after the 09:00 overdue hour).
NOW = datetime(2026, 10, 28, 10, 0, tzinfo=SITE_TZ)
TODAY = date(2026, 10, 28)
YESTERDAY = TODAY - timedelta(days=1)
ORDER: tuple[ComplianceCardId, ...] = (
    "sources",
    "seal_yesterday",
    "chain",
    "retention",
    "tsa",
    "clock",
    "content_filter",
    "lease_evidence",
    "replica",
    "backup",
)
#: The Kamu SM production server (``config.tsa.legal_grade``).
PRODUCTION = TsaConfig(provider="zamane", url="http://zd.kamusm.gov.tr")


# -- the site -------------------------------------------------------------------------------------


@dataclass
class Site:
    factory: sessionmaker[Session]
    layout: Layout
    config: Config
    app: FastAPI

    def channel(self, config: Config) -> Channel:
        return Channel(self.app.state.arci, config)

    def status(self, now: datetime = NOW, config: Config | None = None) -> ComplianceStatus:
        config = config or self.config
        with self.factory() as session:
            result = compute_status(session, self.layout, config, self.channel(config), now)
            session.rollback()  # read only
        return result

    def card(
        self, card: ComplianceCardId, now: datetime = NOW, config: Config | None = None
    ) -> ComplianceCard:
        config = config or self.config
        with self.factory() as session:
            ctx = CardContext(
                db=session,
                layout=self.layout,
                config=config,
                channel=self.channel(config),
                now=now,
            )
            result = compute_card(ctx, card)
            session.rollback()
        return result

    def add(self, *rows: Any) -> None:
        with self.factory() as session:
            session.add_all(rows)
            session.commit()

    def alert(
        self,
        key: str,
        rule: str | None = None,
        *,
        severity: str = "high",
        state: str = "open",
        at: datetime = NOW,
        title: str = "Uyarı",
        details: Mapping[str, Any] | None = None,
    ) -> int:
        row = Alert(
            rule=rule or key.partition(":")[0],  # R15:<name> → R15, R20-lease:<cause> → R20-lease
            severity=severity,
            state=state,
            title=title,
            time=at,
            details=dict(details or {}),
            dedup_key=key,
        )
        with self.factory() as session:
            session.add(row)
            session.commit()
            return row.id

    def resolve_all(self) -> None:
        with self.factory() as session:
            session.execute(text("UPDATE alerts SET state = 'resolved', resolved_at = now()"))
            session.commit()

    def setting(self, key: str, value: Any) -> None:
        with self.factory() as session:
            set_setting(session, key, value, updated_by="test")
            session.commit()

    def write(self, path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")

    def place_sealed(self, *days: date) -> None:
        """A manifest and a time stamp in the vault (what "sealed" means for cards 4, 5, 9)."""
        for day in days:
            self.write(self.layout.manifest(day), {"day": day.isoformat()})
            self.layout.token(day).write_bytes(b"token")


@pytest.fixture
def site(
    app_session_factory: sessionmaker[Session], layout: Layout, api_config: Config, app: FastAPI
) -> Site:
    return Site(app_session_factory, layout, api_config, app)


def watched(name: str = "core-r1", **columns: Any) -> Source:
    columns.setdefault("type", "mikrotik")
    columns.setdefault("last_seen", NOW - timedelta(minutes=2))
    return Source(name=name, **columns)


def reason(card: ComplianceCard) -> tuple[str, str]:
    return card.reason_tr, card.reason_en


def facts(card: ComplianceCard) -> dict[str, tuple[str, str]]:
    return {fact.key: (fact.value_tr, fact.value_en) for fact in card.facts}


def verify_state(
    layout: Layout, *, all_days: bool = False, day: bool = False, days: int = 0
) -> Path:
    """``state/verify/last-{kind}.json`` named as ``arcilogger-core verify`` names it
    (``core/src/arcilogger_core/cli.py:353-355``)."""
    kind = "full" if all_days else "day" if day else f"last-{days}"
    return layout.state / "verify" / f"last-{kind}.json"


def write_verification(
    layout: Layout,
    path: Path,
    at: datetime,
    checks: Mapping[date, bool],
    *,
    deep: bool = False,
    ok: bool | None = None,
) -> None:
    """A verification state file as ``verify`` writes it (``ok``: the overall result, e.g. a
    broken link between days whose own checks passed)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "at": isoformat_site(at, "seconds"),
                "deep": deep,
                "ok": all(checks.values()) if ok is None else ok,
                "checks": [
                    {
                        "day": day.isoformat(),
                        "ok": ok,
                        "errors": [] if ok else ["manifest sha256 mismatch"],
                    }
                    for day, ok in checks.items()
                ],
            }
        ),
        encoding="utf-8",
    )


# -- order, ids, the common fields ----------------------------------------------------------------


def test_cards_come_in_order_with_their_ids(site: Site) -> None:
    status = site.status()
    assert [card.id for card in status.cards] == list(ORDER)
    assert [card.number for card in status.cards] == list(range(1, 11))
    assert [card.page for card in status.cards] == [
        "sources",
        "sealing",
        "sealing",
        "sealing",
        "sealing",
        "system",
        "search",
        "sources",
        "sealing",
        "settings",
    ]
    assert [card.title_tr for card in status.cards] == [
        "Log kaynakları veri gönderiyor",
        "Dünün kayıtları mühürlendi ve doğrulandı",
        "Mühür zinciri bütün",
        "Saklama süresi",
        "Kamu SM kontörü ve zaman damgası sertifikası",
        "Sunucu saati",
        "İçerik filtresi kayıtları",
        "DHCP ve ARP kayıtları (erişim kayıtları)",
        "İkinci kopya (replika)",
        "Yapılandırma ve veritabanı yedekleri",
    ]
    assert [card.title_en for card in status.cards] == [
        "Log sources are sending",
        "Yesterday's records sealed and verified",
        "Seal chain intact",
        "Retention period",
        "Kamu SM credit and time-stamp certificate",
        "Server clock",
        "Content filter records",
        "DHCP and ARP records (access records)",
        "Second copy (replica)",
        "Configuration and database backups",
    ]
    for card in status.cards:
        assert card.doc_tr == f"UYUM-DURUMU.md bölüm 2.{card.number}"
        assert card.doc_en == f"COMPLIANCE-STATUS.md section 2.{card.number}"
        assert card.checked_at == NOW
        assert card.checked_at.utcoffset() == timedelta(hours=3)
    verify = {card.id for card in status.cards if card.verify_on_site_tr is not None}
    assert verify == {"tsa", "content_filter", "replica"}
    assert {card.id for card in status.cards if card.verify_on_site_en is not None} == verify
    assert status.generated_at == NOW
    assert status.generated_at.isoformat().endswith("+03:00")
    tones = [card.tone for card in status.cards]
    assert status.counts.model_dump() == {
        tone: tones.count(tone) for tone in ("ok", "warning", "error", "neutral")
    }
    assert sum(status.counts.model_dump().values()) == 10
    assert status.site_name == EXAMPLE_SITE_NAME
    assert site.status(config=site.config.model_copy(update={"site_name": ""})).site_name is None
    assert status.notice_tr == (
        "ARCILOGGER, 5651 yükümlülüklerini desteklemek için tasarlanmıştır. Bu kartlar kayıt "
        "toplama, mühürleme ve yedeklemenin teknik denetimlerinin sonucudur; hukuki bir "
        "değerlendirme ya da uygunluk beyanı değildir. Sonuçları hukuk müşavirinizle teyit edin."
    )
    assert status.notice_en == (
        "ARCILOGGER is designed to support 5651 obligations. These cards are the results of "
        "technical checks of log collection, sealing and backups; they are neither a legal "
        "assessment nor a statement of conformity. Confirm the results with your legal counsel."
    )


# -- card 1 ---------------------------------------------------------------------------------------


def test_sources_card_states(site: Site) -> None:
    card = site.card("sources")
    assert card.tone == "warning"
    assert reason(card) == (
        "İzlenen log kaynağı yok. Kaynakları Ayarlar → Kaynaklar'da tanımlayın.",
        "No log source is watched. Define the sources in Settings → Sources.",
    )
    assert card.page == "sources"

    # Only enabled, expected, configured sources are watched.
    site.add(
        watched("fortigate", type="fortigate", heartbeat_minutes=10),
        watched("core-r1", last_seen=NOW - timedelta(minutes=14)),
        watched("ap-172.20.10.23", expected=False, last_seen=NOW - timedelta(hours=5)),
        watched("unknown-192.0.2.10", unknown=True, last_seen=NOW - timedelta(hours=5)),
        watched("switch-old", enabled=False, last_seen=None),
    )
    card = site.card("sources")
    assert card.tone == "ok"
    assert reason(card) == (
        "İzlenen 2 kaynağın tümü kalp atışı süresi içinde veri gönderdi.",
        "All 2 watched sources sent data within their heartbeat.",
    )
    assert facts(card) == {
        "watched": ("2", "2"),
        "active": ("2", "2"),
        "silent": ("—", "—"),
        "heartbeat": ("kaynağa göre, varsayılan 15 dk", "per source, default 15 min"),
    }
    assert [fact.label_tr for fact in card.facts] == [
        "İzlenen kaynak",
        "Veri gönderen",
        "Sessiz",
        "Kalp atışı süresi",
    ]
    assert [fact.label_en for fact in card.facts] == [
        "Watched sources",
        "Sending",
        "Silent",
        "Heartbeat",
    ]

    # Silent beyond its own heartbeat, no R15 alert yet (the start-up grace period).
    site.add(watched("switch-1", last_seen=NOW - timedelta(minutes=16)))
    card = site.card("sources")
    assert card.tone == "warning"
    assert reason(card) == (
        "switch-1 kalp atışı süresinden uzun süredir sessiz; uyarı başlangıç bekleme süresi "
        "içinde.",
        "switch-1 silent for longer than the heartbeat; the alert is still in its start-up "
        "grace period.",
    )
    assert facts(card)["silent"] == ("switch-1", "switch-1")
    assert facts(card)["active"] == ("2", "2")

    # An R15 alert (open or acknowledged) turns it red; a resolved one does not count.
    site.alert("R15:switch-0", "R15", state="resolved")
    first = site.alert("R15:switch-1", "R15", state="acknowledged", at=NOW - timedelta(minutes=1))
    card = site.card("sources")
    assert card.tone == "error"
    assert reason(card) == (
        "1 kaynak kalp atışı süresinden uzun süredir veri göndermiyor: switch-1.",
        "1 source(s) have sent nothing for longer than their heartbeat: switch-1.",
    )
    assert [alert.id for alert in card.alerts] == [first]
    assert card.alerts[0].rule == "R15"
    assert card.alerts[0].opened_at == NOW - timedelta(minutes=1)

    # Names cut after five with "+{k}"; at most five alerts, newest first.
    ids = [
        site.alert(f"R15:sw-{n}", "R15", at=NOW - timedelta(minutes=10 - n)) for n in range(1, 7)
    ]
    card = site.card("sources")
    assert reason(card) == (
        "7 kaynak kalp atışı süresinden uzun süredir veri göndermiyor: "
        "sw-1, sw-2, sw-3, sw-4, sw-5 +2.",
        "7 source(s) have sent nothing for longer than their heartbeat: "
        "sw-1, sw-2, sw-3, sw-4, sw-5 +2.",
    )
    assert [alert.id for alert in card.alerts] == [first, *reversed(ids)][:5]
    assert [alert.opened_at for alert in card.alerts] == sorted(
        (alert.opened_at for alert in card.alerts), reverse=True
    )


# -- card 2 ---------------------------------------------------------------------------------------

#: Card 2's days: verified, sealed, late, failed, pending, no data.
D_VERIFIED, D_SEALED, D_LATE, D_FAILED, D_PENDING, D_EMPTY = (
    date(2026, 10, day) for day in (20, 21, 22, 23, 24, 26)
)


@pytest.fixture
def vault(api_config: Config) -> Iterator[SealedVault]:
    sealed = make_vault(api_config.data_dir, tsa_now=at_site(D_VERIFIED - timedelta(days=30)))
    sealed.seal_days([D_VERIFIED, D_SEALED])
    sealed.write_day(D_LATE)
    assert sealed.seal(D_LATE, now=at_site(D_LATE + timedelta(days=3), 1, 0)).status == "sealed"
    sealed.write_day(D_FAILED)
    assert sealed.fail_seal(D_FAILED).status == "pending"
    sealed.write_day(D_PENDING)
    yield sealed
    sealed.unlock_all()


def morning_after(day: date, hour: int = 10) -> datetime:
    return datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=SITE_TZ).replace(
        hour=hour
    )


def test_seal_yesterday_card_states(site: Site, vault: SealedVault) -> None:
    production = site.config.model_copy(update={"tsa": PRODUCTION})
    finished = morning_after(D_VERIFIED, 2)
    site.add(
        SealVerification(
            day=D_VERIFIED,
            started_at=finished - timedelta(seconds=3),
            finished_at=finished,
            username="denetci",
            deep=False,
            outcome="ok",
        )
    )

    # verified, from the Kamu SM production server
    card = site.card("seal_yesterday", morning_after(D_VERIFIED), production)
    assert card.tone == "ok"
    assert reason(card) == (
        "Dün (20.10.2026) mühürlendi ve doğrulandı.",
        "Yesterday (20.10.2026) was sealed and verified.",
    )
    state = json.loads(site.layout.seal_state(D_VERIFIED).read_text(encoding="utf-8"))
    assert facts(card) == {
        "day": ("20.10.2026", "20.10.2026"),
        "state": ("Mühürlendi ve doğrulandı", "Sealed and verified"),
        "sealed_at": (
            format_datetime(datetime.fromisoformat(state["sealed_at"])),
            format_datetime(datetime.fromisoformat(state["sealed_at"])),
        ),
        "verification": (
            f"{format_datetime(finished)}, başarılı",
            f"{format_datetime(finished)}, passed",
        ),
        "server": ("Kamu SM üretim", "Kamu SM production"),
    }

    # verified, but stamped by another server (Q5: card 2 amber)
    card = site.card("seal_yesterday", morning_after(D_VERIFIED))
    assert card.tone == "warning"
    assert reason(card) == (
        "Dün mühürlendi ve doğrulandı, ancak damga Kamu SM üretim sunucusundan değil.",
        "Yesterday was sealed and verified, but the time stamp is not from the Kamu SM "
        "production server.",
    )
    assert facts(card)["server"] == ("Deneme (mock)", "Trial (mock)")

    # sealed, not verified yet
    card = site.card("seal_yesterday", morning_after(D_SEALED), production)
    assert card.tone == "warning"
    assert reason(card) == (
        "Dün (21.10.2026) mühürlendi, henüz doğrulanmadı (VerifyDaily 01:00'de çalışır).",
        "Yesterday (21.10.2026) is sealed but not verified yet (VerifyDaily runs at 01:00).",
    )
    assert facts(card)["verification"] == ("—", "—")

    # stamped late
    card = site.card("seal_yesterday", morning_after(D_LATE), production)
    assert card.tone == "warning"
    assert reason(card) == (
        "Dün (22.10.2026) geç damgalandı.",
        "Yesterday (22.10.2026) was time-stamped late.",
    )

    # failed: the summary's own message in each language
    card = site.card("seal_yesterday", morning_after(D_FAILED), production)
    assert card.tone == "error"
    cell = VaultView(site.layout, production, now=morning_after(D_FAILED)).summary(D_FAILED)
    assert all((cell["message_tr"], cell["message_en"]))
    assert reason(card) == (
        f"Dünün (23.10.2026) mühürlenmesi ya da doğrulaması başarısız: {cell['message_tr']}",
        f"Sealing or verification of yesterday (23.10.2026) failed: {cell['message_en']}",
    )
    assert facts(card)["state"] == ("Başarısız", "Failed")

    # pending before the overdue hour (09:00) and from it on
    card = site.card("seal_yesterday", morning_after(D_PENDING, 8), production)
    assert card.tone == "warning"
    assert reason(card) == (
        "Dünün (24.10.2026) mühürlenmesi bekleniyor (Seal görevi 00:10'dan sonra çalışır).",
        "Yesterday (24.10.2026) is waiting to be sealed (the Seal task runs after 00:10).",
    )
    card = site.card("seal_yesterday", morning_after(D_PENDING, 9), production)
    assert card.tone == "error"
    assert reason(card) == (
        "Dün (24.10.2026) saat 09:00'a kadar mühürlenmedi.",
        "Yesterday (24.10.2026) was not sealed by 09:00.",
    )
    site.setting("alerts.seal_overdue_hour", 11)
    assert site.card("seal_yesterday", morning_after(D_PENDING, 10), production).tone == "warning"
    card = site.card("seal_yesterday", morning_after(D_PENDING, 11), production)
    assert reason(card)[0] == "Dün (24.10.2026) saat 11:00'a kadar mühürlenmedi."

    # no data: neutral without a watched source, red with one
    card = site.card("seal_yesterday", morning_after(D_EMPTY), production)
    assert card.tone == "neutral"
    assert reason(card) == (
        "Dün için kayıt yok; henüz izlenen kaynak tanımlanmadı.",
        "No records for yesterday; no watched source is defined yet.",
    )
    site.add(watched())
    card = site.card("seal_yesterday", morning_after(D_EMPTY), production)
    assert card.tone == "error"
    assert reason(card) == (
        "Dün (26.10.2026) için ham kayıt yok; kaynaklar veri göndermemiş olabilir.",
        "There are no raw records for yesterday (26.10.2026); the sources may have sent nothing.",
    )

    # the alert of exactly that day
    site.alert(f"R20-seal:{D_EMPTY.isoformat()}", "R20-seal")
    site.alert(f"R20-seal:{D_PENDING.isoformat()}", "R20-seal")
    card = site.card("seal_yesterday", morning_after(D_EMPTY), production)
    assert [alert.rule for alert in card.alerts] == ["R20-seal"]
    assert len(site.card("seal_yesterday", morning_after(D_SEALED), production).alerts) == 0


# -- card 3 ---------------------------------------------------------------------------------------

A, B, C = date(2026, 10, 20), date(2026, 10, 21), date(2026, 10, 22)


@pytest.fixture
def chain(api_config: Config) -> Iterator[SealedVault]:
    sealed = make_vault(api_config.data_dir, tsa_now=at_site(A - timedelta(days=30)))
    sealed.seal_days([A, B, C])
    yield sealed
    sealed.unlock_all()


def test_chain_card_states(site: Site, chain: SealedVault) -> None:
    layout = site.layout
    card = site.card("chain")
    assert card.tone == "warning"
    assert reason(card) == (
        "Son 30 günde 3 mühürlü gün son 35 günde doğrulanmadı.",
        "3 sealed day(s) of the last 30 were not verified in the last 35 days.",
    )
    assert facts(card) == {
        "verified": ("0", "0"),
        "not_verified": ("3", "3"),
        "failed": ("0", "0"),
        "no_data": ("27", "27"),
        "full_check": ("—", "—"),
        "tasks": (
            "günlük son 2 gün, haftalık son 90 gün, aylık tüm zincir",
            "daily the last 2 days, weekly the last 90 days, monthly the whole chain",
        ),
    }

    # verified by the weekly task (verify --last 3 on a young chain); no full check yet and the
    # first sealed day is younger than 35 days
    weekly = NOW - timedelta(days=1)
    write_verification(layout, verify_state(layout, days=3), weekly, {A: True, B: True, C: True})
    card = site.card("chain")
    assert card.tone == "ok"
    assert reason(card) == (
        "Son 30 günün mühürlü 3 günü doğrulandı; tam zincir denetimi her ayın 1'inde çalışır, "
        "henüz çalışmadı.",
        "All 3 sealed days of the last 30 are verified; the full chain check runs on the 1st of "
        "each month and has not run yet.",
    )
    assert facts(card)["verified"] == ("3", "3")

    # a full check that passed
    full = verify_state(layout, all_days=True)
    full_at = NOW - timedelta(days=2)
    write_verification(layout, full, full_at, {A: True, B: True, C: True}, deep=True)
    card = site.card("chain")
    assert card.tone == "ok"
    at = format_datetime(full_at)
    assert reason(card) == (
        f"Son 30 günün mühürlü 3 günü doğrulandı; tam zincir denetimi {at} tarihinde başarılı.",
        f"All 3 sealed days of the last 30 are verified; the full chain check of {at} passed.",
    )
    assert facts(card)["full_check"] == (f"{at}, başarılı", f"{at}, passed")

    # older than 35 days
    old_at = NOW - timedelta(days=36)
    write_verification(layout, full, old_at, {A: True, B: True, C: True}, deep=True)
    card = site.card("chain")
    assert card.tone == "warning"
    old = format_datetime(old_at)
    assert reason(card) == (
        f"Son tam zincir denetimi {old}; 35 günden eski.",
        f"The last full chain check was on {old}, more than 35 days ago.",
    )

    # a day of the last 30 not sealed yet (yesterday's own pending day is card 2's)
    chain.write_day(date(2026, 10, 25))
    chain.write_day(YESTERDAY)
    card = site.card("chain")
    assert card.tone == "warning"
    assert reason(card) == (
        "Son 30 günde 1 gün henüz mühürlenmedi: 25.10.2026.",
        "1 day(s) of the last 30 are not sealed yet: 25.10.2026.",
    )

    # a sealed day without a passing verification within 35 days
    write_verification(layout, verify_state(layout, days=3), weekly, {A: True, C: True})
    write_verification(layout, verify_state(layout, days=2), NOW - timedelta(days=40), {B: True})
    card = site.card("chain")
    assert card.tone == "warning"
    assert reason(card)[0] == "Son 30 günde 1 mühürlü gün son 35 günde doğrulanmadı."
    assert facts(card)["verified"] == ("2", "2")
    assert facts(card)["not_verified"] == ("1", "1")

    # the full check failed, or an R20-verify alert is open
    failed_at = NOW - timedelta(hours=5)
    write_verification(layout, full, failed_at, {A: True, B: True, C: True}, deep=True, ok=False)
    write_verification(layout, verify_state(layout, days=2), weekly, {B: True})
    card = site.card("chain")
    assert card.tone == "error"
    failed = format_datetime(failed_at)
    assert reason(card) == (
        f"Tam zincir denetimi ({failed}) başarısız.",
        f"The full chain check ({failed}) failed.",
    )
    assert facts(card)["full_check"] == (f"{failed}, başarısız", f"{failed}, failed")
    write_verification(layout, full, full_at, {A: True, B: True, C: True}, deep=True)
    assert site.card("chain").tone == "warning"  # the pending day again
    alert_at = NOW - timedelta(hours=1)
    alert_id = site.alert("R20-verify", "R20-verify", at=alert_at)
    card = site.card("chain")
    assert card.tone == "error"
    assert reason(card)[1] == f"The full chain check ({format_datetime(alert_at)}) failed."
    assert [alert.id for alert in card.alerts] == [alert_id]

    # a failed day comes first
    write_verification(layout, verify_state(layout, day=True), NOW - timedelta(hours=2), {B: False})
    card = site.card("chain")
    assert card.tone == "error"
    assert reason(card) == (
        "Son 30 günde 1 gün başarısız: 21.10.2026.",
        "1 day(s) of the last 30 failed: 21.10.2026.",
    )
    assert facts(card)["failed"] == ("1", "1")


def test_chain_card_without_sealed_days_and_without_a_full_check(
    site: Site, chain: SealedVault
) -> None:
    layout = site.layout
    assert site.card("chain", config=site.config).tone == "warning"  # sealed, not verified
    # 34 days after A: no sealed day among the 30, the full check is recent
    later = NOW + timedelta(days=26)
    full = verify_state(layout, all_days=True)
    write_verification(layout, full, later - timedelta(days=1), {A: True, B: True, C: True})
    card = site.card("chain", later)
    assert card.tone == "neutral"
    assert reason(card) == ("Son 30 günde mühürlü gün yok.", "No sealed day in the last 30 days.")
    # without a full check while the first sealed day is 35 days ago or more
    full.unlink()
    assert site.card("chain", later).tone == "neutral"  # A is 34 days back
    card = site.card("chain", later + timedelta(days=1))
    assert card.tone == "warning"
    assert reason(card) == (
        "Tam zincir denetimi hiç çalışmadı.",
        "The full chain check has never run.",
    )


# -- card 4 ---------------------------------------------------------------------------------------


def deletion_record(site: Site, *days: date) -> None:
    """A stamped deletion record of retention (``vault/deletions/<date>-imha.json`` + ``.zd``)."""
    path = site.layout.deletions / f"{TODAY.isoformat()}-imha.json"
    site.write(
        path, {"created_at": isoformat_site(NOW), "days": [{"day": d.isoformat()} for d in days]}
    )
    path.with_name(path.name + ".zd").write_bytes(b"token")


def test_retention_card_states(site: Site) -> None:
    on = site.config.model_copy(update={"retention": RetentionConfig(enabled=True, days=760)})
    card = site.card("retention")
    assert card.tone == "neutral"
    assert reason(card) == ("Henüz mühürlü gün yok.", "No sealed day yet.")
    assert facts(card) == {
        "oldest": ("—", "—"),
        "retention": ("kapalı", "off"),
        "deleted": ("0", "0"),
    }

    # a young installation
    young = TODAY - timedelta(days=100)
    site.place_sealed(young, young + timedelta(days=1))
    card = site.card("retention", config=on)
    assert card.tone == "ok"
    assert reason(card) == (
        f"En eski mühürlü gün {format_date(young)} (100 gün önce); kurulum 730 günden genç.",
        f"The oldest sealed day is {format_date(young)} (100 days ago); the installation is "
        "younger than 730 days.",
    )
    assert facts(card)["retention"] == ("açık, 760 gün", "on, 760 days")

    # 730 days in the vault
    full = TODAY - timedelta(days=730)
    site.place_sealed(full)
    card = site.card("retention", config=on)
    assert card.tone == "ok"
    assert reason(card) == (
        f"En eski mühürlü gün {format_date(full)} (730 gün önce); 730 günlük süre kasada.",
        f"The oldest sealed day is {format_date(full)} (730 days ago); the vault holds the 730 "
        "days.",
    )
    assert facts(card)["oldest"] == (format_date(full), format_date(full))

    # an installation older than 730 days whose vault holds less
    for day in (full,):
        site.layout.manifest(day).unlink()
        site.layout.token(day).unlink()
    oldest = TODAY - timedelta(days=729)
    site.place_sealed(oldest)
    deletion_record(site, full)  # the first sealed day exactly 730 days ago
    card = site.card("retention", config=on)
    assert card.tone == "error"
    assert reason(card) == (
        f"Kurulum 730 günden eski, ancak en eski mühürlü gün {format_date(oldest)} (729 gün önce).",
        f"The installation is older than 730 days, but the oldest sealed day is "
        f"{format_date(oldest)} (729 days ago).",
    )
    assert facts(card)["deleted"] == ("1", "1")

    # retention off: a day exactly retention.days (760) old is not beyond it
    site.place_sealed(TODAY - timedelta(days=760))
    card = site.card("retention")
    assert card.tone == "ok"
    assert reason(card)[1].startswith(
        f"The oldest sealed day is {format_date(TODAY - timedelta(days=760))} (760 days ago)"
    )
    # retention off and days beyond retention.days in the vault
    site.place_sealed(TODAY - timedelta(days=800), TODAY - timedelta(days=761))
    card = site.card("retention")
    assert card.tone == "warning"
    oldest = TODAY - timedelta(days=800)
    assert reason(card) == (
        f"Saklama kapalı ve 760 günden eski 2 mühürlü gün kasada duruyor (en eskisi "
        f"{format_date(oldest)}). Kişisel verilerin gereğinden uzun saklanmaması için saklamayı "
        "açın.",
        f"Retention is off and 2 sealed day(s) older than 760 days are in the vault (the oldest "
        f"{format_date(oldest)}). Turn retention on so that personal data is not kept longer "
        "than needed.",
    )
    # with retention on, the same vault is fine
    assert site.card("retention", config=on).tone == "ok"

    # an open alert of the retention task comes first
    site.alert("R20-retention:failed", "R20-retention")
    card = site.card("retention", config=on)
    assert card.tone == "error"
    assert reason(card) == (
        "Saklama görevinin açık bir uyarısı var.",
        "The retention task has an open alert.",
    )
    assert len(card.alerts) == 1


# -- card 5 ---------------------------------------------------------------------------------------


def write_credit(site: Site, *entries: tuple[datetime, int | None]) -> None:
    site.layout.credit_log.parent.mkdir(parents=True, exist_ok=True)
    site.layout.credit_log.write_text(
        "".join(
            json.dumps(
                {"time": isoformat_site(at, "seconds"), "provider": "zamane", "credits": credits}
            )
            + "\n"
            for at, credits in entries
        ),
        encoding="utf-8",
    )


def sealed_with_certificate(site: Site, day: date, not_after: datetime | None) -> None:
    site.place_sealed(day)
    site.write(
        site.layout.seal_state(day),
        {
            "status": "sealed",
            "sealed_at": isoformat_site(at_site(day + timedelta(days=1)), "seconds"),
            "tsa_cert_not_after": None if not_after is None else not_after.isoformat(),
        },
    )


def test_tsa_card_states(site: Site) -> None:
    production = site.config.model_copy(update={"tsa": PRODUCTION})

    # not the Kamu SM production server (Q5: red)
    for tsa, label in (
        (TsaConfig(provider="mock"), ("Deneme (mock)", "Trial (mock)")),
        (TsaConfig(provider="rfc3161", url="http://203.0.113.5/tsa"), ("RFC 3161", "RFC 3161")),
        (TsaConfig(provider="zamane", url="http://tzd.kamusm.gov.tr"), ("Kamu SM test",) * 2),
    ):
        card = site.card("tsa", config=site.config.model_copy(update={"tsa": tsa}))
        assert card.tone == "error"
        assert reason(card) == (
            f"Damgalar Kamu SM üretim sunucusundan alınmıyor (sağlayıcı: {label[0]}). Kamu SM "
            "bağlantısını Ayarlar → Kamu SM'de tamamlayın.",
            f"Time stamps are not taken from the Kamu SM production server (provider: "
            f"{label[1]}). Complete the Kamu SM connection in Settings → Kamu SM.",
        )
        assert facts(card)["server"] == label
    assert card.verify_on_site_tr == (
        "Kontör değeri Kamu SM istemcisinin çıktısından okunur; okuma biçimi sahada "
        "doğrulanmalıdır."
    )
    assert card.verify_on_site_en == (
        "The credit is read from the Kamu SM client's output; verify the format on site."
    )

    # no credit reading at all; then no sealed day yet
    card = site.card("tsa", config=production)
    assert card.tone == "warning"
    assert reason(card) == (
        "Kontör bilinmiyor ya da son okuma (—) iki günden eski.",
        "The credit is unknown or the last reading (—) is more than two days old.",
    )
    write_credit(site, (NOW - timedelta(hours=3), 9000))
    card = site.card("tsa", config=production)
    assert card.tone == "neutral"
    assert reason(card) == (
        "Henüz Kamu SM ile damgalanmış gün yok.",
        "No day has been stamped with Kamu SM yet.",
    )

    # all fine
    sealed_with_certificate(site, YESTERDAY - timedelta(days=1), NOW + timedelta(days=100))
    sealed_with_certificate(site, YESTERDAY, NOW + timedelta(days=400))  # the newest counts
    card = site.card("tsa", config=production)
    assert card.tone == "ok"
    assert reason(card) == (
        "Kalan kontör 9.000 (eşik 500); son damganın TSA sertifikası 400 gün daha geçerli.",
        "9,000 credits left (threshold 500); the TSA certificate of the newest time stamp is "
        "valid for 400 more days.",
    )
    until = format_date(NOW + timedelta(days=400))
    assert facts(card) == {
        "server": ("Kamu SM üretim", "Kamu SM production"),
        "credits": ("9.000", "9,000"),
        "estimated_days": ("9.000", "9,000"),
        "threshold": ("500", "500"),
        "credit_at": (format_datetime(NOW - timedelta(hours=3)),) * 2,
        "cert_until": (until, until),
        "cert_days": ("400", "400"),
    }

    # the certificate ends within 180 days
    sealed_with_certificate(site, YESTERDAY, NOW + timedelta(days=180))
    card = site.card("tsa", config=production)
    assert card.tone == "warning"
    soon = format_date(NOW + timedelta(days=180))
    assert reason(card) == (
        f"Son damganın TSA sertifikası {soon} tarihinde doluyor (180 gün kaldı).",
        f"The TSA certificate of the newest time stamp expires on {soon} (180 days left).",
    )
    sealed_with_certificate(site, YESTERDAY, NOW + timedelta(days=181))
    assert site.card("tsa", config=production).tone == "ok"

    # the newest reading is more than two days old
    read_at = NOW - timedelta(days=2, minutes=1)
    write_credit(site, (read_at, 9000))
    card = site.card("tsa", config=production)
    assert card.tone == "warning"
    assert reason(card)[0] == (
        f"Kontör bilinmiyor ya da son okuma ({format_datetime(read_at)}) iki günden eski."
    )
    write_credit(site, (read_at, 9000), (NOW - timedelta(hours=1), None))  # unreachable
    assert site.card("tsa", config=production).tone == "warning"

    # at or under the threshold (estimate: one stamp a day without measured use)
    write_credit(site, (NOW - timedelta(hours=1), 500))
    card = site.card("tsa", config=production)
    assert card.tone == "warning"
    assert reason(card) == (
        "Kalan kontör 500; eşik 500. Tahmini 500 gün yeter.",
        "500 credits left; threshold 500. Estimated to last 500 days.",
    )

    # the certificate has expired
    expired = NOW - timedelta(days=1)
    sealed_with_certificate(site, YESTERDAY, expired)
    card = site.card("tsa", config=production)
    assert card.tone == "error"
    assert reason(card) == (
        f"Son damganın TSA sertifikasının süresi {format_date(expired)} tarihinde doldu.",
        f"The TSA certificate of the newest time stamp expired on {format_date(expired)}.",
    )

    # the credit is used up (before the certificate)
    write_credit(site, (NOW - timedelta(hours=1), 0))
    card = site.card("tsa", config=production)
    assert card.tone == "error"
    assert reason(card) == ("Kamu SM kontörü bitti.", "The Kamu SM credit is used up.")
    site.alert("R20-credit", "R20-credit")
    assert len(site.card("tsa", config=production).alerts) == 1


# -- card 6 ---------------------------------------------------------------------------------------


def write_clock(site: Site, *samples: dict[str, Any]) -> None:
    site.layout.clock_samples(TODAY).parent.mkdir(parents=True, exist_ok=True)
    site.layout.clock_samples(TODAY).write_text(
        "".join(json.dumps(sample) + "\n" for sample in samples), encoding="utf-8"
    )


def sample(at: datetime, offset_us: int | None, *, ok: bool = True) -> dict[str, Any]:
    return {
        "time": isoformat_site(at, "seconds"),
        "server": "172.20.10.1" if ok else None,
        "offset_us": offset_us,
        "ok": ok,
    }


def with_local_source(config: Config) -> Config:
    return config.model_copy(
        update={"clock": config.clock.model_copy(update={"ntp_server": "172.20.10.1"})}
    )


def test_clock_card_counts_the_limit_itself_as_a_problem(site: Site) -> None:
    config = with_local_source(site.config)
    write_clock(site, sample(NOW - timedelta(minutes=10), 1_000_000))
    card = site.card("clock", config=config)
    assert card.tone == "error"
    assert reason(card) == (
        "Saat farkı 1.000 ms; sınır 1.000 ms.",
        "Clock offset 1,000 ms; limit 1,000 ms.",
    )
    # the Panel keeps "<=" (owner question Q9)
    assert clock_box(site.layout, config, NOW).ok is True
    write_clock(site, sample(NOW - timedelta(minutes=10), -999_999))
    assert site.card("clock", config=config).tone == "ok"
    write_clock(site, sample(NOW - timedelta(minutes=10), -1_000_000))
    assert site.card("clock", config=config).tone == "error"


def test_clock_card_states(site: Site) -> None:
    config = with_local_source(site.config)
    card = site.card("clock", config=config)
    assert card.tone == "warning"
    assert reason(card) == ("Henüz saat ölçümü yok.", "No clock measurement yet.")
    assert card.page == "system"

    # older than 3 hours
    stale_at = NOW - timedelta(hours=3, minutes=1)
    write_clock(site, sample(stale_at, 3200))
    card = site.card("clock", config=config)
    assert card.tone == "warning"
    stale = format_datetime(stale_at)
    assert reason(card) == (
        f"Son saat ölçümü {stale}; 3 saatten eski (ClockCheck saatte bir çalışır).",
        f"The last clock measurement was at {stale}, more than 3 hours ago (ClockCheck runs "
        "hourly).",
    )

    # within the limit
    at = NOW - timedelta(minutes=20)
    write_clock(site, sample(at, 3200))
    card = site.card("clock", config=config)
    assert card.tone == "ok"
    assert reason(card) == (
        f"Saat farkı 3,2 ms (172.20.10.1, {format_datetime(at)}); sınır 1.000 ms.",
        f"Clock offset 3.2 ms (172.20.10.1, {format_datetime(at)}); limit 1,000 ms.",
    )
    assert facts(card) == {
        "checked_at": (format_datetime(at),) * 2,
        "server": ("172.20.10.1", "172.20.10.1"),
        "offset": ("3,2 ms", "3.2 ms"),
        "limit": ("1.000 ms", "1,000 ms"),
        "local_source": ("ayarlı", "set"),
    }

    # no local time source: only the fallback
    card = site.card("clock")
    assert card.tone == "warning"
    assert reason(card) == (
        "Yerel saat kaynağı ayarlanmamış; saat yalnızca yedek sunucuyla "
        "(time.ume.tubitak.gov.tr) eşitleniyor.",
        "No local time source is set; the clock follows only the fallback server "
        "(time.ume.tubitak.gov.tr).",
    )
    assert facts(card)["local_source"] == ("yok", "none")

    # an open clock alert
    site.alert("R20-clock", "R20-clock")
    card = site.card("clock", config=config)
    assert card.tone == "error"
    assert reason(card) == (
        "Saat sunucusuna ulaşılamadı ya da saat uyarısı açık (172.20.10.1).",
        "The time server could not be reached or a clock alert is open (172.20.10.1).",
    )
    site.resolve_all()
    site.alert("R20-clock:step", "R20-clock")
    assert site.card("clock", config=config).tone == "error"
    site.resolve_all()

    # the time server did not answer
    write_clock(site, sample(NOW - timedelta(minutes=5), None, ok=False))
    card = site.card("clock", config=config)
    assert card.tone == "error"
    assert reason(card)[1] == (
        "The time server could not be reached or a clock alert is open (172.20.10.1)."
    )
    assert facts(card)["offset"] == ("—", "—")

    # beyond the limit
    write_clock(site, sample(NOW - timedelta(minutes=5), -1_500_000))
    card = site.card("clock", config=config)
    assert card.tone == "error"
    assert reason(card) == (
        "Saat farkı -1.500 ms; sınır 1.000 ms.",
        "Clock offset -1,500 ms; limit 1,000 ms.",
    )


# -- card 7 ---------------------------------------------------------------------------------------


def evidence(**changes: Any) -> FilterEvidence:
    values: dict[str, Any] = {
        "applies": True,
        "last_at": NOW - timedelta(minutes=5),
        "today": {"fg_web": 12, "fg_dns": 13},
        "days": [(TODAY, 12, 13), (YESTERDAY, 3, 0)],
        "web_profiles": ["default", "okul"],
        "window_hours": 24,
        "window_expected": True,
        "alert_due": False,
        "silent_kinds": [],
    }
    values.update(changes)
    return FilterEvidence(**values)


def test_content_filter_card_states(site: Site, monkeypatch: pytest.MonkeyPatch) -> None:
    found: list[FilterEvidence] = []

    def given(**changes: Any) -> ComplianceCard:
        found[:] = [evidence(**changes)]
        return site.card("content_filter")

    monkeypatch.setattr(cards, "filter_evidence", lambda session, *, now: found[0])
    last = format_datetime(NOW - timedelta(minutes=5))

    card = given()
    assert card.tone == "ok"
    assert reason(card) == (
        f"Son kayıt {last}; bugün 12 web, 13 DNS filtresi kaydı.",
        f"Last record {last}; today 12 web and 13 DNS filter records.",
    )
    assert card.page == "search"
    assert facts(card) == {
        "last": (last, last),
        "today_web": ("12", "12"),
        "today_dns": ("13", "13"),
        "days": ("28.10: 25 · 27.10: 3", "28.10: 25 · 27.10: 3"),
        "profiles": ("default, okul", "default, okul"),
        "dns_profile": (
            "FortiGate DNS filtresi kayıtlarından profil adı okunmuyor (sahada doğrula).",
            "No profile name is read from FortiGate DNS filter records (verify on site).",
        ),
        "expected_days": (
            "Pzt, Sal, Çar, Per, Cum, Cmt, Paz",
            "Mon, Tue, Wed, Thu, Fri, Sat, Sun",
        ),
        "holidays": ("beklenmez", "not expected"),
    }
    assert card.verify_on_site_tr == (
        "Güvenlik duvarı politikalarının web ve DNS filtresi olaylarını kaydettiği ve "
        "ARCILOGGER'a gönderdiği sahada doğrulanmalıdır."
    )
    assert card.verify_on_site_en == (
        "Verify on site that the firewall policies log web and DNS filter events and send them "
        "to ARCILOGGER."
    )

    # one kind silent in the window, the other arrives
    card = given(silent_kinds=["dns"])
    assert card.tone == "warning"
    assert reason(card) == (
        "DNS filtresi kaydı 24 saattir gelmiyor; diğer tür geliyor.",
        "No DNS filter record for 24 hours; the other kind still arrives.",
    )
    assert reason(given(silent_kinds=["web"]))[0] == (
        "Web filtresi kaydı 24 saattir gelmiyor; diğer tür geliyor."
    )

    # nothing in the window, which includes a day on which nothing is expected
    old = NOW - timedelta(hours=30)
    card = given(last_at=old, window_expected=False, silent_kinds=["web", "dns"])
    assert card.tone == "warning"
    assert reason(card) == (
        f"Son 24 saatte içerik filtresi kaydı yok; bu süre beklenmeyen bir gün içeriyor (son "
        f"kayıt: {format_datetime(old)}).",
        f"No content filter record in the last 24 hours; this period includes a day on which "
        f"none is expected (last record: {format_datetime(old)}).",
    )
    assert given(last_at=NOW - timedelta(hours=24)).tone == "ok"  # the window's edge is in it

    # due: nothing in a window of expected days ({last} "—": none in 8 days)
    card = given(last_at=None, alert_due=True, window_hours=36)
    assert card.tone == "error"
    assert reason(card) == (
        "Beklenen günlerde son 36 saattir web ya da DNS filtresi kaydı gelmedi (son kayıt: —).",
        "No web or DNS filter record in the last 36 hours of expected days (last record: —).",
    )

    # no FortiGate source: nothing expected, whatever else holds
    card = given(applies=False, alert_due=True)
    assert card.tone == "neutral"
    assert reason(card) == (
        "FortiGate kaynağı tanımlı değil; içerik filtresi kaydı beklenmiyor.",
        "No FortiGate source is defined; no content filter records are expected.",
    )

    # facts: profiles cut after 10, the expected days and the holiday setting
    profiles = [f"profil-{n:02d}" for n in range(12)]
    shown = ", ".join(profiles[:10]) + " +2"
    site.setting("alerts.filter_days", [6, 7])
    site.setting("alerts.filter_skip_holidays", False)
    card = given(web_profiles=profiles, days=[])
    assert facts(card)["profiles"] == (shown, shown)
    assert facts(card)["days"] == ("—", "—")
    assert facts(card)["expected_days"] == ("Cmt, Paz", "Sat, Sun")
    assert facts(card)["holidays"] == ("beklenen gün sayılır", "count as expected days")
    site.setting("alerts.filter_days", [])
    assert facts(given())["expected_days"] == ("—", "—")
    assert facts(given(web_profiles=[]))["profiles"] == ("—", "—")

    # its alerts: the R20-filter: prefix
    alert_id = site.alert("R20-filter:none")
    assert [alert.id for alert in given().alerts] == [alert_id]


def test_content_filter_card_reads_the_real_evidence(site: Site) -> None:
    card = site.card("content_filter")
    assert card.tone == "neutral"
    site.add(watched("fortigate", type="fortigate"))
    seen = NOW - timedelta(hours=2)
    site.add(
        FgWeb(
            time=seen.astimezone(UTC),
            time_received=seen.astimezone(UTC),
            source="fortigate",
            sender="172.31.255.1",
            raw_day=site_day(seen),
            raw_line=1,
            raw_offset=0,
            srcip="172.20.42.101",
            hostname="www.example.com",
            profile="default",
        ),
        SearchCount(
            hour=seen.astimezone(UTC).replace(minute=0, second=0, microsecond=0),
            source="fortigate",
            raw_day=site_day(seen),
            kind=kind_of("utm", "webfilter"),
            tbl="fg_web",
            n=4,
        ),
    )
    card = site.card("content_filter")
    last = format_datetime(seen)
    assert card.tone == "ok"  # no DNS record in the 7 days: DNS is not "silent"
    assert reason(card) == (
        f"Son kayıt {last}; bugün 4 web, 0 DNS filtresi kaydı.",
        f"Last record {last}; today 4 web and 0 DNS filter records.",
    )
    assert facts(card)["last"] == (last, last)
    assert facts(card)["today_web"] == ("4", "4")
    assert facts(card)["profiles"] == ("default", "default")


# -- card 8 ---------------------------------------------------------------------------------------


def lease_rows(site: Site, table: str, n: int, at: datetime = NOW, source: str = "core-r1") -> None:
    site.add(
        SearchCount(
            hour=at.astimezone(UTC).replace(minute=0, second=0, microsecond=0),
            source=source,
            raw_day=site_day(at),
            kind="dhcp",
            tbl=table,
            n=n,
        )
    )


def test_lease_evidence_card_follows_lease_alerts(site: Site) -> None:
    site.alert(
        "R20-lease:none:mt_dhcp",
        severity="medium",
        title="DHCP kaydı gelmiyor: Ofis (172.20.10.0/24)",
        details={"title_en": "No DHCP records: Ofis (172.20.10.0/24)", "mac": "00:00:5E:00:53:01"},
    )
    # no watched source: nothing is expected, the alert notwithstanding
    card = site.card("lease_evidence")
    assert card.tone == "neutral"
    assert reason(card) == (
        "İzlenen kaynak yok; erişim kaydı beklenmiyor.",
        "No watched source; no access records are expected.",
    )

    site.add(watched())
    lease_rows(site, "mt_dhcp", 40)
    card = site.card("lease_evidence")
    assert card.tone == "warning"
    assert reason(card) == ("1 kira kanıtı sorunu açık.", "1 lease evidence problem(s) open.")
    assert facts(card)["problems"] == (
        "DHCP kaydı gelmiyor: Ofis (172.20.10.0/24)",
        "No DHCP records: Ofis (172.20.10.0/24)",
    )
    assert card.page == "sources"

    for severity in ("high", "critical"):
        site.alert(f"R20-lease:{severity}", severity=severity, title=f"Sorun {severity}")
        card = site.card("lease_evidence")
        assert card.tone == "error"
    assert reason(card) == ("3 kira kanıtı sorunu açık.", "3 lease evidence problem(s) open.")
    # without title_en: the fixed English text; never the dedup keys
    assert facts(card)["problems"] == (
        "Sorun critical; Sorun high; DHCP kaydı gelmiyor: Ofis (172.20.10.0/24)",
        "Lease evidence problem; Lease evidence problem; No DHCP records: Ofis (172.20.10.0/24)",
    )
    assert "R20-lease:" not in card.model_dump_json()

    # titles cut after five, at most five alerts
    for n in range(4):
        site.alert(
            f"R20-lease:x{n}", severity="low", title=f"T{n}", at=NOW - timedelta(hours=n + 1)
        )
    card = site.card("lease_evidence")
    assert reason(card)[0] == "7 kira kanıtı sorunu açık."
    assert facts(card)["problems"][0].endswith(" +2")
    assert len(card.alerts) == 5

    # resolved alerts do not count: the tables decide again
    site.resolve_all()
    card = site.card("lease_evidence")
    assert card.tone == "ok"
    assert card.alerts == []
    assert facts(card)["problems"] == ("—", "—")


def test_lease_evidence_card_without_alerts_reads_the_tables(site: Site) -> None:
    site.add(watched(), watched("fortigate", type="fortigate"))
    site.add(Source(name="unknown-192.0.2.10", type="generic", unknown=True))
    # not counted: older than 24 hours (the hour that holds the start counts), another table,
    # a source that is not watched
    lease_rows(site, "mt_dhcp", 7, at=NOW - timedelta(hours=25, minutes=1))
    lease_rows(site, "fg_traffic", 100)
    lease_rows(site, "mt_arp", 100, source="unknown-192.0.2.10")
    card = site.card("lease_evidence")
    assert card.tone == "warning"
    assert reason(card) == (
        "Son 24 saatte DHCP ya da ARP kaydı gelmedi.",
        "No DHCP or ARP record arrived in the last 24 hours.",
    )
    assert facts(card) == {
        "mt_dhcp": ("0 satır (24 saat)", "0 rows (24 h)"),
        "mt_arp": ("0 satır (24 saat)", "0 rows (24 h)"),
        "fg_dhcp": ("0 satır (24 saat)", "0 rows (24 h)"),
        "problems": ("—", "—"),
    }
    assert [fact.label_tr for fact in card.facts] == [
        "MikroTik DHCP",
        "MikroTik ARP",
        "FortiGate DHCP",
        "Açık kira kanıtı sorunları",
    ]
    assert card.facts[-1].label_en == "Open lease evidence problems"

    lease_rows(site, "mt_dhcp", 1200, at=NOW - timedelta(hours=23, minutes=59))
    lease_rows(site, "mt_dhcp", 34, at=NOW - timedelta(hours=1))
    card = site.card("lease_evidence")
    assert card.tone == "ok"
    assert reason(card) == (
        "Son 24 saatte MikroTik DHCP kayıtları geldi; açık kira kanıtı uyarısı yok.",
        "MikroTik DHCP records arrived in the last 24 hours; no lease evidence alert is open.",
    )
    assert facts(card)["mt_dhcp"] == ("1.234 satır (24 saat)", "1,234 rows (24 h)")

    lease_rows(site, "mt_arp", 5)
    lease_rows(site, "fg_dhcp", 6, source="fortigate")
    card = site.card("lease_evidence")
    assert reason(card)[1] == (
        "MikroTik DHCP, MikroTik ARP, FortiGate DHCP records arrived in the last 24 hours; no "
        "lease evidence alert is open."
    )
    assert facts(card)["fg_dhcp"] == ("6 satır (24 saat)", "6 rows (24 h)")


# -- card 9 ---------------------------------------------------------------------------------------

TARGET = "//NAS-01/arcilogger-worm"


def replica_state(site: Site, day: date, status: str = "ok", at: datetime | None = NOW) -> None:
    site.write(
        site.layout.replica_state(day),
        {
            "status": status,
            "verified_at": None if at is None else isoformat_site(at, "seconds"),
            "target": TARGET,
            "files": {"manifest": "x"},
        },
    )


def test_replica_card_states(site: Site) -> None:
    on = site.config.model_copy(
        update={"replica": ReplicaConfig(enabled=True, target=Path(TARGET))}
    )
    card = site.card("replica")
    assert card.tone == "error"
    assert reason(card) == (
        "Replika kapalı: mühürlü günlerin ikinci kopyası alınmıyor.",
        "The replica is off: no second copy of the sealed days is made.",
    )
    assert card.verify_on_site_tr == (
        "Replika hedefinin değiştirilemez (WORM) olduğu ARCILOGGER'dan denetlenemez; sahada "
        "doğrulayın."
    )
    assert card.verify_on_site_en == (
        "Whether the replica target is write-once (WORM) cannot be checked from ARCILOGGER; "
        "verify it on site."
    )

    # no sealed day of the 7 ending yesterday (an older one does not count)
    site.place_sealed(TODAY - timedelta(days=8))
    card = site.card("replica", config=on)
    assert card.tone == "neutral"
    assert reason(card) == ("Son 7 günde mühürlü gün yok.", "No sealed day in the last 7 days.")

    # sealed days without a checked copy
    first, second = TODAY - timedelta(days=7), YESTERDAY
    site.place_sealed(first, second)
    replica_state(site, second, status="failed", at=NOW - timedelta(hours=1))  # not a copy
    card = site.card("replica", config=on)
    assert card.tone == "warning"
    assert reason(card) == (
        f"2 mühürlü gün henüz kopyalanmadı (en eskisi {format_date(first)}).",
        f"2 sealed day(s) not copied yet (the oldest {format_date(first)}).",
    )
    assert facts(card)["pending"] == ("2", "2")

    copied = NOW - timedelta(hours=8)
    replica_state(site, first, at=copied - timedelta(days=1))
    replica_state(site, second, at=copied)
    card = site.card("replica", config=on)
    assert card.tone == "ok"
    assert reason(card) == (
        "Son 7 günün mühürlü 2 günü replika hedefine kopyalandı ve denetlendi.",
        "All 2 sealed days of the last 7 were copied to the replica target and checked.",
    )
    assert facts(card) == {
        "enabled": ("açık", "on"),
        "last_copy": (format_datetime(copied),) * 2,
        "pending": ("0", "0"),
        "stale_hours": ("48", "48"),
    }
    assert [fact.label_tr for fact in card.facts] == [
        "Replika",
        "Son kopya",
        "Bekleyen gün",
        "Gecikme eşiği (saat)",
    ]

    # an open replica alert
    alert_id = site.alert("R20-replica:stale", "R20-replica")
    card = site.card("replica", config=on)
    assert card.tone == "error"
    assert reason(card) == (
        "Replika uyarısı açık: kopya gecikti ya da kopyalama başarısız.",
        "A replica alert is open: a copy is late or copying failed.",
    )
    assert [alert.id for alert in card.alerts] == [alert_id]
    assert "NAS-01" not in card.model_dump_json()
    # switched off comes first
    assert site.card("replica").reason_tr.startswith("Replika kapalı")


# -- card 10 --------------------------------------------------------------------------------------


def backup_state(site: Site, *, ok: bool, finished: datetime, size: int | None = None) -> None:
    site.write(
        site.layout.state / "backup" / "last.json",
        {
            "schema": 1,
            "finished": isoformat_site(finished, "seconds"),
            "ok": ok,
            "file": "D:\\ARCILOGGER\\backups\\scheduled\\arcilogger-20261028-0140.dump",
            "size": size,
            "sha256": "0" * 64,
            "error": None if ok else "pg_dump exited with 1",
        },
    )


def confirm(site: Site, sha256: str) -> datetime:
    with site.factory() as session:
        row = record_audit(
            session, actor=None, action=CONFIG_BACKUP_CONFIRM, details={"config_sha256": sha256}
        )
        session.commit()
        return row.time


def test_backup_card_states(site: Site, installed: Installed) -> None:
    off = site.config.model_copy(
        update={"backup": site.config.backup.model_copy(update={"enabled": False})}
    )
    card = site.card("backup", config=off)
    assert card.tone == "error"
    assert reason(card) == ("Gece veritabanı yedeği kapalı.", "The nightly database backup is off.")
    assert facts(card)["enabled"] == ("kapalı", "off")
    assert card.page == "settings"

    # a new installation: the first backup has not run yet
    card = site.card("backup")
    assert card.tone == "neutral"
    assert reason(card) == (
        "İlk veritabanı yedeği henüz alınmadı (Backup görevi 01:40'ta çalışır).",
        "The first database backup has not run yet (the Backup task runs at 01:40).",
    )
    site.add(JobRun(job="alerts.system", started_at=NOW - timedelta(hours=47), status="ok"))
    assert site.card("backup").tone == "neutral"
    site.add(JobRun(job="alerts.system", started_at=NOW - timedelta(hours=48), status="ok"))
    card = site.card("backup")
    assert card.tone == "warning"  # no successful backup 48 hours after the installation
    assert reason(card)[0] == "Son başarılı veritabanı yedeği —; 36 saatten eski."

    # the last backup is older than 36 hours
    old = NOW - timedelta(hours=36, minutes=1)
    backup_state(site, ok=True, finished=old, size=1_288_490_189)
    card = site.card("backup")
    assert card.tone == "warning"
    assert reason(card) == (
        f"Son başarılı veritabanı yedeği {format_datetime(old)}; 36 saatten eski.",
        f"The last successful database backup was at {format_datetime(old)}, more than 36 hours "
        "ago.",
    )
    site.setting("alerts.backup_stale_hours", 48)
    assert reason(site.card("backup"))[0] == (
        "Yapılandırma dosyalarının çevrimdışı kopyası hiç onaylanmadı (YEDEKLEME.md bölüm 3)."
    )
    site.setting("alerts.backup_stale_hours", 36)

    # fresh, the configuration copy never confirmed
    finished = NOW - timedelta(hours=8)
    backup_state(site, ok=True, finished=finished, size=1_288_490_189)
    card = site.card("backup")
    assert card.tone == "warning"
    assert reason(card) == (
        "Yapılandırma dosyalarının çevrimdışı kopyası hiç onaylanmadı (YEDEKLEME.md bölüm 3).",
        "The offline copy of the configuration files was never confirmed (BACKUP.md section 3).",
    )
    assert facts(card)["config_copy"] == ("hiç", "never")

    # confirmed for the current file
    current = site.channel(site.config).files.sha256
    assert current is not None
    confirmed = confirm(site, current)
    card = site.card("backup")
    assert card.tone == "ok"
    assert reason(card) == (
        f"Son veritabanı yedeği {format_datetime(finished)} (1,2 GB); yapılandırma kopyası "
        f"{format_datetime(confirmed)} tarihinde onaylandı.",
        f"Last database backup {format_datetime(finished)} (1.2 GB); configuration copy "
        f"confirmed on {format_datetime(confirmed)}.",
    )
    assert facts(card) == {
        "last_backup": (format_datetime(finished),) * 2,
        "size": ("1,2 GB", "1.2 GB"),
        "enabled": ("açık", "on"),
        "config_copy": (format_datetime(confirmed),) * 2,
    }

    # the configuration changed after the confirmation
    with installed.config_path.open("a", encoding="utf-8") as handle:
        handle.write("\n# changed\n")
    card = site.card("backup")
    assert card.tone == "warning"
    assert reason(card) == (
        f"Yapılandırma son onaydan ({format_datetime(confirmed)}) sonra değişti; yeni bir "
        "çevrimdışı kopya alın.",
        f"The configuration changed after the last confirmation ({format_datetime(confirmed)}); "
        "make a new offline copy.",
    )
    confirm(site, site.channel(site.config).files.sha256 or "")
    assert site.card("backup").tone == "ok"

    # the last backup failed, or a backup alert is open
    backup_state(site, ok=False, finished=NOW - timedelta(hours=2))
    card = site.card("backup")
    assert card.tone == "error"
    assert reason(card) == (
        "Son veritabanı yedeği başarısız ya da yedek uyarısı açık.",
        "The last database backup failed or a backup alert is open.",
    )
    assert facts(card)["size"] == ("—", "—")
    backup_state(site, ok=True, finished=finished, size=1_288_490_189)
    site.alert("R20-backup:prune", "R20-backup", severity="medium")
    card = site.card("backup")
    assert card.tone == "error"
    assert len(card.alerts) == 1
    # switched off comes first
    assert site.card("backup", config=off).reason_tr == "Gece veritabanı yedeği kapalı."


# -- failures, privacy, wording -------------------------------------------------------------------


def test_a_failing_card_is_neutral_and_the_others_answer(
    site: Site, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    site.add(watched())
    # card 3: an unreadable full-check file; card 7: a database error inside its query
    full = verify_state(site.layout, all_days=True)
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text('{"at": "secret detail', encoding="utf-8")

    def broken(session: Session, *, now: datetime) -> FilterEvidence:
        session.execute(text("SELECT * FROM no_such_table_with_172_20_42_101"))
        raise AssertionError("not reached")

    monkeypatch.setattr(cards, "filter_evidence", broken)
    with caplog.at_level(logging.ERROR, logger="arcilogger.api.compliance"):
        status = site.status()
    by_id = {card.id: card for card in status.cards}
    assert reason(by_id["chain"]) == (
        "Bu denetim şu anda yapılamadı: JSONDecodeError",
        "This check could not be run now: JSONDecodeError",
    )
    assert reason(by_id["content_filter"]) == (
        "Bu denetim şu anda yapılamadı: ProgrammingError",
        "This check could not be run now: ProgrammingError",
    )
    for card in (by_id["chain"], by_id["content_filter"]):
        assert card.tone == "neutral"
        assert (card.facts, card.alerts) == ([], [])
        assert all((card.title_tr, card.doc_tr))
    text_out = status.model_dump_json()
    assert "secret detail" not in text_out
    assert "no_such_table" not in text_out
    assert {record.getMessage() for record in caplog.records} >= {
        "compliance card chain could not be computed",
        "compliance card content_filter could not be computed",
    }
    # the cards after the failed query still read the database
    assert by_id["lease_evidence"].tone == "warning"
    assert by_id["lease_evidence"].reason_tr == "Son 24 saatte DHCP ya da ARP kaydı gelmedi."
    assert by_id["backup"].reason_tr.startswith("İlk veritabanı yedeği")
    assert by_id["sources"].tone == "ok"
    assert len(status.cards) == 10
    assert status.counts.neutral == sum(1 for card in status.cards if card.tone == "neutral")


#: Data about log subjects the site holds; none of it may reach a card (contract 0.1 item 4).
CLIENT_IP, CLIENT_MAC = "172.20.42.101", "00:00:5E:00:53:01"
PERSON, HOST = "Ayşe Örnek", "LAPTOP-AYSE"
SENDER = "172.31.255.1"


@pytest.fixture
def busy(site: Site) -> Site:
    """A site with data about log subjects everywhere the cards look."""
    site.add(
        watched("fortigate", type="fortigate", address=SENDER),
        watched("switch-1", address="172.20.10.2", last_seen=NOW - timedelta(hours=2)),
    )
    seen = NOW - timedelta(hours=1)
    site.add(
        FgWeb(
            time=seen.astimezone(UTC),
            time_received=seen.astimezone(UTC),
            source="fortigate",
            sender=SENDER,
            raw_day=site_day(seen),
            raw_line=1,
            raw_offset=0,
            srcip=CLIENT_IP,
            hostname=HOST,
            url=f"https://{HOST}/",
            profile="default",
        )
    )
    lease_rows(site, "mt_dhcp", 3, source="switch-1")
    details = {"srcip": CLIENT_IP, "mac": CLIENT_MAC, "person": PERSON, "host": HOST}
    site.alert("R15:switch-1", "R15", details=details, title="Log kaynağı sessiz: switch-1")
    site.alert(
        "R20-lease:shared-mac",
        details={**details, "title_en": "A shared MAC"},
        title="Bir MAC birden çok adreste",
    )
    site.alert("R20-replica", "R20-replica", details={"target": TARGET, **details})
    site.alert("R20-backup", details={"file": "arcilogger-20261028-0140.dump", **details})
    site.place_sealed(YESTERDAY)
    replica_state(site, YESTERDAY)
    backup_state(site, ok=True, finished=NOW - timedelta(hours=3), size=10)
    write_clock(site, sample(NOW - timedelta(minutes=3), 1500))
    return site


def test_cards_carry_no_client_address_no_person_and_no_replica_target(busy: Site) -> None:
    on = busy.config.model_copy(
        update={"replica": ReplicaConfig(enabled=True, target=Path(TARGET))}
    )
    status = busy.status(config=on)
    out = status.model_dump_json()
    for secret in (
        CLIENT_IP,
        CLIENT_MAC,
        PERSON,
        HOST,
        SENDER,
        "172.20.10.2",
        "NAS-01",
        "arcilogger-worm",
        "arcilogger-20261028-0140.dump",
        "srcip",
        "R15:switch-1",
        "R20-lease:",
    ):
        assert secret not in out, secret
    # what may be shown is shown
    by_id = {card.id: card for card in status.cards}
    assert "switch-1" in by_id["sources"].reason_tr
    assert facts(by_id["content_filter"])["profiles"] == ("default", "default")
    assert facts(by_id["lease_evidence"])["problems"] == (
        "Bir MAC birden çok adreste",
        "A shared MAC",
    )


def _fold(value: str) -> str:
    """Lower case with the dotless and dotted i folded and white space collapsed
    (``web/src/locales/locales.test.ts``)."""
    lowered = value.lower().replace("i̇", "i").replace("ı", "i")
    return " ".join(lowered.split())


#: ``COMPLIANCE_CLAIMS`` of ``tests/test_docs_consistency.py`` and the claim list of
#: ``web/src/locales/locales.test.ts``, plus the two words of contract 0.1 item 7.
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


def _strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, tuple | list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _strings(item)


def test_every_card_text_has_both_languages_and_no_conformity_word(busy: Site) -> None:
    # Every fixed text is a pair with both languages.
    pairs = 0
    for name in dir(texts):
        value = getattr(texts, name)
        text_pair = isinstance(value, tuple) and len(value) == 2
        if name.isupper() and text_pair and all(isinstance(item, str) for item in value):
            assert all(value), name
            pairs += 1
    assert pairs > 60
    for card_id, labels in texts.FACT_LABELS.items():
        assert labels, card_id
        for key, (label_tr, label_en) in labels.items():
            assert all((label_tr, label_en)), f"{card_id}.{key}"
    fixed = [
        text for name in dir(texts) if name.isupper() for text in _strings(getattr(texts, name))
    ]

    # Every computed card in several states has both languages.
    computed: list[str] = []
    configs: list[Config] = [
        busy.config,
        busy.config.model_copy(update={"tsa": PRODUCTION, "site_name": ""}),
    ]
    for config in configs:
        for now in (NOW, NOW + timedelta(days=40)):
            status = busy.status(now, config)
            computed += [status.notice_tr, status.notice_en]
            for card in status.cards:
                assert all((card.title_tr, card.title_en))
                assert all((card.reason_tr, card.reason_en))
                assert card.reason_tr != card.reason_en, card.id
                assert card.doc_tr.startswith("UYUM-DURUMU.md")
                assert card.doc_en.startswith("COMPLIANCE-STATUS.md")
                assert (card.verify_on_site_tr is None) == (card.verify_on_site_en is None)
                for fact in card.facts:
                    assert all((fact.label_tr, fact.label_en, fact.value_tr, fact.value_en))
                computed += [card.title_tr, card.title_en, card.reason_tr, card.reason_en]
                computed += [
                    part
                    for fact in card.facts
                    for part in (fact.label_tr, fact.label_en, fact.value_tr, fact.value_en)
                ]

    for value in fixed + computed:
        folded = _fold(value)
        assert [claim for claim in CLAIMS if _fold(claim) in folded] == [], value
    assert "5651 yükümlülüklerini desteklemek için tasarlanmıştır" in texts.NOTICE[0]
    assert "designed to support 5651 obligations" in texts.NOTICE[1]
