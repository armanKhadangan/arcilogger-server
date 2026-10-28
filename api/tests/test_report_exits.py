"""The internet exit in the official 5651 report (contract 2.12, 3.5): the exit label table and
the reading-table keys, the section-4 exit row, one method paragraph per resolution and mode,
the conditional double-NAT text, the "Oturum aranmadı" lines, the exit row and the
unconfigured-sender warning of each match, an outcome stored before the upgrade, and the PDFs of
the E-cases through the API (E-c2, E-c5 / legacy (d), E-k2, E-k3, E-k4, E-q, E-e, E-e3, E-n, E-p2,
``no_egress``, ``not_logged``).

Values of the worker's goldens: T1 ``100.64.12.9`` (the site's WAN NAT address) and T2
``100.64.13.9``, P1 ``203.0.113.200`` and P2 ``198.51.100.77``, the DMZ NAT address
``172.20.99.1``."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.lookup_testing import (
    AT,
    DAY,
    NOW,
    PC,
    PERSON_NAME,
    PHONE,
    PHONE_HOSTNAME,
    PHONE_MAC,
    WAN,
    WAN2,
    Site,
    build_site,
    enter_exits,
    iso,
    unknown_sender_traffic,
)
from arcilogger_api.pdftext import normalized, pdf_text
from arcilogger_api.reports import render, texts
from arcilogger_api.reports.render import exit_label_key
from arcilogger_api.testing import LoggedIn
from arcilogger_core.layout import Layout
from arcilogger_db.egress import MIGRATION_KEY, legacy_egresses
from arcilogger_db.settings import set_setting
from arcilogger_worker.lookup import LookupOutcome, LookupRequest
from arcilogger_worker.lookup.texts import ENGINE_EXPLANATIONS, explain
from arcilogger_worker.lookup.types import (
    EXPLANATIONS,
    EgressEntry,
    EgressResolution,
    EgressSearched,
    LookupEgress,
)
from arcilogger_worker.testing import Evidence, tr

from .test_report_pdf import _minimal_inputs, download, lookup, signed_in

T1, T2 = WAN, WAN2
P1, P2 = "203.0.113.200", "198.51.100.77"
DMZ_NAT = "172.20.99.1"
DEST = "203.0.113.10"
#: When the exits of a test were entered: a month before the evidence.
ENTERED = tr(DAY - timedelta(days=30), 9)

Login = Callable[..., LoggedIn]


def _exit(name: str, mode: str, label: str = "", **fields: Any) -> EgressEntry:
    return EgressEntry.from_dict({"name": name, "label": label, "mode": mode, **fields})


def _text(
    resolution: str,
    exits: Iterable[EgressEntry],
    *,
    mode: str = "candidates",
    searched: EgressSearched | None = None,
    explanations: Iterable[Any] = (),
    double_nat: bool = True,
    public_ip: str | None = "192.0.2.44",
) -> str:
    """The PDF text of an outcome with ``outcome.egress`` and no matches (no database)."""
    inputs = _minimal_inputs()
    outcome = LookupOutcome(
        request=LookupRequest(at=AT, public_ip=public_ip, public_port=41000),
        mode=mode,  # type: ignore[arg-type]
        matches=(),
        double_nat=double_nat,
        attribution=None,
        explanations=tuple(explanations),
        data_window=None,
        tolerance_seconds=60,
        generated_at=NOW,
        egress=LookupEgress(
            resolution=resolution,  # type: ignore[arg-type]
            egresses=tuple(exits),
            searched=searched or EgressSearched(),
        ),
    )
    inputs["outcome"] = outcome.to_dict()
    return normalized(pdf_text(render(inputs).pdf))


def _section_4(text: str) -> str:
    return text.split("4. Yöntem", 1)[1].split("5. Sonuçlar", 1)[0]


def _no_compliance_claim(text: str) -> None:
    lowered = text.lower()
    assert "legally compliant" not in lowered
    assert "uyumludur" not in lowered
    assert "yasal olarak uyumlu" not in lowered


# -- the label table and the reading-table keys ---------------------------------------------------


def test_the_exit_label_table_has_the_eight_labels() -> None:
    assert dict(texts.EXIT_LABELS) == {
        "public": ("Genel IP", "Public IP"),
        "transip": ("NAT adresi", "NAT address"),
        "fallback_direct": ("Doğrudan hat doğrulanamadı", "Direct line not confirmed"),
        "fallback_transip": ("NAT adresi doğrulanamadı", "NAT address not confirmed"),
        "observed": ("Kayıtlarda görülen adres", "Address seen in the logs"),
        "public_unknown": ("Genel adresi bilinmeyen hatlar", "Lines with unknown public address"),
        "unconfigured": ("Çıkış tanımlı değil", "No exits defined"),
        "no_egress": ("Çıkış yok", "No exit"),
    }
    assert dict(texts.EXIT_MODES) == {
        "direct": ("Doğrudan", "Direct"),
        "isp_nat": ("Servis sağlayıcı NAT'ı", "ISP NAT"),
        "not_logged": ("FortiGate'ten geçmiyor", "Does not pass the FortiGate"),
        "internal": ("İnternet çıkışı değil", "Not an internet exit"),
    }
    direct = _exit("hat-1", "direct", public=[P1])
    isp_nat = _exit("hat-2", "isp_nat", transips=[T2], public=[P2])
    assert exit_label_key(LookupEgress("fallback", (direct,))) == "fallback_direct"
    assert exit_label_key(LookupEgress("fallback", (isp_nat,))) == "fallback_transip"
    resolutions: tuple[EgressResolution, ...] = (
        "public",
        "transip",
        "observed",
        "public_unknown",
        "no_egress",
    )
    for resolution in resolutions:
        assert exit_label_key(LookupEgress(resolution, (isp_nat,))) == resolution


def test_every_reading_table_key_is_an_explanation() -> None:
    keys = texts.READING_TABLE_KEYS
    assert len(keys) == len(set(keys)) == 15
    assert set(keys) <= set(ENGINE_EXPLANATIONS) | set(EXPLANATIONS)


# -- one paragraph per (resolution, mode) ---------------------------------------------------------

#: The opening words of each of the eleven method paragraphs of 2.12.
LEADS = {
    "public_direct": "hat-1 (Ana hat) çıkışı doğrudan tanımlı: genel IP adresi",
    "isp_nat": "çıkışı servis sağlayıcı NAT'ı olarak tanımlı: bu tanıma göre",
    "not_logged": "misafir çıkışına ait; bu çıkış FortiGate'ten geçmez",
    "internal": "dmz tanımına göre FortiGate'in iç ağlara (DMZ, başka bir VLAN, VPN ya da şube) "
    "yaptığı kaynak NAT'ın adresidir; bir",
    "fallback_direct": "hat-1 (Ana hat) doğrudan olarak tanımlı, ancak",
    "fallback_transip": "hat-2 (Yedek hat) çıkışı servis sağlayıcı NAT'ı olarak tanımlı; NAT "
    "adresi",
    "fallback_other": "Aranan adreslerin bir kısmı",
    "observed": "hiçbir internet çıkışında tanımlı değil, ancak",
    "public_unknown": "hiçbir çıkışın genel adresi olarak tanımlı değil. Arama, genel adresi",
    "unconfigured": "İnternet çıkışları tanımlanmamış: servis sağlayıcının ikinci bir NAT yaptığı "
    "varsayıldı ve FortiGate'in",
    "no_egress": "kurumun tanımlı internet çıkışlarından hiçbirine ait değil ve o sırada",
}
HAT_1 = _exit("hat-1", "direct", "Ana hat", public=[P1])
HAT_2 = _exit("hat-2", "isp_nat", "Yedek hat", public=[P2], transips=[T2])
MISAFIR = _exit("misafir", "not_logged", public=["198.51.100.90"])
DMZ = _exit("dmz", "internal", transips=[DMZ_NAT], interfaces=["dmz"])
IMPLICIT = _exit("", "isp_nat")
OBSERVED = explain("egress_observed_not_configured", address="192.0.2.44", at=AT.isoformat())

#: (case, resolution, exits, outcome mode, stored explanations, the paragraphs it prints, label).
CASES: list[tuple[str, str, tuple[EgressEntry, ...], str, tuple[Any, ...], set[str], str]] = [
    ("public_direct", "public", (HAT_1,), "none", (), {"public_direct"}, "Genel IP / Public IP"),
    ("isp_nat", "public", (HAT_2,), "candidates", (), {"isp_nat"}, "Genel IP / Public IP"),
    ("transip", "transip", (HAT_2,), "candidates", (), {"isp_nat"}, "NAT adresi / NAT address"),
    ("not_logged", "public", (MISAFIR,), "none", (), {"not_logged"}, "Genel IP / Public IP"),
    ("internal", "transip", (DMZ,), "none", (), {"internal"}, "NAT adresi / NAT address"),
    (
        "fallback_direct",
        "fallback",
        (HAT_1,),
        "candidates",
        (),
        {"fallback_direct"},
        "Doğrudan hat doğrulanamadı / Direct line not confirmed",
    ),
    (
        "fallback_transip",
        "fallback",
        (HAT_2,),
        "candidates",
        (),
        {"fallback_transip"},
        "NAT adresi doğrulanamadı / NAT address not confirmed",
    ),
    (
        "fallback_other",
        "fallback",
        (HAT_1, _exit("wan", "isp_nat", "WAN", transips=[T1])),
        "candidates",
        (),
        {"fallback_direct", "fallback_other"},
        "Doğrudan hat doğrulanamadı / Direct line not confirmed",
    ),
    (
        "observed",
        "observed",
        (),
        "exact",
        (OBSERVED,),
        {"observed"},
        "Kayıtlarda görülen adres / Address seen in the logs",
    ),
    (
        "public_unknown",
        "public_unknown",
        (_exit("hat-2", "isp_nat", "Yedek hat", transips=[T2]),),
        "candidates",
        (),
        {"public_unknown"},
        "Genel adresi bilinmeyen hatlar / Lines with unknown public address",
    ),
    (
        "unconfigured",
        "unconfigured",
        (IMPLICIT,),
        "candidates",
        (),
        {"unconfigured"},
        "Çıkış tanımlı değil / No exits defined",
    ),
    ("no_egress", "no_egress", (HAT_1, HAT_2), "none", (), {"no_egress"}, "Çıkış yok / No exit"),
]

#: The requested address of the cases that do not ask for 192.0.2.44, and the cases whose
#: outcome is double NAT.
ADDRESSES = {"internal": DMZ_NAT, "not_logged": "198.51.100.90", "transip": T2}
DOUBLE_NAT = {
    "isp_nat",
    "transip",
    "fallback_direct",
    "fallback_transip",
    "fallback_other",
    "public_unknown",
    "unconfigured",
}


@pytest.mark.parametrize(
    ("case", "resolution", "exits", "mode", "explanations", "printed", "label"),
    CASES,
    ids=[case[0] for case in CASES],
)
def test_one_method_paragraph_per_resolution_and_mode(
    case: str,
    resolution: str,
    exits: tuple[EgressEntry, ...],
    mode: str,
    explanations: tuple[Any, ...],
    printed: set[str],
    label: str,
) -> None:
    text = _text(
        resolution,
        exits,
        mode=mode,
        explanations=explanations,
        public_ip=ADDRESSES.get(case, "192.0.2.44"),
        double_nat=case in DOUBLE_NAT,
    )
    method = _section_4(text)
    for name, lead in LEADS.items():
        assert (lead in method) is (name in printed), (case, name)
    assert f"Çıkış / Exit {label}" in method
    if case == "public_unknown":
        assert "yapıldı: hat-2 (Yedek hat); bu çıkışlarda" in method  # {exits}: name (label)
    if case == "fallback_other":
        assert (
            "Aranan adreslerin bir kısmı wan (WAN) çıkışına aittir: bu çıkış yükseltmede" in method
        )
    if case == "transip":
        assert "İstenen adres bu çıkışta FortiGate'in kendi NAT adresidir" in method
    if case == "observed":  # the stored text, printed once (not again as a bullet)
        assert text.count(OBSERVED.text_tr) == 1
    _no_compliance_claim(text)


def test_the_exit_row_names_the_exits_except_for_unconfigured_and_no_egress() -> None:
    fallback = _text("fallback", (HAT_1, _exit("wan", "isp_nat", "WAN", transips=[T1])))
    assert (
        "Çıkış / Exit Doğrudan hat doğrulanamadı / Direct line not confirmed — hat-1 (Ana hat), "
        "Doğrudan / Direct; wan (WAN), Servis sağlayıcı NAT'ı / ISP NAT Açıklama" in fallback
    )
    internal = _text("transip", (DMZ,), mode="none", public_ip=DMZ_NAT, double_nat=False)
    assert (
        "NAT adresi / NAT address — dmz, İnternet çıkışı değil / Not an internet exit" in internal
    )
    unconfigured = _text("unconfigured", (IMPLICIT,))
    assert "Çıkış / Exit Çıkış tanımlı değil / No exits defined Açıklama" in unconfigured
    nobody = _text("no_egress", (HAT_1, HAT_2), mode="none", double_nat=False)
    assert "Çıkış / Exit Çıkış yok / No exit Açıklama" in nobody
    assert "Sorgu anında tanımlı çıkışlar: hat-1 (Ana hat): Doğrudan, 203.0.113.200; hat-2 " in (
        nobody
    )
    assert "Exits defined at the time of the lookup: hat-1 (Ana hat): Direct, 203.0.113.200;" in (
        nobody
    )


def test_the_isp_nat_paragraph_states_what_was_searched() -> None:
    """The interface clause only while the search kept it; the port hint as stored; a catch-all's
    NAT addresses in words; an unclaimed search (catch-all or O5) says what it covered."""
    on_wan2 = _exit(
        "hat-2", "isp_nat", "Yedek hat", public=[P2], transips=[T2], interfaces=["wan2"]
    )
    kept = EgressSearched(transips=(T2,), interfaces=("wan2",), interface_filter=True)
    hint = explain("port_hint", egresses="hat-2")
    text = _section_4(_text("public", (on_wan2,), searched=kept, explanations=(hint,)))
    assert "NAT adresini (100.64.13.9) ikinci kez çevirir" in text
    assert "Arama bu çıkışın NAT adresleriyle ve arayüzleriyle (wan2) sınırlandı." in text
    assert (
        "Port koruma: yalnızca sıralamada kullanıldı (sahada doğrulandı olarak işaretli)." in text
    )
    assert "Port preservation: used for ranking only (marked as verified on site)." in text
    dropped = EgressSearched(transips=(T2,), interfaces=("wan2",), interface_filter=False)
    text = _section_4(_text("public", (on_wan2,), searched=dropped))
    assert "Arama bu çıkışın NAT adresleriyle sınırlandı." in text
    assert "Port koruma: kullanılmadı." in text
    # a catch-all with a public address searched the unclaimed addresses (X12, O5 wording)
    catch_all = _exit("tanimsiz", "isp_nat", public=[P2])
    unclaimed = EgressSearched(unclaimed=True, unclaimed_seen=("100.64.12.77", "198.51.100.250"))
    text = _section_4(_text("public", (catch_all,), searched=unclaimed))
    assert "()" not in text
    assert (
        "NAT adresini (hiçbir çıkışa tanımlı olmayan özel ya da paylaşılan NAT adresleri)" in text
    )
    assert "Arama bu çıkışın NAT adresleriyle" not in text
    assert (
        "Arama hiçbir çıkışa tanımlı olmayan bütün özel ya da paylaşılan kaynak NAT adresleri "
        "(12.10.2026 11:23:41 çevresinde görülenler: 100.64.12.77) adresleriyle yapıldı." in text
    )
    assert "198.51.100.250" not in text  # a public address is never in the unclaimed predicate


#: A ``public_unknown`` search of hat-2 (wan1) and a catch-all, as the engine stores it: the
#: interface condition is set when any exit's part kept one.
PUBLIC_UNKNOWN_SEARCHED = EgressSearched(
    transips=(T2,),
    unclaimed=True,
    unclaimed_seen=("100.64.12.77",),
    interfaces=("wan1", "wan3"),
    interface_filter=True,
)


def test_public_unknown_names_no_interface_when_an_exit_searched_them_all() -> None:
    """A catch-all without interfaces searched every interface beside hat-2 on wan1: the
    sentence names no interface (deviation d-3); nor when the engine dropped an exit's
    interfaces (``egress_interface_not_seen``)."""
    on_wan1 = _exit("hat-2", "isp_nat", "Yedek hat", transips=[T2], interfaces=["wan1"])
    everywhere = _exit("tanimsiz", "isp_nat")
    searched = replace(PUBLIC_UNKNOWN_SEARCHED, interfaces=("wan1",))
    text = _section_4(_text("public_unknown", (on_wan1, everywhere), searched=searched))
    assert (
        "Arama 100.64.13.9 ve hiçbir çıkışa tanımlı olmayan bütün özel ya da paylaşılan kaynak "
        "NAT adresleri (12.10.2026 11:23:41 çevresinde görülenler: 100.64.12.77) adresleriyle "
        "yapıldı." in text
    )
    assert "ve arayüzleriyle" not in text
    assert "on the interfaces" not in text
    lined = _exit("tanimsiz", "isp_nat", interfaces=["wan3"])
    not_seen = explain(
        "egress_interface_not_seen", egress="tanimsiz", interfaces="wan3", at=AT.isoformat()
    )
    text = _section_4(
        _text(
            "public_unknown",
            (on_wan1, lined),
            searched=searched,
            explanations=(not_seen,),
        )
    )
    assert "ve arayüzleriyle" not in text
    assert "on the interfaces" not in text


def test_public_unknown_names_the_interfaces_when_every_exit_kept_them() -> None:
    on_wan1 = _exit("hat-2", "isp_nat", "Yedek hat", transips=[T2], interfaces=["wan1"])
    lined = _exit("tanimsiz", "isp_nat", interfaces=["wan3"])
    kept = explain(
        "egress_interface_not_seen_kept", egress="hat-2", interfaces="wan1", at=AT.isoformat()
    )
    text = _section_4(
        _text(
            "public_unknown",
            (on_wan1, lined),
            searched=PUBLIC_UNKNOWN_SEARCHED,
            explanations=(kept,),
        )
    )
    assert (
        "(12.10.2026 11:23:41 çevresinde görülenler: 100.64.12.77) adresleriyle ve arayüzleriyle "
        "(wan1, wan3) yapıldı." in text
    )
    assert "(seen around 12.10.2026 11:23:41: 100.64.12.77) on the interfaces (wan1, wan3)." in text


def test_a_fallback_says_bu_hatta_only_for_an_exit_with_interfaces() -> None:
    searched = EgressSearched(transips=(P1,), unclaimed=True, outside_seen=(T1,))
    plain = _section_4(_text("fallback", (HAT_1,), searched=searched))
    assert (
        "kayıtlar o sırada özel ya da paylaşılan başka kaynak NAT adresleri (100.64.12.9)" in plain
    )
    assert "kayıtlar o sırada bu hatta" not in plain
    lined = _exit("hat-1", "direct", "Ana hat", public=[P1], interfaces=["wan1"])
    on_line = _section_4(_text("fallback", (lined,), searched=searched))
    assert "kayıtlar o sırada bu hatta özel ya da paylaşılan başka kaynak NAT adresleri" in on_line
    assert "private or shared source-NAT addresses on this line then (100.64.12.9)" in on_line
    # F reached through a shared interface: the shared-interface addition, not "converted"
    other = _exit("hat-3", "isp_nat", transips=["100.64.14.9"], interfaces=["wan1"])
    shared = _section_4(_text("fallback", (lined, other), searched=searched))
    assert (
        "Aranan adreslerin bir kısmı hat-3 çıkışına aittir: bu çıkış hat-1 (Ana hat) çıkışıyla "
        "aynı arayüzü (wan1) paylaştığı için aynı hat sayıldı." in shared
    )
    assert "yükseltmede eski ayarlardan aktarıldı" not in shared


# -- the double-NAT text --------------------------------------------------------------------------


def test_the_double_nat_text_follows_the_resolution() -> None:
    configured = _text("public", (HAT_2,))
    assert (
        "Çift NAT: hat-2 (Yedek hat) çıkışı servis sağlayıcı NAT'ı olarak tanımlı; servis "
        "sağlayıcının yönlendiricisi FortiGate'in adresini ikinci kez çevirir." in configured
    )
    assert "Genel adres başka müşterilerle paylaşılıyorsa (CGNAT)" in configured
    assert "bu hattı 'Doğrudan' olarak tanımlayın" in configured
    for resolution, exits in (
        ("unconfigured", (IMPLICIT,)),
        ("public_unknown", (_exit("hat-2", "isp_nat", transips=[T2]),)),
    ):
        assumed = _text(resolution, exits)
        assert "Çift NAT varsayımı: bu adres için servis sağlayıcının ikinci bir NAT yaptığı " in (
            assumed
        )
        assert "varsayıldı" in assumed
        assert "(CGNAT)" in assumed
    # transip: the request names the FortiGate's own NAT address (with or without a session)
    for mode in ("candidates", "none"):
        transip = _text("transip", (HAT_2,), mode=mode, public_ip=T2)
        assert "Çift NAT" not in transip
        assert "kesin olarak eşlenemez" not in transip
    # a fallback: advice (a) only, the bridge-mode check for a direct exit only, no ISP router
    # as a fact, never "define as Direct", and the stored double_nat note left out (X2)
    stored = explain("double_nat")
    direct = _text("fallback", (HAT_1,), explanations=(stored,))
    assert "Kesin eşleşme için (a) servis sağlayıcının ilgili zamana ait NAT kaydı" in direct
    assert "ya da hattın o tarihte köprü modunda olduğunun servis sağlayıcıdan doğrulanması" in (
        direct
    )
    transip_fallback = _text("fallback", (HAT_2,), explanations=(stored,))
    assert "köprü" not in transip_fallback
    for text in (direct, transip_fallback):
        assert "'Doğrudan' olarak tanımlayın" not in text
        assert "define this line as 'Direct'" not in text
        assert "Çift NAT" not in text
        assert "(CGNAT)" not in text
        assert "ISS modeminin" not in text
    for text in (configured, direct, transip_fallback):
        assert "ISS modeminin arkasındadır" not in text
        assert "Ayarlar → NAT" not in text


def test_a_request_without_a_public_ip_gets_no_double_nat_variant() -> None:
    """A destination search (c1: double NAT means no direct exit, no ``egress``) speaks of no
    address the request does not hold (deviation d)."""
    inputs = _minimal_inputs()
    outcome = LookupOutcome(
        request=LookupRequest(at=AT, dest_ip=DEST, dest_port=443),
        mode="none",
        matches=(),
        double_nat=True,
        attribution=None,
        explanations=(),
        data_window=None,
        tolerance_seconds=60,
        generated_at=NOW,
    )
    inputs["outcome"] = outcome.to_dict()
    text = normalized(pdf_text(render(inputs).pdf))
    assert "Çift NAT / Double NAT" in text
    assert "Çift NAT varsayımı" not in text
    assert "(CGNAT)" not in text
    assert "aday oturum da bulunamadı; bu nedenle rapor bir sonuç içermez" in text
    assert "(a) servis sağlayıcının ilgili zamana ait NAT kaydı" in text


def test_an_answer_that_searched_no_session_says_so_in_sections_4_and_5() -> None:
    cases = [
        (
            "no_egress",
            (HAT_1, HAT_2),
            "192.0.2.44",
            "Oturum aranmadı: istenen adres 192.0.2.44 tanımlı internet çıkışlarından hiçbirine "
            "ait değil (bölüm 4).",
        ),
        (
            "public",
            (MISAFIR,),
            "198.51.100.90",
            "Oturum aranmadı: istenen adres 198.51.100.90 FortiGate'ten geçmeyen misafir çıkışına "
            "ait (bölüm 4).",
        ),
        (
            "transip",
            (DMZ,),
            DMZ_NAT,
            "Oturum aranmadı: istenen adres 172.20.99.1 internet çıkışı olmayan dmz tanımına ait "
            "(bölüm 4).",
        ),
    ]
    for resolution, exits, address, line in cases:
        text = _text(resolution, exits, mode="none", public_ip=address, double_nat=False)
        assert text.count(line) == 2, resolution
        assert "Ölçütlere uyan kayıt bulunamadı" not in text
        assert "No session was searched: the requested address" in text
    # a quiet direct line searched and found nothing: the generic texts stay
    quiet = _text("public", (HAT_1,), mode="none", public_ip=P1, double_nat=False)
    assert "Oturum aranmadı" not in quiet
    assert "Ölçütlere uyan kayıt bulunamadı" in quiet


# -- an outcome stored before the upgrade ---------------------------------------------------------


@pytest.mark.parametrize("stored", ["candidates", "none"])
def test_a_wave_2_outcome_renders_without_exit_rows(stored: str) -> None:
    """A stored Wave 2 outcome (no ``egress``, ``dstintf`` or ``source_table``; reports are
    rendered on their first download): no exit row, no method paragraph, no FortiGate lease
    additions; its double NAT as the assumed variant with the CGNAT sentence."""
    inputs = _minimal_inputs()
    inputs["outcome"] = WAVE_2_CANDIDATES if stored == "candidates" else WAVE_2_NONE
    text = normalized(pdf_text(render(inputs).pdf))
    assert "Çıkış / Exit" not in text
    assert "dstintf" not in text
    assert "İnternet çıkışı / Internet exit" not in text
    for lead in LEADS.values():
        assert lead not in text
    assert "Çift NAT varsayımı: bu adres için" in text
    assert "(CGNAT)" in text
    assert "ISS modeminin arkasındadır" not in text
    assert "WAN arayüzü" not in text
    assert "Ayarlar → NAT" not in text
    assert "kira süresi (FortiGate kaydı)" not in text
    assert "sahada doğrula" not in text
    if stored == "candidates":
        assert "Aday 1: puan 90/100" in text
        assert "Sonuç, zaman penceresi (± tolerans) içindeki oturumların" in text
    else:
        assert "aday oturum da bulunamadı; bu nedenle rapor bir sonuç içermez" in text
        assert "Ölçütlere uyan kayıt bulunamadı" in text
    _no_compliance_claim(text)


# -- the E-cases through the API ------------------------------------------------------------------


@pytest.fixture
def site(app_session_factory: sessionmaker[Session], layout: Layout) -> Site:
    return build_site(app_session_factory, layout)


def exits(
    factory: sessionmaker[Session], value: dict[str, Any], *, converted: bool = False
) -> None:
    """``value`` entered as ``nat.egresses`` a month before the evidence; ``converted``: with
    the status row of revision 0018 that keeps the exits *as converted* (D15)."""
    stored = enter_exits(factory, value, at=ENTERED, by=":migration")
    if converted:
        with factory() as session:
            status = {"migration": "0018_nat_egresses", "new": stored}
            set_setting(session, MIGRATION_KEY, status, updated_by=":migration")
            session.commit()


def traffic(factory: sessionmaker[Session], layout: Layout, *rows: dict[str, Any]) -> None:
    """Session records written through ``Evidence.traffic`` (``at`` and the client)."""
    with factory() as session:
        evidence = Evidence(session, layout)
        for row in rows:
            values = dict(row)
            evidence.traffic(values.pop("at"), values.pop("srcip"), values.pop("srcport"), **values)
        session.commit()


def report(client: TestClient, **query: Any) -> tuple[dict[str, Any], str]:
    data = lookup(client, **query)
    text = normalized(pdf_text(download(client, data["id"])))
    _no_compliance_claim(text)
    return data, text


def test_e_c2_fallback_report(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    site: Site,
) -> None:
    """E-c2: the direct line hat-1 (wan1) is double-NATed to the unclaimed 100.64.12.9 near the
    moment; P1 with a port falls back to candidates on its own line."""
    traffic(
        app_session_factory,
        layout,
        {"at": AT, "srcip": PC, "srcport": 51009, "transip": T1, "dstintf": "wan1"},
        {"at": AT, "srcip": PHONE, "srcport": 51008, "transip": T2, "dstintf": "wan2"},
    )
    exits(
        app_session_factory,
        {
            "hat-1": {"mode": "direct", "public": [P1], "interfaces": ["wan1"], "label": "Ana hat"},
            "hat-2": {"mode": "isp_nat", "transips": [T2], "interfaces": ["wan2"]},
        },
    )
    signed_in(client, login_as, app_session_factory)
    data, text = report(client, public_ip=P1, public_port=51009)
    assert data["outcome"]["egress"]["resolution"] == "fallback"
    method = _section_4(text)
    assert (
        "Çıkış / Exit Doğrudan hat doğrulanamadı / Direct line not confirmed — hat-1 (Ana hat), "
        "Doğrudan / Direct" in method
    )
    assert (
        "hat-1 (Ana hat) doğrudan olarak tanımlı, ancak 203.0.113.200 o anın çevresinde" in method
    )
    assert "kayıtlar o sırada bu hatta özel ya da paylaşılan başka kaynak NAT adresleri" in method
    assert "(100.64.12.9) de gösteriyor" in method
    assert "(12.10.2026 11:23:41 çevresinde görülenler: 100.64.12.9)" in method
    assert "ya da hattın o tarihte köprü modunda olduğunun servis sağlayıcıdan doğrulanması" in text
    assert "'Doğrudan' olarak tanımlayın" not in text
    assert "ISS modeminin" not in text  # the stored double_nat note is left out (X2)
    assert "Çift NAT" not in text
    assert "Çıkış / Exit - · dstintf wan1" in text  # an unclaimed NAT address
    assert PHONE not in text  # never line 2's session


def test_legacy_d_fallback_names_the_address_seen_and_the_other_exit(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    site: Site,
) -> None:
    """E-c5 on legacy (d) as converted: line 1 in bridge mode until 18:14 (A), then router mode
    (100.64.12.9); the requester's session at 18:19 through W. A was seen, but not alone: the
    fallback into ``wan`` (F) says that the address appears only part of the time, names
    100.64.12.9 and adds the "searched addresses of another exit" paragraph."""
    rows = []
    for minute in range(4, 15, 2):
        at = tr(DAY, 18) + timedelta(minutes=minute)
        rows.append({"at": at, "srcip": PHONE, "srcport": 41000 + minute, "transip": P1})
    for minute in range(14, 31, 2):
        at = tr(DAY, 18) + timedelta(minutes=minute)
        rows.append({"at": at, "srcip": PHONE, "srcport": 40000 + minute, "transip": T1})
    rows.append({"at": tr(DAY, 18, 19, 7), "srcip": PC, "srcport": 52001, "transip": T1})
    traffic(app_session_factory, layout, *rows)
    exits(app_session_factory, legacy_egresses([P1], T1, False)[0], converted=True)
    signed_in(client, login_as, app_session_factory)
    data, text = report(client, at=iso(tr(DAY, 18, 19, 7)), public_ip=P1, public_port=52001)
    egress = data["outcome"]["egress"]
    assert (egress["resolution"], [e["name"] for e in egress["egresses"]]) == (
        "fallback",
        ["genel-1", "wan"],
    )
    method = _section_4(text)
    assert (
        "o anın çevresinde FortiGate kayıtlarında NAT adresi olarak görünmüyor ya da yalnızca "
        in (method)
    )
    assert "bir kısmında görünüyor; kayıtlar o sırada özel ya da paylaşılan başka kaynak NAT " in (
        method
    )
    assert "adresleri (100.64.12.9) de gösteriyor" in method
    assert (
        "Aranan adreslerin bir kısmı wan (FortiGate WAN adresi (yükseltmede aktarıldı)) çıkışına "
        "aittir: bu çıkış yükseltmede eski ayarlardan aktarıldı" in method
    )
    assert "'Doğrudan' olarak tanımlayın" not in text


def test_e_k2_transip_fallback_report(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    site: Site,
) -> None:
    """E-k2: hat-2's NAT address with a port, not in the logs near the moment while other
    private addresses are: the transip variant, without bridge mode or "define as Direct"."""
    traffic(
        app_session_factory,
        layout,
        {"at": AT, "srcip": PC, "srcport": 51009, "transip": "100.64.12.77"},
    )
    exits(
        app_session_factory,
        {
            "hat-1": {"mode": "direct", "public": [P1]},
            "hat-2": {"mode": "isp_nat", "transips": [T2], "public": [P2]},
        },
    )
    signed_in(client, login_as, app_session_factory)
    data, text = report(client, public_ip=T2, public_port=51009)
    assert data["outcome"]["egress"]["resolution"] == "fallback"
    method = _section_4(text)
    assert (
        "Çıkış / Exit NAT adresi doğrulanamadı / NAT address not confirmed — hat-2, Servis "
        "sağlayıcı NAT'ı / ISP NAT" in method
    )
    assert (
        "hat-2 çıkışı servis sağlayıcı NAT'ı olarak tanımlı; NAT adresi 100.64.13.9 o anın "
        "çevresinde FortiGate kayıtlarında görünmüyor" in method
    )
    assert "Kesin eşleşme için (a) servis sağlayıcının ilgili zamana ait NAT kaydı" in method
    assert "köprü" not in text
    assert "'Doğrudan' olarak tanımlayın" not in text
    assert "ISS modeminin" not in text


def test_e_k3_a_session_that_changed_exit_names_both_exits(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    site: Site,
) -> None:
    """E-k3: an interim row on wan1 and the final row on wan2 of one session through a NAT
    address both exits share; the match's exit is the row nearest the moment, and its row names
    the other exit its cited records show (deviation d)."""
    traffic(
        app_session_factory,
        layout,
        {
            "at": AT - timedelta(seconds=30),
            "srcip": PC,
            "srcport": 51031,
            "transip": T1,
            "dstintf": "wan1",
            "interim": True,
        },
        {
            "at": AT + timedelta(seconds=60),
            "srcip": PC,
            "srcport": 51031,
            "transip": T1,
            "dstintf": "wan2",
        },
    )
    exits(
        app_session_factory,
        {
            "hat-1": {"mode": "isp_nat", "transips": [T1], "public": [P1], "interfaces": ["wan1"]},
            "hat-2": {"mode": "isp_nat", "transips": [T1], "public": [P2], "interfaces": ["wan2"]},
        },
    )
    signed_in(client, login_as, app_session_factory)
    data, text = report(client, public_ip=T1, public_port=51031)
    (match,) = data["outcome"]["matches"]
    assert match["egress"] == "hat-1"
    assert (
        "Çıkış / Exit hat-1 · dstintf wan1 · oturumun kayıtlarında ayrıca: hat-2 / the "
        "session's records also show: hat-2 Oturum aralığı" in text
    )
    # two sessions that changed exit: which exits were whose is not stored, so each row shows
    # what its own cited lines say
    traffic(
        app_session_factory,
        layout,
        {
            "at": AT - timedelta(seconds=20),
            "srcip": PHONE,
            "srcport": 51032,
            "transip": T1,
            "dstintf": "wan1",
            "interim": True,
        },
        {
            "at": AT + timedelta(seconds=50),
            "srcip": PHONE,
            "srcport": 51032,
            "transip": T1,
            "dstintf": "wan2",
        },
    )
    data, text = report(client, public_ip=T1)
    assert sum(1 for m in data["outcome"]["matches"] if m["egress"] == "hat-1") == 2
    assert (
        "oturumun kayıtları birden çok çıkış gösteriyor: NAT 100.64.12.9 · dstintf wan1; NAT "
        "100.64.12.9 · dstintf wan2" in text
    )
    assert "oturumun kayıtlarında ayrıca" not in text


def _changed_beside_a_session_on_one_exit(
    client: TestClient,
    factory: sessionmaker[Session],
    layout: Layout,
    value: dict[str, Any],
    interfaces: tuple[str, str],
) -> tuple[str, str]:
    """The PC's session changes exit (wan1, then wan2) while the phone's moves between
    ``interfaces`` of one exit; the report on the shared NAT address T1, and the blocks of the
    two candidates (the PC's, the phone's)."""
    traffic(
        factory,
        layout,
        {
            "at": AT - timedelta(seconds=30),
            "srcip": PC,
            "srcport": 51031,
            "transip": T1,
            "dstintf": "wan1",
            "interim": True,
        },
        {
            "at": AT + timedelta(seconds=60),
            "srcip": PC,
            "srcport": 51031,
            "transip": T1,
            "dstintf": "wan2",
        },
        {
            "at": AT - timedelta(seconds=20),
            "srcip": PHONE,
            "srcport": 51032,
            "transip": T1,
            "dstintf": interfaces[0],
            "interim": True,
        },
        {
            "at": AT + timedelta(seconds=50),
            "srcip": PHONE,
            "srcport": 51032,
            "transip": T1,
            "dstintf": interfaces[1],
        },
    )
    exits(factory, value)
    data, text = report(client, public_ip=T1)
    (changed,) = [e for e in data["outcome"]["explanations"] if e["key"] == "session_changed_exit"]
    assert changed["params"]["count"] == 1  # the engine: only the PC's session changed exit
    results = text.split("5. Sonuçlar", 1)[1].split("6. Kanıt listesi", 1)[0]
    blocks = re.split(r"Aday \d+: puan", results)[1:]
    (pc,) = [block for block in blocks if f"{PC}:51031" in block]
    (phone,) = [block for block in blocks if f"{PHONE}:51032" in block]
    assert "birden çok çıkış" not in text
    return pc, phone


def test_a_session_on_two_interfaces_of_one_exit_did_not_change_exit(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    site: Site,
) -> None:
    """Beside E-k3's session, a second one moves from wan1 to wan1b, both hat-1's: only the
    session that changed exit names the other exit (deviation d-10)."""
    signed_in(client, login_as, app_session_factory)
    pc, phone = _changed_beside_a_session_on_one_exit(
        client,
        app_session_factory,
        layout,
        {
            "hat-1": {
                "mode": "isp_nat",
                "transips": [T1],
                "public": [P1],
                "interfaces": ["wan1", "wan1b"],
            },
            "hat-2": {"mode": "isp_nat", "transips": [T1], "public": [P2], "interfaces": ["wan2"]},
        },
        ("wan1", "wan1b"),
    )
    assert (
        "Çıkış / Exit hat-1 · dstintf wan1 · oturumun kayıtlarında ayrıca: hat-2 / the "
        "session's records also show: hat-2 Oturum aralığı" in pc
    )
    assert "Çıkış / Exit hat-1 · dstintf wan1 Oturum aralığı" in phone
    assert "oturumun kayıtları" not in phone


def test_a_shared_nat_address_on_two_interfaces_of_one_exit_did_not_change_exit(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    site: Site,
) -> None:
    """T1 is shared by hat-1 (wan1) and hat-2 (wan2, wan2b); the answer stores hat-1 alone. The
    phone's lines on wan2 and wan2b are hat-2's both: no change on its row, while the PC's
    session, hat-1 then hat-2, still names hat-2 (deviation d-10)."""
    signed_in(client, login_as, app_session_factory)
    pc, phone = _changed_beside_a_session_on_one_exit(
        client,
        app_session_factory,
        layout,
        {
            "hat-1": {"mode": "isp_nat", "transips": [T1], "public": [P1], "interfaces": ["wan1"]},
            "hat-2": {
                "mode": "isp_nat",
                "transips": [T1],
                "public": [P2],
                "interfaces": ["wan2", "wan2b"],
            },
        },
        ("wan2", "wan2b"),
    )
    assert "oturumun kayıtlarında ayrıca: hat-2 / the session's records also show: hat-2" in pc
    assert "Çıkış / Exit hat-2 · dstintf wan2 Oturum aralığı" in phone
    assert "oturumun kayıtları" not in phone


def test_e_k4_exits_changed_after_the_event_in_the_method(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    site: Site,
) -> None:
    """E-k4: hat-2's public addresses changed by the upgrade after the moment, and the
    catch-all ``tanimsiz`` deleted after it by an administrator: the method section names the
    change, the author in words and what the exit holds now, in each language."""
    traffic(
        app_session_factory, layout, {"at": AT, "srcip": PHONE, "srcport": 51008, "transip": T2}
    )
    base = {
        "hat-1": {"mode": "direct", "public": [P1]},
        "hat-2": {"mode": "isp_nat", "public": [P2], "transips": [T2]},
    }
    exits(app_session_factory, base)
    changed = {**base, "hat-2": {**base["hat-2"], "public": [P2, "198.51.100.78"]}}
    enter_exits(app_session_factory, changed, at=tr(DAY, 12), by=":migration")
    signed_in(client, login_as, app_session_factory)
    _data, text = report(client, public_ip=P2)
    method = _section_4(text)
    assert "anından sonra şu internet çıkışlarının tanımı değişti: hat-2 (" in method
    assert "yükseltme (eski NAT ayarlarından aktarıldı)" in method
    assert "upgrade (converted from the old NAT settings)" in method
    assert "şimdi: genel 198.51.100.77, 198.51.100.78" in method

    converted, _dropped = legacy_egresses([P1], "", False)  # genel-1 + tanimsiz
    exits(app_session_factory, converted, converted=True)
    enter_exits(
        app_session_factory, {"genel-1": converted["genel-1"]}, at=tr(DAY, 12), by="yonetici"
    )
    _data, text = report(client, public_ip="192.0.2.45")
    method = _section_4(text)
    assert "tanimsiz (" in method
    assert "yonetici; silindi)" in method
    assert "yonetici; deleted)" in method


def test_e_q_internal_nat_address_report(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    site: Site,
) -> None:
    exits(
        app_session_factory,
        {
            "hat-1": {"mode": "direct", "public": [P1]},
            "dmz": {"mode": "internal", "transips": [DMZ_NAT], "interfaces": ["dmz"]},
        },
    )
    signed_in(client, login_as, app_session_factory)
    data, text = report(client, public_ip=DMZ_NAT, public_port=40020)
    assert data["mode"] == "none"
    method = _section_4(text)
    assert (
        "Çıkış / Exit NAT adresi / NAT address — dmz, İnternet çıkışı değil / Not an internet "
        "exit" in method
    )
    line = (
        "Oturum aranmadı: istenen adres 172.20.99.1 internet çıkışı olmayan dmz tanımına ait "
        "(bölüm 4)."
    )
    assert text.count(line) == 2  # sections 4 and 5
    assert line in text.split("5. Sonuçlar", 1)[1]
    assert "Ölçütlere uyan kayıt bulunamadı" not in text
    # the paragraph, and the stored egress_internal text in the method section
    assert "kaynak NAT'ın adresidir; bir internet çıkışı değildir ve oturum aranmadı" in method
    assert "kaynak NAT'ın adresidir, bir internet çıkışı değildir; oturum aranmadı" in method
    assert "Çift NAT" not in text


def test_e_e_dynamic_public_address_report(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    site: Site,
) -> None:
    """E-e: hat-2's listed public address that may change: the configured double NAT with the
    CGNAT caveat, and the ``egress_public_dynamic`` text inside the isp_nat paragraph, once."""
    traffic(
        app_session_factory, layout, {"at": AT, "srcip": PHONE, "srcport": 51008, "transip": T2}
    )
    hat_2 = {"mode": "isp_nat", "public": [P2], "transips": [T2], "public_dynamic": True}
    exits(app_session_factory, {"hat-1": {"mode": "direct", "public": [P1]}, "hat-2": hat_2})
    signed_in(client, login_as, app_session_factory)
    data, text = report(client, public_ip=P2, public_port=41000)
    assert data["outcome"]["egress"]["resolution"] == "public"
    method = _section_4(text)
    assert "Çıkış / Exit Genel IP / Public IP — hat-2, Servis sağlayıcı NAT'ı / ISP NAT" in method
    assert (
        "hat-2 çıkışı servis sağlayıcı NAT'ı olarak tanımlı: bu tanıma göre servis sağlayıcının "
        "yönlendiricisi FortiGate'in bu hattaki NAT adresini (100.64.13.9) ikinci kez çevirir"
        in (method)
    )
    assert "Arama bu çıkışın NAT adresleriyle sınırlandı. Port koruma: kullanılmadı." in method
    dynamic = "hat-2 hattının genel adresi değişebilir olarak işaretli"
    assert text.count(dynamic) == 1
    assert dynamic in method
    assert "Çift NAT: hat-2 çıkışı servis sağlayıcı NAT'ı olarak tanımlı" in method
    assert "Genel adres başka müşterilerle paylaşılıyorsa (CGNAT)" in method
    assert "Çıkış / Exit hat-2 · dstintf -" in text


def test_no_double_nat_paragraph_for_transip(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    site: Site,
) -> None:
    """The FortiGate's own NAT address of an ISP NAT exit (legacy ``wan``): exact with a port,
    nothing with a port no session used, candidates without a port; never a double-NAT text."""
    exits(app_session_factory, {"wan": {"mode": "isp_nat", "transips": [WAN]}})
    signed_in(client, login_as, app_session_factory)
    for query, mode in (
        ({"public_port": 51001}, "exact"),
        ({"public_port": 1}, "none"),
        ({}, "candidates"),
    ):
        data, text = report(client, public_ip=WAN, **query)
        assert (data["mode"], data["outcome"]["egress"]["resolution"]) == (mode, "transip")
        assert data["outcome"]["double_nat"] is True
        assert "Çift NAT" not in text, query
        assert "kesin olarak eşlenemez" not in text, query
        assert "İstenen adres bu çıkışta FortiGate'in kendi NAT adresidir" in text
        # the shared (RFC 6598) address is the exit's NAT address, never called public
        assert "genel/WAN" not in text, query
        assert (
            "Açıklama Talepteki adres FortiGate'in bu çıkıştaki kendi NAT (WAN) adresidir; "
            "transip:transport talepteki adres ve portla birebir eşleşen oturumlar listelenir."
            in text
        ) is (mode == "exact"), query


def test_e_e3_observed_without_exits_report(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    site: Site,
) -> None:
    """E-e3: a site that never defined exits; the asked address is the FortiGate's own NAT
    address of the requester's session near the moment: exact on it, no assumed double NAT and
    no other person's session."""
    observed = "203.0.113.201"
    traffic(
        app_session_factory, layout, {"at": AT, "srcip": PC, "srcport": 51011, "transip": observed}
    )
    signed_in(client, login_as, app_session_factory)
    data, text = report(client, public_ip=observed, public_port=51011)
    assert data["mode"] == "exact"
    assert [
        (m["traffic"]["srcip"], m["traffic"]["srcport"]) for m in data["outcome"]["matches"]
    ] == [(PC, 51011)]
    assert "Kayıtlarda görülen adres / Address seen in the logs" in text
    assert text.count("hiçbir internet çıkışında tanımlı değil, ancak") == 1
    assert "Çift NAT varsayımı" not in text
    assert "Çift NAT" not in text
    assert PERSON_NAME in text  # the requester's own chain
    for other in (PHONE, PHONE_MAC, PHONE_HOSTNAME):
        assert other not in text


