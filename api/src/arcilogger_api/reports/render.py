"""PDF of an official 5651 report ("Resmi Rapor", spec 9.5) — a pure function of its inputs.

:func:`render` turns the object of :func:`arcilogger_api.reports.inputs.build_inputs` into PDF
bytes with reportlab in **invariant mode** (fixed creation date and document id, deterministic
font subsets): the same inputs give the same bytes, on any machine with the same reportlab and
font files (both are recorded in the inputs). Sections:

1. report details (lookup number, organisation, operator, times, ARCILOGGER version);
2. request details (makam, dosya/evrak no, talep tarihi, gerekçe);
3. query (time as entered and as interpreted, addresses, ports, tolerance);
4. method (incl. the double-NAT explanation and what would make the answer exact), the result
   of the raw line check (:mod:`.consistency`) and the engine's explanations;
5. results (exact matches or candidates, each with the full attribution chain; internal chains;
   a result whose raw lines contradict it is shown as such, never as an exact match and without
   a confidence);
6. evidence (raw lines with file, line number, byte offset, SHA-256 and the check; raw files with
   their hashes; sealed days with manifest/token hashes, time-stamp serial, genTime, TSA and the
   verification result);
7. how to verify the report (its SHA-256 and the optional time stamp);
8. legal note.

Every page carries "Sayfa n / N", the lookup number, the version and the neutral wording rule
(no claim of legal compliance).
"""

from __future__ import annotations

import hashlib
import io
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Final
from xml.sax.saxutils import escape

from reportlab.lib.pagesizes import A4  # type: ignore[import-untyped]
from reportlab.lib.units import mm  # type: ignore[import-untyped]
from reportlab.pdfbase.pdfmetrics import stringWidth  # type: ignore[import-untyped]
from reportlab.platypus import (  # type: ignore[import-untyped]
    CondPageBreak,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from arcilogger_api.reports import texts
from arcilogger_api.reports.inputs import evidence_key
from arcilogger_core.fmt import format_date, format_datetime
from arcilogger_worker.lookup import (
    AttributionChain,
    EvidenceRef,
    Explanation,
    LookupOutcome,
    LookupRequest,
    NatMatch,
    PersonInfo,
)
from arcilogger_worker.pdf.fonts import SANS, register_fonts
from arcilogger_worker.pdf.styles import (
    LINE,
    MARGIN,
    MUTED,
    SUBTLE,
    PagedCanvas,
)
from arcilogger_worker.pdf.styles import fit as _fit
from arcilogger_worker.pdf.styles import report_styles as _styles

LABEL_WIDTH: Final = 50 * mm


@dataclass(frozen=True)
class Rendered:
    pdf: bytes
    pages: int

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.pdf).hexdigest()


# -- value formatting -----------------------------------------------------------------------------


def _x(value: object) -> str:
    """Escape for reportlab's paragraph markup; line breaks become ``<br/>``."""
    return escape(str(value)).replace("\r\n", "\n").replace("\n", "<br/>")


def _dash(value: object) -> str:
    return "—" if value is None or value == "" else str(value)


def _site(generated: Mapping[str, Any]) -> str:
    """The organisation's name (``site_name``); "" while the installation has none set."""
    return str(generated.get("site_name") or "")


def _time(value: datetime | str | None) -> str:
    """``12.10.2026 18:19:07`` (Europe/Istanbul)."""
    if value is None:
        return "—"
    moment = datetime.fromisoformat(value) if isinstance(value, str) else value
    return format_datetime(moment)


def _time_utc(value: datetime | str | None) -> str:
    """``12.10.2026 18:19:07 (UTC 12.10.2026 15:19:07)``."""
    if value is None:
        return "—"
    moment = datetime.fromisoformat(value) if isinstance(value, str) else value
    return f"{format_datetime(moment)} (UTC {moment.astimezone(UTC):%d.%m.%Y %H:%M:%S})"


def _day(value: date | str | None) -> str:
    if value is None:
        return "—"
    return format_date(date.fromisoformat(value) if isinstance(value, str) else value)


def _endpoint(ip: str | None, port: int | None) -> str:
    return f"{ip or '?'}:{port}" if port is not None else _dash(ip)


def _protocol(number: int | None) -> str:
    if number is None:
        return "—"
    name = texts.PROTOCOLS.get(number)
    return f"{number} ({name})" if name else str(number)


def _seconds(milliseconds: int) -> str:
    """``1500`` → ``1,5 sn`` (Turkish decimal comma)."""
    whole, rest = divmod(milliseconds, 1000)
    return f"{whole} sn" if not rest else f"{whole},{f'{rest:03d}'.rstrip('0')} sn"


def _yes(value: bool | None) -> str:
    return "—" if value is None else ("evet" if value else "hayır")


def _assignment(person: PersonInfo) -> str:
    """`` · atama: 12.03.2026 09:00:00 tarihinden itibaren`` (or ``… - <end>``) for a person
    from the assignment history; nothing for a ``current`` link (whose note is the
    ``person_link_at_lookup_time`` explanation), so reports of older lookups render unchanged."""
    if person.link != "history" or person.assigned_from is None:
        return ""
    start = _time(person.assigned_from)
    if person.assigned_to is None:
        return f" · atama: {start} tarihinden itibaren"
    return f" · atama: {start} - {_time(person.assigned_to)}"


