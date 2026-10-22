"""Controller and view-model of the ARCILOGGER Lisans Yöneticisi (no Tk import).

The graphical issuer (``arcilogger_license_gui.py``) is a thin Tk view over :class:`LicenseManager`.
Every rule (patterns, modules, limits, the registry, the weak-binding refusal, the earlier-licence
rule, the licence format) comes from the issuer's shared functions
(``arcilogger_license_issuer``) and ``arcilogger_core.license``; this module only turns form
values into their parameters and their results into texts and table rows.

Nothing here touches the network. The passphrase is a parameter of the calls that need it and is
never stored: not in :class:`Settings`, not in an attribute, not in a log.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Final, Literal

import arcilogger_license_issuer as issuer
from arcilogger_license_issuer import ToolError as ToolError

import arcilogger_core
from arcilogger_core.fmt import format_date
from arcilogger_core.license import LICENSOR_EMAIL, LICENSOR_NAME
from arcilogger_core.license.document import MAX_LIMIT as MAX_LIMIT
from arcilogger_core.license.document import LicenseDocument
from arcilogger_core.license.errors import LicenseError as LicenseError
from arcilogger_core.license.fingerprint import SERVER_ID_PATTERN
from arcilogger_core.license.modules import CORE_MODULE, MODULES, Module
from arcilogger_core.license.request import LicenseRequest, format_utc_seconds, request_sha256
from arcilogger_core.license.state import MAINTENANCE_SOON_DAYS

#: The issuer module the model calls; the window reaches it only through here, so both always use
#: the same module (and the same ``ToolError``).
__all__ = ["MAX_LIMIT", "LicenseError", "ToolError", "issuer"]

APP_NAME: Final = "ARCILOGGER Lisans Yöneticisi"
APP_NAME_EN: Final = "ARCILOGGER Licence Manager"
SETTINGS_FILE: Final = "settings.json"
SETTINGS_SCHEMA: Final = 1
#: The only keys ever written to the settings file (paths; never a passphrase).
SETTINGS_KEYS: Final = ("private_key", "registry", "output_dir")
DEFAULT_LIMIT: Final = "10"
UNLIMITED_TR: Final = "Sınırsız"
_MODULES_BY_KEY: Final = {item.key: item for item in MODULES}
_TURKISH_ASCII: Final = str.maketrans(
    {"ç": "c", "ğ": "g", "ı": "i", "ö": "o", "ş": "s", "ü": "u", "â": "a", "î": "i", "û": "u"}
)


# -- messages -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Message:
    """A text for the owner: Turkish first, English under it; ``code`` for licence errors."""

    tr: str
    en: str
    code: str | None = None

    def text(self) -> str:
        head = f"{self.code}: " if self.code else ""
        return f"{head}{self.tr}\n\n{self.en}"


def message_of(exc: BaseException) -> Message:
    """The Turkish/English text of a refusal of the issuer, the licence code or the OS."""
    if isinstance(exc, ToolError):
        return Message(exc.tr, exc.en)
    if isinstance(exc, LicenseError):
        return Message(exc.message_tr, exc.message_en, exc.code)
    if isinstance(exc, PermissionError):
        where = exc.filename or exc
        return Message(
            "Dosya başka bir program tarafından kullanılıyor ya da yazma izni yok (Excel'de "
            f"açıksa kapatın): {where}",
            f"the file is in use by another program or not writable (close it in Excel): {where}",
        )
    if isinstance(exc, UnicodeDecodeError):
        return Message(
            f"Dosya metin olarak okunamadı (UTF-8 bekleniyor): {exc}",
            f"the file is not UTF-8 text: {exc}",
        )
    if isinstance(exc, OSError):
        return Message(f"Dosya işlemi başarısız: {exc}", f"file operation failed: {exc}")
    return Message(f"Beklenmeyen hata: {exc}", f"unexpected error: {exc}")


class FormError(Exception):
    """Several refusals at once (the issue form shows every error before the passphrase)."""

    def __init__(self, messages: Sequence[Message]) -> None:
        super().__init__("; ".join(message.en for message in messages))
        self.messages = tuple(messages)


def _collect(errors: list[Message], check: Callable[[], Any]) -> Any:
    """Run one check; a refusal is added to ``errors`` and None returned."""
    try:
        return check()
    except (ToolError, LicenseError, ValueError, OSError) as exc:
        errors.append(message_of(exc))
        return None


#: A placeholder for the issuer's option name in its texts; :func:`_labelled` puts the window's
#: Turkish label into the Turkish text and the English label into the English one.
_OPTION: Final = "\x00"


def _labelled[T](tr: str, en: str, check: Callable[..., T], *args: Any) -> T:
    """``check(*args, option)`` of the issuer, its refusal worded with the window's labels (the
    command line's ``--option`` names mean nothing in the window)."""
    try:
        return check(*args, _OPTION)
    except ToolError as exc:
        raise ToolError(
            exc.tr.replace(_OPTION, tr), exc.en.replace(_OPTION, en), exit_code=exc.exit_code
        ) from None


# -- settings -------------------------------------------------------------------------------------


@dataclass
class Settings:
    """The remembered paths. The passphrase is not a field and is never written."""

    private_key: str = ""
    registry: str = ""
    output_dir: str = ""

    def to_json(self) -> dict[str, Any]:
        return {"schema": SETTINGS_SCHEMA, **{key: getattr(self, key) for key in SETTINGS_KEYS}}


