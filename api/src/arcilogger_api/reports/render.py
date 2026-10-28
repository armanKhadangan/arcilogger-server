"""PDF of an official 5651 report ("Resmi Rapor", spec 9.5) — a pure function of its inputs.

:func:`render` turns the object of :func:`arcilogger_api.reports.inputs.build_inputs` into PDF
bytes with reportlab in **invariant mode** (fixed creation date and document id, deterministic
font subsets): the same inputs give the same bytes, on any machine with the same reportlab and
font files (both are recorded in the inputs). Sections:

1. report details (lookup number, organisation, operator, times, ARCILOGGER version);
2. request details (makam, dosya/evrak no, talep tarihi, gerekçe);
3. query (time as entered and as interpreted, addresses, ports, tolerance);
4. method: the internet exit of a public-IP lookup (its label and exits, one paragraph per
   resolution and mode), the FortiGate DHCP record format note taken from the stored chains,
   the result of the raw line check (:mod:`.consistency`), the double-NAT explanation where it
   applies (a configured or an assumed second NAT, the CGNAT caveat, what would make the answer
   exact; never for a ``transip`` answer, only advice (a) for a ``fallback``) and the engine's
   explanations; an answer that searched no session says so ("Oturum aranmadı") here and in 5;
5. results (exact matches or candidates, each with its exit and ``dstintf``, a warning on a
   match with rows of an unconfigured sender, and the full attribution chain: the lease row with
   the FortiGate lease time and last DHCP evidence and the notes of a low lease beside it, or
   the srcmac step; internal chains; a result whose raw lines contradict it is shown as such,
   never as an exact match and without a confidence);
6. evidence (raw lines with file, line number, byte offset, SHA-256 and the check; a FortiGate
   DHCP row outside the lease timeline marked as no attribution; raw files with their hashes;
   sealed days with manifest/token hashes, time-stamp serial, genTime, TSA and the verification
   result);
7. how to verify the report (its SHA-256 and the optional time stamp);
8. legal note.

Every page carries "Sayfa n / N", the lookup number, the version and the neutral wording rule
(no claim of legal compliance).

An outcome stored before the internet exits (``egress``, ``dstintf`` and the lease's
``source_table`` all None; a report is rendered on its first download) prints none of the exit
rows, paragraphs or FortiGate lease additions, and its double NAT as the assumed variant. An
outcome explanation whose stored text a method paragraph quotes (``egress_observed_not_configured``,
``egress_not_logged_but_seen``, ``egress_public_dynamic``) is not printed again as a bullet, nor
are a fallback's ``double_nat`` notes (which name the ISP router as a fact); every other key
stays a bullet. Chain notes on the record format go to the method section once, those of a low
lease beside its lease row.
"""

from __future__ import annotations

import hashlib
import io
import ipaddress
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
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
from arcilogger_db.egress import PRIVATE_OR_SHARED
from arcilogger_worker.lookup import (
    AttributionChain,
    EvidenceRef,
    Explanation,
    LeaseInfo,
    LookupOutcome,
    LookupRequest,
    NatMatch,
    PersonInfo,
)
from arcilogger_worker.lookup.types import EgressEntry, EgressSearched, LookupEgress
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


# -- internet exits (contract 2.12) ---------------------------------------------------------------

#: Outcome explanations a fallback's method paragraph stands in for: their stored text names the
#: ISP router as a fact, which a fallback never does (deviation d).
_FALLBACK_LEFT_OUT: Final = frozenset({"double_nat", "double_nat_no_candidates"})
#: ``key="value"`` or ``key=value`` of a FortiGate raw line.
_FIELD: Final = r'(?:^|\s){key}=(?:"([^"]*)"|(\S*))'


def exit_label_key(egress: LookupEgress) -> str:
    """The key of :data:`texts.EXIT_LABELS` of ``egress``: its resolution, a ``fallback`` by the
    mode of the exit whose check failed (``direct``, or an ``isp_nat`` exit's NAT address)."""
    if egress.resolution != "fallback":
        return egress.resolution
    failed = egress.egresses[0] if egress.egresses else None
    return (
        "fallback_direct" if failed is not None and failed.mode == "direct" else "fallback_transip"
    )