def test_e_n_an_unclaimed_search_is_stated(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    site: Site,
) -> None:
    """E-n: hat-2's NAT address is not in the logs near the moment while the unclaimed
    100.64.12.9 is: the search widens to the unclaimed addresses (O5) and the paragraph says
    what it covered instead of "limited to this exit's NAT addresses"."""
    exits(
        app_session_factory,
        {
            "hat-1": {"mode": "direct", "public": [P1]},
            "hat-2": {"mode": "isp_nat", "transips": [T2], "public": [P2]},
        },
    )
    signed_in(client, login_as, app_session_factory)
    data, text = report(client, public_ip=P2)
    assert data["outcome"]["egress"]["searched"]["unclaimed"] is True
    method = _section_4(text)
    assert "Arama bu çıkışın NAT adresleriyle" not in method
    assert (
        "Arama 100.64.13.9 ve hiçbir çıkışa tanımlı olmayan bütün özel ya da paylaşılan kaynak NAT "
        "adresleri (12.10.2026 11:23:41 çevresinde görülenler: 100.64.12.9) adresleriyle "
        "yapıldı." in method
    )
    assert (
        "The search covered 100.64.13.9 and every private or shared source-NAT address no exit "
        "claims (seen around 12.10.2026 11:23:41: 100.64.12.9)." in method
    )
    # "T" glossed as the requested moment in the report's time format, never left bare
    assert "T çevresinde" not in text
    assert "near T" not in text
    assert "Çıkış / Exit - · dstintf -" in text  # its candidates left by an unclaimed address