def settings_dir(
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> Path:
    """``%APPDATA%\\ARCILOGGER Lisans Yöneticisi`` on Windows, else ``~/.config/…``
    (``$XDG_CONFIG_HOME`` when set)."""
    env = os.environ if environ is None else environ
    system = sys.platform if platform is None else platform
    base_home = Path.home() if home is None else home
    if system == "win32":
        appdata = env.get("APPDATA")
        base = Path(appdata) if appdata else base_home / "AppData" / "Roaming"
    else:
        xdg = env.get("XDG_CONFIG_HOME")
        base = Path(xdg) if xdg else base_home / ".config"
    return base / APP_NAME


def default_settings_path() -> Path:
    return settings_dir() / SETTINGS_FILE


def load_settings(path: Path) -> Settings:
    """The saved settings; a missing or unreadable file gives empty settings."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Settings()
    if not isinstance(data, dict):
        return Settings()
    values = {key: data.get(key) for key in SETTINGS_KEYS}
    return Settings(**{key: value for key, value in values.items() if isinstance(value, str)})


def save_settings(settings: Settings, path: Path) -> None:
    """Write the settings (paths only) atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    text = json.dumps(settings.to_json(), ensure_ascii=False, indent=2) + "\n"
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def check_private_key_path(path: Path) -> None:
    """A ``<key-id>.private.pem`` that exists and is outside every git working tree."""
    issuer.key_id_of(path)
    if not path.is_file():
        raise ToolError(f"Dosya yok: {path}", f"no such file: {path}")
    issuer.refuse_in_repository(path, "Özel anahtar", "private key")


def registry_missing(path: Path) -> ToolError:
    return ToolError(
        f"Kayıt defteri bulunamadı ya da boş: {path}. Ayarlar'da var olanı seçin ya da "
        "'Yeni oluştur' ile oluşturun.",
        f"registry not found or empty: {path}. Choose the existing one in Settings or create "
        "a new one",
    )


def check_registry_path(path: Path) -> issuer.Registry:
    """An existing registry outside every git working tree with the exact header.

    Unlike the command line's first ``issue``, the window never creates a registry on the fly:
    it remembers the path, so a missing file means a moved or renamed registry (whose earlier
    licences would be skipped); "Yeni oluştur" writes the header-only file."""
    if path.exists() and not path.is_file():
        raise ToolError(f"Bir dosya değil: {path}", f"not a file: {path}")
    registry = issuer.read_registry(path)
    if not registry.exists:
        raise registry_missing(path)
    return registry


def check_output_dir(path: Path) -> None:
    """An existing folder outside every git working tree."""
    if not path.is_dir():
        raise ToolError(f"Klasör yok: {path}", f"no such folder: {path}")
    issuer.refuse_in_repository(path, "Lisans dosyaları klasörü", "output folder", folder=True)


# -- keys -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class KeyInfo:
    key_id: str
    public_key: str
    public_sha256: str

    @property
    def line(self) -> str:
        return f'"{self.key_id}": "{self.public_key}",'


def key_id_problem(key_id: str) -> Message | None:
    """Live check of a new key id (``KEY_ID_PATTERN``, never ``test-…``)."""
    if not key_id:
        return Message(
            "Anahtar kimliği girin (örnek: arci-2026a).", "enter a key id (e.g. arci-2026a)"
        )
    try:
        issuer.check_key_id(key_id)
    except ToolError:
        # The rule is the issuer's; the command line's regex text is worded for people here.
        return Message(
            "3-32 karakter: küçük harf, rakam ve tire; harf ya da rakamla başlar, test- ile "
            "başlamaz (örnek: arci-2026a).",
            "3-32 characters: lower-case letters, digits and hyphens; starts with a letter or "
            "digit, never test- (e.g. arci-2026a)",
        )
    return None


def passphrase_strength(passphrase: str) -> tuple[int, Message]:
    """A hint 0-4 (length and character classes); the rule itself is the issuer's 12 characters."""
    classes = sum(
        bool(re.search(pattern, passphrase))
        for pattern in (r"[a-zçğıöşü]", r"[A-ZÇĞİÖŞÜ]", r"[0-9]", r"[^\w\s]|\s")
    )
    length = len(passphrase)
    if length < issuer.MIN_PASSPHRASE:
        score = 0
    elif length < 16:
        score = 1 + (classes >= 3)
    elif length < 24:
        score = 2 + (classes >= 3)
    else:
        score = 4
    labels = (
        ("Çok kısa (en az 12 karakter)", "too short (at least 12 characters)"),
        ("Zayıf: daha uzun bir parola cümlesi seçin", "weak: choose a longer passphrase"),
        ("Orta", "fair"),
        ("İyi", "good"),
        ("Güçlü", "strong"),
    )
    return score, Message(*labels[score])


def generate_key(key_id: str, folder: Path, passphrase: str, again: str) -> issuer.KeyResult:
    """``keygen`` of the issuer; the two passphrase entries must match."""
    problem = key_id_problem(key_id)
    if problem is not None:
        raise issuer.usage(problem.tr, problem.en)
    if passphrase != again:
        raise issuer.usage("Parolalar aynı değil.", "the passphrases differ")
    return issuer.generate_key(key_id, folder, passphrase)


def test_key(path: Path, passphrase: str) -> KeyInfo:
    """Open the private key once: its key id and the public key's SHA-256 fingerprint."""
    check_private_key_path(path)
    key = issuer.open_private_key(path, passphrase)
    public = issuer.public_b64(key)
    del key
    return KeyInfo(issuer.key_id_of(path), public, issuer.public_sha256(public))


test_key.__test__ = False  # type: ignore[attr-defined]  # not a pytest test


# -- modules, limits, dates -----------------------------------------------------------------------


class ModuleChoice:
    """Ticked modules with the registry's rules: ``core_5651`` always on; ticking a module ticks
    what it requires, unticking one unticks what requires it. Unknown keys are refused."""

    def __init__(self, selected: Iterable[str] = ()) -> None:
        self._on: set[str] = {item.key for item in MODULES if item.always} | {CORE_MODULE}
        for key in selected:
            self.set(key, on=True)

    @staticmethod
    def modules() -> tuple[Module, ...]:
        return MODULES

    @staticmethod
    def locked(key: str) -> bool:
        return key == CORE_MODULE or _MODULES_BY_KEY[key].always

    def is_on(self, key: str) -> bool:
        if key not in _MODULES_BY_KEY:
            raise KeyError(key)
        return key in self._on

    def set(self, key: str, *, on: bool) -> None:
        if key not in _MODULES_BY_KEY:
            raise KeyError(f"unknown module {key!r}")
        if self.locked(key):
            return
        if on:
            pending = [key]
            while pending:
                current = pending.pop()
                if current not in self._on:
                    self._on.add(current)
                    pending.extend(_MODULES_BY_KEY[current].requires)
        else:
            pending = [key]
            while pending:
                current = pending.pop()
                if current in self._on and not self.locked(current):
                    self._on.discard(current)
                    pending.extend(item.key for item in MODULES if current in item.requires)

    def selected(self) -> list[str]:
        return [item.key for item in MODULES if item.key in self._on]

    def option(self) -> str:
        """The ``--modules`` value (``core_5651`` is added by the issuer anyway)."""
        return ",".join(self.selected())


def limit_value(text: str, unlimited: bool, label_tr: str, label_en: str = "") -> int | None:
    """A Spinbox (1…MAX_LIMIT) plus "Sınırsız": the issuer's ``parse_limit``, its refusal
    worded for the window (the box, not the command line's 'unlimited')."""
    try:
        return issuer.parse_limit("unlimited" if unlimited else text.strip(), label_tr)
    except ToolError:
        raise issuer.usage(
            f"{label_tr} 1-{MAX_LIMIT} arasında olmalı ya da {UNLIMITED_TR} işaretlenmeli.",
            f"{label_en or label_tr} must be 1-{MAX_LIMIT} or tick Unlimited",
        ) from None


_DOTTED_DAY: Final = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")


def day_value(text: str, label_tr: str, label_en: str) -> date:
    """A date typed as YYYY-AA-GG or, as people in Turkey write it, GG.AA.YYYY: the issuer's
    ``parse_day``."""
    value = text.strip()
    dotted = _DOTTED_DAY.fullmatch(value)
    if dotted:
        day, month, year = dotted.groups()
        value = f"{year}-{int(month):02d}-{int(day):02d}"
    return _labelled(label_tr, label_en, issuer.parse_day, value)


def limit_text(value: str) -> str:
    """A registry limit cell for people: empty is unlimited."""
    return value if value else UNLIMITED_TR


def add_years(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year + years)
    except ValueError:  # 29 February
        return day.replace(year=day.year + years, day=28)


def maintenance_for(today: date, years: int = 1) -> date:
    """Maintenance for ``years`` from today: today + N years - 1 day (the "+1 yıl" button)."""
    return add_years(today, years) - timedelta(days=1)


def today_utc() -> date:
    return datetime.now(UTC).date()


# -- request --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RequestView:
    request: LicenseRequest

    @property
    def binding(self) -> str:
        return issuer.binding_of(self.request)

    @property
    def strong(self) -> bool:
        return self.binding == "strong"

    @property
    def sha256(self) -> str:
        return request_sha256(self.request)

    def fields(self) -> list[tuple[str, str]]:
        request = self.request
        organization = request.organization or "(ayarlanmamış / not set)"
        return [
            ("Kuruluş / Organisation", organization),
            ("Sunucu kimliği / Server id", request.server_id),
            ("Kurulum kimliği / Installation id", request.installation_id),
            ("Ürün sürümü / Product version", request.product_version),
            ("Platform", f"{request.platform} (SMBIOS: {'var' if request.smbios else 'yok'})"),
            ("Oluşturma günü / Created", format_utc_seconds(request.created_at)),
        ]

    def binding_message(self) -> Message:
        if self.strong:
            return Message(
                "Güçlü bağlama: Windows, SMBIOS UUID ile.",
                "strong binding: Windows with an SMBIOS UUID",
            )
        return Message(
            f"Zayıf bağlama ({self.request.platform}, SMBIOS "
            f"{'var' if self.request.smbios else 'yok'}): sunucu kimliği tek bir düzenlemeyle "
            "başka bir makineye taşınabilir. Yalnızca bilerek verin.",
            f"weak binding ({self.request.platform}, SMBIOS "
            f"{'yes' if self.request.smbios else 'no'}): the server id can be copied to another "
            "machine with one edit. Issue it on purpose only.",
        )


def decode_request_text(text: str) -> RequestView:
    """The issuer's ``decode_request``; raises :class:`LicenseError` (``request_malformed``,
    ``request_checksum``)."""
    return RequestView(issuer.decode_request(text))


# -- issue ----------------------------------------------------------------------------------------

EarlierMode = Literal["", "supersedes", "additional"]


@dataclass
class IssueForm:
    """The values of the "Lisans ver" screen, as typed."""

    request_text: str = ""
    licensee: str = ""
    modules: ModuleChoice = field(default_factory=ModuleChoice)
    sources: str = DEFAULT_LIMIT
    sources_unlimited: bool = False
    users: str = DEFAULT_LIMIT
    users_unlimited: bool = False
    maintenance_until: str = ""
    valid_until_enabled: bool = False
    valid_until: str = ""
    extra_server_ids: str = ""
    note: str = ""
    allow_weak_binding: bool = False
    earlier_mode: EarlierMode = ""
    supersedes: list[str] = field(default_factory=list)
    #: The licence "Yenile / Renew" started from: the only marked row kept for any request.
    renewal_of: str = ""

    def extra_ids(self) -> list[str]:
        return [line.strip() for line in self.extra_server_ids.splitlines() if line.strip()]


def licensee_slug(licensee: str) -> str:
    """A file-name part: Turkish letters to ASCII, everything else to ``-``."""
    text = licensee.casefold().replace("i̇", "i").translate(_TURKISH_ASCII)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:40].strip("-")
    return slug or "lisans"