def _exit_name(entry: EgressEntry) -> str:
    """``hat-1 (Hat 1)``; the name alone for an exit without a label."""
    return f"{entry.name} ({entry.label})" if entry.label else entry.name


def _exit_names(entries: Iterable[EgressEntry]) -> str:
    return ", ".join(_exit_name(entry) for entry in entries) or "-"


def _pair(text: tuple[str, str], **values: str) -> tuple[str, str]:
    """A (Turkish, English) text with the same values in both."""
    return text[0].format(**values), text[1].format(**values)


def exit_row(egress: LookupEgress) -> str:
    """Section 4 "Çıkış / Exit": the resolution's label, then each exit as "name (label), mode"
    (none for ``unconfigured``, whose exit is the implicit one, and ``no_egress``, whose exits
    the method paragraph lists)."""
    label_tr, label_en = texts.EXIT_LABELS[exit_label_key(egress)]
    row = f"{label_tr} / {label_en}"
    if egress.resolution in ("unconfigured", "no_egress") or not egress.egresses:
        return row
    exits = "; ".join(
        f"{_exit_name(entry)}, {' / '.join(texts.EXIT_MODES[entry.mode])}"
        for entry in egress.egresses
    )
    return f"{row} — {exits}"


def _private_or_shared(address: str) -> bool:
    try:
        found = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(found in network for network in PRIVATE_OR_SHARED)


def _searched(searched: EgressSearched) -> tuple[str, str]:
    """``{searched}``: the addresses searched, and, when the search covered the unclaimed ones,
    those in words with the private or shared ones the logs showed near the moment (the
    predicate never covers a public address)."""
    addresses = ", ".join(searched.transips)
    if not searched.unclaimed:
        return addresses or "-", addresses or "-"
    seen = ", ".join(a for a in searched.unclaimed_seen if _private_or_shared(a)) or "-"
    unclaimed_tr, unclaimed_en = _pair(texts.UNCLAIMED_SEARCHED, seen=seen)
    if not addresses:
        return unclaimed_tr, unclaimed_en
    return f"{addresses} ve {unclaimed_tr}", f"{addresses} and {unclaimed_en}"


def _interfaces(searched: EgressSearched, clause: tuple[str, str]) -> tuple[str, str]:
    """The interface clause of a paragraph, only when the search kept its interface condition."""
    if not (searched.interface_filter and searched.interfaces):
        return "", ""
    return _pair(clause, interfaces=", ".join(searched.interfaces))


def _interfaces_dropped(outcome: LookupOutcome, entry: EgressEntry) -> bool:
    """The exit's part of the search had no interface condition: it declares no interfaces, or
    the engine dropped them (``egress_interface_not_seen`` names it; not the ``_kept`` note)."""
    if not entry.interfaces:
        return True
    return any(
        item.key == "egress_interface_not_seen" and item.params.get("egress") == entry.name
        for item in outcome.explanations
    )


def _exit_key(
    exits: Sequence[EgressEntry], signature: tuple[str | None, str | None], *, unstored: bool
) -> str | tuple[str, str | None]:
    """The exit a session line left by, decided as the engine's ``_exit_of`` does but over the
    stored exits: the one exit claiming its ``transip`` as a NAT address, several by its
    ``dstintf``, else the exit whose public address it is; ``("?", transip)`` when none can be
    told. ``unstored``: the ``session_changed_exit`` note names an exit the stored list lacks
    (a ``transip`` answer stores only its own), which may share the address by interface, so the
    one claiming exit counts only on a ``dstintf`` it declares (or while it declares none)."""
    transip, dstintf = signature
    unknown = ("?", transip)
    try:
        address = ipaddress.ip_address(transip or "")
    except ValueError:
        return unknown

    def holds(networks: Iterable[str]) -> bool:
        return any(address in ipaddress.ip_network(n, strict=False) for n in networks)

    nat = [entry for entry in exits if holds(entry.transips)]
    if len(nat) == 1:
        (claimed,) = nat
        if not (unstored and claimed.interfaces and dstintf not in claimed.interfaces):
            return claimed.name
    elif nat:
        found = next((entry.name for entry in nat if dstintf in entry.interfaces), None)
        if found is not None:
            return found
    return next((entry.name for entry in exits if holds(entry.public)), unknown)