def _chain_evidence(chain: AttributionChain) -> list[EvidenceRef]:
    refs: list[EvidenceRef] = []
    for step in (chain.lease, chain.device, chain.person):
        if step is not None:
            refs += step.evidence
    for other in chain.alternatives:
        refs += _chain_evidence(other)
    return refs


def _contradicts(check: Mapping[str, Any] | None) -> bool:
    """The re-parsed raw line does not support what the report shows (:mod:`.consistency`)."""
    return check is not None and not check.get("ok") and check.get("error") != "not_checked"


def _check_text(check: Mapping[str, Any]) -> str:
    """``UYARI: … kaynak IP raporda X, ham satırda Y; …`` for a contradicting line."""
    error = check.get("error")
    if error:
        reason = texts.CHECK_ERRORS.get(str(error), str(error))
        return (
            f"UYARI: ham satır raporla uyuşmuyor: {reason}. / WARNING: the raw line does not match."
        )
    differences = "; ".join(
        f"{texts.CHECK_FIELDS.get(str(m['field']), m['field'])} raporda {_dash(m['report'])}, "
        f"ham satırda {_dash(m['raw'])}"
        for m in check.get("mismatches") or []
    )
    return (
        f"UYARI: ham satır, raporda veritabanından gösterilen kayıtla uyuşmuyor: {differences}. "
        "/ WARNING: the raw line does not match the database row shown."
    )


# -- document -------------------------------------------------------------------------------------