def module_names(keys: Iterable[str]) -> str:
    """Turkish names of registry keys (unknown keys as they are)."""
    return ", ".join(
        _MODULES_BY_KEY[key].name_tr if key in _MODULES_BY_KEY else key for key in keys
    )


@dataclass(frozen=True)
class IssueOutcome:
    result: issuer.IssueResult
    email: str
    revocations: tuple[tuple[str, str, Message], ...]  # (licence id, revoked.py line, when)

    @property
    def license_id(self) -> str:
        return self.result.document.payload.license_id

    @property
    def file_sha256(self) -> str:
        return self.result.document.sha256


def email_text(result: issuer.IssueResult) -> str:
    """The Turkish e-mail for the customer (copied by the owner, sent by the owner)."""
    payload = result.document.payload
    lines = [
        f"Konu: ARCILOGGER lisans dosyanız ({payload.license_id})",
        "",
        "Merhaba,",
        "",
        f"{payload.licensee} için düzenlenen ARCILOGGER lisans dosyası ektedir:",
        f"  {result.path.name}",
        f"  SHA-256: {result.document.sha256}",
        "",
        "Yüklemek için:",
        "  1. ARCILOGGER web arayüzüne yönetici olarak girin.",
        "  2. Ayarlar → Lisans → Lisans dosyası bölümünde dosyayı seçip yükleyin",
        "     (parolanız yeniden sorulur).",
        '  3. Durumun "Lisanslı" olduğunu ve modüllerinizi sayfada görün.',
        "  Komut satırından da yüklenebilir: arcilogger-db license install <dosya.lic>",
        "",
        "Lisans özeti:",
        f"  Lisans kimliği: {payload.license_id}",
        f"  Modüller: {module_names(payload.modules)}",
        f"  Log kaynağı sınırı: {payload.log_sources or UNLIMITED_TR}",
        f"  Kullanıcı sınırı: {payload.users or UNLIMITED_TR}",
        f"  Bakım bitişi: {format_date(payload.maintenance_until)}",
    ]
    if payload.valid_until is not None:
        lines.append(f"  Geçerlilik sonu: {format_date(payload.valid_until)}")
    if payload.valid_until is None:
        term = (
            "Lisans süresizdir; bakım bitişinden sonra yayımlanan sürümler yalnızca 5651 Temel ile "
            "çalışır."
        )
    else:
        term = (
            f"Bu bir deneme lisansıdır: {format_date(payload.valid_until)} tarihinden sonra "
            "ücretli modüller kapanır; 5651 Temel çalışmaya devam eder."
        )
    lines += [
        f"  Sunucu kimliği: {', '.join(payload.server_ids)}",
        "",
        f"{term} Sunucu değişikliğinde yeni sunucunun istek kodunu gönderin.",
        "",
        "Saygılarımızla,",
        LICENSOR_NAME,
        LICENSOR_EMAIL,
    ]
    return "\n".join(lines) + "\n"


