"""Official 5651 report ("Resmi Rapor", spec 9.5) end to end: content (Turkish text, request
details, method with the double-NAT explanation, results with the attribution chain, raw lines
with file and line number, hashes, seals and time stamps, page numbers, the legal wording), the
stored PDF (same bytes on every download, SHA-256 stored and audited, re-rendering the stored
inputs gives the same bytes), sealed days read from the vault, the optional time stamp of the
report and the permissions."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any, get_args

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from reportlab.pdfbase.pdfmetrics import stringWidth  # type: ignore[import-untyped]
from reportlab.platypus.doctemplate import LayoutError  # type: ignore[import-untyped]
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api import rawlines
from arcilogger_api.lookup_testing import (
    AT,
    DAY,
    DEVICE_NAME,
    NOW,
    PC,
    PC_MAC,
    PERSON_NAME,
    PHONE,
    PHONE_MAC,
    WAN,
    Site,
    accept_privacy,
    build_site,
    iso,
    mock_tsa,
    seal_day,
)
from arcilogger_api.pdftext import normalized, pdf_text
from arcilogger_api.reports import inputs as report_inputs
from arcilogger_api.reports import render, store, texts
from arcilogger_api.reports.fonts import SANS, register_fonts
from arcilogger_api.reports.inputs import _day_facts as day_facts
from arcilogger_api.reports.inputs import chain_first_day
from arcilogger_api.reports.render import provider_line
from arcilogger_api.testing import EXAMPLE_SITE_NAME, LoggedIn
from arcilogger_core.config import Config
from arcilogger_core.immutability import is_locked, unlock
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import site_day, utc_now
from arcilogger_core.tsa.verify import verify_token
from arcilogger_db.models import AuditLog, FgTraffic, Lease, LookupReport, MtDhcp
from arcilogger_db.settings import set_setting
from arcilogger_worker.correlation.leases import rebuild_day
from arcilogger_worker.lookup import DestinationMatch, LookupOutcome, LookupRequest
from arcilogger_worker.pdf.styles import fit
from arcilogger_worker.testing import Evidence, tr

Login = Callable[..., LoggedIn]
AUTHORITY = "İzmir Cumhuriyet Başsavcılığı Bilişim Suçları Bürosu"
FILE_NO = "2026/5651-ĞÜŞİÖÇ"
REASON = "Soruşturma kapsamında IP kullanıcısının tespiti (ilgili yazı: 13.10.2026)."


@pytest.fixture(autouse=True)
def _unlock_sealed_files(tmp_path: Path) -> Iterator[None]:
    yield
    for path in tmp_path.rglob("*"):
        if path.is_file() and is_locked(path):
            unlock(path)


@pytest.fixture
def site(app_session_factory: sessionmaker[Session], layout: Layout) -> Site:
    return build_site(app_session_factory, layout)


def body(**query: Any) -> dict[str, Any]:
    return {
        "request": {
            "authority": AUTHORITY,
            "file_no": FILE_NO,
            "request_date": "2026-10-13",
            "reason": REASON,
        },
        "query": {"at": iso(AT), **query},
    }


def signed_in(
    client: TestClient, login_as: Login, factory: sessionmaker[Session], role: str = "admin"
) -> LoggedIn:
    who = login_as(client, role, display_name=f"Denetçi Şükrü {role}")
    accept_privacy(factory, who.user_id)
    return who


def setting(factory: sessionmaker[Session], key: str, value: Any) -> None:
    with factory() as session:
        set_setting(session, key, value, updated_by="admin")
        session.commit()


def audits(factory: sessionmaker[Session], action: str) -> list[AuditLog]:
    with factory() as session:
        return list(
            session.scalars(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id))
        )


def report_row(factory: sessionmaker[Session], lookup_id: int) -> LookupReport:
    with factory() as session:
        return session.scalars(
            select(LookupReport).where(LookupReport.lookup_id == lookup_id)
        ).one()


def lookup(client: TestClient, **query: Any) -> dict[str, Any]:
    response = client.post("/api/v1/lookup/5651", json=body(**query))
    assert response.status_code == 201, response.text
    result: dict[str, Any] = response.json()
    return result


def download(client: TestClient, lookup_id: int) -> bytes:
    response = client.get(f"/api/v1/lookup/{lookup_id}/report.pdf")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == (
        f'attachment; filename="ARCILOGGER-5651-rapor-{lookup_id}.pdf"'
    )
    assert response.headers["x-arci-sha256"] == hashlib.sha256(response.content).hexdigest()
    if os.environ.get("ARCILOGGER_DUMP_REPORTS"):  # manual inspection of the layout
        folder = Path(os.environ["ARCILOGGER_DUMP_REPORTS"])
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"report-{lookup_id}.pdf").write_bytes(response.content)
    return response.content


def page_count(pdf: bytes) -> int:
    return len(re.findall(rb"/Type /Page\b", pdf))


# -- content --------------------------------------------------------------------------------------


def test_double_nat_report_content(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    site: Site,
) -> None:
    operator = signed_in(client, login_as, app_session_factory, "auditor")
    data = lookup(
        client, public_ip="192.0.2.44", public_port=41234, dest_ip="203.0.113.10", dest_port=443
    )
    pdf = download(client, data["id"])
    assert pdf.startswith(b"%PDF-1.4")
    text = normalized(pdf_text(pdf))

    # Turkish text survives (ToUnicode maps of the embedded DejaVu subsets)
    for phrase in (
        "5651 Erişim Kaydı Sorgu Raporu",
        "Resmi Rapor",
        "Talep bilgileri",
        AUTHORITY,
        FILE_NO,
        "13.10.2026",  # request date
        REASON,
        f"Denetçi Şükrü auditor ({operator.username})",
        EXAMPLE_SITE_NAME,  # site_name of api_config (conftest.py)
        "ARCILOGGER sürümü",
    ):
        assert phrase in text, phrase
    # query and method, double NAT, what would make it exact
    assert "12.10.2026 11:23:41 (UTC 12.10.2026 08:23:41) (Europe/Istanbul)" in text
    assert "192.0.2.44" in text
    assert "41234" in text
    assert "± 60 sn" in text
    assert "Aday listesi (zamana ve hedefe göre sıralı)" in text
    assert "Çift NAT" in text
    assert "ISS modeminin ilgili zamana ait NAT kaydı" in text
    assert "köprü (bridge) moduna" in text
    assert (
        "Kesin eşleşme için ISS modeminin NAT kaydı gerekir; aşağıda zaman ve hedefe göre "
        "adaylar listelenmiştir." in text
    )
    assert "Aday puanı (0-95" in text
    # result with the full chain
    assert f"{PC}:51001 → 203.0.113.10:443" in text
    assert f"{WAN}:51001" in text
    assert f"MAC {PC_MAC}" in text
    assert "PLN-PC14" in text
    assert DEVICE_NAME in text
    assert f"{PERSON_NAME} · departman Planlama · unvan Planlama Uzmanı" in text
    assert "hedef IP ve port eşleşti" in text
    # evidence: raw lines with file, line number, byte offset, hashes; not sealed yet
    inputs = report_row(app_session_factory, data["id"]).inputs
    evidence = inputs["evidence"]
    assert evidence
    assert all(item["line"]["status"] == "ok" for item in evidence)
    traffic = next(item for item in evidence if item["row_id"] == site.s1)
    raw_line = Evidence(Session(), layout).raw_line(
        "fortigate", DAY, traffic["raw"]["line"], traffic["raw"]["offset"]
    )
    assert traffic["line"]["sha256"] == hashlib.sha256(raw_line.rstrip(b"\n")).hexdigest()
    assert traffic["line"]["text"] == raw_line.rstrip(b"\n").decode()
    assert f"K{traffic['no']}" in text
    assert (
        f"kaynak fortigate · gün 12.10.2026 · satır {traffic['raw']['line']} · bayt "
        f"{traffic['raw']['offset']}" in text
    )
    assert "spool/fortigate/2026-10-12.log" in text
    assert 'devname="FIREWALL-01"' in text  # the raw line itself
    assert traffic["line"]["sha256"] in text
    spool = layout.spool_file("fortigate", DAY)
    assert hashlib.sha256(spool.read_bytes()).hexdigest() in text  # spool hash at report time
    assert "Henüz mühürlenmedi" in text
    assert "henüz mühürlenmedi" in text.lower()
    # how to verify, legal wording, page numbers on every page
    assert "certutil -hashfile" in text
    assert "zaman damgası istenmedi" in text
    assert "5651 yükümlülüklerini desteklemek için tasarlanmıştır" in text
    assert "hukuki uygunluk beyanı değildir" in text
    lowered = text.lower()
    assert "uyumludur" not in lowered
    assert "yasal olarak uyumlu" not in lowered
    assert "legally compliant" not in lowered
    pages = page_count(pdf)
    assert pages >= 2
    for number in range(1, pages + 1):
        assert f"Sayfa {number} / {pages}" in text
    assert report_row(app_session_factory, data["id"]).pages == pages


def test_exact_and_internal_reports(
    client: TestClient, login_as: Login, app_session_factory: sessionmaker[Session], site: Site
) -> None:
    setting(app_session_factory, "nat.public_ips", [WAN])
    signed_in(client, login_as, app_session_factory)
    exact = lookup(client, public_ip=WAN, public_port=51001, protocol=6)
    text = normalized(pdf_text(download(client, exact["id"])))
    assert "Kesin eşleşme (FortiGate NAT kaydı)" in text
    assert "Sonuç 1: kesin eşleşme" in text
    assert "6 (TCP)" in text
    assert "Çift NAT" not in text  # the public IP is configured: no double NAT
    assert "idari atama" in text  # the person link is administrative
    assert "sorgunun yapıldığı andaki cihaz-kişi atamasını" in text  # ... at lookup time

    internal = lookup(client, mac="02-00-5E-00-53-64")
    text = normalized(pdf_text(download(client, internal["id"])))
    assert "İç IP / MAC sorgusu (NAT adımı yok)" in text
    assert "MAC 02:00:5e:00:53:64" in text
    assert "Cihaz bir kişiye bağlanmamış." in text
    assert "rastgele (yerel) MAC: evet" in text  # 02:… is locally administered (a phone)

    nothing = lookup(client, public_ip="192.0.2.44", public_port=1, dest_ip="203.0.113.9")
    text = normalized(pdf_text(download(client, nothing["id"])))
    assert "Ölçütlere uyan kayıt bulunamadı" in text
    assert "Bu sonuç için kanıt satırı yok." in text


# -- storage and stability ------------------------------------------------------------------------


def test_the_stored_report_is_served_unchanged_and_audited(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    site: Site,
) -> None:
    admin = signed_in(client, login_as, app_session_factory)
    data = lookup(client, public_ip="192.0.2.44", public_port=41234)
    first = download(client, data["id"])
    # new evidence arrives afterwards: the stored report must not change
    with app_session_factory() as session:
        Evidence(session, layout).traffic(AT, PC, 61000)
        session.commit()
    second = download(client, data["id"])
    assert second == first
    row = report_row(app_session_factory, data["id"])
    digest = hashlib.sha256(first).hexdigest()
    assert (row.sha256, row.size, row.download_count) == (digest, len(first), 2)
    assert (row.username, row.user_id, row.timestamp_status) == (
        admin.username,
        admin.user_id,
        "not_requested",
    )
    assert row.path == f"reports/ARCILOGGER-5651-rapor-{data['id']}.pdf"
    assert (layout.exports / row.path).read_bytes() == first
    # re-rendering the stored inputs gives the same bytes (deterministic)
    assert render(json.loads(json.dumps(row.inputs))).pdf == first

    (created,) = audits(app_session_factory, "lookup.report")
    assert created.target == f"lookup:{data['id']}"
    assert created.details == {
        "lookup_id": data["id"],
        "sha256": digest,
        "size": len(first),
        "pages": row.pages,
        "version": row.version,
        "file": row.path,
        "timestamp": {"status": "not_requested"},
    }
    downloads = audits(app_session_factory, "lookup.report_download")
    assert [d.details for d in downloads] == [
        {"lookup_id": data["id"], "file": "pdf", "sha256": digest, "download": 1},
        {"lookup_id": data["id"], "file": "pdf", "sha256": digest, "download": 2},
    ]
    shown = client.get(f"/api/v1/lookup/{data['id']}").json()["report"]
    assert (shown["sha256"], shown["pages"], shown["download_count"]) == (digest, row.pages, 2)
    listed = client.get("/api/v1/lookups").json()["items"][0]
    assert listed["report_sha256"] == digest
    # no token without the time stamp setting
    assert client.get(f"/api/v1/lookup/{data['id']}/report.pdf.zd").status_code == 404


def test_a_changed_or_missing_report_file_is_refused_not_regenerated(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    site: Site,
) -> None:
    signed_in(client, login_as, app_session_factory)
    data = lookup(client, internal_ip=PC)
    pdf = download(client, data["id"])
    path = layout.exports / report_row(app_session_factory, data["id"]).path
    path.write_bytes(pdf + b"% appended\n")
    response = client.get(f"/api/v1/lookup/{data['id']}/report.pdf")
    assert (response.status_code, response.json()["error"]["code"]) == (409, "report_damaged")
    path.unlink()
    response = client.get(f"/api/v1/lookup/{data['id']}/report.pdf")
    assert (response.status_code, response.json()["error"]["code"]) == (409, "report_missing")
    assert len(audits(app_session_factory, "lookup.report")) == 1
    assert len(audits(app_session_factory, "lookup.report_download")) == 1


def test_render_is_deterministic(
    client: TestClient, login_as: Login, app_session_factory: sessionmaker[Session], site: Site
) -> None:
    signed_in(client, login_as, app_session_factory)
    data = lookup(client, public_ip="192.0.2.44", public_port=41234)
    pdf = download(client, data["id"])
    inputs = report_row(app_session_factory, data["id"]).inputs
    again = render(inputs)
    assert again.pdf == pdf
    assert again.sha256 == hashlib.sha256(pdf).hexdigest()
    changed = json.loads(json.dumps(inputs))
    changed["lookup"]["file_no"] = "2026/1"
    assert render(changed).pdf != pdf
    assert b"/CreationDate (D:20000101000000" in pdf  # reportlab invariant mode
    assert b"Helvetica" not in pdf  # only the embedded DejaVu subsets
    assert b"/Lang (tr-TR)" in pdf


# -- sealed days and time stamps ------------------------------------------------------------------


def test_report_of_a_sealed_day_reads_the_vault_and_verifies_the_seal(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    api_config: Config,
    layout: Layout,
    site: Site,
) -> None:
    spool_before = layout.spool_file("fortigate", DAY).read_bytes()
    seal_day(api_config, layout, DAY)
    assert not layout.spool_file("fortigate", DAY).exists()  # the sealer took it over
    signed_in(client, login_as, app_session_factory)
    data = lookup(client, public_ip="192.0.2.44", public_port=41234, dest_ip="203.0.113.10")
    pdf = download(client, data["id"])
    text = normalized(pdf_text(pdf))
    inputs = report_row(app_session_factory, data["id"]).inputs
    files = {(f["source"], f["day"]): f for f in inputs["files"]}
    fortigate = files[("fortigate", DAY.isoformat())]
    assert (fortigate["status"], fortigate["matches_manifest"]) == ("sealed", True)
    assert fortigate["file"] == "vault/2026/10/12/fortigate.log.gz"
    assert fortigate["manifest"]["sha256_uncompressed"] == hashlib.sha256(spool_before).hexdigest()
    (day,) = inputs["days"]
    verification = day["verification"]
    assert verification["ok"] is True, verification
    assert day["tsa"] == {
        "provider": "mock",
        "legal_grade": False,
        "legal_grade_claimed": False,
        "test_stamp": True,
    }
    assert verification["root_sha256"]
    manifest = layout.manifest(DAY)
    assert day["manifest"]["sha256"] == hashlib.sha256(manifest.read_bytes()).hexdigest()
    for value in (
        day["manifest"]["sha256"],
        day["token"]["sha256"],
        verification["serial"],
        fortigate["sha256"],
        "vault/2026/10/12/manifest-2026-10-12.json",
    ):
        assert value in text, value
    assert "DOĞRULANDI" in text
    assert "YASAL GEÇERLİ DEĞİL" in text  # the mock TSA is never legally valid
    assert "teyit edilmedi" not in text

    # A Kamu SM production day whose root is not (yet) in tsa.legal_root_fingerprints (the
    # default): neutral, never "test"; with the root listed: legal grade (decision 5).
    def provider_row(tsa: dict[str, Any], ok: bool = True) -> str:
        changed = json.loads(json.dumps(inputs))
        changed["days"][0]["tsa"] = tsa
        changed["days"][0]["verification"]["ok"] = ok
        return normalized(pdf_text(render(changed).pdf))

    claimed = {"provider": "zamane", "legal_grade": False, "legal_grade_claimed": True}
    unlisted = provider_row(claimed)
    assert "test/geliştirme" not in unlisted
    assert "kayıtlı ve damga doğrulandı" in unlisted
    assert "yasal sınıf olarak teyit edilmedi" in unlisted
    assert verification["root_sha256"] in unlisted.replace(" ", ""), unlisted
    failed = provider_row(claimed, ok=False)
    assert "fakat damga doğrulanamadı" in failed
    assert "test/geliştirme" not in failed
    listed = provider_row(claimed | {"legal_grade": True})
    assert "zamane — Kamu SM üretim sunucusu (zd.kamusm.gov.tr) damgası" in listed
    assert "yasal geçerli sağlayıcı" not in listed  # finding 6: the server is named, no claim
    assert "evet — dosya mühürlendiği gibi" in text
    assert "zincirin ilk günü" in text
    first_traffic = next(item for item in inputs["evidence"] if item["table"] == "fg_traffic")
    assert first_traffic["line"]["origin"] == "vault"
    offset = first_traffic["line"]["offset"]
    expected = spool_before[offset : spool_before.index(b"\n", offset)]
    assert first_traffic["line"]["text"] == expected.decode()


@pytest.mark.parametrize("damage", ["content", "gzip"])
def test_report_of_a_day_whose_vault_file_was_changed(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    api_config: Config,
    layout: Layout,
    site: Site,
    damage: str,
) -> None:
    seal_day(api_config, layout, DAY)
    sealed = layout.vault_day(DAY) / "core-r1.log.gz"
    unlock(sealed)
    original = gzip.decompress(sealed.read_bytes())
    if damage == "content":  # a valid gzip file with other content
        sealed.write_bytes(gzip.compress(original.replace(b"PLN-PC14", b"PLN-PC15")))
    else:  # the compressed data itself is damaged
        data = bytearray(sealed.read_bytes())
        data[len(data) // 2] ^= 0xFF
        sealed.write_bytes(bytes(data))
    signed_in(client, login_as, app_session_factory)
    found = lookup(client, internal_ip=PC)
    text = normalized(pdf_text(download(client, found["id"])))
    inputs = report_row(app_session_factory, found["id"]).inputs
    files = {f["source"]: f for f in inputs["files"]}
    assert files["core-r1"]["matches_manifest"] is False
    assert inputs["days"][0]["verification"]["ok"] is False
    assert "DOĞRULANAMADI" in text
    assert "HAYIR — dosya farklı" in text
    lines = [item["line"] for item in inputs["evidence"] if item["raw"]["source"] == "core-r1"]
    if damage == "content":
        assert all(line["status"] == "ok" for line in lines)
        assert "PLN-PC15" in text  # the report shows what the file says now, flagged above
    else:
        assert files["core-r1"]["error"].startswith("core-r1.log.gz: damaged gzip data")
        assert {line["status"] for line in lines} <= {"ok", "unreadable"}


def test_a_seal_that_cannot_be_checked_is_reported(
    app: FastAPI,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    api_config: Config,
    layout: Layout,
    site: Site,
) -> None:
    """No pinned root certificates (not the mock provider, no ``tsa.roots_dir``): the report
    says the seal could not be verified instead of claiming it."""
    seal_day(api_config, layout, DAY)
    (layout.state / "mock-tsa" / "root.pem").unlink()
    app.state.arci.config = Config.model_validate(
        {**api_config.model_dump(), "tsa": {"provider": "rfc3161"}}
    )
    with TestClient(app, base_url="http://testserver") as client:
        signed_in(client, login_as, app_session_factory)
        data = lookup(client, internal_ip=PC)
        text = normalized(pdf_text(download(client, data["id"])))
    (day,) = report_row(app_session_factory, data["id"]).inputs["days"]
    assert day["verification"]["ok"] is False
    assert day["verification"]["errors"][0].startswith("no trusted root certificates")
    assert "DOĞRULANAMADI / NOT VERIFIED: no trusted root certificates" in text


def test_ip_conflict_unknown_client_and_wrong_offset_in_the_report(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    site: Site,
) -> None:
    rogue = "00:0c:29:00:00:99"
    with app_session_factory() as session:
        evidence = Evidence(session, layout)
        evidence.arp(tr(DAY, 11, 4, 56), PC, rogue, "vlan42")
        evidence.arp(tr(DAY, 11, 34, 56), PC, rogue, "vlan42")
        evidence.traffic(AT, "172.20.41.77", 50000, "198.51.100.7", 443)  # no lease
        session.execute(
            update(FgTraffic)
            .where(FgTraffic.id == site.s3)
            .values(raw_offset=FgTraffic.raw_offset + 1)
        )
        session.commit()
        rebuild_day(session, DAY, horizon=NOW)
        session.commit()
    signed_in(client, login_as, app_session_factory)
    data = lookup(client, public_ip="192.0.2.44", public_port=41234, tolerance_seconds=120)
    assert data["outcome"]["truncated"] is False
    text = normalized(pdf_text(download(client, data["id"])))
    assert "Olası diğer zincir (IP çakışması)" in text
    assert f"MAC {rogue}" in text
    assert f"MAC {PC_MAC}" in text
    assert "Zincir güveni / Confidence düşük" in text
    # Every chain of the conflict is low: the other device never reads as more reliable.
    alternative = text.split("Olası diğer zincir (IP çakışması)", 1)[1]
    found = re.search(r"Zincir güveni / Confidence (\S+)", alternative)
    assert found is not None
    assert found.group(1) == "düşük"
    assert "172.20.41.77 adresi için bu anda geçerli bir IP-MAC kaydı (kira) yok" in text
    assert "UYARI: satır, veritabanında kayıtlı bayt konumunda başlamıyor." in text
    inputs = report_row(app_session_factory, data["id"]).inputs
    moved = next(item for item in inputs["evidence"] if item["row_id"] == site.s3)
    assert moved["line"]["status"] == "offset_mismatch"
    assert moved["line"]["offset"] == moved["line"]["expected_offset"] - 1
    (entry,) = audits(app_session_factory, "lookup.run")
    conflicted = [r for r in entry.details["results"] if "alternatives" in r]
    assert conflicted
    assert conflicted[0]["alternatives"][0]["person"] is None


def test_report_time_stamp_with_the_configured_provider(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    site: Site,
) -> None:
    tsa = mock_tsa(layout)
    credits = tsa.credits
    setting(app_session_factory, "lookup.report_timestamp", True)
    signed_in(client, login_as, app_session_factory)
    data = lookup(client, internal_ip=PC)
    pdf = download(client, data["id"])
    text = normalized(pdf_text(pdf))
    assert "RFC 3161 zaman damgasına gönderilir (1 kontör)" in text
    assert f"ARCILOGGER-5651-rapor-{data['id']}.pdf.zd" in text
    assert "UYARI: Yapılandırılan zaman damgası sağlayıcısı (mock) yasal geçerli değildir" in text
    row = report_row(app_session_factory, data["id"])
    assert row.timestamp_status == "stamped"
    stamp = row.timestamp or {}
    assert (stamp["provider"], stamp["legal_grade"], stamp["verified"]) == ("mock", False, True)
    assert stamp["token_file"] == f"reports/ARCILOGGER-5651-rapor-{data['id']}.pdf.zd"
    assert tsa.credits == credits - 1  # one kontör per report
    response = client.get(f"/api/v1/lookup/{data['id']}/report.pdf.zd")
    assert response.status_code == 200
    token = response.content
    assert hashlib.sha256(token).hexdigest() == stamp["token_sha256"]
    check = verify_token(token, roots=[tsa.root_certificate], digest=hashlib.sha256(pdf).digest())
    assert check.ok
    assert check.info is not None
    assert check.info.serial == stamp["serial"]
    (created,) = audits(app_session_factory, "lookup.report")
    assert created.details["timestamp"] == {
        "status": "stamped",
        "provider": "mock",
        "legal_grade": False,
        "serial": stamp["serial"],
        "gen_time": stamp["gen_time"],
        "verified": True,
    }
    assert audits(app_session_factory, "lookup.report_download")[-1].details["file"] == "zd"
    # downloading again neither renders nor stamps again
    assert download(client, data["id"]) == pdf
    assert tsa.credits == credits - 1
    # a changed token file is refused
    (layout.exports / stamp["token_file"]).write_bytes(token[:-1] + b"\x00")
    response = client.get(f"/api/v1/lookup/{data['id']}/report.pdf.zd")
    assert (response.status_code, response.json()["error"]["code"]) == (409, "report_damaged")


def test_a_failed_time_stamp_still_delivers_the_report(
    app: FastAPI,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    api_config: Config,
    layout: Layout,
    site: Site,
) -> None:
    # Zamane without the client jar: the provider cannot be built
    app.state.arci.config = Config.model_validate(
        {**api_config.model_dump(), "tsa": {"provider": "zamane"}}
    )
    setting(app_session_factory, "lookup.report_timestamp", True)
    with TestClient(app, base_url="http://testserver") as client:
        signed_in(client, login_as, app_session_factory)
        data = lookup(client, internal_ip=PC)
        text = normalized(pdf_text(download(client, data["id"])))
        assert "zamane sağlayıcısıyla RFC 3161 zaman damgasına gönderilir" in text
        row = report_row(app_session_factory, data["id"])
        assert row.timestamp_status == "failed"
        assert "zamane_jar" in (row.timestamp or {})["error"]
        assert client.get(f"/api/v1/lookup/{data['id']}/report.pdf.zd").status_code == 404
        (created,) = audits(app_session_factory, "lookup.report")
        assert created.details["timestamp"]["status"] == "failed"


# -- permissions ----------------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["hr", "viewer"])
def test_roles_without_lookup_permission_get_no_report(
    app: FastAPI, login_as: Login, app_session_factory: sessionmaker[Session], site: Site, role: str
) -> None:
    with (
        TestClient(app, base_url="http://testserver") as admin,
        TestClient(app, base_url="http://testserver") as other,
    ):
        signed_in(admin, login_as, app_session_factory)
        data = lookup(admin, internal_ip=PC)
        signed_in(other, login_as, app_session_factory, role)
        for path in ("report.pdf", "report.pdf.zd"):
            response = other.get(f"/api/v1/lookup/{data['id']}/{path}")
            assert (response.status_code, response.json()["error"]["code"]) == (403, "forbidden")
    assert audits(app_session_factory, "lookup.report") == []


def test_an_administrator_may_generate_the_report_of_an_auditors_lookup(
    app: FastAPI, login_as: Login, app_session_factory: sessionmaker[Session], site: Site
) -> None:
    with (
        TestClient(app, base_url="http://testserver") as auditor_client,
        TestClient(app, base_url="http://testserver") as admin_client,
    ):
        auditor = signed_in(auditor_client, login_as, app_session_factory, "auditor")
        admin = signed_in(admin_client, login_as, app_session_factory, "admin")
        data = lookup(auditor_client, internal_ip=PC)
        text = normalized(pdf_text(download(admin_client, data["id"])))
        assert f"Sorguyu yapan / Operator Denetçi Şükrü auditor ({auditor.username})" in text
        assert f"Raporu oluşturan / Generated by Denetçi Şükrü admin ({admin.username})" in text
        assert download(auditor_client, data["id"])  # the owner gets the same report
        downloads = audits(app_session_factory, "lookup.report_download")
        assert [d.actor_id for d in downloads] == [admin.user_id, auditor.user_id]


# -- long values, double NAT without candidates, lost seals, unreadable files ---------------------


def test_long_reason_and_long_raw_lines_are_split_across_pages(
    client: TestClient, login_as: Login, app_session_factory: sessionmaker[Session], site: Site
) -> None:
    """A table row taller than a page is split (``splitInRow``), so every value the API accepts
    can be laid out: the longest reason with many line breaks, a raw line of 64 KiB without a
    single space (the default ``collector.max_message_bytes``); a 1 MiB line (the setting allows
    it) is cut with a warning."""
    signed_in(client, login_as, app_session_factory)
    reason = ("Satır ğüşıöç\n" * 400)[:3990] + "\nSON SATIR"
    assert len(reason) <= 4000
    response = client.post(
        "/api/v1/lookup/5651",
        json={
            **body(public_ip="192.0.2.44", public_port=41234),
            "request": {**body()["request"], "reason": reason},
        },
    )
    assert response.status_code == 201, response.text
    data = response.json()
    pdf = download(client, data["id"])
    text = normalized(pdf_text(pdf))
    assert "SON SATIR" in text
    assert text.count("Satır ğüşıöç") >= 300
    pages = report_row(app_session_factory, data["id"]).pages
    assert pages > 8
    assert f"Sayfa {pages} / {pages}" in text

    inputs = report_row(app_session_factory, data["id"]).inputs
    item = next(item for item in inputs["evidence"] if item["line"]["status"] == "ok")
    for length, cut in ((64 * 1024, False), (1024 * 1024, True)):
        changed = json.loads(json.dumps(inputs))
        line = next(e for e in changed["evidence"] if e["no"] == item["no"])["line"]
        line["text"] = "url=" + "ab" * (length // 2) + "SON"
        line["length"] = len(line["text"])
        shown = normalized(pdf_text(render(changed).pdf))
        assert ("Satır çok uzun" in shown) is cut
        if cut:
            assert f"Satır çok uzun ({line['length']} bayt): raporda ilk 73728 karakteri" in shown
        else:
            assert "abSON" in shown.replace(" ", "")


def test_double_nat_without_candidates_does_not_promise_a_candidate_list(
    client: TestClient, login_as: Login, app_session_factory: sessionmaker[Session], site: Site
) -> None:
    signed_in(client, login_as, app_session_factory)
    nothing = lookup(client, public_ip="192.0.2.44", public_port=1, dest_ip="203.0.113.9")
    assert (nothing["mode"], nothing["outcome"]["double_nat"]) == ("none", True)
    text = normalized(pdf_text(download(client, nothing["id"])))
    assert "Sonuç yok / No result" in text
    assert "Çift NAT" in text
    assert "kesin olarak eşlenemez" in text
    assert "aday oturum da bulunamadı; bu nedenle rapor bir sonuç içermez" in text
    assert "ISS modeminin ilgili zamana ait NAT kaydı" in text  # what would make it exact
    assert "aday listesidir" not in text
    assert "The result is a list of candidate sessions" not in text

    found = lookup(client, public_ip="192.0.2.44", public_port=41234)
    text = normalized(pdf_text(download(client, found["id"])))
    assert "Sonuç, zaman penceresi (± tolerans) içindeki oturumların" in text
    assert "aday oturum da bulunamadı" not in text


@pytest.mark.parametrize("removed", [("manifest", "token"), ("token",)])
def test_a_lost_seal_is_never_shown_as_not_sealed_yet(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    api_config: Config,
    layout: Layout,
    site: Site,
    removed: tuple[str, ...],
) -> None:
    """The day was sealed, its files are in the vault, but the manifest and/or token are gone:
    the report says the seal is missing (red), not "henüz mühürlenmedi"."""
    seal_day(api_config, layout, DAY)
    for name in removed:
        path = layout.manifest(DAY) if name == "manifest" else layout.token(DAY)
        unlock(path)
        path.unlink()
    signed_in(client, login_as, app_session_factory)
    data = lookup(client, public_ip="192.0.2.44", public_port=41234, dest_ip="203.0.113.10")
    text = normalized(pdf_text(download(client, data["id"])))
    inputs = report_row(app_session_factory, data["id"]).inputs
    fortigate = next(f for f in inputs["files"] if f["source"] == "fortigate")
    assert (fortigate["status"], fortigate["origin"]) == ("seal_missing", "vault")
    (day,) = inputs["days"]
    assert (day["status"], day["sealed"], day["verification"]["ok"]) == (
        "seal_missing",
        False,
        False,
    )
    assert "MÜHÜR BULUNAMADI" in text
    assert "SEAL NOT FOUND" in text
    assert "henüz mühürlenmedi" not in text.lower()
    assert "YOK — kasada bulunamadı" in text
    assert "DOĞRULANAMADI" in text
    if "manifest" in removed:
        assert fortigate["matches_manifest"] is None  # nothing to compare with
        assert "dosya farklı" not in text
        assert "Manifestle eşleşme" not in text
        assert day["verification"]["errors"] == ["manifest missing"]
    else:
        assert fortigate["matches_manifest"] is True
        assert "evet — dosya mühürlendiği gibi" in text
        assert "time-stamp token missing" in day["verification"]["errors"]
        assert "zincirin ilk günü" in text


def test_an_unsealed_day_is_flagged_once_sealing_is_overdue(
    client: TestClient, login_as: Login, app_session_factory: sessionmaker[Session], site: Site
) -> None:
    signed_in(client, login_as, app_session_factory)
    data = lookup(client, public_ip="192.0.2.44", public_port=41234)
    download(client, data["id"])
    inputs = report_row(app_session_factory, data["id"]).inputs
    (day,) = inputs["days"]
    assert day["status"] == "unsealed"
    assert day["late"] is ((site_day(utc_now()) - DAY).days >= 2)
    for late in (False, True):
        changed = json.loads(json.dumps(inputs))
        changed["days"][0]["late"] = late
        text = normalized(pdf_text(render(changed).pdf))
        assert "Henüz mühürlenmedi" in text
        assert ("gecikmiş" in text) is late


def test_the_chain_start_is_checked_not_assumed(
    api_config: Config, layout: Layout, app_session_factory: sessionmaker[Session], site: Site
) -> None:
    """A manifest without a link to the previous day is "the first day of the chain" only when
    it is the oldest sealed day (the rule of ``python -m arcilogger_core verify``)."""
    seal_day(api_config, layout, DAY)
    tsa = mock_tsa(layout)
    assert chain_first_day(layout) == DAY
    for first_day, ok in ((DAY, True), (DAY - timedelta(days=1), False)):
        facts = day_facts(
            layout,
            DAY,
            "sealed",
            roots=[tsa.root_certificate],
            roots_error=None,
            first_day=first_day,
            today=DAY + timedelta(days=1),
        )
        assert facts["verification"]["ok"] is ok, facts["verification"]
        assert facts["previous"] is None
        inputs = {
            **_minimal_inputs(),
            "days": [facts],
        }
        text = normalized(pdf_text(render(inputs).pdf))
        if ok:
            assert "zincirin ilk günü: kasadaki en eski mühürlü gün" in text
        else:
            assert "chain broken: no link to the previous day" in text
            assert "YOK — manifest önceki güne bağlanmıyor" in text


def _minimal_inputs() -> dict[str, Any]:
    """Inputs of a report without results (enough for rendering single sections)."""
    outcome = LookupOutcome(
        request=LookupRequest(at=AT, public_ip="192.0.2.44", public_port=1),
        mode="none",
        matches=(),
        double_nat=True,
        attribution=None,
        explanations=(),
        data_window=None,
        tolerance_seconds=60,
        generated_at=NOW,
    ).to_dict()
    return {
        "lookup": {
            "id": 1,
            "created_at": NOW.isoformat(),
            "authority": AUTHORITY,
            "file_no": FILE_NO,
            "request_date": "2026-10-13",
            "reason": REASON,
            "operator": {"username": "denetci", "display_name": "Denetçi"},
        },
        "outcome": outcome,
        "evidence": [],
        "files": [],
        "days": [],
        "generated": {
            "at": NOW.isoformat(),
            "by": {"username": "denetci", "display_name": "Denetçi"},
            "version": "0.0.0",
            "site_name": EXAMPLE_SITE_NAME,
            "report_file": "x.pdf",
        },
        "timestamp": {"requested": False},
    }


def test_a_report_without_an_organisation_name_leaves_the_name_out() -> None:
    """``site_name`` is empty until the installation sets it: no broken sentence, no empty
    field between separators; a configured name reads as before."""
    named = _minimal_inputs()
    text = normalized(pdf_text(render(named).pdf))
    assert "Bu rapor, Örnek A.Ş. ağında ARCILOGGER ile yapılan 1 numaralı 5651 sorgusunun" in text
    assert "Kurum / Organisation Örnek A.Ş. Sorguyu yapan" in text
    assert "ARCILOGGER 0.0.0 · Örnek A.Ş. · Oluşturma 13.10.2026 09:00:00" in text
    unnamed = json.loads(json.dumps(named))
    unnamed["generated"]["site_name"] = ""
    text = normalized(pdf_text(render(unnamed).pdf))
    assert "Bu rapor, ARCILOGGER ile yapılan 1 numaralı 5651 sorgusunun sonucunu" in text
    assert "ağında" not in text
    assert "Kurum / Organisation — Sorguyu yapan" in text
    assert "ARCILOGGER 0.0.0 · Oluşturma 13.10.2026 09:00:00" in text
    assert "·  ·" not in text


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (PermissionError(13, "Access is denied"), 503, "report_access_denied"),
        (OSError(5, "Input/output error"), 503, "report_evidence_unreadable"),
    ],
)
def test_no_report_is_stored_while_a_cited_file_cannot_be_read(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    site: Site,
    monkeypatch: pytest.MonkeyPatch,
    error: OSError,
    status: int,
    code: str,
) -> None:
    """Windows: the API service may not read ``spool`` (installer ACLs). The report is refused
    with a clear error instead of being stored for good with "file unreadable" — once the
    permission is fixed, the same request makes the complete report."""
    signed_in(client, login_as, app_session_factory)
    data = lookup(client, public_ip="192.0.2.44", public_port=41234)
    real_stat = Path.stat

    def stat(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self.is_relative_to(layout.spool):
            raise error
        return real_stat(self, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "stat", stat)
        response = client.get(f"/api/v1/lookup/{data['id']}/report.pdf")
    failure = response.json()["error"]
    assert (response.status_code, failure["code"]) == (status, code)
    assert "spool/" in failure["message_tr"]
    with app_session_factory() as session:
        assert session.scalars(select(LookupReport)).all() == []
    assert not (layout.exports / "reports").exists() or not any(
        (layout.exports / "reports").iterdir()
    )
    assert audits(app_session_factory, "lookup.report") == []
    assert audits(app_session_factory, "lookup.report_download") == []
    text = normalized(pdf_text(download(client, data["id"])))  # permission fixed
    assert "Henüz mühürlenmedi" in text


def test_a_layout_failure_stores_nothing_and_says_so(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    site: Site,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signed_in(client, login_as, app_session_factory)
    data = lookup(client, internal_ip=PC)

    def broken(inputs: Any) -> Any:
        raise LayoutError("Flowable too large")

    monkeypatch.setattr(store, "render", broken)
    response = client.get(f"/api/v1/lookup/{data['id']}/report.pdf")
    assert (response.status_code, response.json()["error"]["code"]) == (
        500,
        "report_render_failed",
    )
    with app_session_factory() as session:
        assert session.scalars(select(LookupReport)).all() == []
    assert audits(app_session_factory, "lookup.report") == []


@pytest.mark.parametrize("moment", ["before_first_file", "before_second_file", "mid_read"])
def test_a_day_sealed_while_the_report_is_collected_is_read_again(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    api_config: Config,
    layout: Layout,
    site: Site,
    monkeypatch: pytest.MonkeyPatch,
    moment: str,
) -> None:
    """The nightly Seal task finishes the cited day while the report reads its files (it moves
    the files to the vault and deletes the spool copies). The stored report must not say "seal
    not found" for good: the day is read again as sealed."""
    signed_in(client, login_as, app_session_factory)
    data = lookup(client, internal_ip=PC)  # cites core-r1 (DHCP) and fortigate (traffic) lines
    real_facts, real_scan = report_inputs._file_facts, rawlines.scan_file
    calls: list[str] = []

    def seal_once() -> None:
        if not layout.manifest(DAY).exists():
            seal_day(api_config, layout, DAY)

    def file_facts(*args: Any, **kwargs: Any) -> Any:
        calls.append(args[1])
        if (moment, len(calls)) in (("before_first_file", 1), ("before_second_file", 2)):
            seal_once()
        return real_facts(*args, **kwargs)

    def scan_file(origin: rawlines.Origin, path: Path, *args: Any, **kwargs: Any) -> Any:
        if moment == "mid_read" and origin == "spool":
            seal_once()  # the spool file is gone before it is opened
        return real_scan(origin, path, *args, **kwargs)

    monkeypatch.setattr(report_inputs, "_file_facts", file_facts)
    monkeypatch.setattr(report_inputs, "scan_file", scan_file)
    text = normalized(pdf_text(download(client, data["id"])))
    assert layout.manifest(DAY).exists()
    inputs = _inputs(app_session_factory, data["id"])
    assert [day["status"] for day in inputs["days"]] == ["sealed"]
    assert inputs["days"][0]["verification"]["ok"] is True
    assert {(f["status"], f["origin"]) for f in inputs["files"]} == {("sealed", "vault")}
    assert "MÜHÜR BULUNAMADI" not in text
    assert "SEAL NOT FOUND" not in text
    assert "DOĞRULANDI" in text