def _port_hinted(outcome: LookupOutcome, name: str) -> bool:
    """The stored ``port_hint`` explanation names exit ``name`` (the hint ranked candidates)."""
    for item in outcome.explanations:
        if item.key == "port_hint":
            return name in [n.strip() for n in str(item.params.get("egresses") or "").split(",")]
    return False


def _isp_nat_paragraph(
    outcome: LookupOutcome,
    egress: LookupEgress,
    entry: EgressEntry,
    stored: Mapping[str, Explanation],
) -> tuple[str, str]:
    searched = egress.searched
    transips = ", ".join(entry.transips)
    named = (transips, transips) if transips else texts.CATCH_ALL_TRANSIPS
    text_tr = texts.METHOD_ISP_NAT_TR.format(exit=_exit_name(entry), transips=named[0])
    text_en = texts.METHOD_ISP_NAT_EN.format(exit=_exit_name(entry), transips=named[1])
    if searched.unclaimed:  # a catch-all, or widened by O5: the search went beyond its addresses
        clause_tr, clause_en = _interfaces(searched, texts.METHOD_SEARCHED_INTERFACES)
        found_tr, found_en = _searched(searched)
        text_tr += " " + texts.METHOD_SEARCHED[0].format(searched=found_tr, interfaces=clause_tr)
        text_en += " " + texts.METHOD_SEARCHED[1].format(searched=found_en, interfaces=clause_en)
    else:
        clause_tr, clause_en = _interfaces(searched, texts.METHOD_INTERFACES)
        text_tr += " " + texts.METHOD_ISP_NAT_LIMITED[0].format(interfaces=clause_tr)
        text_en += " " + texts.METHOD_ISP_NAT_LIMITED[1].format(interfaces=clause_en)
    port = texts.PORT_USED if _port_hinted(outcome, entry.name) else texts.PORT_NOT_USED
    text_tr += " " + texts.METHOD_PORT[0].format(port=port[0])
    text_en += " " + texts.METHOD_PORT[1].format(port=port[1])
    if egress.resolution == "transip":
        text_tr += " " + texts.METHOD_TRANSIP_TR
        text_en += " " + texts.METHOD_TRANSIP_EN
    elif entry.public_dynamic and "egress_public_dynamic" in stored:
        dynamic = stored["egress_public_dynamic"]
        text_tr += " " + dynamic.text_tr
        text_en += " " + dynamic.text_en
    return text_tr, text_en


def _fallback_paragraphs(egress: LookupEgress, address: str) -> list[tuple[str, str]]:
    failed, others = egress.egresses[0], egress.egresses[1:]
    seen = ", ".join(egress.searched.outside_seen) or "-"
    found_tr, found_en = _searched(egress.searched)
    values = {"exit": _exit_name(failed), "address": address, "seen": seen}
    if failed.mode == "direct":
        on_tr, on_en = texts.ON_LINE if failed.interfaces else ("", "")
        text_tr = texts.METHOD_FALLBACK_DIRECT_TR.format(**values, searched=found_tr, on_line=on_tr)
        text_en = texts.METHOD_FALLBACK_DIRECT_EN.format(**values, searched=found_en, on_line=on_en)
    else:
        text_tr = texts.METHOD_FALLBACK_TRANSIP_TR.format(**values, searched=found_tr)
        text_en = texts.METHOD_FALLBACK_TRANSIP_EN.format(**values, searched=found_en)
    paragraphs = [(text_tr, text_en)]
    if others:
        # F shares an interface with E when E declares any; otherwise both are as converted
        other = _exit_names(others)
        if failed.interfaces:
            shared = [i for i in failed.interfaces if any(i in o.interfaces for o in others)]
            text = (texts.METHOD_FALLBACK_SHARED_TR, texts.METHOD_FALLBACK_SHARED_EN)
            paragraphs.append(
                _pair(text, other=other, exit=_exit_name(failed), interfaces=", ".join(shared))
            )
        else:
            text = (texts.METHOD_FALLBACK_OTHER_TR, texts.METHOD_FALLBACK_OTHER_EN)
            paragraphs.append(_pair(text, other=other))
    return paragraphs