def revocation_note(replaced: Mapping[str, str] | None, server_ids: Sequence[str]) -> Message:
    """When to send the ``revoked.py`` line: a server change or a leak, not a renewal on the
    same server."""
    old = set((replaced or {}).get("server_ids", "").split())
    if old and old <= set(server_ids):
        return Message(
            "Aynı sunucuda yenileme: bu satırı göndermeniz gerekmez. Yalnızca lisans sızdıysa ya "
            "da yanlış düzenlendiyse geliştiriciye gönderin.",
            "renewal on the same server: no need to send this line. Send it to the developer "
            "only if the licence leaked or was issued by mistake.",
        )
    return Message(
        "Sunucu değişti: bu satırı geliştiriciye gönderin (revoked.py; yükseltilen kurulumlarda "
        "geçerli olur). Lisans sızdıysa da gönderin.",
        "the server changed: send this line to the developer (revoked.py; effective on "
        "installations that upgrade). Send it for a leaked licence too.",
    )


# -- registry view --------------------------------------------------------------------------------

Highlight = Literal["", "soon", "ended", "replaced"]

#: (column id, Turkish heading, English heading) of the "Kayıtlar" table.
REGISTRY_COLUMNS: Final = (
    ("issued_at", "Tarih (UTC)", "Date (UTC)"),
    ("license_id", "Lisans kimliği", "Licence id"),
    ("licensee", "Lisans sahibi", "Licensee"),
    ("organization", "Kuruluş", "Organisation"),
    ("server_ids", "Sunucu kimlikleri", "Server ids"),
    ("modules", "Modüller", "Modules"),
    ("limits", "Kaynak / kullanıcı", "Sources / users"),
    ("maintenance_until", "Bakım bitişi", "Maintenance until"),
    ("valid_until", "Geçerlilik sonu", "Valid until"),
    ("binding", "Bağlama", "Binding"),
    ("supersedes", "Yerine geçtiği", "Supersedes"),
    ("replaced_by", "Yerine geçen", "Replaced by"),
)