def test_no_egress_and_not_logged_searched_no_session(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    site: Site,
) -> None:
    exits(
        app_session_factory,
        {
            "hat-1": {"mode": "direct", "public": [P1], "label": "Ana hat"},
            "hat-2": {"mode": "isp_nat", "transips": [T2], "public": [P2]},
            "misafir": {"mode": "not_logged", "public": ["198.51.100.90"]},
        },
    )
    signed_in(client, login_as, app_session_factory)
    data, text = report(client, public_ip="192.0.2.44", public_port=41000)
    assert (data["mode"], data["outcome"]["egress"]["resolution"]) == ("none", "no_egress")
    line = (
        "Oturum aranmadı: istenen adres 192.0.2.44 tanımlı internet çıkışlarından hiçbirine ait "
        "değil (bölüm 4)."
    )
    assert text.count(line) == 2
    assert "Ölçütlere uyan kayıt bulunamadı" not in text
    assert "o sırada yapılandırılmış FortiGate kaynaklarının kayıtlarında da görünmüyor" in text
    assert (
        "Sorgu anında tanımlı çıkışlar: hat-1 (Ana hat): Doğrudan, 203.0.113.200; hat-2: Servis "
        "sağlayıcı NAT'ı, 198.51.100.77; misafir: FortiGate'ten geçmiyor, 198.51.100.90." in text
    )
    assert "Çıkış / Exit Çıkış yok / No exit Açıklama" in text

    data, text = report(client, public_ip="198.51.100.90", public_port=41000)
    assert (data["mode"], data["outcome"]["egress"]["resolution"]) == ("none", "public")
    line = (
        "Oturum aranmadı: istenen adres 198.51.100.90 FortiGate'ten geçmeyen misafir çıkışına ait "
        "(bölüm 4)."
    )
    assert text.count(line) == 2
    assert "Ölçütlere uyan kayıt bulunamadı" not in text
    assert "198.51.100.90, misafir çıkışına ait; bu çıkış FortiGate'ten geçmez" in text
    assert (
        "Genel IP / Public IP — misafir, FortiGate'ten geçmiyor / Does not pass the FortiGate"
        in (text)
    )