def method_paragraphs(outcome: LookupOutcome) -> tuple[list[tuple[str, str]], set[str]]:
    """The method paragraph of the exit's (resolution, mode) (2.12) and the outcome explanation
    keys whose stored text it quotes (those are not printed again as bullets); nothing for an
    outcome stored before the exits existed (``egress`` None) or without a public IP."""
    egress = outcome.egress
    address = outcome.request.public_ip
    if egress is None or address is None:
        return [], set()
    stored = {item.key: item for item in outcome.explanations}
    resolution = egress.resolution
    entry = egress.egresses[0] if egress.egresses else None
    values = {"address": address, "exit": "" if entry is None else _exit_name(entry)}
    paragraphs: list[tuple[str, str]] = []
    quoted: set[str] = set()
    if resolution in ("public", "transip") and entry is not None:
        if entry.mode == "direct":
            text = (texts.METHOD_PUBLIC_DIRECT_TR, texts.METHOD_PUBLIC_DIRECT_EN)
            paragraphs.append(_pair(text, **values))
        elif entry.mode == "isp_nat":
            paragraphs.append(_isp_nat_paragraph(outcome, egress, entry, stored))
            if entry.public_dynamic and "egress_public_dynamic" in stored:
                quoted.add("egress_public_dynamic")
        elif entry.mode == "not_logged":
            text = (texts.METHOD_NOT_LOGGED_TR, texts.METHOD_NOT_LOGGED_EN)
            paragraphs.append(_pair(text, **values))
        else:
            text = (texts.METHOD_INTERNAL_TR, texts.METHOD_INTERNAL_EN)
            paragraphs.append(_pair(text, **values))
    elif resolution == "fallback" and entry is not None:
        paragraphs += _fallback_paragraphs(egress, address)
        quoted |= _FALLBACK_LEFT_OUT
    elif resolution == "observed":
        key = "egress_not_logged_but_seen" if egress.egresses else "egress_observed_not_configured"
        if key in stored:
            paragraphs.append((stored[key].text_tr, stored[key].text_en))
            quoted.add(key)
    elif resolution == "public_unknown":
        text = (texts.METHOD_PUBLIC_UNKNOWN_TR, texts.METHOD_PUBLIC_UNKNOWN_EN)
        paragraph_tr, paragraph_en = _pair(
            text, address=address, exits=_exit_names(egress.egresses)
        )
        if egress.searched.unclaimed:
            # ``interface_filter`` holds when any exit's part kept one: the clause only when
            # none of the exits searched lost it (a catch-all without interfaces searched all)
            clause_tr, clause_en = (
                ("", "")
                if any(_interfaces_dropped(outcome, e) for e in egress.egresses)
                else _interfaces(egress.searched, texts.METHOD_SEARCHED_INTERFACES)
            )
            found_tr, found_en = _searched(egress.searched)
            paragraph_tr += " " + texts.METHOD_SEARCHED[0].format(
                searched=found_tr, interfaces=clause_tr
            )
            paragraph_en += " " + texts.METHOD_SEARCHED[1].format(
                searched=found_en, interfaces=clause_en
            )
        paragraphs.append((paragraph_tr, paragraph_en))
    elif resolution == "unconfigured":
        paragraphs.append((texts.METHOD_UNCONFIGURED_TR, texts.METHOD_UNCONFIGURED_EN))
    elif resolution == "no_egress":
        text = (texts.METHOD_NO_EGRESS_TR, texts.METHOD_NO_EGRESS_EN)
        paragraphs.append(_pair(text, address=address))
        configured_tr = "; ".join(
            f"{_exit_name(e)}: {texts.EXIT_MODES[e.mode][0]}, {', '.join(e.public) or '-'}"
            for e in egress.egresses
        )
        configured_en = "; ".join(
            f"{_exit_name(e)}: {texts.EXIT_MODES[e.mode][1]}, {', '.join(e.public) or '-'}"
            for e in egress.egresses
        )
        paragraphs.append(
            (
                texts.METHOD_CONFIGURED[0].format(configured=configured_tr or "-"),
                texts.METHOD_CONFIGURED[1].format(configured=configured_en or "-"),
            )
        )
    return paragraphs, quoted