@dataclass(frozen=True)
class RegistryRowView:
    row: Mapping[str, str]
    highlight: Highlight
    replaced_by: tuple[str, ...]

    @property
    def license_id(self) -> str:
        return self.row["license_id"]

    def values(self) -> tuple[str, ...]:
        row = self.row
        cells = {
            "issued_at": row["issued_at"].replace("T", " ").removesuffix("Z")[:16],
            "modules": module_names(row["modules"].split()),
            "limits": f"{limit_text(row['max_sources'])} / {limit_text(row['max_users'])}",
            "valid_until": row["valid_until"] or "-",
            "binding": {"strong": "güçlü", "weak": "zayıf"}.get(row["binding"], row["binding"]),
            "supersedes": {"additional": "ek sunucu"}.get(row["supersedes"], row["supersedes"]),
            "replaced_by": ", ".join(self.replaced_by),
        }
        return tuple(cells.get(column, row.get(column, "")) for column, _, _ in REGISTRY_COLUMNS)

    def search_text(self) -> str:
        return " ".join((*self.row.values(), *self.values())).casefold()

    @property
    def due(self) -> bool:
        """Due for renewal: maintenance ending within 30 days or ended, and not replaced."""
        return self.highlight in ("soon", "ended") and not self.replaced_by


def _day(text: str) -> date | None:
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def highlight_of(row: Mapping[str, str], today: date) -> Highlight:
    """``ended`` (grey) after the maintenance day, ``soon`` (orange) within 30 days (a replaced
    row is ``replaced`` instead, see :func:`registry_views`)."""
    until = _day(row.get("maintenance_until", ""))
    if until is None:
        return ""
    if until < today:
        return "ended"
    if until <= today + timedelta(days=MAINTENANCE_SOON_DAYS):
        return "soon"
    return ""


def registry_views(rows: Sequence[Mapping[str, str]], today: date) -> list[RegistryRowView]:
    replaced: dict[str, list[str]] = {}
    for row in rows:
        for old in row["supersedes"].split():
            if old != "additional":
                replaced.setdefault(old, []).append(row["license_id"])
    views = []
    for row in rows:
        replaced_by = tuple(replaced.get(row["license_id"], []))
        # A renewed or replaced licence is not "ending soon": grey italics, not orange.
        highlight: Highlight = "replaced" if replaced_by else highlight_of(row, today)
        views.append(RegistryRowView(row, highlight, replaced_by))
    return views


def filter_views(
    views: Iterable[RegistryRowView], query: str = "", *, due_only: bool = False
) -> list[RegistryRowView]:
    words = query.casefold().split()
    return [
        view
        for view in views
        if (not due_only or view.due) and all(word in view.search_text() for word in words)
    ]


def _number(text: str) -> float:
    return float(text) if text else float("inf")


def sort_views(
    views: Iterable[RegistryRowView], column: str, *, descending: bool = False
) -> list[RegistryRowView]:
    index = [name for name, _, _ in REGISTRY_COLUMNS].index(column)

    def key(view: RegistryRowView) -> tuple[Any, ...]:
        if column == "limits":
            return (_number(view.row["max_sources"]), _number(view.row["max_users"]))
        if column in ("issued_at", "maintenance_until", "valid_until"):
            return (view.row[column],)
        return (view.values()[index].casefold(),)

    return sorted(views, key=key, reverse=descending)


# -- verify ---------------------------------------------------------------------------------------

VerifyMode = Literal["private", "public", "committed"]


@dataclass(frozen=True)
class VerifyOutcome:
    ok: bool
    message: Message
    details: tuple[tuple[str, str], ...] = ()


def document_details(document: LicenseDocument) -> tuple[tuple[str, str], ...]:
    payload = document.payload
    return (
        ("Lisans kimliği / Licence id", payload.license_id),
        ("Lisans sahibi / Licensee", payload.licensee),
        ("Düzenleme / Issued", format_utc_seconds(payload.issued_at)),
        ("Sunucu kimlikleri / Server ids", " ".join(payload.server_ids)),
        ("Kurulum kimliği / Installation id", payload.installation_id or "-"),
        ("Modüller / Modules", module_names(payload.modules)),
        ("Log kaynağı / Log sources", str(payload.log_sources or UNLIMITED_TR)),
        ("Kullanıcı / Users", str(payload.users or UNLIMITED_TR)),
        ("Bakım bitişi / Maintenance until", payload.maintenance_until.isoformat()),
        (
            "Geçerlilik sonu / Valid until",
            payload.valid_until.isoformat() if payload.valid_until else "-",
        ),
        ("Anahtar / Key id", document.key_id),
        ("Dosya SHA-256 / File SHA-256", document.sha256),
    )


def inspect_details(result: issuer.InspectResult) -> tuple[tuple[str, str], ...]:
    """``inspect``: the content without any trust decision."""
    details: list[tuple[str, str]] = [
        ("Dosya / File", str(result.path or "")),
        ("Boyut / Size", f"{result.size} bayt / bytes"),
        ("Dosya SHA-256 / File SHA-256", result.sha256),
        ("İçerik SHA-256 / Payload SHA-256", result.payload_sha256),
    ]
    if result.signature is not None:
        details.append(
            ("İmza / Signature", f"{result.signature.get('alg')} {result.signature.get('key_id')}")
        )
    if result.payload is not None:
        details += [(str(key), str(value)) for key, value in result.payload.items()]
    return tuple(details)


# -- the controller -------------------------------------------------------------------------------


