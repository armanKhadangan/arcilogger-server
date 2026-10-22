"""Build an evidence package ("Kanıt Paketi", build specification section 9.6).

A package is one ZIP file that lets someone outside the company check sealed days on their own:

* for every requested day, the sealed files from the vault (all sources or a chosen subset;
  a source that is included is always included as the whole original file), the manifest and
  its time-stamp token (``.zd``); the day's ARCILOGGER audit log only on request
  (``include_audit_log``; it holds other requests and staff data, KVKK) — otherwise it is listed
  in ``paket.json`` as excluded with its sealed SHA-256;
* deletion records and the chain anchor when a day links to a deleted day or lists a deletion,
  renewal stamps that cover the included days;
* the trusted root certificates the package's tokens actually chain to, the TSA certificates
  taken from the tokens and the CRLs of a CRL folder when one exists;
* optionally an *extract*: the raw lines a lookup is about, byte for byte, with their numbers;
* the standalone verifier (``verify.py`` + ``verify.ps1`` + ``verify.sh``), the Turkish guide
  ``DOGRULAMA.txt`` (ASCII file name, Turkish content), the English ``VERIFY-EN.txt``, an
  optional PDF report (``rapor/<name>.pdf``, the stored bytes — never rendered here) with its
  optional RFC 3161 token (``rapor/<name>.pdf.zd``), or a note why that token is not there;
* ``paket.json``: the canonical JSON index (RFC 8785) with size and SHA-256 of every file.

The vault is only read. Before anything is written, each day's token is verified against the
pinned roots and the chain links between the included days are checked; while the sealed files
are copied into the ZIP their SHA-256 is compared with the manifest, and the build stops if a
file differs. The finished ZIP (``<name>.part``) is then checked by the standalone verifier's own
code, read back from the ZIP, and renamed only if that finds no failure: ARCILOGGER never hands
out a package its own verifier rejects. Built twice from the same inputs, the package is
byte-identical except for the creation time (and, when encrypted, the random AES salts).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding

from arcilogger_core import PRODUCT_NAME, __version__
from arcilogger_core.canonical import canonical_json
from arcilogger_core.evidence.archive import ArchivePackage, ArchiveWriter
from arcilogger_core.evidence.extract import format_extract, read_lines
from arcilogger_core.evidence.instructions import (
    LEGAL_NOTE_TR,
    DayFacts,
    GuideFacts,
    RootFacts,
    render_english,
    render_turkish,
)
from arcilogger_core.evidence.model import SOURCE_NAME, EvidenceError, PackageInfo, RawRef
from arcilogger_core.evidence.standalone import VERIFIER_FILES, VERIFY_PY
from arcilogger_core.evidence.standalone.verify import (
    GUIDE_EN,
    GUIDE_TR,
    INDEX_NAME,
    INDEX_SCHEMA,
    VERIFIER_VERSION,
    PackageError,
    verify_package,
)
from arcilogger_core.fsutil import fsync_dir, fsync_file
from arcilogger_core.layout import Layout
from arcilogger_core.seal import sha256_file, unsealed_days
from arcilogger_core.timeutil import isoformat_site, to_site, utc_now
from arcilogger_core.tsa.asn1 import READABLE_DIGESTS, parse_container
from arcilogger_core.tsa.base import TokenInfo, TsaError
from arcilogger_core.tsa.verify import (
    confirmed_legal_grade,
    inspect_token,
    is_test_stamp,
    verify_token,
)

log = logging.getLogger("arcilogger.evidence")

VAULT = "vault/"
ROOTS_FOLDER = "sertifikalar/kokler/"
ROOTS_BUNDLE = "sertifikalar/kokler.pem"
TSA_FOLDER = "sertifikalar/tsa/"
CRL_FOLDER = "sil/"
REPORT_FOLDER = "rapor/"
EXTRACT_FOLDER = "extract/"
PACKAGE_PREFIX = "ARCILOGGER-kanit-"
CRL_SUFFIXES = (".crl", ".pem", ".der")
SAFE_FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")
REPORT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,100}\.pdf$")
#: No 0/O, 1/l/I: the password is read from a screen and typed into 7-Zip.
_SYMBOLS = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
MIN_PASSWORD_LENGTH = 12
#: Why the day's ARCILOGGER audit log is not in a package (paket.json ``excluded``).
AUDIT_EXCLUDED_REASON = (
    "ARCILOGGER denetim kaydı varsayılan olarak pakete alınmaz: başka sorguları ve personel "
    "bilgilerini içerir (KVKK veri en aza indirme). / ARCILOGGER's audit log is left out by "
    "default: it holds other requests and staff data (KVKK data minimisation)."
)
NO_CRL_NOTE = (
    "ARCILOGGER henüz SİL indirmiyor; Kamu SM SİL adresleri kurulumda netleştirilecek "
    "(docs/kamusm.md, K10)."
)


def verifier_sha256() -> str:
    """SHA-256 of the ``verify.py`` that goes into every package of this ARCILOGGER version.

    The copy inside a package cannot vouch for itself: whoever could change the package could
    change the verifier too. This value is shown with the package (CLI, API, audit record) so a
    recipient can confirm it through a channel that does not depend on the package.
    """
    return sha256_file(VERIFY_PY)


def generate_password(groups: int = 6, size: int = 4) -> str:
    """A random password such as ``Hk7m-PqR2-…``: 24 symbols of 57, about 140 bits."""
    return "-".join("".join(secrets.choice(_SYMBOLS) for _ in range(size)) for _ in range(groups))


@dataclass
class _Entry:
    """One file of the package: bytes in memory, or a vault file streamed and checked."""

    name: str
    role: str
    data: bytes | None = None
    source: Path | None = None
    expected_sha256: str = ""
    executable: bool = False
    compress: bool = True


@dataclass
class _Day:
    day: date
    manifest: dict[str, Any]
    manifest_bytes: bytes
    token_bytes: bytes
    info: TokenInfo
    certificates: list[x509.Certificate]
    included: list[dict[str, Any]]
    excluded: list[dict[str, Any]]
    #: SHA-256 fingerprint of the trusted root the day's token chains to.
    root_sha256: str = ""
    #: Claimed by the manifest, confirmed by the verified chain (tsa.legal_root_fingerprints).
    legal_grade: bool = False
    #: The day's ARCILOGGER audit log goes into the package (only when asked for).
    audit_included: bool = False

    @property
    def legal_grade_claimed(self) -> bool:
        tsa = self.manifest.get("tsa")
        return isinstance(tsa, dict) and tsa.get("legal_grade") is True

    @property
    def require_critical_eku(self) -> bool:
        tsa = self.manifest.get("tsa")
        return not (isinstance(tsa, dict) and tsa.get("require_critical_eku") is False)

    @property
    def audit(self) -> dict[str, Any] | None:
        entry = self.manifest.get("audit_log")
        return entry if isinstance(entry, dict) else None

    @property
    def folder(self) -> str:
        return f"{VAULT}{self.day.year:04d}/{self.day.month:02d}/{self.day.day:02d}"

    @property
    def manifest_name(self) -> str:
        return f"manifest-{self.day.isoformat()}.json"


def _vault_path(layout: Layout, relative: str) -> Path:
    """A path the manifest names, relative to the vault; it may not leave the vault."""
    parts = PurePosixPath(relative).parts
    if not parts or relative.startswith("/") or any(p in ("..", ".") or ":" in p for p in parts):
        raise EvidenceError(f"unsafe path in a sealed record: {relative!r}")
    return layout.vault.joinpath(*parts)


def _token_certificates(token: bytes) -> list[x509.Certificate]:
    content, _ = parse_container(token)
    return [
        x509.load_der_x509_certificate(choice.chosen.dump())
        for choice in content["content"]["certificates"] or []
        if choice.name == "certificate"
    ]


def _collect_day(
    layout: Layout,
    day: date,
    roots: Sequence[x509.Certificate],
    sources: Sequence[str] | None,
    legal_roots: Collection[str] = (),
) -> _Day:
    manifest_path, token_path = layout.manifest(day), layout.token(day)
    if not manifest_path.is_file() or not token_path.is_file():
        raise EvidenceError(f"{day}: not sealed (no manifest or time stamp in the vault)")
    manifest_bytes = manifest_path.read_bytes()
    token_bytes = token_path.read_bytes()
    try:
        manifest = json.loads(manifest_bytes)
    except ValueError as exc:
        raise EvidenceError(f"{day}: the manifest is not JSON: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("day") != day.isoformat():
        raise EvidenceError(f"{day}: the manifest is for another day")
    found = manifest.get("tsa")
    tsa: dict[str, Any] = found if isinstance(found, dict) else {}
    result = verify_token(
        token_bytes,
        roots=roots,
        file_path=manifest_path,
        require_critical_eku=tsa.get("require_critical_eku") is not False,
    )
    if not result.ok or result.info is None:
        raise EvidenceError(f"{day}: the time stamp does not verify: {'; '.join(result.errors)}")
    claimed = tsa.get("legal_grade") is True
    if claimed and is_test_stamp(result.info.policy, result.details.get("root") or ""):
        raise EvidenceError(
            f"{day}: the manifest claims a Kamu SM production stamp, but the token is from the "
            "test (mock) TSA — package not built"
        )
    try:
        certificates = _token_certificates(token_bytes)
    except (TsaError, ValueError) as exc:  # pragma: no cover - the token verified just above
        raise EvidenceError(f"{day}: cannot read the certificates of the token") from exc
    files = [entry for entry in manifest.get("files") or [] if isinstance(entry, dict)]
    included = [entry for entry in files if sources is None or entry.get("source") in sources]
    excluded = [entry for entry in files if entry not in included]
    return _Day(
        day=day,
        manifest=manifest,
        manifest_bytes=manifest_bytes,
        token_bytes=token_bytes,
        info=result.info,
        certificates=certificates,
        included=included,
        excluded=excluded,
        root_sha256=result.details.get("root_sha256") or "",
        legal_grade=confirmed_legal_grade(claimed, result, legal_roots),
    )


def _collect_days(
    layout: Layout,
    days: Sequence[date],
    roots: Sequence[x509.Certificate],
    sources: Sequence[str] | None,
    legal_roots: Collection[str] = (),
) -> tuple[list[_Day], list[date]]:
    """The sealed days of the request, and the requested days without a seal of their own that
    an included manifest lists as a run of empty days (anything else unsealed is refused)."""
    sealed = [day for day in days if layout.manifest(day).is_file() or layout.token(day).is_file()]
    collected = [_collect_day(layout, day, roots, sources, legal_roots) for day in sealed]
    covered: set[date] = set()
    for collected_day in collected:
        try:
            run = unsealed_days(collected_day.manifest)
        except ValueError as exc:
            raise EvidenceError(
                f"{collected_day.day}: the manifest's list of empty days: {exc}"
            ) from exc
        if run is not None:
            covered |= {run[0] + timedelta(days=n) for n in range((run[1] - run[0]).days + 1)}
    empty = [day for day in days if day not in sealed]
    for missing in empty:
        if missing not in covered:
            raise EvidenceError(f"{missing}: not sealed (no manifest or time stamp in the vault)")
    if not collected:
        raise EvidenceError("none of the requested days has a seal of its own (no log at all)")
    return collected, empty


def requires_critical_eku(layout: Layout, days: Sequence[date]) -> bool:
    """The EKU rule a package of ``days`` applies to its record and report tokens: strict
    (RFC 3161 2.3, a critical EKU) unless a sealed manifest of one of these days records that
    the site accepted a non-critical one (``tsa.require_critical_eku = false``) — the same rule
    the standalone verifier takes from the package's manifests."""
    for day in days:
        try:
            manifest = json.loads(layout.manifest(day).read_bytes())
        except (OSError, ValueError):
            continue  # an unsealed day (skipped as empty) or one the build refuses
        tsa = manifest.get("tsa") if isinstance(manifest, dict) else None
        if isinstance(tsa, dict) and tsa.get("require_critical_eku") is False:
            return False
    return True