def not_searched(outcome: LookupOutcome) -> tuple[str, str] | None:
    """The "Oturum aranmadı" text of sections 4 and 5 for an answer that searched no session
    (``no_egress``, a ``not_logged`` exit, an ``internal`` exit's NAT address); ``None`` for
    every other answer, which keeps the generic texts."""
    egress = outcome.egress
    address = outcome.request.public_ip
    if outcome.mode != "none" or egress is None or address is None:
        return None
    if egress.resolution == "no_egress":
        return _pair(texts.NOT_SEARCHED_NO_EGRESS, address=address)
    entry = egress.egresses[0] if egress.egresses else None
    if entry is None:
        return None
    if egress.resolution == "public" and entry.mode == "not_logged":
        return _pair(texts.NOT_SEARCHED_NOT_LOGGED, address=address, exit=_exit_name(entry))
    if egress.resolution == "transip" and entry.mode == "internal":
        return _pair(texts.NOT_SEARCHED_INTERNAL, address=address, exit=_exit_name(entry))
    return None


def _chains(outcome: LookupOutcome) -> Iterator[AttributionChain]:
    """Every chain of the outcome, alternatives included, in report order."""

    def walk(chain: AttributionChain) -> Iterator[AttributionChain]:
        yield chain
        for other in chain.alternatives:
            yield from walk(other)

    for match in outcome.matches:
        if match.chain is not None:
            yield from walk(match.chain)
    if outcome.attribution is not None:
        yield from walk(outcome.attribution)


def format_notes(outcome: LookupOutcome) -> list[tuple[str, str]]:
    """The method note on the FortiGate DHCP record format, from the stored chains only (never
    the setting at render time): the stored "verified" text (who marked it, and when), else the
    fixed "not verified" sentence; nothing without FortiGate leases."""
    verified: list[tuple[str, str]] = []
    unverified = False
    for chain in _chains(outcome):
        for item in chain.explanations:
            if item.key == "fortigate_format_verified":
                if (item.text_tr, item.text_en) not in verified:
                    verified.append((item.text_tr, item.text_en))
            elif item.key == "fortigate_format_unverified":
                unverified = True
    if verified:
        return verified
    return [texts.FORMAT_UNVERIFIED] if unverified else []