class _Builder:
    def __init__(self, inputs: Mapping[str, Any]) -> None:
        self.inputs = inputs
        self.s = _styles()
        self.story: list[Any] = []
        self.outcome = LookupOutcome.from_dict(inputs["outcome"])
        self.numbers = {item["key"]: int(item["no"]) for item in inputs.get("evidence") or []}
        #: Evidence whose raw line contradicts the report (reports made before the check have
        #: no ``check``: nothing is marked).
        self.contradicted = {
            item["key"] for item in inputs.get("evidence") or [] if _contradicts(item.get("check"))
        }

    # paragraphs and tables

    def p(self, markup: str, style: str = "base") -> Any:
        return Paragraph(markup, self.s[style])

    def add(self, *flowables: Any) -> None:
        self.story.extend(flowables)

    def heading(self, text_tr: str, text_en: str) -> None:
        self.add(
            CondPageBreak(30 * mm),
            self.p(f"{_x(text_tr)} <font size=8 color='#475569'>/ {_x(text_en)}</font>", "h2"),
        )

    def bilingual(self, text_tr: str, text_en: str, style: str = "base") -> None:
        self.add(self.p(_x(text_tr), style), self.p(_x(text_en), "small"), Spacer(1, 3))

    def kv(self, rows: Sequence[tuple[str, Any]], *, header: str | None = None) -> Any:
        data: list[list[Any]] = []
        commands: list[tuple[Any, ...]] = [
            # plain-string cells (the spanned header's empty cell) would otherwise use the
            # standard Helvetica, which is not embedded and has no "ı", "ş", "ğ"
            ("FONTNAME", (0, 0), (-1, -1), SANS),
            ("GRID", (0, 0), (-1, -1), 0.4, LINE),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 2.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ]
        if header is not None:
            data.append([self.p(header, "label"), ""])
            commands += [("SPAN", (0, 0), (1, 0)), ("BACKGROUND", (0, 0), (1, 0), SUBTLE)]
        for label, value in rows:
            cell = value if not isinstance(value, str) else self.p(value)
            data.append([self.p(_x(label), "label"), cell])
        width = A4[0] - 2 * MARGIN
        table = Table(
            data, colWidths=[LABEL_WIDTH, width - LABEL_WIDTH], hAlign="LEFT", splitInRow=1
        )
        table.setStyle(TableStyle(commands))
        return table

    def refs(self, evidence: Iterable[EvidenceRef]) -> str:
        numbers = sorted(
            {self.numbers[key] for key in map(evidence_key, evidence) if key in self.numbers}
        )
        return ", ".join(f"K{number}" for number in numbers) if numbers else "—"

    def contradicting(self, evidence: Iterable[EvidenceRef]) -> list[int]:
        """Numbers of the evidence items among ``evidence`` whose raw line contradicts the
        report."""
        keys = {evidence_key(ref) for ref in evidence} & self.contradicted
        return sorted(self.numbers[key] for key in keys if key in self.numbers)

    def contradiction_warning(self) -> bool:
        """The red warning of a result its raw lines contradict (sections 4 and 5)."""
        found = self.inputs.get("consistency")
        if not isinstance(found, Mapping) or found.get("ok"):
            return False
        refs = ", ".join(f"K{number}" for number in found.get("contradicted") or [])
        self.add(
            self.p(_x(texts.CONSISTENCY_FAILED_TR.format(refs=refs)), "warn"),
            self.p(_x(texts.CONSISTENCY_FAILED_EN.format(refs=refs)), "small"),
            Spacer(1, 4),
        )
        return True

    def consistency(self) -> None:
        """Section 4: the re-check of the cited raw lines (reports made since the check)."""
        found = self.inputs.get("consistency")
        if not isinstance(found, Mapping):
            return
        self.add(self.p(_x(texts.CONSISTENCY_TITLE), "h3"))
        self.bilingual(texts.CONSISTENCY_TR, texts.CONSISTENCY_EN, "small")
        if self.contradiction_warning():
            return
        if found.get("checked"):
            self.add(self.p(_x(texts.CONSISTENCY_OK_TR.format(checked=found["checked"]))))
        else:
            self.add(self.p(_x(texts.CONSISTENCY_NONE_TR), "small"))

    def explanations(self, items: Iterable[Explanation]) -> None:
        for item in items:
            self.add(
                self.p(_x(item.text_tr), "bullet"),
                self.p(_x(item.text_en), "small"),
            )

    # sections

    def build(self) -> list[Any]:
        self.header()
        self.report_details()
        self.request_details()
        self.query()
        self.method()
        self.results()
        self.evidence()
        self.verification()
        self.legal()
        return self.story

    def header(self) -> None:
        lookup = self.inputs["lookup"]
        site = _site(self.inputs["generated"])
        where = f"{site} ağında " if site else ""
        self.add(
            self.p(_x(texts.TITLE_TR), "title"),
            self.p(_x(f"Resmi Rapor · {texts.TITLE_EN}"), "subtitle"),
            Spacer(1, 4),
            self.p(
                _x(
                    f"Bu rapor, {where}ARCILOGGER ile yapılan "
                    f"{lookup['id']} numaralı 5651 sorgusunun sonucunu ve dayandığı kayıtları "
                    "içerir."
                )
            ),
            Spacer(1, 2),
        )

    def report_details(self) -> None:
        lookup = self.inputs["lookup"]
        generated = self.inputs["generated"]
        operator = lookup["operator"]
        by = generated["by"]
        self.heading("1. Rapor bilgileri", "Report details")
        self.add(
            self.kv(
                [
                    ("Sorgu no / Lookup no", _x(lookup["id"])),
                    ("Kurum / Organisation", _x(_dash(_site(generated)))),
                    (
                        "Sorguyu yapan / Operator",
                        _x(f"{operator['display_name']} ({operator['username']})"),
                    ),
                    ("Sorgu zamanı / Lookup time", _x(_time_utc(lookup.get("created_at")))),
                    (
                        "Raporu oluşturan / Generated by",
                        _x(f"{by['display_name']} ({by['username']})"),
                    ),
                    ("Rapor zamanı / Generated at", _x(_time_utc(generated["at"]))),
                    ("ARCILOGGER sürümü / Version", _x(generated["version"])),
                    ("Rapor dosyası / File", self.p(_x(generated["report_file"]), "mono")),
                ]
            )
        )

    def request_details(self) -> None:
        lookup = self.inputs["lookup"]
        self.heading("2. Talep bilgileri", "Request details")
        self.add(
            self.kv(
                [
                    ("Makam / Authority", _x(lookup["authority"])),
                    ("Dosya / evrak no / File no", _x(lookup["file_no"])),
                    ("Talep tarihi / Request date", _x(_day(lookup["request_date"]))),
                    ("Gerekçe / Reason", _x(lookup["reason"])),
                ]
            )
        )

    def query(self) -> None:
        request: LookupRequest = self.outcome.request
        time_input = self.inputs["lookup"].get("time_input") or {}
        entered = ""
        if time_input:
            how = (
                "saat dilimi girişte belirtilmiş"
                if time_input.get("offset_given")
                else f"saat dilimi belirtilmedi, {time_input.get('time_zone')} kabul edildi"
            )
            entered = f"<br/>Girilen / entered: “{_x(time_input.get('text'))}” — {_x(how)}"
        self.heading("3. Sorgu ölçütleri", "Query")
        self.add(
            self.kv(
                [
                    ("Zaman / Time", _x(_time_utc(request.at)) + " (Europe/Istanbul)" + entered),
                    (
                        "Tolerans / Tolerance",
                        _x(f"± {self.outcome.tolerance_seconds} sn"),
                    ),
                    ("Genel IP / Public IP", _x(_dash(request.public_ip))),
                    ("Genel port / Public port", _x(_dash(request.public_port))),
                    ("Protokol / Protocol", _x(_protocol(request.protocol))),
                    ("Hedef IP / Destination IP", _x(_dash(request.dest_ip))),
                    ("Hedef port / Destination port", _x(_dash(request.dest_port))),
                    ("Hedef alan adı / Domain", _x(_dash(request.dest_domain))),
                    ("İç IP / Internal IP", _x(_dash(request.internal_ip))),
                    ("MAC adresi / MAC address", _x(_dash(request.mac))),
                ]
            )
        )

    def method(self) -> None:
        outcome = self.outcome
        mode_tr, mode_en = texts.MODES[outcome.mode]
        self.heading("4. Yöntem", "Method")
        self.add(
            self.kv(
                [
                    ("Sorgu türü / Mode", _x(f"{mode_tr} / {mode_en}")),
                    ("Açıklama", _x(texts.MODE_DETAIL_TR[outcome.mode])),
                ]
            ),
            Spacer(1, 5),
        )
        self.bilingual(texts.METHOD_TR, texts.METHOD_EN)
        self.consistency()
        if outcome.double_nat and outcome.mode in ("candidates", "none"):
            # the "candidate list" sentence only when candidates follow (spec 12.4)
            self.add(self.p("Çift NAT / Double NAT", "h3"))
            self.bilingual(
                f"{texts.DOUBLE_NAT_TR} {texts.DOUBLE_NAT_RESULT_TR[outcome.mode]} "
                f"{texts.DOUBLE_NAT_EXACT_TR}",
                f"{texts.DOUBLE_NAT_EN} {texts.DOUBLE_NAT_RESULT_EN[outcome.mode]} "
                f"{texts.DOUBLE_NAT_EXACT_EN}",
            )
        if outcome.mode == "candidates":
            self.bilingual(texts.SCORE_TR, texts.SCORE_EN)
        if outcome.explanations:
            self.add(self.p("Sorgu motorunun açıklamaları / Explanations", "h3"))
            self.explanations(outcome.explanations)
        window = outcome.data_window
        if window is not None:
            covered = (
                f"{_day(window.db_first_day)} - {_day(window.db_last_day)}"
                if window.db_first_day
                else "—"
            )
            self.add(
                Spacer(1, 3),
                self.p(
                    _x(
                        f"Veri penceresi: istenen gün {_day(window.requested_day)}; "
                        f"veritabanında: {_yes(window.in_database)}; mühürlü arşivde: "
                        f"{_yes(window.in_vault)}; veritabanının kapsadığı günler: {covered}."
                    ),
                    "small",
                ),
            )

    # results

    def results(self) -> None:
        outcome = self.outcome
        self.heading("5. Sonuçlar", "Results")
        count = len(outcome.matches)
        if outcome.mode == "none":
            self.add(
                self.p(
                    _x("Ölçütlere uyan kayıt bulunamadı. Nedenleri bölüm 4'teki açıklamalardadır."),
                    "warn",
                )
            )
            return
        self.contradiction_warning()
        if outcome.mode == "internal" and outcome.attribution is not None:
            self.add(self.p("İç IP / MAC adresinin zinciri / Attribution chain", "h3"))
            self.chain(outcome.attribution)
            return
        bad = sum(1 for match in outcome.matches if self.match_contradicted(match))
        noun = "kesin eşleşme" if outcome.mode == "exact" else "aday oturum"
        summary = f"{count - bad} {noun}" if bad else f"{count} {noun}"
        if bad:
            summary += f", {bad} kanıtla uyuşmayan sonuç (bkz. uyarı)"
        if outcome.truncated:
            summary += " (liste kısaltıldı; bkz. açıklamalar)"
        self.add(self.p(_x(summary + "."), "base"), Spacer(1, 3))
        for index, match in enumerate(outcome.matches, start=1):
            self.match(index, match)

    def match_evidence(self, match: NatMatch) -> list[EvidenceRef]:
        refs = list(match.traffic.evidence)
        if match.chain is not None:
            refs += _chain_evidence(match.chain)
        return refs

    def match_contradicted(self, match: NatMatch) -> list[int]:
        """Evidence numbers of ``match`` (session and chain) whose raw line contradicts it."""
        return self.contradicting(self.match_evidence(match))

    def match(self, index: int, match: NatMatch) -> None:
        traffic = match.traffic
        contradicted = self.match_contradicted(match)
        if contradicted:
            # never presented as an exact match or with its score: the evidence says otherwise
            title = f"Sonuç {index}: {texts.CONTRADICTED_TR} / {texts.CONTRADICTED_EN}"
        elif match.kind == "exact":
            title = f"Sonuç {index}: kesin eşleşme / exact match"
        else:
            title = f"Aday {index}: puan {match.score}/100 / candidate, score {match.score}"
        distance = (
            "0 sn (istenen an oturum aralığının içinde)"
            if match.time_distance_ms == 0
            else _seconds(match.time_distance_ms)
        )
        rows = [
            (
                "Oturum / Session",
                _x(
                    f"{_endpoint(traffic.srcip, traffic.srcport)} → "
                    f"{_endpoint(traffic.dstip, traffic.dstport)} · protokol "
                    f"{_protocol(traffic.proto)} · FortiGate oturum no {_dash(traffic.sessionid)}"
                ),
            ),
            ("NAT (transip:transport)", _x(_endpoint(traffic.transip, traffic.transport))),
            (
                "Oturum aralığı / Interval",
                _x(f"{_time(traffic.start)} - {_time(traffic.end)}"),
            ),
            ("Zaman farkı / Time distance", _x(distance)),
            (
                "Hedef / Destination",
                _x(texts.DESTINATION.get(match.destination_match, match.destination_match)),
            ),
            ("Kanıt / Evidence", _x(self.refs(traffic.evidence))),
        ]
        if contradicted:
            numbers = ", ".join(f"K{number}" for number in contradicted)
            rows.append(
                (
                    "UYARI / Warning",
                    self.p(
                        _x(
                            f"Bu sonucun dayandığı ham satırlar ({numbers}) yukarıdaki değerlerle "
                            "uyuşmuyor; sonuç ham kayıtlarla desteklenmiyor (bkz. bölüm 6). / The "
                            "raw lines of this result contradict it."
                        ),
                        "warn",
                    ),
                )
            )
        self.add(
            CondPageBreak(40 * mm),
            KeepTogether([self.kv(rows, header=_x(title))]),
            Spacer(1, 2),
        )
        if match.chain is None:
            self.add(
                self.p(
                    _x(
                        f"{traffic.srcip} adresi için bu anda geçerli bir IP-MAC kaydı (kira) yok; "
                        "zincir kurulamadı."
                    ),
                    "note",
                ),
                Spacer(1, 6),
            )
            return
        self.chain(match.chain, contradicted=bool(contradicted))
        self.add(Spacer(1, 6))

    def chain(
        self, chain: AttributionChain, *, alternative: bool = False, contradicted: bool = False
    ) -> None:
        """``contradicted``: the result the chain belongs to is contradicted by its raw lines
        (the chain's own lines are checked here as well)."""
        lease_evidence = chain.lease.evidence if chain.lease is not None else ()
        contradicted = contradicted or bool(self.contradicting(lease_evidence))
        vlan = f" · VLAN {chain.vlan}" if chain.vlan is not None else ""
        vlan += f" ({chain.vlan_name})" if chain.vlan_name else ""
        rows: list[tuple[str, Any]] = [
            ("İç IP / Internal IP", _x(f"{chain.internal_ip}{vlan} · an: {_time(chain.at)}")),
        ]
        lease = chain.lease
        if lease is not None:
            end = _time(lease.end) if lease.end is not None else "açık"
            rows.append(
                (
                    "IP-MAC kaydı / Lease",
                    _x(
                        f"MAC {lease.mac} · ana bilgisayar adı {_dash(lease.hostname)} · "
                        f"{_time(lease.start)} - {end} · bitiş: "
                        f"{texts.END_REASONS.get(lease.end_reason, lease.end_reason)} · güven: "
                        f"{texts.CONFIDENCE.get(lease.confidence, lease.confidence)} · kayıt no "
                        f"{_dash(lease.lease_id)} · kanıt: {self.refs(lease.evidence)}"
                    ),
                )
            )
        device = chain.device
        if device is not None:
            kind = texts.DEVICE_TYPES.get(device.type or "", device.type) if device.type else None
            hostnames = ", ".join(device.hostnames) if device.hostnames else None
            rows.append(
                (
                    "Cihaz / Device",
                    _x(
                        f"{_dash(device.name)} · MAC {device.mac} · ana bilgisayar adları "
                        f"{_dash(hostnames)} · üretici {_dash(device.vendor)} · tür "
                        f"{_dash(kind)} · rastgele (yerel) MAC: {_yes(device.randomized)} · "
                        "altyapı cihazı: "
                        f"{_yes(device.infrastructure)} · güven: "
                        f"{texts.CONFIDENCE.get(device.confidence, device.confidence)}"
                    ),
                )
            )
        person = chain.person
        if person is not None:
            rows.append(
                (
                    "Kişi / Person",
                    _x(
                        f"{person.name} · departman {_dash(person.department)} · unvan "
                        f"{_dash(person.title)} · güven: "
                        f"{texts.CONFIDENCE.get(person.confidence, person.confidence)} "
                        f"(idari atama){_assignment(person)}"
                    ),
                )
            )
        else:
            rows.append(("Kişi / Person", _x("Cihaz bir kişiye bağlanmamış.")))
        rows += [
            (
                "Departman / Department",
                _x(
                    f"{_dash(chain.department)} ("
                    f"{texts.DEPARTMENT_SOURCES.get(chain.department_source, '—')})"
                ),
            ),
            (
                "Zincir güveni / Confidence",
                self.p(
                    _x(
                        f"{texts.CONTRADICTED_TR}: güven düzeyi verilemez (veritabanına göre "
                        f"{texts.CONFIDENCE.get(chain.confidence, chain.confidence)}; bkz. bölüm "
                        "6) / no confidence: contradicted by its evidence"
                    ),
                    "warn",
                )
                if contradicted
                else _x(texts.CONFIDENCE.get(chain.confidence, chain.confidence)),
            ),
        ]
        header = "Olası diğer zincir (IP çakışması)" if alternative else "Atıf zinciri / Chain"
        self.add(self.kv(rows, header=_x(header)))
        if chain.explanations:
            self.add(Spacer(1, 2))
            self.explanations(chain.explanations)
        for other in chain.alternatives:
            self.add(Spacer(1, 3))
            self.chain(other, alternative=True)

    # evidence

    def evidence(self) -> None:
        self.heading("6. Kanıt listesi", "Evidence")
        self.bilingual(texts.EVIDENCE_INTRO_TR, texts.EVIDENCE_INTRO_EN, "small")
        items = self.inputs.get("evidence") or []
        if not items:
            self.add(self.p(_x("Bu sonuç için kanıt satırı yok."), "base"))
        for item in items:
            self.evidence_item(item)
        self.add(self.p("6.2 Ham kayıt dosyaları / Raw log files", "h3"))
        files = self.inputs.get("files") or []
        if not files:
            self.add(self.p(_x("Ham dosya kullanılmadı."), "small"))
        for facts in files:
            self.file_facts(facts)
        self.add(self.p("6.3 Mühür ve zaman damgaları / Seals and time stamps", "h3"))
        days = self.inputs.get("days") or []
        if not days:
            self.add(self.p(_x("Kanıt satırı olmadığı için gün yok."), "small"))
        for facts in days:
            self.day_facts(facts)

    def evidence_item(self, item: Mapping[str, Any]) -> None:
        raw = item.get("raw")
        line = item.get("line") or {}
        table = texts.TABLES.get(item["table"], item["table"])
        rows: list[tuple[str, Any]] = [
            ("Kayıt / Record", _x(f"{table} ({item['table']} #{_dash(item.get('row_id'))})")),
            ("Zaman / Time", _x(_time(item.get("time")))),
            ("Özet / Summary", _x(_dash(item.get("summary")))),
        ]
        if raw is not None:
            where = f"kaynak {raw['source']} · gün {_day(raw['day'])} · satır {raw['line']}"
            if line.get("offset") is not None:
                where += f" · bayt {line['offset']}"
            rows.append(("Ham satır yeri / Location", _x(where)))
            if line.get("file"):
                rows.append(("Dosya / File", self.p(_x(line["file"]), "mono")))
        status = line.get("status")
        if status in ("ok", "offset_mismatch"):
            text = str(line["text"])
            rows.append(
                ("Ham satır / Raw line", self.p(_x(text[: texts.RAW_DISPLAY_CHARS]), "mono"))
            )
            if len(text) > texts.RAW_DISPLAY_CHARS:
                rows.append(
                    (
                        "Kısaltıldı / Cut",
                        self.p(
                            _x(
                                f"Satır çok uzun ({line.get('length')} bayt): raporda ilk "
                                f"{texts.RAW_DISPLAY_CHARS} karakteri gösterildi. Satırın "
                                "tamamı yukarıdaki dosyada ve ARCILOGGER ham satır "
                                "görünümündedir; aşağıdaki SHA-256 satırın tamamınındır. / The "
                                "line is cut in this report; the hash covers the whole line."
                            ),
                            "warn",
                        ),
                    )
                )
            rows.append(("Satır SHA-256 / Line hash", self.p(_x(line["sha256"]), "mono")))
            if status == "offset_mismatch":
                rows.append(
                    (
                        "Uyarı",
                        self.p(
                            _x(
                                f"{texts.LINE_STATUS['offset_mismatch']} Kayıtlı bayt: "
                                f"{line.get('expected_offset')}, dosyadaki: {line.get('offset')}."
                            ),
                            "warn",
                        ),
                    )
                )
        else:
            message = texts.LINE_STATUS.get(str(status)) or texts.FILE_STATUS.get(str(status))
            rows.append(("Ham satır / Raw line", self.p(_x(_dash(message)), "warn")))
        check = item.get("check")
        if _contradicts(check):
            rows.append(("Karşılaştırma / Check", self.p(_x(_check_text(check or {})), "warn")))
        elif check is not None and check.get("ok"):
            rows.append(
                (
                    "Karşılaştırma / Check",
                    _x("ham satır, raporda gösterilen kayıtla uyuşuyor / the raw line matches"),
                )
            )
        self.add(KeepTogether([self.kv(rows, header=_x(f"K{item['no']}"))]), Spacer(1, 4))

    def file_facts(self, facts: Mapping[str, Any]) -> None:
        status = str(facts.get("status"))
        rows: list[tuple[str, Any]] = [
            (
                "Durum / Status",
                self.p(
                    _x(texts.FILE_STATUS.get(status, status)),
                    "warn" if status in ("seal_missing", "unreadable") else "base",
                ),
            ),
        ]
        if facts.get("file"):
            rows.append(("Dosya / File", self.p(_x(facts["file"]), "mono")))
        if facts.get("sha256"):
            label = (
                "SHA-256 (rapor anı) / at report time"
                if status == "unsealed"
                else "SHA-256 (dosya) / file"
            )
            rows.append((label, self.p(_x(facts["sha256"]), "mono")))
        if facts.get("size") is not None:
            size = f"{facts['size']} bayt"
            if facts.get("lines") is not None:
                size += f" · {facts['lines']} tam satır"
            rows.append(("Boyut / Size", _x(size)))
        manifest = facts.get("manifest")
        if manifest:
            rows.append(
                ("Manifestteki SHA-256 / in manifest", self.p(_x(manifest.get("sha256")), "mono"))
            )
            rows.append(
                (
                    "Açılmış içerik SHA-256 / uncompressed",
                    self.p(_x(_dash(manifest.get("sha256_uncompressed"))), "mono"),
                )
            )
            rows.append(("Manifestteki satır / lines", _x(_dash(manifest.get("lines")))))
        if facts.get("matches_manifest") is not None:
            matches = bool(facts["matches_manifest"])
            if matches:
                verdict = "evet — dosya mühürlendiği gibi"
            elif manifest:
                verdict = "HAYIR — dosya farklı"
            else:
                verdict = "HAYIR — dosya günün manifestinde listelenmemiş"
            rows.append(
                (
                    "Manifestle eşleşme / matches",
                    self.p(_x(verdict), "base" if matches else "warn"),
                )
            )
        if facts.get("error"):
            rows.append(("Hata / Error", self.p(_x(facts["error"]), "warn")))
        header = f"{facts['source']} · {_day(facts['day'])}"
        self.add(KeepTogether([self.kv(rows, header=_x(header))]), Spacer(1, 4))

    def day_facts(self, facts: Mapping[str, Any]) -> None:
        header = f"Gün / Day {_day(facts['day'])}"
        status = facts.get("status") or ("sealed" if facts.get("sealed") else "unsealed")
        if status == "unsealed":
            rows: list[tuple[str, Any]] = [
                ("Durum / Status", self.p(_x(texts.UNSEALED_TR), "note")),
            ]
            if facts.get("late"):
                rows.append(("Gecikme / Overdue", self.p(_x(texts.UNSEALED_LATE_TR), "warn")))
            self.add(KeepTogether([self.kv(rows, header=_x(header))]), Spacer(1, 4))
            return
        verification = facts.get("verification") or {}
        tsa = facts.get("tsa") or {}
        manifest = facts.get("manifest")
        token = facts.get("token")
        rows = []
        if status == "seal_missing":
            rows.append(
                (
                    "Durum / Status",
                    self.p(_x(f"{texts.SEAL_MISSING_TR} / {texts.SEAL_MISSING_EN}"), "warn"),
                )
            )
        for label, found, key in (
            ("Manifest", manifest, "file"),
            ("Manifest SHA-256", manifest, "sha256"),
            ("Zaman damgası / Token", token, "file"),
            ("Damga SHA-256 / Token hash", token, "sha256"),
        ):
            if found:
                rows.append((label, self.p(_x(found[key]), "mono")))
            else:
                rows.append((label, self.p(_x("YOK — kasada bulunamadı / not found"), "warn")))
        if manifest:
            provider_text, style = provider_line(tsa, verification)
            rows.append(("Sağlayıcı / Provider", self.p(_x(provider_text), style)))
        if token:
            rows += [
                ("Seri no / Serial", self.p(_x(_dash(verification.get("serial"))), "mono")),
                ("Damga zamanı / genTime", _x(_time_utc(verification.get("gen_time")))),
                ("TSA", _x(_dash(verification.get("tsa_name")))),
                ("Politika / Policy", self.p(_x(_dash(verification.get("policy"))), "mono")),
            ]
        if facts.get("stamped_late"):
            rows.append(("Geç damga / Late", self.p(_x("Gün geç mühürlendi."), "note")))
        if verification.get("ok"):
            text = (
                "DOĞRULANDI — damga güvenilen köke bağlanıyor"
                f" ({_dash(verification.get('root'))}), manifest özeti damgadakiyle aynı, günün "
                "mühürlü dosyaları manifestle eşleşiyor, önceki güne bağlantı tutarlı. / Verified."
            )
            rows.append(("Doğrulama / Verification", self.p(_x(text))))
        else:
            errors = "; ".join(verification.get("errors") or []) or "bilinmeyen hata"
            rows.append(
                (
                    "Doğrulama / Verification",
                    self.p(_x(f"DOĞRULANAMADI / NOT VERIFIED: {errors}"), "warn"),
                )
            )
        warnings = verification.get("warnings") or []
        if warnings:
            rows.append(("Uyarılar / Warnings", self.p(_x("; ".join(warnings)), "small")))
        if manifest:
            rows.append(("Önceki gün / Previous day", self.previous_day(facts)))
        self.add(KeepTogether([self.kv(rows, header=_x(header))]), Spacer(1, 4))

    def previous_day(self, facts: Mapping[str, Any]) -> Any:
        """The manifest's link to the day before; "first day" only where the chain starts."""
        previous = facts.get("previous")
        if isinstance(previous, dict):
            return self.p(
                _x(
                    f"{_day(previous.get('day'))} · manifest SHA-256 "
                    f"{_dash(previous.get('manifest_sha256'))}"
                ),
                "mono",
            )
        if facts.get("chain_first_day", facts["day"]) == facts["day"]:
            return self.p(_x("— (zincirin ilk günü: kasadaki en eski mühürlü gün)"))
        return self.p(
            _x(
                "YOK — manifest önceki güne bağlanmıyor, ancak bu gün zincirin ilk günü değil "
                "(bkz. doğrulama). / No link to the previous day."
            ),
            "warn",
        )

    # closing sections

    def verification(self) -> None:
        plan = self.inputs.get("timestamp") or {}
        report_file = self.inputs["generated"]["report_file"]
        self.heading("7. Raporun doğrulanması", "Verifying this report")
        self.bilingual(texts.VERIFY_TR, texts.VERIFY_EN)
        if not plan.get("requested"):
            self.add(
                self.p(
                    _x(
                        "Bu rapor için zaman damgası istenmedi (ayar lookup.report_timestamp "
                        "kapalı). / No time stamp was requested for this report."
                    ),
                    "small",
                )
            )
            return
        provider = _dash(plan.get("provider"))
        self.add(
            self.p(
                _x(
                    f"Raporun SHA-256 özeti, rapor oluşturulduktan hemen sonra {provider} "
                    f"sağlayıcısıyla RFC 3161 zaman damgasına gönderilir (1 kontör); damga "
                    f"dosyası {report_file}.zd adıyla raporun yanında saklanır. Damganın sonucu "
                    "(seri no, zaman, doğrulama) ARCILOGGER'da sorgu kaydında ve denetim kaydında "
                    "(lookup.report) görülür. / The report's hash is time-stamped right after "
                    f"it is generated; the token is stored as {report_file}.zd."
                )
            )
        )
        if not plan.get("legal_grade"):
            self.add(
                Spacer(1, 2),
                self.p(
                    _x(
                        f"UYARI: Yapılandırılan zaman damgası sağlayıcısı ({provider}) yasal "
                        "geçerli değildir (Kamu SM üretim sunucusu değil); bu damga yalnızca test "
                        "amaçlıdır. / WARNING: the configured time-stamp provider is not legally "
                        "valid (not the Kamu SM production server)."
                    ),
                    "warn",
                ),
            )

    def legal(self) -> None:
        self.heading("8. Yasal not", "Legal note")
        self.bilingual(texts.LEGAL_NOTE_TR, texts.LEGAL_NOTE_EN)
        self.bilingual(texts.PERSON_NOTE_TR, texts.PERSON_NOTE_EN)