def _root_of(
    token: bytes,
    data: bytes,
    roots: Sequence[x509.Certificate],
    require_critical_eku: bool = True,
) -> str:
    """SHA-256 fingerprint of the root a record's or report's token chains to ("" if it does not
    verify — the standalone self-check then refuses the package, as before)."""
    try:
        algorithm = inspect_token(token).digest_alg
        digest = hashlib.new(READABLE_DIGESTS[algorithm], data).digest()
    except (TsaError, ValueError, KeyError):
        return ""
    check = verify_token(
        token, roots=roots, digest=digest, require_critical_eku=require_critical_eku
    )
    return check.details.get("root_sha256") or ""


def _check_links(days: Sequence[_Day]) -> None:
    """Consecutive days of the package must be chained in the vault as they were sealed.

    A broken link means the vault itself is inconsistent (a manifest or token changed after the
    next day was sealed); such a package would not verify, so it is not built.
    """
    by_day = {day.day: day for day in days}
    for day in days:
        run = unsealed_days(day.manifest)  # empty days between it and the day it links to
        before = by_day.get((run[0] if run else day.day) - timedelta(days=1))
        previous = day.manifest.get("previous")
        if before is None or previous is None:
            continue  # a chain start (or restart) is reported by the verifier, not refused
        if (
            not isinstance(previous, dict)
            or previous.get("day") != before.day.isoformat()
            or previous.get("manifest_sha256") != hashlib.sha256(before.manifest_bytes).hexdigest()
            or previous.get("token_sha256") != hashlib.sha256(before.token_bytes).hexdigest()
        ):
            raise EvidenceError(
                f"{day.day}: the chain link to {before.day} does not match the vault (the "
                "previous day was changed after this day was sealed) — package not built"
            )