def test_no_report_while_a_day_keeps_changing_its_seal_state(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    site: Site,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signed_in(client, login_as, app_session_factory)
    data = lookup(client, internal_ip=PC)
    flips = iter(range(1000))
    monkeypatch.setattr(report_inputs, "_seal_complete", lambda layout, day: next(flips) % 2 == 1)
    response = client.get(f"/api/v1/lookup/{data['id']}/report.pdf")
    failure = response.json()["error"]
    assert (response.status_code, failure["code"]) == (503, "report_seal_in_progress")
    assert "vault/2026/10/12/" in failure["message_tr"]
    with app_session_factory() as session:
        assert session.scalars(select(LookupReport)).all() == []
    monkeypatch.undo()
    assert "Henüz mühürlenmedi" in normalized(pdf_text(download(client, data["id"])))


def _sealed_day(*, legal_grade: bool, ok: bool) -> dict[str, Any]:
    return {
        "day": DAY.isoformat(),
        "sealed": True,
        "status": "sealed",
        "manifest": {"file": "vault/2026/10/12/manifest-2026-10-12.json", "sha256": "a" * 64},
        "token": {"file": "vault/2026/10/12/manifest-2026-10-12.json.zd", "sha256": "b" * 64},
        "tsa": {"provider": "zamane", "legal_grade": legal_grade},
        "stamped_late": False,
        "previous": None,
        "chain_first_day": DAY.isoformat(),
        "verification": {
            "ok": ok,
            "errors": [] if ok else ["token: signature does not verify"],
            "warnings": [],
            "serial": "1234",
            "gen_time": NOW.isoformat(),
            "root": "Kamu SM Kök",
            "tsa_name": "Kamu SM TSA",
            "policy": "2.16.792.1.2.1.1.5.7.3.1",
        },
    }


@pytest.mark.parametrize("ok", [True, False])
def test_the_report_names_the_server_and_never_claims_legal_validity(ok: bool) -> None:
    """Spec 0.6/21: the official report never states that a stamp is legally valid (the web
    badge already only names the server); a manifest's own claim is shown positively only next
    to a passing verification."""
    inputs = {**_minimal_inputs(), "days": [_sealed_day(legal_grade=True, ok=ok)]}
    text = normalized(pdf_text(render(inputs).pdf))
    assert "yasal geçerli sağlayıcı" not in text
    assert re.search(r"yasal geçerli(?!liği| değil)", text) is None
    assert "legally valid" not in text.replace("not legally valid", "")
    if ok:
        assert "zamane — Kamu SM üretim sunucusu (zd.kamusm.gov.tr) damgası" in text
    else:
        assert "fakat damga doğrulanamadı" in text
    test_day = {**_minimal_inputs(), "days": [_sealed_day(legal_grade=False, ok=True)]}
    assert "YASAL GEÇERLİ DEĞİL" in normalized(pdf_text(render(test_day).pdf))


@pytest.mark.parametrize(
    ("tsa", "ok", "expected", "style"),
    [
        # Production, chain confirmed against tsa.legal_root_fingerprints.
        (
            {"provider": "zamane", "legal_grade": True, "legal_grade_claimed": True},
            True,
            "zamane — Kamu SM üretim sunucusu (zd.kamusm.gov.tr) damgası",
            "base",
        ),
        # Production, verified, but the site listed no root fingerprint (the default): neutral,
        # never "YASAL GEÇERLİ DEĞİL (test/geliştirme sağlayıcısı)".
        (
            {"provider": "zamane", "legal_grade": False, "legal_grade_claimed": True},
            True,
            "yasal sınıf olarak teyit edilmedi",
            "note",
        ),
        # Production claimed, the check failed.
        (
            {"provider": "zamane", "legal_grade": False, "legal_grade_claimed": True},
            False,
            "fakat damga doğrulanamadı",
            "warn",
        ),
        # A production claim on a test (mock) stamp.
        (
            {
                "provider": "zamane",
                "legal_grade": False,
                "legal_grade_claimed": True,
                "test_stamp": True,
            },
            True,
            "fakat damga test (mock) zaman damgası biriminden",
            "warn",
        ),
        # The mock TSA.
        (
            {"provider": "mock", "legal_grade": False, "legal_grade_claimed": False},
            True,
            "mock — YASAL GEÇERLİ DEĞİL (test/geliştirme sağlayıcısı)",
            "warn",
        ),
        # Another server (Kamu SM test server, a generic RFC 3161 TSA): not production.
        (
            {"provider": "rfc3161", "legal_grade": False, "legal_grade_claimed": False},
            True,
            "rfc3161 — YASAL GEÇERLİ DEĞİL (Kamu SM üretim sunucusu değil)",
            "warn",
        ),
        # Inputs stored before decision 5 (legal_grade was the manifest's claim): as before.
        (
            {"provider": "zamane", "legal_grade": True},
            True,
            "zamane — Kamu SM üretim sunucusu (zd.kamusm.gov.tr) damgası",
            "base",
        ),
        (
            {"provider": "zamane", "legal_grade": False},
            True,
            "zamane — YASAL GEÇERLİ DEĞİL (Kamu SM üretim sunucusu değil)",
            "warn",
        ),
    ],
)
def test_the_provider_row_keeps_the_server_and_the_confirmed_grade_apart(
    tsa: dict[str, Any], ok: bool, expected: str, style: str
) -> None:
    """Orchestrator decision 5: whether the day is a production one and whether its legal grade
    is confirmed by the verified chain are separate facts; only a test stamp is called one."""
    text, got_style = provider_line(tsa, {"ok": ok})
    assert expected in text
    assert got_style == style
    if tsa.get("legal_grade_claimed") and not tsa.get("test_stamp"):
        assert "YASAL GEÇERLİ DEĞİL" not in text
        assert "test/geliştirme" not in text
    day = {**_sealed_day(legal_grade=False, ok=ok), "tsa": tsa}
    rendered = normalized(pdf_text(render({**_minimal_inputs(), "days": [day]}).pdf))
    assert normalized(expected) in rendered


def test_the_report_inputs_keep_the_claim_and_the_test_stamp_apart(
    api_config: Config, layout: Layout
) -> None:
    """A day sealed with the mock TSA: no production claim, a test stamp, no legal grade."""
    seal_day(api_config, layout, DAY)
    facts = day_facts(
        layout,
        DAY,
        "sealed",
        roots=[],
        roots_error=None,
        first_day=DAY,
        today=DAY + timedelta(days=3),
    )
    assert facts["tsa"] == {
        "provider": "mock",
        "legal_grade": False,
        "legal_grade_claimed": False,
        "test_stamp": True,
    }
    text, style = provider_line(facts["tsa"], facts["verification"])
    assert (text, style) == ("mock — YASAL GEÇERLİ DEĞİL (test/geliştirme sağlayıcısı)", "warn")


def test_header_and_footer_lines_fit_the_page() -> None:
    register_fonts()  # a report registers them before it draws; alone the test must too
    width = 100.0
    assert fit("kısa", 7.5, width) == "kısa"
    long = "Dosya/evrak no " + "Ğ" * 300
    cut = fit(long, 7.5, width)
    assert cut.endswith("…")
    assert long.startswith(cut[:-1])
    assert stringWidth(cut, SANS, 7.5) <= width


@pytest.mark.parametrize("suffix", [".log.gz", ".zd", ".json"])
def test_no_report_while_a_sealed_file_cannot_be_opened(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    api_config: Config,
    layout: Layout,
    site: Site,
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
) -> None:
    """The vault file, the token or the manifest of a cited sealed day exists but cannot be
    opened: refused (503), nothing stored, the file named in the message."""
    seal_day(api_config, layout, DAY)
    signed_in(client, login_as, app_session_factory)
    data = lookup(client, internal_ip=PC)
    real_open = Path.open

    def open_(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self.is_relative_to(layout.vault) and self.name.endswith(suffix):
            raise PermissionError(13, "Access is denied", str(self))
        return real_open(self, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", open_)
        response = client.get(f"/api/v1/lookup/{data['id']}/report.pdf")
    failure = response.json()["error"]
    assert (response.status_code, failure["code"]) == (503, "report_access_denied")
    assert "vault/2026/10/12/" in failure["message_en"]
    with app_session_factory() as session:
        assert session.scalars(select(LookupReport)).all() == []
    assert "DOĞRULANDI" in normalized(pdf_text(download(client, data["id"])))


# -- the cited raw lines against the database -----------------------------------------------------


def _inputs(factory: sessionmaker[Session], lookup_id: int) -> dict[str, Any]:
    inputs: dict[str, Any] = report_row(factory, lookup_id).inputs
    return inputs


def test_unchanged_rows_agree_with_their_raw_lines(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    site: Site,
) -> None:
    """Every cited line is re-parsed and compared with what the report shows: on unchanged data
    (sessions, DHCP and ARP leases, an IP conflict, a release) everything agrees."""
    rogue = "00:0c:29:00:00:99"
    with app_session_factory() as session:
        evidence = Evidence(session, layout)
        evidence.arp(tr(DAY, 11, 4, 56), PC, rogue, "vlan42")  # an IP conflict: ARP lines
        evidence.arp(tr(DAY, 11, 34, 56), PC, rogue, "vlan42")
        evidence.dhcp(tr(DAY, 12, 4, 56), "deassigned", PC, PC_MAC, server="dhcp42")
        session.commit()
        rebuild_day(session, DAY, horizon=NOW)
        session.commit()
    setting(app_session_factory, "nat.public_ips", [WAN])
    signed_in(client, login_as, app_session_factory)
    for query in (
        {"public_ip": WAN, "public_port": 51001, "protocol": 6},
        {"mac": "02-00-5E-00-53-64"},
        {"internal_ip": PC},
        {"public_ip": WAN, "public_port": 51002, "tolerance_seconds": 300},
    ):
        data = lookup(client, **query)
        text = normalized(pdf_text(download(client, data["id"])))
        inputs = _inputs(app_session_factory, data["id"])
        consistency = inputs["consistency"]
        wrong = [
            (i["key"], i["check"]) for i in inputs["evidence"] if not (i["check"] or {}).get("ok")
        ]
        assert consistency["ok"] is True, wrong
        assert consistency["checked"] >= 1
        assert consistency["contradicted"] == []
        assert "uyuşmuyor" not in text
        assert "Ham satır karşılaştırması" in text
        for item in inputs["evidence"]:
            if item["line"]["status"] == "ok":
                assert item["check"] == {"ok": True, "mismatches": [], "error": None}, item


def test_a_session_row_changed_after_ingest_is_flagged_not_reported_as_exact(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    api_config: Config,
    layout: Layout,
    site: Site,
) -> None:
    """The finding's reproduction: the database row of the session now names another client
    (a manual repair, a faulty rebuild or tampering) while the sealed raw line still names the
    PC. The report must not present the database answer as an exact match."""
    seal_day(api_config, layout, DAY)
    with app_session_factory() as session:
        session.execute(update(FgTraffic).where(FgTraffic.id == site.s1).values(srcip=PHONE))
        session.commit()
    setting(app_session_factory, "nat.public_ips", [WAN])
    signed_in(client, login_as, app_session_factory)
    data = lookup(client, public_ip=WAN, public_port=51001, protocol=6)
    assert data["mode"] == "exact"  # the engine reads the database only
    text = normalized(pdf_text(download(client, data["id"])))
    inputs = _inputs(app_session_factory, data["id"])
    item = next(i for i in inputs["evidence"] if i["row_id"] == site.s1)
    assert item["line"]["origin"] == "vault"
    assert item["check"]["ok"] is False
    assert {"field": "srcip", "report": PHONE, "raw": PC} in item["check"]["mismatches"]
    assert inputs["consistency"]["ok"] is False
    assert item["no"] in inputs["consistency"]["contradicted"]
    assert "Sonuç 1: kesin eşleşme" not in text
    assert "Sonuç 1: KANITLA UYUŞMAYAN SONUÇ" in text
    assert f"kaynak IP raporda {PHONE}, ham satırda {PC}" in text
    assert "bu sorgunun sonucu mühürlü ham kayıtlarla desteklenmiyor" in text
    assert "güven düzeyi verilemez" in text
    assert "0 kesin eşleşme, 1 kanıtla uyuşmayan sonuç" in text
    assert "DOĞRULANDI" in text  # the seal itself is intact: it is the database that changed


def test_a_lease_changed_after_ingest_is_flagged(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    site: Site,
) -> None:
    """The IP-MAC record now names another device; its DHCP line still names the PC."""
    with app_session_factory() as session:
        session.execute(update(Lease).where(Lease.mac == PC_MAC).values(mac=PHONE_MAC))
        session.commit()
    signed_in(client, login_as, app_session_factory)
    data = lookup(client, internal_ip=PC)
    text = normalized(pdf_text(download(client, data["id"])))
    inputs = _inputs(app_session_factory, data["id"])
    contradicted = [
        i for i in inputs["evidence"] if i["no"] in inputs["consistency"]["contradicted"]
    ]
    assert contradicted
    dhcp = next(i for i in contradicted if i["table"] == "mt_dhcp")
    assert {"field": "mac", "report": PHONE_MAC, "raw": PC_MAC} in dhcp["check"]["mismatches"]
    assert f"MAC adresi raporda {PHONE_MAC}, ham satırda {PC_MAC}" in text
    assert "güven düzeyi verilemez" in text
    assert "KANITLA UYUŞMAYAN SONUÇ" in text


def test_a_cited_line_of_another_record_type_is_flagged(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    site: Site,
) -> None:
    """The row points at a line that is no FortiGate session at all (the DHCP line)."""
    with app_session_factory() as session:
        dhcp = session.scalars(select(MtDhcp).where(MtDhcp.id == site.dhcp_pc)).one()
        session.execute(
            update(FgTraffic)
            .where(FgTraffic.id == site.s1)
            .values(source=dhcp.source, raw_line=dhcp.raw_line, raw_offset=dhcp.raw_offset)
        )
        session.commit()
    setting(app_session_factory, "nat.public_ips", [WAN])
    signed_in(client, login_as, app_session_factory)
    data = lookup(client, public_ip=WAN, public_port=51001, protocol=6)
    text = normalized(pdf_text(download(client, data["id"])))
    inputs = _inputs(app_session_factory, data["id"])
    item = next(i for i in inputs["evidence"] if i["row_id"] == site.s1)
    assert item["check"] == {"ok": False, "mismatches": [], "error": "other_record_type"}
    assert "kanıt olarak gösterilen türde bir kayıt değil" in text
    assert "Sonuç 1: KANITLA UYUŞMAYAN SONUÇ" in text


def _soft_eku_tsa(layout: Layout) -> None:
    """Re-issue the mock TSA's certificate with a non-critical timeStamping EKU (K6: what Kamu
    SM's profile may turn out to be); the API's provider loads it from ``state/mock-tsa``."""
    from datetime import UTC, datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    from arcilogger_api.lookup_testing import mock_tsa

    tsa = mock_tsa(layout)
    root_key = serialization.load_pem_private_key((tsa.state_dir / "root.key").read_bytes(), None)
    assert isinstance(root_key, ec.EllipticCurvePrivateKey)
    key = ec.generate_private_key(ec.SECP256R1())
    start = datetime.now(UTC) - timedelta(days=30)
    usage = x509.KeyUsage(True, False, False, False, False, False, False, False, False)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Soft EKU TSA")]))
        .issuer_name(tsa.root_certificate.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(start + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.TIME_STAMPING]), False)
        .add_extension(usage, True)
        .sign(root_key, hashes.SHA256())
    )
    tsa._write_cert("tsa.pem", certificate)
    tsa._write_key("tsa.key", key)


def test_report_stamps_follow_the_sites_eku_rule(api_config: Config, layout: Layout) -> None:
    """tsa.require_critical_eku = false (K6) applies to report stamps as to the days (finding
    7): with a non-critical EKU the stamp verifies only when the site accepted that profile."""
    from arcilogger_api.reports.inputs import TimestampPlan

    _soft_eku_tsa(layout)
    plan = TimestampPlan(requested=True, provider="mock", legal_grade=False)
    folder = layout.exports / "reports"
    folder.mkdir(parents=True, exist_ok=True)
    strict_pdf, relaxed_pdf = folder / "strict.pdf", folder / "relaxed.pdf"
    strict_pdf.write_bytes(b"%PDF-1.7 strict")
    relaxed_pdf.write_bytes(b"%PDF-1.7 relaxed")
    status, facts = store._stamp(api_config, layout, strict_pdf, plan)
    assert (status, facts["verified"]) == ("stamped", False)
    assert any("not critical" in error for error in facts["errors"]), facts["errors"]
    relaxed = Config.model_validate(
        {
            **api_config.model_dump(),
            "tsa": {**api_config.tsa.model_dump(), "require_critical_eku": False},
        }
    )
    status, facts = store._stamp(relaxed, layout, relaxed_pdf, plan)
    assert (status, facts["verified"], facts["errors"]) == ("stamped", True, [])


def test_every_destination_match_has_its_own_report_label() -> None:
    """Weak destination matches (another port, an address known only from another client) are
    never printed as plain matches in the official report."""
    labels = {key: texts.DESTINATION[key] for key in get_args(DestinationMatch)}
    assert len(set(labels.values())) == len(labels)
    assert labels["ip_other_port"] == "hedef IP eşleşti, port farklı"
    assert labels["domain_other_port"] == "alan adı eşleşti, port farklı"
    assert "CDN" in labels["domain_address"]
    assert "alan adı eşleşti" not in labels["domain_address"]
    assert labels["domain_unverified"].startswith("hedef doğrulanmadı")
    assert "eşleşti" not in labels["domain_unverified"]