def provider_line(tsa: Mapping[str, Any], verification: Mapping[str, Any]) -> tuple[str, str]:
    """The "Sağlayıcı / Provider" row of a sealed day and its paragraph style.

    Neutral wording (spec 0.6, 21): the report names the server, it never states that a stamp is
    legally valid — that is for the lawyer (section 8). Orchestrator decision 5 keeps three facts
    apart (:func:`arcilogger_api.reports.inputs._day_facts`):

    * ``legal_grade_claimed`` — the manifest says the stamp came from the Kamu SM production
      server (provider ``zamane`` at ``zd.kamusm.gov.tr``);
    * ``test_stamp`` — the stamp is from ARCILOGGER's test (mock) TSA (provider, policy or root);
    * ``legal_grade`` — confirmed by the verified chain: it ends at a root whose SHA-256
      fingerprint the site listed in ``tsa.legal_root_fingerprints`` (empty by default).

    Only a production day is ever called one; "YASAL GEÇERLİ DEĞİL" is reserved for days whose
    manifest does not claim the production server, and "test/geliştirme" for test stamps. Inputs
    stored before these facts existed (no ``legal_grade_claimed``: their ``legal_grade`` was the
    manifest's claim) read as before: the claim is shown positively next to a passing check.
    """
    provider = _dash(tsa.get("provider"))
    verified = verification.get("ok") is True
    if "legal_grade_claimed" in tsa:
        claimed = tsa.get("legal_grade_claimed") is True
        confirmed = tsa.get("legal_grade") is True
    else:
        claimed = tsa.get("legal_grade") is True
        confirmed = claimed
    test = tsa.get("test_stamp") is True or tsa.get("provider") == "mock"
    if claimed and test:
        return (
            f"{provider} — manifestte Kamu SM üretim sunucusu damgası olarak kayıtlı, fakat damga "
            "test (mock) zaman damgası biriminden; yasal sınıf sayılmaz / production server "
            "claimed, but the stamp is a test (mock) one",
            "warn",
        )
    if claimed and confirmed and verified:
        return f"{provider} — Kamu SM üretim sunucusu (zd.kamusm.gov.tr) damgası", "base"
    if claimed and verified:
        root = verification.get("root_sha256")
        compare = f" ({root})" if root else ""
        return (
            f"{provider} — manifestte Kamu SM üretim sunucusu (zd.kamusm.gov.tr) damgası olarak "
            "kayıtlı ve damga doğrulandı; kök sertifikanın SHA-256 parmak izi yapılandırılan "
            "listede (tsa.legal_root_fingerprints) olmadığı için yasal sınıf olarak teyit "
            f"edilmedi. Kökün parmak izini{compare} Kamu SM'nin yayımladığı değerle "
            "karşılaştırın. / Production server claimed and verified; its root fingerprint is "
            "not configured, so it is not confirmed as legal grade — compare it with the value "
            "Kamu SM publishes.",
            "note",
        )
    if claimed:
        return (
            f"{provider} — manifestte Kamu SM üretim sunucusu damgası olarak kayıtlı, fakat damga "
            "doğrulanamadı (bkz. doğrulama) / claimed, not verified",
            "warn",
        )
    if test:
        return f"{provider} — YASAL GEÇERLİ DEĞİL (test/geliştirme sağlayıcısı)", "warn"
    return f"{provider} — YASAL GEÇERLİ DEĞİL (Kamu SM üretim sunucusu değil)", "warn"