def _day_entries(layout: Layout, day: _Day) -> list[_Entry]:
    entries = [
        _Entry(f"{day.folder}/{day.manifest_name}", "manifest", data=day.manifest_bytes),
        _Entry(f"{day.folder}/{day.manifest_name}.zd", "token", data=day.token_bytes),
    ]
    audit = [(day.audit, "audit")] if day.audit_included and day.audit is not None else []
    for entry, role in [*((entry, "log") for entry in day.included), *audit]:
        relative = str(entry.get("path", ""))
        source = _vault_path(layout, relative)
        if not source.is_file():
            raise EvidenceError(
                f"{relative}: listed in the {day.day} manifest but not in the vault"
            )
        if "bytes" in entry and source.stat().st_size != entry["bytes"]:
            raise EvidenceError(f"{relative}: size differs from the manifest (changed?)")
        entries.append(
            _Entry(
                VAULT + relative,
                role,
                source=source,
                expected_sha256=str(entry.get("sha256")),
                compress=False,  # already gzip
            )
        )
    return entries


class _Records:
    """Deletion records, the chain anchor and renewal stamps that belong with the days."""

    def __init__(
        self,
        layout: Layout,
        roots: Sequence[x509.Certificate] = (),
        *,
        require_critical_eku: bool = True,
    ) -> None:
        self.layout = layout
        self.entries: dict[str, _Entry] = {}
        self.roots = roots
        self.require_critical_eku = require_critical_eku
        #: Fingerprints of the roots the records' tokens chain to (only those are bundled).
        self.used_roots: set[str] = set()

    def add(
        self,
        relative: str,
        role: str,
        sha256: str | None = None,
        token_sha256: str | None = None,
    ) -> None:
        path = _vault_path(self.layout, relative)
        if not path.is_file():
            raise EvidenceError(f"{relative}: sealed record missing in the vault")
        data = path.read_bytes()
        if sha256 and hashlib.sha256(data).hexdigest() != sha256:
            raise EvidenceError(f"{relative}: differs from the hash sealed in the manifest")
        self.entries[VAULT + relative] = _Entry(VAULT + relative, role, data=data)
        token = path.with_name(path.name + ".zd")
        if token.is_file():
            token_data = token.read_bytes()
            if token_sha256 and hashlib.sha256(token_data).hexdigest() != token_sha256:
                raise EvidenceError(f"{relative}.zd: differs from the hash sealed in the manifest")
            name = f"{VAULT}{relative}.zd"
            self.entries[name] = _Entry(name, f"{role}_token", data=token_data)
            root = _root_of(token_data, data, self.roots, self.require_critical_eku)
            if root:
                self.used_roots.add(root)
        elif token_sha256:
            raise EvidenceError(f"{relative}.zd: time stamp missing in the vault")

    def collect(self, days: Sequence[_Day]) -> list[_Entry]:
        sealed = {day.day for day in days}
        for day in days:
            for record in day.manifest.get("deletions") or []:
                self.add(
                    str(record.get("path", "")),
                    "deletion",
                    str(record.get("sha256") or "") or None,
                    str(record.get("token_sha256") or "") or None,
                )
            self._anchor(day, sealed)
        self._renewals(days)
        return list(self.entries.values())

    def _anchor(self, day: _Day, sealed: set[date]) -> None:
        """The previous day was deleted by retention: ship the anchor and its record."""
        previous = day.manifest.get("previous")
        if not isinstance(previous, dict):
            return
        previous_day = date.fromisoformat(str(previous.get("day")))
        anchor_path = self.layout.chain_anchor
        if (
            previous_day in sealed
            or self.layout.manifest(previous_day).exists()
            or not anchor_path.is_file()
        ):
            return
        data = anchor_path.read_bytes()
        try:
            anchor = json.loads(data)
            record = str(anchor["record"]) if anchor.get("day") == previous_day.isoformat() else ""
        except (ValueError, KeyError, AttributeError) as exc:
            raise EvidenceError(f"the chain anchor is unreadable: {exc}") from exc
        if not record:
            return
        relative = anchor_path.relative_to(self.layout.vault).as_posix()
        self.entries[VAULT + relative] = _Entry(VAULT + relative, "chain_anchor", data=data)
        self.add(f"deletions/{record}", "deletion")

    def _renewals(self, days: Sequence[_Day]) -> None:
        folder = self.layout.vault / "renewals"
        if not folder.is_dir():
            return
        hashes = set()
        for day in days:
            hashes.add(hashlib.sha256(day.manifest_bytes).hexdigest())
            hashes.add(hashlib.sha256(day.token_bytes).hexdigest())
        for record in sorted(folder.glob("*.json")):
            text = record.read_bytes().decode("utf-8", "replace")
            if any(digest in text for digest in hashes):
                self.add(record.relative_to(self.layout.vault).as_posix(), "renewal")