def test_every_match_row_prints_its_exit_and_dstintf(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
) -> None:
    """Two lines without a public address: an unclaimed address is searched in both, and each
    candidate names the exit it left by (the phone's session through hat-2 on wan2)."""
    build_site(app_session_factory, layout, wan2=True)
    exits(
        app_session_factory,
        {
            "hat-1": {"mode": "isp_nat", "transips": [T1], "label": "Ana hat"},
            "hat-2": {"mode": "isp_nat", "transips": [T2], "label": "Yedek hat"},
        },
    )
    signed_in(client, login_as, app_session_factory)
    data, text = report(client, public_ip="192.0.2.44")
    matches = data["outcome"]["matches"]
    assert data["outcome"]["egress"]["resolution"] == "public_unknown"
    assert {m["egress"] for m in matches} == {"hat-1", "hat-2"}
    assert (
        "Genel adresi bilinmeyen hatlar / Lines with unknown public address — hat-1 (Ana hat)"
        in (text)
    )
    assert "yapıldı: hat-1 (Ana hat), hat-2 (Yedek hat); bu çıkışlarda" in text
    blocks = re.split(r"Aday \d+: puan", text)[1:]
    assert len(blocks) == len(matches)
    for block in blocks:
        assert "Çıkış / Exit" in block
        assert "dstintf" in block
    assert "Çıkış / Exit hat-2 (Yedek hat) · dstintf wan2" in text
    assert "Çıkış / Exit hat-1 (Ana hat) · dstintf -" in text
    assert "Çift NAT varsayımı" in text