# -- page decoration ------------------------------------------------------------------------------


class _NumberedCanvas(PagedCanvas):
    """Draws header and footer after the last page, when the page count is known."""

    def __init__(self, *args: Any, footer: dict[str, str], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._footer = footer

    def counted(self, total: int) -> None:
        self._footer["pages"] = str(total)

    def decorate(self, total: int) -> None:
        width, height = A4
        info = self._footer
        page = f"Sayfa {self._pageNumber} / {total}"
        self.saveState()
        self.setStrokeColor(LINE)
        self.setLineWidth(0.5)
        self.setFont(SANS, 7.5)
        self.setFillColor(MUTED)
        self.drawString(MARGIN, height - 11 * mm, _fit(info["header"], 7.5, width - 2 * MARGIN))
        self.line(MARGIN, height - 12.5 * mm, width - MARGIN, height - 12.5 * mm)
        self.line(MARGIN, 15 * mm, width - MARGIN, 15 * mm)
        room = width - 2 * MARGIN - stringWidth(page, SANS, 7.5) - 6 * mm
        self.drawString(MARGIN, 11 * mm, _fit(info["left"], 7.5, room))
        self.drawRightString(width - MARGIN, 11 * mm, page)
        self.setFont(SANS, 6.3)
        self.drawString(MARGIN, 7.6 * mm, texts.FOOTER_NOTE_TR)
        self.drawString(MARGIN, 5 * mm, texts.FOOTER_NOTE_EN)
        self.restoreState()


def render(inputs: Mapping[str, Any]) -> Rendered:
    """The PDF of ``inputs`` (same inputs → same bytes)."""
    register_fonts()
    lookup = inputs["lookup"]
    generated = inputs["generated"]
    footer = {
        "header": (
            f"{texts.TITLE_TR} · Sorgu no {lookup['id']} · Dosya/evrak no {lookup['file_no']}"
        ),
        "left": " · ".join(
            part
            for part in (
                f"ARCILOGGER {generated['version']}",
                _site(generated),
                f"Oluşturma {_time(generated['at'])}",
            )
            if part
        ),
    }
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=18 * mm,
        bottomMargin=20 * mm,
        title=f"{texts.TITLE_TR} — Sorgu no {lookup['id']}",
        author=f"ARCILOGGER {generated['version']}",
        subject=f"5651 sorgusu {lookup['id']}",
        creator=f"ARCILOGGER {generated['version']}",
        invariant=1,
        lang="tr-TR",
        initialFontName=SANS,
    )
    document.build(
        _Builder(inputs).build(),
        canvasmaker=lambda *args, **kwargs: _NumberedCanvas(*args, footer=footer, **kwargs),
    )
    return Rendered(pdf=buffer.getvalue(), pages=int(footer.get("pages", "0")))