def _certificate_facts(certificate: x509.Certificate, path: str) -> dict[str, Any]:
    der = certificate.public_bytes(Encoding.DER)
    return {
        "path": path,
        "subject": certificate.subject.rfc4514_string(),
        "issuer": certificate.issuer.rfc4514_string(),
        "sha256": hashlib.sha256(der).hexdigest(),
        "sha1": hashlib.sha1(der).hexdigest(),  # noqa: S324 - fingerprint for comparison only
        "not_before": isoformat_site(certificate.not_valid_before_utc, "seconds"),
        "not_after": isoformat_site(certificate.not_valid_after_utc, "seconds"),
    }


def _roots(
    roots: Sequence[x509.Certificate], used: Collection[str]
) -> tuple[list[_Entry], list[dict[str, Any]]]:
    """Only the trusted roots the package's tokens actually chain to (never, say, a test root
    the server also trusts): the recipient is asked to compare exactly these with Kamu SM."""
    unique = {
        digest: root
        for root in roots
        if (digest := hashlib.sha256(root.public_bytes(Encoding.DER)).hexdigest()) in used
    }
    entries, index, bundle = [], [], b""
    for digest, certificate in sorted(unique.items()):
        name = f"{ROOTS_FOLDER}{digest[:16]}.pem"
        pem = certificate.public_bytes(Encoding.PEM)
        bundle += pem
        entries.append(_Entry(name, "root", data=pem))
        index.append(_certificate_facts(certificate, name))
    entries.append(_Entry(ROOTS_BUNDLE, "root_bundle", data=bundle))
    return entries, index