def test_a_destination_search_determines_no_exit(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    site: Site,
) -> None:
    """Without a public IP in the request no exit is resolved: the match's exit row says so and
    never prints "-", which states that no exit claims the NAT address (deviation d)."""
    traffic(
        app_session_factory,
        layout,
        {
            "at": AT,
            "srcip": PC,
            "srcport": 51041,
            "dstip": "198.51.100.44",
            "dstport": 8443,
            "dstintf": "wan1",
        },
    )
    exits(app_session_factory, {"hat-1": {"mode": "isp_nat", "transips": [T1], "public": [P1]}})
    signed_in(client, login_as, app_session_factory)
    data, text = report(client, dest_ip="198.51.100.44", dest_port=8443)
    (match,) = data["outcome"]["matches"]
    assert (data["outcome"]["egress"], match["egress"]) == (None, None)
    assert (
        "Çıkış / Exit çıkış belirlenmedi (talepte genel IP yok) / exit not determined (no public "
        "IP in the request) · dstintf wan1 Oturum aralığı" in text
    )
    assert "Çıkış / Exit -" not in text


def test_e_p2_unconfigured_sender_row(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    site: Site,
) -> None:
    """E-p2: 20 candidates, one of them from a sender that is no configured source: only that
    match carries the warning row, with the sender's name."""
    rows = [
        {"at": AT + timedelta(seconds=n), "srcip": f"172.20.42.{100 + n}", "srcport": 52000 + n}
        for n in range(16)
    ]
    traffic(app_session_factory, layout, *rows)
    with app_session_factory() as session:
        unknown_sender_traffic(Evidence(session, layout), AT, "172.20.42.120", 51021, transip=T1)
        session.commit()
    exits(app_session_factory, {"hat-1": {"mode": "isp_nat", "transips": [T1], "public": [P1]}})
    signed_in(client, login_as, app_session_factory)
    data, text = report(client, public_ip=P1, public_port=51021)
    matches = data["outcome"]["matches"]
    assert len(matches) == 20
    assert [m["untrusted_sources"] for m in matches if m["untrusted_sources"]] == [
        ["unknown-192.0.2.7"]
    ]
    warning = (
        "Bu eşleşmenin kayıtları yapılandırmada tanımlı olmayan bir göndericiden geldi: "
        "unknown-192.0.2.7; kurumun FortiGate'inden geldiği doğrulanmadan kanıt olarak "
        "kullanılmamalıdır."
    )
    assert text.count("Bu eşleşmenin kayıtları yapılandırmada tanımlı olmayan") == 1
    assert warning in text
    assert "1 eşleşmenin kayıtları yapılandırmada tanımlı olmayan bir göndericiden geldi" in (
        _section_4(text)
    )