class LicenseManager:
    """The application state behind the window: settings, the decoded request, the issue cycle,
    the registry view and the verification. Every method that refuses raises
    :class:`ToolError`, :class:`LicenseError` or :class:`FormError`; :func:`message_of` turns
    them into texts."""

    def __init__(self, settings_path: Path | None = None, *, today: date | None = None) -> None:
        self.settings_path = settings_path or default_settings_path()
        self.settings = load_settings(self.settings_path)
        self._today = today
        self.request_view: RequestView | None = None
        #: The organisation :meth:`decode` last put into the licensee (replaced by the next
        #: request's organisation unless the owner changed it).
        self._prefilled = ""

    @property
    def today(self) -> date:
        return self._today or today_utc()

    # settings -------------------------------------------------------------------------------

    def save(self) -> None:
        save_settings(self.settings, self.settings_path)

    def set_private_key(self, path: str) -> None:
        check_private_key_path(Path(path))
        self.settings.private_key = path
        self.save()

    def set_registry(self, path: str) -> issuer.Registry:
        registry = check_registry_path(Path(path))
        self.settings.registry = path
        self.save()
        return registry

    def create_registry(self, path: str) -> issuer.Registry:
        registry = issuer.create_registry(Path(path))
        self.settings.registry = path
        self.save()
        return registry

    def set_output_dir(self, path: str) -> None:
        check_output_dir(Path(path))
        self.settings.output_dir = path
        self.save()

    def settings_problems(self) -> list[Message]:
        """What stops the issue screen (every setting is checked again before signing)."""
        problems: list[Message] = []
        if not self.settings.private_key:
            problems.append(
                Message(
                    "Özel anahtar dosyası seçilmedi (Ayarlar).",
                    "no private key file selected (Settings)",
                )
            )
        else:
            _collect(problems, lambda: check_private_key_path(Path(self.settings.private_key)))
        if not self.settings.registry:
            problems.append(
                Message("Kayıt defteri seçilmedi (Ayarlar).", "no registry selected (Settings)")
            )
        else:
            _collect(problems, lambda: check_registry_path(Path(self.settings.registry)))
        if not self.settings.output_dir:
            problems.append(
                Message(
                    "Lisans dosyaları klasörü seçilmedi (Ayarlar).",
                    "no output folder selected (Settings)",
                )
            )
        else:
            _collect(problems, lambda: check_output_dir(Path(self.settings.output_dir)))
        return problems

    def test_key(self, passphrase: str) -> KeyInfo:
        if not self.settings.private_key:
            raise ToolError("Özel anahtar dosyası seçilmedi.", "no private key file selected")
        return test_key(Path(self.settings.private_key), passphrase)

    # request --------------------------------------------------------------------------------

    def decode(self, form: IssueForm) -> RequestView:
        """Decode the request code; the licensee is prefilled from its organisation (also over
        the previous request's organisation, never over a name the owner typed)."""
        self.request_view = None
        view = decode_request_text(form.request_text)
        self.request_view = view
        organization = view.request.organization
        if organization and (not form.licensee or form.licensee == self._prefilled):
            form.licensee = organization
            self._prefilled = organization
        if view.strong:
            form.allow_weak_binding = False
        return view

    def forget_request(self) -> None:
        """After an issue: the next licence starts from an empty form and a new request."""
        self.request_view = None
        self._prefilled = ""

    def registry_rows(self) -> tuple[Mapping[str, str], ...]:
        if not self.settings.registry:
            return ()
        return issuer.read_registry(Path(self.settings.registry)).rows

    def earlier(self, form: IssueForm) -> list[Mapping[str, str]]:
        """The issuer's earlier licences for the decoded request, plus the licence a renewal
        from "Kayıtlar" started from (``form.renewal_of``). A row ticked for another request is
        not carried over: it shows only while it matches the request."""
        if not self.settings.registry:
            return []
        rows: list[Mapping[str, str]] = []
        if self.request_view is not None:
            extra = [item for item in form.extra_ids() if SERVER_ID_PATTERN.fullmatch(item)]
            rows = issuer.earlier_licences(
                Path(self.settings.registry), self.request_view.request, form.licensee.strip(),
                extra,
            )  # fmt: skip
        found = {row["license_id"] for row in rows}
        marked = [
            row
            for row in self.registry_rows()
            if row["license_id"] == form.renewal_of and row["license_id"] not in found
        ]
        return [*rows, *marked]

    # issue ----------------------------------------------------------------------------------

    def validate(self, form: IssueForm) -> issuer.IssuePlan:
        """Every check before the passphrase; all errors at once (:class:`FormError`)."""
        errors = self.settings_problems()
        view: RequestView | None
        if not form.request_text.strip():
            view = None
            errors.append(
                Message(
                    "İstek kodunu yapıştırın ya da dosyadan yükleyin.",
                    "paste the request code or load it from a file",
                )
            )
        else:
            view = _collect(errors, lambda: decode_request_text(form.request_text))
        self.request_view = view
        if view is not None and not view.strong and not form.allow_weak_binding:
            errors.append(
                Message(
                    "Zayıf bağlama: istek Windows'tan SMBIOS UUID ile gelmiyor. Bilerek vermek "
                    'için "Zayıf bağlamaya izin ver" kutusunu işaretleyin.',
                    "weak binding: the request does not come from Windows with an SMBIOS UUID. "
                    'Tick "Allow weak binding" to issue it on purpose.',
                )
            )
        modules = _collect(errors, lambda: issuer.parse_modules(form.modules.option()))
        sources = _collect(
            errors,
            lambda: limit_value(
                form.sources, form.sources_unlimited, "Log kaynağı sınırı", "log sources limit"
            ),
        )
        users = _collect(
            errors,
            lambda: limit_value(
                form.users, form.users_unlimited, "Kullanıcı sınırı", "users limit"
            ),
        )
        _collect(
            errors,
            lambda: _labelled("Lisans sahibi", "licensee", issuer.check_licensee, form.licensee),
        )
        extra = _collect(errors, lambda: issuer.check_server_ids(form.extra_ids()))
        _collect(errors, lambda: _labelled("Not", "note", issuer.check_note, form.note))
        maintenance = _collect(
            errors,
            lambda: day_value(form.maintenance_until, "Bakım bitişi", "maintenance until"),
        )
        valid: date | None = None
        if form.valid_until_enabled:
            valid = _collect(
                errors,
                lambda: _labelled(
                    "Geçerlilik sonu", "valid until", issuer.check_valid_until,
                    day_value(form.valid_until, "Geçerlilik sonu", "valid until"),
                ),
            )  # fmt: skip
        supersedes = (
            list(dict.fromkeys(form.supersedes)) if form.earlier_mode == "supersedes" else []
        )
        if form.earlier_mode == "supersedes" and not supersedes:
            errors.append(
                Message(
                    "Yerine geçtiği lisansları işaretleyin.",
                    "tick the licences this one supersedes",
                )
            )
        replaced = _collect(errors, lambda: issuer.check_license_ids(supersedes))
        if view is not None and not errors:
            earlier = _collect(errors, lambda: self.earlier(form)) or []
            shown = {row["license_id"] for row in earlier}
            rows = _collect(errors, self.registry_rows) or ()
            registered = {row["license_id"] for row in rows}
            unrelated = [item for item in supersedes if item in registered and item not in shown]
            if unrelated:
                errors.append(
                    Message(
                        f"İşaretli lisans bu istekle ilgili değil: {', '.join(unrelated)} (lisans "
                        "sahibi, kuruluş, kurulum ve sunucu farklı). Doğru lisansı işaretleyin.",
                        "the ticked licence does not belong to this request: "
                        f"{', '.join(unrelated)} (other licensee, organisation, installation "
                        "and server). Tick the right licence.",
                    )
                )
            if earlier and form.earlier_mode == "":
                errors.append(
                    Message(
                        'Bu müşteri ya da sunucu için daha önce lisans düzenlenmiş: "Yenileme / '
                        'değiştirme" ya da "Ek sunucu" seçin.',
                        "a licence was issued for this customer or server before: choose "
                        '"supersedes" or "additional server"',
                    )
                )
        if errors:
            raise FormError(errors)
        assert view is not None  # noqa: S101 - no error means the request was decoded
        assert maintenance is not None  # noqa: S101 - likewise
        inputs = issuer.IssueInputs(
            modules=modules,
            max_sources=sources,
            max_users=users,
            maintenance_until=maintenance,
            valid_until=valid,
            extra_server_ids=extra,
            supersedes=replaced,
            licensee=form.licensee,
            key_id=issuer.key_id_of(Path(self.settings.private_key)),
            registry_path=Path(self.settings.registry),
            out=None,
            note=form.note,
            additional_server=form.earlier_mode == "additional",
            allow_weak_binding=form.allow_weak_binding and not view.strong,
        )
        try:
            return issuer.prepare_issue(inputs, view.request)
        except (ToolError, LicenseError, OSError) as exc:
            raise FormError([message_of(exc)]) from exc

    def sign(self, plan: issuer.IssuePlan, passphrase: str) -> issuer.SignedLicence:
        """Open the private key, sign, verify; the key object is dropped right after."""
        key = issuer.open_private_key(Path(self.settings.private_key), passphrase)
        try:
            return issuer.sign_licence(plan, key)
        finally:
            del key

    def suggested_path(self, signed: issuer.SignedLicence) -> Path:
        name = f"{licensee_slug(signed.plan.inputs.licensee)}-{signed.license_id}.lic"
        return Path(self.settings.output_dir) / name

    def write(self, signed: issuer.SignedLicence, path: Path) -> IssueOutcome:
        """Registry row first, then the file (never overwritten here)."""
        issuer.refuse_in_repository(path, "Lisans dosyası", "licence file")
        if path.exists():
            raise ToolError(
                f"{path} zaten var: başka bir ad seçin.", f"{path} exists: choose another name"
            )
        registry = signed.plan.registry.path
        if not issuer.read_registry(registry).exists:  # moved or renamed since the checks
            raise registry_missing(registry)
        result = issuer.write_issued(signed, path)
        rows = {row["license_id"]: row for row in signed.plan.registry.rows}
        revocations = tuple(
            (item, line, revocation_note(rows.get(item), signed.plan.server_ids))
            for item, line in zip(result.supersedes, result.revoked_lines, strict=True)
        )
        return IssueOutcome(result=result, email=email_text(result), revocations=revocations)

    # registry -------------------------------------------------------------------------------

    def registry_views(self) -> list[RegistryRowView]:
        return registry_views(self.registry_rows(), self.today)

    def renewal_form(self, row: Mapping[str, str]) -> IssueForm:
        """ "Yenile / Renew": the row's licensee, modules, limits and extra servers; maintenance
        = old + 1 year (today + 1 year - 1 day when the old one already ended); the row ticked
        as superseded. A new request code is still required."""
        old = _day(row["maintenance_until"]) or self.today
        maintenance = add_years(old, 1) if old >= self.today else maintenance_for(self.today)
        form = IssueForm(
            licensee=row["licensee"],
            modules=ModuleChoice(key for key in row["modules"].split() if key in _MODULES_BY_KEY),
            sources=row["max_sources"] or DEFAULT_LIMIT,
            sources_unlimited=not row["max_sources"],
            users=row["max_users"] or DEFAULT_LIMIT,
            users_unlimited=not row["max_users"],
            maintenance_until=maintenance.isoformat(),
            # The registry lists the request's server first, then the extra ones.
            extra_server_ids="\n".join(row["server_ids"].split()[1:]),
            earlier_mode="supersedes",
            supersedes=[row["license_id"]],
            renewal_of=row["license_id"],
        )
        self.forget_request()
        return form

    def export_registry(self, target: Path) -> Path:
        """A copy of the registry for accounting (the registry itself is never rewritten)."""
        source = Path(self.settings.registry)
        if not source.is_file():
            raise ToolError("Kayıt defteri yok.", "there is no registry")
        if target.resolve() == source.resolve():
            raise ToolError(
                "Kopya kayıt defterinin kendisi olamaz.", "the copy cannot be the registry itself"
            )
        key = self.settings.private_key
        if target.name.casefold().endswith(".private.pem") or (
            key and target.resolve() == Path(key).resolve()
        ):
            raise ToolError(
                "Özel anahtarın üzerine yazılmaz: başka bir ad seçin.",
                "the private key is never overwritten: choose another name",
            )
        if target.exists():
            raise ToolError(
                f"{target} zaten var: başka bir ad seçin.", f"{target} exists: choose another name"
            )
        issuer.refuse_in_repository(target, "Dışa aktarılan dosya", "exported file")
        issuer.read_registry(source)
        shutil.copyfile(source, target)
        return target

    # verify ---------------------------------------------------------------------------------

    def inspect(self, path: Path) -> tuple[tuple[str, str], ...]:
        _need_file(path)
        return inspect_details(issuer.inspect_file(path))

    def verify(
        self,
        path: Path,
        *,
        mode: VerifyMode,
        public_key: str = "",
        passphrase: str = "",
        server_id: str = "",
        at: str = "",
    ) -> VerifyOutcome:
        """``verify``: green with the content, or red with the code and the TR/EN texts."""
        try:
            _need_file(path)
            day = day_value(at, "Tarih", "date") if at.strip() else None
            server = server_id.strip() or None
            if server is not None:
                _labelled("Sunucu kimliği", "server id", issuer.check_server_id, server)
            if mode == "committed":
                result = issuer.verify_file(path, committed=True, server_id=server, at=day)
            elif mode == "public":
                key = _labelled(
                    "Açık anahtar", "public key", issuer.check_public_key, public_key.strip()
                )
                result = issuer.verify_file(path, public_key=key, server_id=server, at=day)
            else:
                if not self.settings.private_key:
                    raise ToolError("Özel anahtar dosyası seçilmedi.", "no private key selected")
                private = issuer.open_private_key(Path(self.settings.private_key), passphrase)
                try:
                    result = issuer.verify_file(path, private_key=private, server_id=server, at=day)
                finally:
                    del private
        except (ToolError, LicenseError, OSError) as exc:
            return VerifyOutcome(ok=False, message=message_of(exc))
        return VerifyOutcome(
            ok=True,
            message=Message(
                "Lisans geçerli. Denetlenen: " + ", ".join(checked_tr(result.checked)),
                "the licence is valid. Checked: " + ", ".join(result.checked),
            ),
            details=document_details(result.document),
        )