def _tsa_certificates(
    days: Sequence[_Day], roots: Sequence[x509.Certificate]
) -> tuple[list[_Entry], list[dict[str, Any]]]:
    root_ders = {root.public_bytes(Encoding.DER) for root in roots}
    unique: dict[str, x509.Certificate] = {}
    for day in days:
        for certificate in day.certificates:
            der = certificate.public_bytes(Encoding.DER)
            if der not in root_ders:
                unique[hashlib.sha256(der).hexdigest()] = certificate
    entries, index = [], []
    for digest, certificate in sorted(unique.items()):
        name = f"{TSA_FOLDER}{digest[:16]}.pem"
        entries.append(_Entry(name, "tsa_certificate", data=certificate.public_bytes(Encoding.PEM)))
        index.append(_certificate_facts(certificate, name))
    return entries, index


def _crls(crl_dir: Path | None) -> list[_Entry]:
    if crl_dir is None or not crl_dir.is_dir():
        return []
    entries = []
    for path in sorted(crl_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() not in CRL_SUFFIXES:
            continue
        if not SAFE_FILE_NAME.match(path.name):
            raise EvidenceError(f"unsupported CRL file name {path.name!r}")
        entries.append(_Entry(CRL_FOLDER + path.name, "crl", data=path.read_bytes()))
    return entries


def _extracts(
    layout: Layout, days: Sequence[_Day], refs: Sequence[RawRef]
) -> tuple[list[_Entry], list[dict[str, Any]]]:
    by_file: dict[tuple[date, str], set[int]] = {}
    for ref in refs:
        by_file.setdefault((ref.day, ref.source), set()).add(ref.line)
    by_day = {day.day: day for day in days}
    entries, index = [], []
    for (day, source), numbers in sorted(by_file.items()):
        collected = by_day.get(day)
        if collected is None:
            raise EvidenceError(f"extract line {day} {source} is outside the package days")
        entry = next((f for f in collected.included if f.get("source") == source), None)
        if entry is None:
            raise EvidenceError(f"extract line {day} {source}: no such sealed file in the package")
        lines = read_lines(_vault_path(layout, str(entry["path"])), numbers)
        name = f"{EXTRACT_FOLDER}{day.isoformat()}/{source}.txt"
        entries.append(_Entry(name, "extract", data=format_extract(lines)))
        index.append(
            {
                "path": name,
                "day": day.isoformat(),
                "source": source,
                "sealed_file": VAULT + str(entry["path"]),
                "lines": sorted(numbers),
            }
        )
    return entries, index


def _day_index(day: _Day) -> dict[str, Any]:
    tsa = day.manifest.get("tsa") or {}
    return {
        "day": day.day.isoformat(),
        "manifest": f"{day.folder}/{day.manifest_name}",
        "manifest_sha256": hashlib.sha256(day.manifest_bytes).hexdigest(),
        "token": f"{day.folder}/{day.manifest_name}.zd",
        "token_sha256": hashlib.sha256(day.token_bytes).hexdigest(),
        "container": day.info.container,
        "gen_time": isoformat_site(day.info.gen_time, "microseconds"),
        "serial": day.info.serial,
        "tsa": day.info.tsa_name,
        "provider": tsa.get("provider"),
        # Claimed by the manifest AND confirmed by the verified chain (a root the site listed
        # in tsa.legal_root_fingerprints); legal_grade_claimed is the manifest's own statement.
        "legal_grade": day.legal_grade,
        "legal_grade_claimed": day.legal_grade_claimed,
        "root_sha256": day.root_sha256,
        "files": [VAULT + str(entry["path"]) for entry in day.included],
        "excluded": [
            {
                "path": entry.get("path"),
                "source": entry.get("source"),
                "sha256": entry.get("sha256"),
            }
            for entry in day.excluded
        ]
        + _audit_excluded(day),
    }


def _audit_excluded(day: _Day) -> list[dict[str, Any]]:
    """The day's audit log, listed as left out (with its sealed SHA-256) unless included."""
    if day.audit_included or day.audit is None:
        return []
    return [
        {
            "path": day.audit.get("path"),
            "source": None,
            "kind": "audit_log",
            "sha256": day.audit.get("sha256"),
            "reason": AUDIT_EXCLUDED_REASON,
        }
    ]


def _guide_facts(
    *,
    package_id: str,
    now: datetime,
    created_by: str,
    site_name: str,
    days: Sequence[_Day],
    sources: Sequence[str] | None,
    lookup_id: str | None,
    reference: str | None,
    roots_index: Sequence[dict[str, Any]],
    names: Sequence[str],
    report: str | None,
    report_token: str | None,
    report_note: str | None,
    encrypted: bool,
    roots: Sequence[x509.Certificate],
) -> GuideFacts:
    by_digest = {hashlib.sha256(r.public_bytes(Encoding.DER)).hexdigest(): r for r in roots}
    return GuideFacts(
        package_id=package_id,
        created_at=now,
        created_by=created_by,
        site=site_name,
        version=__version__,
        days=[
            DayFacts(
                day=day.day,
                folder=day.folder,
                manifest=day.manifest_name,
                token=day.manifest_name + ".zd",
                container=day.info.container,
                gen_time=day.info.gen_time,
                serial=day.info.serial,
                tsa=day.info.tsa_name,
                provider=str((day.manifest.get("tsa") or {}).get("provider", "?")),
                legal_grade=day.legal_grade,
                files=[VAULT + str(entry["path"]) for entry in day.included],
                legal_grade_claimed=day.legal_grade_claimed,
            )
            for day in days
        ],
        sources=sources,
        lookup_id=lookup_id,
        reference=reference,
        roots=[
            RootFacts(
                path=str(item["path"]),
                subject=str(item["subject"]),
                sha256=str(item["sha256"]).upper(),
                sha1=str(item["sha1"]).upper(),
                not_before=by_digest[item["sha256"]].not_valid_before_utc,
                not_after=by_digest[item["sha256"]].not_valid_after_utc,
            )
            for item in roots_index
        ],
        roots_bundle=ROOTS_BUNDLE,
        extract_files=[name for name in names if name.startswith(EXTRACT_FOLDER)],
        report=report,
        report_token=report_token,
        report_note=report_note,
        crl_files=[name for name in names if name.startswith(CRL_FOLDER)],
        encrypted=encrypted,
        extra_records=[
            name for name in names if name.startswith(("vault/deletions/", "vault/renewals/"))
        ],
        verifier_sha256=verifier_sha256(),
        audit_log_included=any(day.audit_included for day in days),
    )


def build_package(
    layout: Layout,
    days: Sequence[date],
    *,
    roots: Sequence[x509.Certificate],
    sources: Sequence[str] | None = None,
    extract: Sequence[RawRef] = (),
    out_dir: Path | None = None,
    encrypt: bool = False,
    password: str | None = None,
    report_pdf: bytes | None = None,
    report_name: str = "rapor.pdf",
    report_token: bytes | None = None,
    report_note: str | None = None,
    include_audit_log: bool = False,
    legal_root_fingerprints: Collection[str] = (),
    created_by: str = "",
    lookup_id: str | None = None,
    reference: str | None = None,
    site_name: str = "",
    crl_dir: Path | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> PackageInfo:
    """Build the evidence package of ``days`` into ``out_dir`` (default ``exports/``).

    ``sources`` limits the sealed files to those sources (``None``: all). ``extract`` lists raw
    lines to copy into ``extract/``; they must belong to the included days and sources.
    ``encrypt`` makes an AES-256 ZIP; without ``password`` a strong one is generated and
    returned once in :attr:`PackageInfo.password` — it is not stored anywhere. ``report_pdf``
    (the stored report's bytes) goes in as ``rapor/<report_name>``, ``report_token`` (its time
    stamp) as ``rapor/<report_name>.zd``; the self-check verifies that token like a deletion
    record's. ``report_note`` (``paket.json`` ``report_note``, the guides, a verifier warning)
    says why a report goes in **without** its time stamp, e.g. a token that did not verify.
    ``include_audit_log`` adds each day's ARCILOGGER audit log (``audit.jsonl.gz``); it holds
    every other lookup of the day and staff data, so it is left out by default (KVKK data
    minimisation) and listed in ``paket.json`` as excluded with its sealed SHA-256.
    ``legal_root_fingerprints`` (``tsa.legal_root_fingerprints``) decides which days count as
    legal grade: claimed by the manifest and chained to one of those roots. Only the roots the
    package's tokens chain to are bundled.
    """
    wanted_days = sorted(set(days))
    if not wanted_days:
        raise EvidenceError("no days requested")
    if not roots:
        raise EvidenceError("no trusted root certificates (tsa.roots_dir)")
    wanted_sources = None if sources is None else sorted(set(sources))
    for name in wanted_sources or []:
        if not SOURCE_NAME.match(name):
            raise EvidenceError(f"invalid source name {name!r}")
    if report_pdf is not None and not REPORT_NAME.match(report_name):
        raise EvidenceError(f"invalid report file name {report_name!r}")
    if report_token is not None and report_pdf is None:
        raise EvidenceError("a report time stamp needs the report")
    if report_note is not None and (report_pdf is None or report_token is not None):
        raise EvidenceError("a report note explains a report without its time stamp")
    if password is not None and len(password) < MIN_PASSWORD_LENGTH:
        raise EvidenceError(f"the password must have at least {MIN_PASSWORD_LENGTH} characters")
    encrypted = encrypt or password is not None
    if encrypted and password is None:
        password = generate_password()
    refs = sorted(set(extract))
    now = clock()

    collected, empty_days = _collect_days(
        layout, wanted_days, roots, wanted_sources, legal_root_fingerprints
    )
    wanted_days = [day.day for day in collected]
    for day in collected:
        day.audit_included = include_audit_log
    _check_links(collected)
    for name in wanted_sources or []:
        if not any(entry.get("source") == name for day in collected for entry in day.included):
            raise EvidenceError(f"source {name!r} has no sealed file in the requested days")

    entries: list[_Entry] = []
    for day in collected:
        entries += _day_entries(layout, day)
    records = _Records(
        layout, roots, require_critical_eku=all(day.require_critical_eku for day in collected)
    )
    entries += records.collect(collected)
    used_roots = {day.root_sha256 for day in collected} | records.used_roots
    if report_pdf is not None and report_token is not None:
        used_roots.add(_root_of(report_token, report_pdf, roots, records.require_critical_eku))
    used_roots.discard("")
    root_entries, roots_index = _roots(roots, used_roots)
    tsa_entries, tsa_index = _tsa_certificates(collected, roots)
    extract_entries, extract_index = _extracts(layout, collected, refs)
    crl_entries = _crls(crl_dir)
    entries += root_entries + tsa_entries + extract_entries + crl_entries
    report = f"{REPORT_FOLDER}{report_name}" if report_pdf is not None else None
    report_zd = f"{report}.zd" if report is not None and report_token is not None else None
    if report_pdf is not None and report is not None:
        entries.append(_Entry(report, "report", data=report_pdf, compress=False))
    if report_token is not None and report_zd is not None:
        entries.append(_Entry(report_zd, "report_token", data=report_token))
    for name, path in VERIFIER_FILES.items():
        entries.append(
            _Entry(name, "verifier", data=path.read_bytes(), executable=name != "verify.ps1")
        )

    scope: dict[str, Any] = {
        "days": [day.isoformat() for day in wanted_days],
        "sources": wanted_sources,
        "extract": [ref.as_json() for ref in refs],
        "lookup_id": lookup_id,
        "reference": reference,
        "audit_log": include_audit_log,
    }
    if empty_days:
        # Requested days without any log that were not stamped one by one: the manifest of a
        # later included day lists them (``unsealed_empty_days``).
        scope["unsealed_empty_days"] = [day.isoformat() for day in empty_days]
    scope_hash = hashlib.sha256(canonical_json(scope)).hexdigest()
    package_id = f"KP-{to_site(now).strftime('%Y%m%d-%H%M%S')}-{scope_hash[:8]}"
    facts = _guide_facts(
        package_id=package_id,
        now=now,
        created_by=created_by,
        site_name=site_name,
        days=collected,
        sources=wanted_sources,
        lookup_id=lookup_id,
        reference=reference,
        roots_index=roots_index,
        names=[entry.name for entry in entries],
        report=report,
        report_token=report_zd,
        report_note=report_note,
        encrypted=encrypted,
        roots=roots,
    )
    entries.append(_Entry(GUIDE_TR, "guide", data=render_turkish(facts)))
    entries.append(_Entry(GUIDE_EN, "guide", data=render_english(facts)))
    names = [entry.name for entry in entries]
    if len(set(names)) != len(names):  # pragma: no cover - would be a bug in this module
        raise EvidenceError("duplicate file names in the package")

    index: dict[str, Any] = {
        "schema": INDEX_SCHEMA,
        "package_id": package_id,
        "created_at": isoformat_site(now, "seconds"),
        "created_by": created_by,
        "product": {"name": PRODUCT_NAME, "version": __version__},
        "site": site_name,
        "notice": LEGAL_NOTE_TR,
        "scope": scope,
        "encrypted": encrypted,
        "days": [_day_index(day) for day in collected],
        "roots": roots_index,
        "tsa_certificates": tsa_index,
        "crls": [entry.name for entry in crl_entries],
        "crl_note": None if crl_entries else NO_CRL_NOTE,
        "extract": extract_index,
        "records": sorted(
            name
            for name in names
            if name.startswith("vault/deletions/") or name.startswith("vault/renewals/")
        ),
        "report": report,
        "report_token": report_zd,
        "report_note": report_note,
        "verifier": {"path": "verify.py", "version": VERIFIER_VERSION},
    }

    target_dir = out_dir or layout.exports
    target_dir.mkdir(parents=True, exist_ok=True)
    final = target_dir / f"{PACKAGE_PREFIX}{package_id}.zip"
    if final.exists():
        raise EvidenceError(f"{final} already exists")
    part = final.with_name(final.name + ".part")
    index_bytes, count = _write(part, entries, index, password if encrypted else None)
    try:
        _self_check(part, password if encrypted else None)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    part.replace(final)
    fsync_dir(final.parent)
    info = PackageInfo(
        package_id=package_id,
        path=final,
        sha256=sha256_file(final),
        size=final.stat().st_size,
        file_count=count,
        password_protected=encrypted,
        days=tuple(wanted_days),
        sources=tuple(wanted_sources) if wanted_sources is not None else None,
        extract_lines=len(refs),
        index_sha256=hashlib.sha256(index_bytes).hexdigest(),
        created_at=now,
        created_by=created_by,
        lookup_id=lookup_id,
        legal_grade=all(day.legal_grade for day in collected),
        legal_grade_claimed=all(day.legal_grade_claimed for day in collected),
        verifier_sha256=verifier_sha256(),
        password=password if encrypted else None,
    )
    log.info(
        "evidence package %s built: %s, %d files, sha256 %s, encrypted %s",
        package_id,
        final.name,
        count,
        info.sha256,
        encrypted,
    )
    return info


def _self_check(part: Path, password: str | None) -> None:
    """Run the standalone verifier's checks on the finished ZIP, read back through the ZIP.

    Quick mode: the sealed files were already hashed while they were copied, and their content
    is bound to that hash. Any failure (not a warning, such as a test stamp) stops the build.
    """
    try:
        with ArchivePackage(part, password) as package:
            report = verify_package(package, deep=False)
    except (PackageError, ValueError, IndexError, RecursionError, OSError) as exc:
        raise EvidenceError(f"the finished package cannot be checked: {exc}") from exc
    failures = [f"{f.code} ({f.where}): {f.en}" for f in report.findings if f.level == "fail"]
    if failures:
        raise EvidenceError(
            "the finished package does not pass the standalone verifier — package not built: "
            + "; ".join(failures[:5])
        )


def _write(
    part: Path, entries: Sequence[_Entry], index: dict[str, Any], password: str | None
) -> tuple[bytes, int]:
    """Write all entries in name order, then ``paket.json`` with their sizes and hashes."""
    files = []
    try:
        with ArchiveWriter(part, password) as writer:
            for entry in sorted(entries, key=lambda item: item.name):
                if entry.source is not None:
                    size, digest = writer.add_file(
                        entry.name,
                        entry.source,
                        expected_sha256=entry.expected_sha256,
                        compress=entry.compress,
                    )
                else:
                    size, digest = writer.add_bytes(
                        entry.name,
                        entry.data or b"",
                        executable=entry.executable,
                        compress=entry.compress,
                    )
                files.append(
                    {"path": entry.name, "bytes": size, "sha256": digest, "role": entry.role}
                )
            index_bytes = canonical_json({**index, "files": files})
            writer.add_bytes(INDEX_NAME, index_bytes)
            count = writer.count
        fsync_file(part)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return index_bytes, count


def package_days(first: date, last: date) -> list[date]:
    """Every calendar day from ``first`` to ``last`` inclusive."""
    if last < first:
        raise EvidenceError(f"{last} is before {first}")
    return [first + timedelta(days=offset) for offset in range((last - first).days + 1)]