# -- a stored Wave 2 outcome ----------------------------------------------------------------------

#: The first candidate of the Wave 2 candidates sample (``web/e2e/samples/lookups.json`` before
#: Wave 3; its one non-fictional destination replaced by 203.0.113.40) and the Wave 2 ``none``
#: sample, as stored before the upgrade: no ``egress``, ``dstintf`` or ``source_table``.
WAVE_2_CANDIDATES: dict[str, Any] = {
    "attribution": None,
    "data_window": {
        "db_first_day": "2026-10-09",
        "db_last_day": "2026-10-09",
        "in_database": True,
        "in_vault": True,
        "only_in_vault": False,
        "requested_day": "2026-10-09",
    },
    "double_nat": True,
    "explanations": [
        {
            "key": "double_nat",
            "params": {},
            "text_en": (
                "An exact match needs the ISP router's NAT record; candidates ranked by time "
                "and destination are listed below."
            ),
            "text_tr": (
                "Kesin eşleşme için ISS modeminin NAT kaydı gerekir; aşağıda zaman ve hedefe "
                "göre adaylar listelenmiştir."
            ),
        },
        {
            "key": "person_department_current",
            "params": {},
            "text_en": (
                "The person is the device-person assignment valid at the time of the event "
                "(assignment history). The department is the person's current one; department "
                "changes are not kept as history."
            ),
            "text_tr": (
                "Kişi, olay anında geçerli olan cihaz-kişi atamasıdır (atama geçmişi). "
                "Departman ise kişinin bugünkü departmanıdır; departman değişikliklerinin "
                "geçmişi tutulmaz."
            ),
        },
    ],
    "generated_at": "2026-10-13T04:00:01.589134+00:00",
    "matches": [
        {
            "chain": {
                "alternatives": [],
                "at": "2026-10-09T08:23:41+00:00",
                "confidence": "medium",
                "department": "Planlama",
                "department_source": "person",
                "device": {
                    "confidence": "high",
                    "evidence": [],
                    "hostnames": [
                        "PLN-PC14",
                    ],
                    "infrastructure": False,
                    "mac": "00:00:5e:00:53:57",
                    "name": "Planlama-PC-14",
                    "randomized": False,
                    "type": "pc",
                    "vendor": None,
                },
                "explanations": [],
                "internal_ip": "172.20.42.57",
                "lease": {
                    "confidence": "high",
                    "end": None,
                    "end_reason": "open",
                    "evidence": [
                        {
                            "raw": {
                                "day": "2026-10-09",
                                "line": 5,
                                "offset": 635,
                                "source": "core-r1",
                            },
                            "row_id": 5,
                            "summary": (
                                "dhcp42 assigned 172.20.42.57 for 00:00:5e:00:53:57 PLN-PC14"
                            ),
                            "table": "mt_dhcp",
                            "time": "2026-10-09T07:42:18+00:00",
                        },
                        {
                            "raw": {
                                "day": "2026-10-09",
                                "line": 27,
                                "offset": 10260,
                                "source": "fortigate",
                            },
                            "row_id": 25,
                            "summary": (
                                "172.20.42.57:61001 -> 203.0.113.40:443 proto 6 snat "
                                "100.64.12.9:61001"
                            ),
                            "table": "fg_traffic",
                            "time": "2026-10-09T10:30:00+00:00",
                        },
                    ],
                    "hostname": "PLN-PC14",
                    "ip": "172.20.42.57",
                    "lease_id": 5,
                    "mac": "00:00:5e:00:53:57",
                    "start": "2026-10-09T07:42:18+00:00",
                    "vlan": 42,
                },
                "person": {
                    "assigned_from": "2026-10-01T06:00:00+00:00",
                    "assigned_to": None,
                    "confidence": "medium",
                    "department": "Planlama",
                    "evidence": [],
                    "link": "history",
                    "name": "Selin Kaya",
                    "person_id": 1,
                    "title": "Planlama Uzmanı",
                },
                "vlan": 42,
                "vlan_name": "Planlama",
            },
            "destination_match": "ip_port",
            "kind": "candidate",
            "score": 90,
            "time_distance_ms": 0,
            "traffic": {
                "dstip": "203.0.113.10",
                "dstport": 443,
                "end": "2026-10-09T08:23:41+00:00",
                "evidence": [
                    {
                        "raw": {
                            "day": "2026-10-09",
                            "line": 2,
                            "offset": 160,
                            "source": "fortigate",
                        },
                        "row_id": 1,
                        "summary": (
                            "172.20.42.57:51001 -> 203.0.113.10:443 proto 6 snat 100.64.12.9:51001"
                        ),
                        "table": "fg_traffic",
                        "time": "2026-10-09T08:23:41+00:00",
                    },
                ],
                "proto": 6,
                "sessionid": 51001,
                "srcip": "172.20.42.57",
                "srcport": 51001,
                "start": "2026-10-09T08:23:31+00:00",
                "transip": "100.64.12.9",
                "transport": 51001,
            },
        },
    ],
    "mode": "candidates",
    "request": {
        "at": "2026-10-09T08:23:41+00:00",
        "dest_domain": None,
        "dest_ip": "203.0.113.10",
        "dest_port": 443,
        "internal_ip": None,
        "mac": None,
        "protocol": None,
        "public_ip": "198.51.100.23",
        "public_port": 41234,
        "tolerance_seconds": 60,
    },
    "tolerance_seconds": 60,
    "truncated": False,
}
WAVE_2_NONE: dict[str, Any] = {
    "attribution": None,
    "data_window": {
        "db_first_day": "2026-10-09",
        "db_last_day": "2026-10-09",
        "in_database": True,
        "in_vault": True,
        "only_in_vault": False,
        "requested_day": "2026-10-09",
    },
    "double_nat": True,
    "explanations": [
        {
            "key": "double_nat_no_candidates",
            "params": {},
            "text_en": (
                "An exact match needs the ISP router's NAT record; there is also no candidate "
                "session to list in this time window."
            ),
            "text_tr": (
                "Kesin eşleşme için ISS modeminin NAT kaydı gerekir; bu zaman aralığında "
                "listelenecek aday oturum da bulunamadı."
            ),
        },
        {
            "key": "no_match",
            "params": {},
            "text_en": "No matching session was found in this time window.",
            "text_tr": "Bu zaman aralığında eşleşen oturum bulunamadı.",
        },
        {
            "key": "destination_not_found",
            "params": {
                "destination": "203.0.113.50:443",
            },
            "text_en": (
                "No session to the requested destination (203.0.113.50:443) was found; other "
                "sessions are not listed as candidates. Check the destination, the time and "
                "the time zone. To see every session of the window regardless of the "
                "destination, run the lookup again with the destination fields empty."
            ),
            "text_tr": (
                "İstenen hedefe (203.0.113.50:443) giden oturum bulunamadı; başka oturumlar "
                "aday olarak listelenmez. Hedef bilgisini, saati ve saat dilimini kontrol "
                "edin. Hedeften bağımsız olarak aralıktaki tüm oturumları görmek için sorguyu "
                "hedef alanlarını boş bırakarak yeniden çalıştırın."
            ),
        },
        {
            "key": "no_match_candidates",
            "params": {},
            "text_en": (
                "No candidate session in this window. An exact answer needs the ISP router's "
                "NAT record (public port → FortiGate port), the requested destination IP/port "
                "or domain and the correct time/time zone; a wider tolerance may also help."
            ),
            "text_tr": (
                "Bu aralıkta aday oturum yok. Sonucu kesinleştirmek için: ISS modeminin NAT "
                "kaydı (genel port → FortiGate çıkış portu), talepteki hedef IP/port veya "
                "alan adı ve doğru saat/saat dilimi gerekir; toleransı genişletmek de "
                "yardımcı olabilir."
            ),
        },
    ],
    "generated_at": "2026-10-13T04:00:01.847764+00:00",
    "matches": [],
    "mode": "none",
    "request": {
        "at": "2026-10-09T08:23:41+00:00",
        "dest_domain": None,
        "dest_ip": "203.0.113.50",
        "dest_port": 443,
        "internal_ip": None,
        "mac": None,
        "protocol": None,
        "public_ip": "198.51.100.23",
        "public_port": None,
        "tolerance_seconds": 60,
    },
    "tolerance_seconds": 60,
    "truncated": False,
}