def _need_file(path: Path) -> None:
    """An empty path box reaches here as ``Path("")`` (the current folder)."""
    if path == Path():
        raise ToolError("Önce bir lisans dosyası seçin.", "choose a licence file first")


def checked_tr(checked: Sequence[str]) -> list[str]:
    """The issuer's list of checks (``VerifyResult.checked``) in Turkish."""
    words = {"signature": "imza", "revocation": "iptal listesi"}
    texts = []
    for item in checked:
        if item.startswith("server "):
            texts.append("sunucu " + item.removeprefix("server "))
        elif item.startswith("valid_until and maintenance at "):
            day = _day(item.removeprefix("valid_until and maintenance at "))
            texts.append(f"geçerlilik sonu ve bakım ({format_date(day) if day else item})")
        else:
            texts.append(words.get(item, item))
    return texts


def load_request_file(path: Path) -> str:
    """The issuer's ``read_request_file``; a file Notepad saved as "Unicode" (UTF-16 with its
    byte order mark) is read too, anything else that is not UTF-8 is refused with a text."""
    try:
        return issuer.read_request_file(path)
    except UnicodeDecodeError:
        data = path.read_bytes()
        if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
            try:
                return data.decode("utf-16")
            except UnicodeDecodeError:
                pass
        raise ToolError(
            f"İstek dosyası metin olarak okunamadı (UTF-8 bekleniyor): {path}",
            f"the request file is not UTF-8 text: {path}",
        ) from None