def _raw_field(line: str, key: str) -> str | None:
    found = re.search(_FIELD.format(key=re.escape(key)), line)
    if found is None:
        return None
    return found.group(1) if found.group(1) is not None else found.group(2)


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
        #: Cited FortiGate DHCP rows that are not in the lease timeline (D8): marked in section 6.
        self.unconverted = {evidence_key(ref) for ref in self.outcome.unconverted_dhcp}
        #: The raw text of each cited line that was read (the second exit of a session).
        self.lines = {
            item["key"]: str(item["line"]["text"])
            for item in inputs.get("evidence") or []
            if (item.get("line") or {}).get("status") in ("ok", "offset_mismatch")
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

    def both(self, text_tr: str, text_en: str, style: str = "base") -> list[Any]:
        """A table cell with the Turkish text and the English one beside it, smaller."""
        return [self.p(_x(text_tr), style), self.p(_x(text_en), "small")]

    def explanations(self, items: Iterable[Explanation], *, left_out: Iterable[str] = ()) -> None:
        skipped = set(left_out)
        for item in items:
            if item.key in skipped:
                continue
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
        rows = [("Sorgu türü / Mode", _x(f"{mode_tr} / {mode_en}"))]
        if outcome.egress is not None:
            rows.append(("Çıkış / Exit", _x(exit_row(outcome.egress))))
        skipped = not_searched(outcome)
        detail = texts.MODE_DETAIL_TR[outcome.mode] if skipped is None else " / ".join(skipped)
        rows.append(("Açıklama", _x(detail)))
        self.add(self.kv(rows), Spacer(1, 5))
        self.bilingual(texts.METHOD_TR, texts.METHOD_EN)
        for text_tr, text_en in format_notes(outcome):
            self.bilingual(text_tr, text_en)
        paragraphs, quoted = method_paragraphs(outcome)
        if paragraphs:
            self.add(self.p("İnternet çıkışı / Internet exit", "h3"))
            for text_tr, text_en in paragraphs:
                self.bilingual(text_tr, text_en)
        self.consistency()
        self.double_nat()
        if outcome.mode == "candidates":
            self.bilingual(texts.SCORE_TR, texts.SCORE_EN)
        if any(item.key not in quoted for item in outcome.explanations):
            self.add(self.p("Sorgu motorunun açıklamaları / Explanations", "h3"))
            self.explanations(outcome.explanations, left_out=quoted)
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

    def double_nat(self) -> None:
        """Why a candidates/none answer cannot be exact and what would make it exact (2.12):
        never for a ``transip`` answer (the request names the FortiGate's own NAT address); a
        configured ``isp_nat`` exit or an assumed second NAT, the CGNAT caveat, the result and
        the advice; for a ``fallback`` only the result and advice (a), the method paragraph
        standing in for the rest. Without a public IP in the request (a destination search) the
        variant and the CGNAT sentence, which speak of "this address", are left out."""
        outcome = self.outcome
        if not outcome.double_nat or outcome.mode not in ("candidates", "none"):
            return
        egress = outcome.egress
        resolution = None if egress is None else egress.resolution
        if resolution == "transip":
            return
        # the "candidate list" sentence only when candidates follow (spec 12.4)
        parts_tr = [texts.DOUBLE_NAT_RESULT_TR[outcome.mode]]
        parts_en = [texts.DOUBLE_NAT_RESULT_EN[outcome.mode]]
        if egress is not None and resolution == "fallback":
            failed = egress.egresses[0] if egress.egresses else None
            direct = failed is not None and failed.mode == "direct"
            bridge_tr, bridge_en = texts.DOUBLE_NAT_BRIDGE if direct else ("", "")
            parts_tr.append(texts.DOUBLE_NAT_ADVICE_A_TR.format(bridge=bridge_tr))
            parts_en.append(texts.DOUBLE_NAT_ADVICE_A_EN.format(bridge=bridge_en))
            self.add(self.p("Kesin eşleşme için / For an exact answer", "h3"))
            self.bilingual(" ".join(parts_tr), " ".join(parts_en))
            return
        if outcome.request.public_ip is not None:
            entry = egress.egresses[0] if egress is not None and egress.egresses else None
            if resolution == "public" and entry is not None and entry.mode == "isp_nat":
                lead_tr = texts.DOUBLE_NAT_CONFIGURED_TR.format(exit=_exit_name(entry))
                lead_en = texts.DOUBLE_NAT_CONFIGURED_EN.format(exit=_exit_name(entry))
            else:  # unconfigured, public_unknown, or an outcome stored before the exits
                lead_tr, lead_en = texts.DOUBLE_NAT_ASSUMED_TR, texts.DOUBLE_NAT_ASSUMED_EN
            parts_tr[:0] = [lead_tr, texts.DOUBLE_NAT_CGNAT_TR]
            parts_en[:0] = [lead_en, texts.DOUBLE_NAT_CGNAT_EN]
        parts_tr.append(texts.DOUBLE_NAT_ADVICE_TR)
        parts_en.append(texts.DOUBLE_NAT_ADVICE_EN)
        self.add(self.p("Çift NAT / Double NAT", "h3"))
        self.bilingual(" ".join(parts_tr), " ".join(parts_en))

    # results

    def results(self) -> None:
        outcome = self.outcome
        self.heading("5. Sonuçlar", "Results")
        count = len(outcome.matches)
        if outcome.mode == "none":
            skipped = not_searched(outcome)
            if skipped is not None:  # no session was searched: never a log-based "nobody"
                self.add(self.p(_x(skipped[0]), "warn"), self.p(_x(skipped[1]), "small"))
                return
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

    def match_exit(self, match: NatMatch) -> str:
        """``hat-2 (Hat 2) · dstintf wan2`` ("-" for an unclaimed row), and for the session
        whose rows left by more than one exit (``session_changed_exit``, each row's exit as
        :func:`_exit_key` tells it) the other exit."""
        exits = self.outcome.egress.egresses if self.outcome.egress is not None else ()
        labels = {entry.name: _exit_name(entry) for entry in exits}
        name = "-" if match.egress is None else labels.get(match.egress, match.egress)
        text = f"{name} · dstintf {match.traffic.dstintf or '-'}"
        signatures = self.signatures(match)
        changed = next(
            (e for e in self.outcome.explanations if e.key == "session_changed_exit"), None
        )
        if changed is None or len(signatures) < 2:
            return text
        # The outcome keeps one exit per match (its row nearest the moment); the engine's note
        # names the exits of the sessions that changed exit. A match varies when its cited rows
        # map to more than one exit (another dstintf of the same exit is no change); with one
        # such session, and only this match varying, those are this match's exits; otherwise
        # the rows' own NAT address and dstintf are shown (deviation d).
        named = [n.strip() for n in str(changed.params.get("egresses") or "").split(",")]
        unstored = any(n and n not in labels for n in named)

        def varies(other: NatMatch) -> bool:
            keys = {_exit_key(exits, s, unstored=unstored) for s in self.signatures(other)}
            return len(keys) > 1

        if not varies(match):
            return text
        own = match.egress or "-"
        others = [n for n in named if n and n != own]
        varying = sum(1 for other in self.outcome.matches if varies(other))
        if changed.params.get("count") == 1 and varying == 1 and own in named and others:
            shown = ", ".join(labels.get(other, other) for other in others)
            return text + " · " + " / ".join(_pair(texts.CHANGED_EXIT, exits=shown))
        rows = "; ".join(f"NAT {_dash(t)} · dstintf {d or '-'}" for t, d in signatures)
        return text + " · " + " / ".join(_pair(texts.CHANGED_ROWS, rows=rows))

    def signatures(self, match: NatMatch) -> list[tuple[str | None, str | None]]:
        """The distinct (``transip``, ``dstintf``) of the match's cited session lines, read
        from the raw lines (the exit a row left by, as the line itself says)."""
        found: list[tuple[str | None, str | None]] = []
        for ref in match.traffic.evidence:
            line = self.lines.get(evidence_key(ref)) if ref.table == "fg_traffic" else None
            if line is None:
                continue
            signature = (_raw_field(line, "transip"), _raw_field(line, "dstintf"))
            if signature not in found:
                found.append(signature)
        return found

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
        ]
        if (
            self.outcome.egress is not None
            or match.egress is not None
            or traffic.dstintf is not None
        ):  # none of them in an outcome stored before the exits existed
            rows.append(("Çıkış / Exit", _x(self.match_exit(match))))
        rows += [
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
        if match.untrusted_sources:
            sources = ", ".join(match.untrusted_sources)
            warning_tr, warning_en = _pair(texts.UNTRUSTED_MATCH, sources=sources)
            rows.append(("UYARI / Warning", self.p(_x(f"{warning_tr} / {warning_en}"), "warn")))
        self.add(
            CondPageBreak(40 * mm),
            KeepTogether([self.kv(rows, header=_x(title))]),
            Spacer(1, 2),
        )
        if match.chain is None:
            self.add(
                self.p(_x(texts.NO_CHAIN[0]), "note"),
                self.p(_x(texts.NO_CHAIN[1]), "small"),
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
        srcmac = chain.mac_source == "srcmac" and chain.device is not None
        if chain.lease is not None:
            mac_evidence = chain.lease.evidence
        else:  # a srcmac chain rests on its session record (checked by srcip and srcmac)
            mac_evidence = chain.device.evidence if srcmac and chain.device is not None else ()
        contradicted = contradicted or bool(self.contradicting(mac_evidence))
        notes = {item.key: item for item in chain.explanations}
        vlan = f" · VLAN {chain.vlan}" if chain.vlan is not None else ""
        vlan += f" ({chain.vlan_name})" if chain.vlan_name else ""
        rows: list[tuple[str, Any]] = [
            ("İç IP / Internal IP", _x(f"{chain.internal_ip}{vlan} · an: {_time(chain.at)}")),
        ]
        lease = chain.lease
        left_out: set[str] = set(texts.FORMAT_KEYS)  # printed once in the method section
        if lease is not None:
            end = _time(lease.end) if lease.end is not None else "açık"
            row = (
                f"MAC {lease.mac} · ana bilgisayar adı {_dash(lease.hostname)} · "
                f"{_time(lease.start)} - {end} · bitiş: "
                f"{texts.END_REASONS.get(lease.end_reason, lease.end_reason)} · güven: "
                f"{texts.CONFIDENCE.get(lease.confidence, lease.confidence)} · kayıt no "
                f"{_dash(lease.lease_id)} · kanıt: {self.refs(lease.evidence)}"
            )
            if lease.source_table == "fg_dhcp":  # never for leases stored before the upgrade
                rows.append(("IP-MAC kaydı / Lease", self.fortigate_lease(row, lease)))
            else:
                rows.append(("IP-MAC kaydı / Lease", _x(row)))
            for key in texts.LEASE_NOTE_KEYS:  # the reason of a low lease, beside it
                if key in notes:
                    rows.append(
                        (
                            "Kira notu / Lease note",
                            self.both(notes[key].text_tr, notes[key].text_en),
                        )
                    )
                    left_out.add(key)
        elif srcmac and chain.device is not None:
            found = notes.get("srcmac_only")
            interface = str(found.params.get("interface") or "-") if found is not None else "-"
            step_tr, step_en = _pair(texts.SRCMAC_STEP, interface=interface)
            rows.append(
                (
                    "MAC adresi / MAC address",
                    self.both(f"MAC {chain.device.mac} · {step_tr}", step_en),
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
        if not alternative:
            header = "Atıf zinciri / Chain"
        elif chain.mac_source == "srcmac":
            header = texts.ALTERNATIVE_SRCMAC
        else:
            header = "Olası diğer zincir (IP çakışması)"
        self.add(self.kv(rows, header=_x(header)))
        if any(item.key not in left_out for item in chain.explanations):
            self.add(Spacer(1, 2))
            self.explanations(chain.explanations, left_out=left_out)
        for other in chain.alternatives:
            self.add(Spacer(1, 3))
            self.chain(other, alternative=True)

    def fortigate_lease(self, row: str, lease: LeaseInfo) -> list[Any]:
        """The lease row of an ``fg_dhcp`` lease: its source, the lease time the FortiGate logged
        and the lease's own last DHCP record at or before the chain's moment, as stored."""
        table_tr, table_en = texts.TABLES["fg_dhcp"], texts.TABLES_EN["fg_dhcp"]
        if lease.lease_seconds is None:
            seconds_tr, seconds_en = texts.CONFIGURED_LEASE
        else:
            seconds_tr, seconds_en = f"{lease.lease_seconds} sn", f"{lease.lease_seconds} s"
        last = _time(lease.last_dhcp_evidence)
        added_tr = texts.FG_LEASE[0].format(lease=seconds_tr, last=last)
        added_en = texts.FG_LEASE[1].format(lease=seconds_en, last=last)
        return self.both(
            f"{row} · kaynak: {table_tr} · {added_tr}", f"source: {table_en} · {added_en}"
        )

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
        if item["table"] in texts.TABLES_EN:
            table += f" / {texts.TABLES_EN[item['table']]}"
        rows: list[tuple[str, Any]] = [
            ("Kayıt / Record", _x(f"{table} ({item['table']} #{_dash(item.get('row_id'))})")),
            ("Zaman / Time", _x(_time(item.get("time")))),
            ("Özet / Summary", _x(_dash(item.get("summary")))),
        ]
        if item["key"] in self.unconverted:  # never to be read as an attribution (D8)
            mark_tr, mark_en = texts.UNCONVERTED_MARK
            rows.append(("Not / Note", self.p(_x(f"{mark_tr} / {mark_en}"), "warn")))
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