def about_text(guide: Path | None) -> Message:
    where = str(guide) if guide is not None else "tools/license-issuer/README.md, BENIOKU.txt"
    return Message(
        f"{APP_NAME}, sürüm {arcilogger_core.__version__}\n"
        f"Hak sahibi: {LICENSOR_NAME} ({LICENSOR_EMAIL})\n"
        "Çevrimdışı çalışır: hiçbir ağ bağlantısı kurmaz, güncelleme denetlemez, telemetri "
        "göndermez. Yalnızca hak sahibinin bilgisayarı içindir; müşterilere verilmez.\n"
        f"Kılavuz: {where}",
        f"{APP_NAME_EN}, version {arcilogger_core.__version__}\n"
        f"Rights holder: {LICENSOR_NAME} ({LICENSOR_EMAIL})\n"
        "Works offline: no network connection, no update check, no telemetry. For the rights "
        "holder's computer only; never given to customers.\n"
        f"Guide: {where}",
    )


def owner_guide(module_file: Path) -> Path | None:
    """README.md next to the script (repository) or ``kilavuz/README.md`` of the zip."""
    here = module_file.resolve()
    candidates = [here.parent / "README.md"]
    if len(here.parents) > 3:
        candidates.append(here.parents[3] / "kilavuz" / "README.md")
    return next((path for path in candidates if path.is_file()), None)
