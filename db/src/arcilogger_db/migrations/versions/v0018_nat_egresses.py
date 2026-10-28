"""Internet exits: the setting ``nat.egresses`` replaces the legacy NAT keys.

Revision ID: 0018_nat_egresses
Revises: 0017_fortigate_dhcp
Create Date: 2026-10-28

ADR-0016 (per-exit NAT, dual WAN; Wave 3 contract 2.2.2, 2.8.2, D18, D32). Settings only, the
same on both storage modes:

* When an administrator set one of the legacy keys ``nat.public_ips``, ``nat.wan_transip`` or
  ``lookup.port_hint`` (a row whose ``updated_by`` is not ``:seed``) and ``nat.egresses`` is
  missing or still the seed's, the three values are converted with a **frozen copy** of
  ``arcilogger_db.egress.legacy_egresses`` and checked with a frozen copy of
  ``validate_egresses`` (migrations never import application code; a test keeps the copies
  equal). A result the rules refuse becomes ``{}`` (the implicit catch-all, pure double NAT) with
  ``conversion_failed``; the log names the rule, never a value. ``nat.egresses`` is written with
  ``updated_by = ':migration'`` and the latest ``updated_at`` of the administrator's legacy rows.
* Two status rows (author ``:migration``, not in ``DEFAULT_SETTINGS``): ``nat.egresses_migration``
  (the legacy values, a number canonical JSON refuses as its text, the result, the dropped
  values with their reason, ``conversion_failed``, ``audited: false``; the seed writes the one
  ``settings.change`` audit entry from it, because no revision writes audit entries) and
  ``nat.egresses_changes`` (one entry per converted exit: its content fingerprint and the time
  and author of the legacy rows' last change).
* Legacy rows owned by the seed are deleted; an administrator's rows are kept as they are,
  unread by this version, so ``Install-Arcilogger.ps1 -Rollback`` without ``-RestoreDatabase``
  runs the previous version with its own NAT values; the first revision after Wave 3 deletes
  them. Nothing converts them again after a rollback (``R20-nat:legacy-changed`` names an edit).

The downgrade (developers and the migration tests; ``-Rollback`` runs none): while
``nat.egresses`` is still the migration's, it is deleted and the kept legacy rows are what the
previous version reads; after an administrator changed it, the legacy rows are rebuilt from the
exits where they allow it (lossy: ``direct`` exits' single addresses, the one ``isp_nat`` exit
with exactly one single NAT address and no interfaces, any ``preserved`` exit), the rest named in
the log. ``nat.egresses`` and both status rows are deleted.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import re
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from ipaddress import IPv4Network
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0018_nat_egresses"
down_revision: str | Sequence[str] | None = "0017_fortigate_dhcp"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

log = logging.getLogger("alembic.runtime.migration")

KEY = "nat.egresses"
MIGRATION_KEY = "nat.egresses_migration"
CHANGES_KEY = "nat.egresses_changes"
LEGACY_KEYS = ("nat.public_ips", "nat.wan_transip", "lookup.port_hint")
#: The shipped defaults of the legacy keys (Wave 2 ``DEFAULT_SETTINGS``).
LEGACY_DEFAULTS: dict[str, Any] = {
    "nat.public_ips": [],
    "nat.wan_transip": "",
    "lookup.port_hint": False,
}
SEED = ":seed"
MIGRATION = ":migration"
#: The largest integer a canonical JSON document holds (``arcilogger_core.canonical``).
MAX_SAFE_INTEGER = 2**53 - 1

# -- frozen copy of arcilogger_db.egress (2026-10-28) ---------------------------------------------

MODES = ("direct", "isp_nat", "not_logged", "internal")
PORT_PRESERVATION = ("unknown", "preserved", "not_preserved")
FIELDS = (
    "label",
    "mode",
    "public",
    "public_dynamic",
    "transips",
    "interfaces",
    "port_preservation",
)
FINGERPRINT_FIELDS = ("mode", "public", "transips", "public_dynamic")
PRIVATE_OR_SHARED = tuple(
    IPv4Network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10")
)
RESERVED = tuple(
    IPv4Network(n)
    for n in ("0.0.0.0/8", "127.0.0.0/8", "169.254.0.0/16", "224.0.0.0/4", "240.0.0.0/4")
)
NOT_PUBLIC = PRIVATE_OR_SHARED + RESERVED
MAX_EGRESSES, MAX_ADDRESSES, MAX_INTERFACES, MAX_LABEL, MAX_INTERFACE_NAME = 8, 16, 8, 64, 35
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
CONTROL = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})
DIRECT_LABEL = "Genel IP (yükseltmede aktarıldı)"
WAN_LABEL = "FortiGate WAN adresi (yükseltmede aktarıldı)"
CATCH_ALL_LABEL = "Genel adresi tanımlanmamış hatlar"


class RefusedError(ValueError):
    """A rule of ``validate_egresses`` refused the value: ``code`` names it."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _control(text: str) -> bool:
    return any(unicodedata.category(char) in CONTROL for char in text)


def _interface_ok(name: Any) -> bool:
    return (
        isinstance(name, str)
        and 1 <= len(name) <= MAX_INTERFACE_NAME
        and name == name.strip()
        and not _control(name)
    )


def validate_egresses(value: Any) -> dict[str, dict[str, Any]]:
    """Frozen ``arcilogger_db.egress.validate_egresses``: the normalised value, or
    :class:`RefusedError` with the code of the first rule it breaks."""
    if not isinstance(value, dict):
        raise RefusedError("setting_type")
    if len(value) > MAX_EGRESSES:
        raise RefusedError("setting_too_many")
    exits = {name: _exit(name, item) for name, item in value.items()}
    _check_across(exits)
    return exits


def _exit(name: Any, value: Any) -> dict[str, Any]:
    if not isinstance(name, str) or not NAME.match(name):
        raise RefusedError("setting_egress_name")
    if not isinstance(value, dict):
        raise RefusedError("setting_egress_field")
    if any(field not in FIELDS for field in value) or "mode" not in value:
        raise RefusedError("setting_egress_field")
    mode = value["mode"]
    if not isinstance(mode, str) or mode not in MODES:
        raise RefusedError("setting_egress_mode")
    label = value.get("label", "")
    if not isinstance(label, str) or len(label) > MAX_LABEL or _control(label):
        raise RefusedError("setting_egress_label")
    public = _networks(value.get("public", []), "public")
    transips = _networks(value.get("transips", []), "transips")
    interfaces = _interfaces(value.get("interfaces", []))
    preservation = value.get("port_preservation", "unknown")
    if not isinstance(preservation, str) or preservation not in PORT_PRESERVATION:
        raise RefusedError("setting_egress_port_preservation")
    dynamic = value.get("public_dynamic", False)
    if not isinstance(dynamic, bool):
        raise RefusedError("setting_egress_field")
    if mode == "direct":
        if not public or transips or preservation != "unknown":
            raise RefusedError("setting_egress_direct")
        if dynamic:
            raise RefusedError("setting_egress_dynamic")
    elif mode == "not_logged":
        if not public or transips or interfaces or preservation != "unknown" or dynamic:
            raise RefusedError("setting_egress_not_logged")
    elif mode == "internal" and (not transips or public or dynamic or preservation != "unknown"):
        raise RefusedError("setting_egress_internal")
    return {
        "label": label,
        "mode": mode,
        "public": [_text(network) for network in public],
        "public_dynamic": dynamic,
        "transips": [_text(network) for network in transips],
        "interfaces": interfaces,
        "port_preservation": preservation,
    }


def _networks(items: Any, field: str) -> list[IPv4Network]:
    if not isinstance(items, list):
        raise RefusedError("setting_egress_field")
    found: list[IPv4Network] = []
    for item in items:
        network = _network(item)
        if field == "public" and any(network.overlaps(space) for space in NOT_PUBLIC):
            raise RefusedError("setting_egress_public_private")
        if network not in found:
            found.append(network)
    if len(found) > MAX_ADDRESSES:
        raise RefusedError("setting_too_many")
    return found


def _network(text: Any) -> IPv4Network:
    if not isinstance(text, str):
        raise RefusedError("setting_egress_address")
    address, slash, prefix = text.partition("/")
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        raise RefusedError("setting_egress_address") from None
    if parsed.version == 6:
        raise RefusedError("setting_ipv6_not_supported")
    if slash and not (prefix.isascii() and prefix.isdigit()):
        raise RefusedError("setting_egress_address")
    bits = int(prefix) if slash else 32
    if not 24 <= bits <= 32:
        raise RefusedError("setting_egress_address")
    try:
        return IPv4Network(f"{parsed}/{bits}")
    except ValueError:
        raise RefusedError("setting_egress_address") from None


def _text(network: IPv4Network) -> str:
    if network.prefixlen == network.max_prefixlen:
        return str(network.network_address)
    return str(network)


def _interfaces(items: Any) -> list[str]:
    if not isinstance(items, list):
        raise RefusedError("setting_egress_field")
    found: list[str] = []
    for item in items:
        if not _interface_ok(item):
            raise RefusedError("setting_interface_invalid")
        if item not in found:
            found.append(item)
    if len(found) > MAX_INTERFACES:
        raise RefusedError("setting_too_many")
    return found


def _overlap(first: list[str], second: list[str]) -> bool:
    return any(IPv4Network(a).overlaps(IPv4Network(b)) for a in first for b in second)


def _check_across(exits: Mapping[str, dict[str, Any]]) -> None:
    for exit_ in exits.values():
        if exit_["mode"] == "isp_nat" and _overlap(exit_["public"], exit_["transips"]):
            raise RefusedError("setting_egress_isp_nat_direct")
    items = list(exits.items())
    pairs = [(a, b) for index, a in enumerate(items) for b in items[index + 1 :]]
    for (_, first), (_, second) in pairs:
        if _overlap(first["public"], second["public"]):
            raise RefusedError("setting_egress_overlap")
    for (_, first), (_, second) in pairs:
        if _overlap(first["public"], second["transips"]) or _overlap(
            second["public"], first["transips"]
        ):
            raise RefusedError("setting_egress_overlap")
        separate = (
            first["interfaces"]
            and second["interfaces"]
            and not set(first["interfaces"]) & set(second["interfaces"])
        )
        if not separate and _overlap(first["transips"], second["transips"]):
            raise RefusedError("setting_egress_overlap")
    if sum(1 for _, e in items if e["mode"] == "isp_nat" and not e["transips"]) > 1:
        raise RefusedError("setting_egress_catch_all")


def fingerprint(exit_: Mapping[str, Any]) -> str:
    """Frozen ``arcilogger_db.egress.fingerprint``."""
    content = {field: exit_[field] for field in FINGERPRINT_FIELDS}
    data = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _v4(text: Any, dropped: list[dict[str, str]]) -> str | None:
    try:
        address = ipaddress.ip_address(str(text).strip())
    except ValueError:
        dropped.append({"value": str(text)[:64], "reason": "invalid"})
        return None
    if address.version != 4:
        dropped.append({"value": str(address), "reason": "ipv6"})
        return None
    if any(address in network for network in RESERVED):
        dropped.append({"value": str(address), "reason": "reserved"})
        return None
    return str(address)


def legacy_egresses(
    public_ips: Any, wan_transip: Any, port_hint: Any
) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
    """Frozen ``arcilogger_db.egress.legacy_egresses`` (contract 2.8.2)."""
    dropped: list[dict[str, str]] = []
    items = public_ips if isinstance(public_ips, list) else [public_ips]
    found = list(dict.fromkeys(a for a in (_v4(x, dropped) for x in items) if a))
    private = [a for a in found if any(ipaddress.ip_address(a) in n for n in PRIVATE_OR_SHARED)]
    keep = [a for a in found if a not in private]
    wan = _v4(wan_transip, dropped) if str(wan_transip or "").strip() else None
    preservation = "preserved" if port_hint is True else "unknown"
    out: dict[str, dict[str, Any]] = {}
    for index in range(0, len(keep), MAX_ADDRESSES):
        out[f"genel-{index // MAX_ADDRESSES + 1}"] = {
            "label": DIRECT_LABEL,
            "mode": "direct",
            "public": keep[index : index + MAX_ADDRESSES],
            "public_dynamic": False,
            "transips": [],
            "interfaces": [],
            "port_preservation": "unknown",
        }
    transips = list(
        dict.fromkeys([*([wan] if wan is not None and wan not in keep else []), *private])
    )
    if transips:
        out["wan"] = {
            "label": WAN_LABEL,
            "mode": "isp_nat",
            "public": [],
            "public_dynamic": False,
            "transips": transips,
            "interfaces": [],
            "port_preservation": preservation,
        }
    elif keep or port_hint is True:
        out["tanimsiz"] = {
            "label": CATCH_ALL_LABEL,
            "mode": "isp_nat",
            "public": [],
            "public_dynamic": False,
            "transips": [],
            "interfaces": [],
            "port_preservation": preservation,
        }
    return out, dropped


# -- the revision ---------------------------------------------------------------------------------


def _rows(bind: sa.Connection, keys: Sequence[str]) -> dict[str, tuple[Any, str, datetime]]:
    found = bind.execute(
        sa.text("SELECT key, value, updated_by, updated_at FROM settings WHERE key = ANY(:keys)"),
        {"keys": list(keys)},
    ).all()
    return {key: (value, author, at) for key, value, author, at in found}


def _upsert(bind: sa.Connection, key: str, value: Any, author: str, at: datetime) -> None:
    bind.execute(
        sa.text(
            "INSERT INTO settings (key, value, updated_by, updated_at) "
            "VALUES (:key, CAST(:value AS jsonb), :author, :at) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, "
            "updated_by = EXCLUDED.updated_by, updated_at = EXCLUDED.updated_at"
        ),
        {"key": key, "value": json.dumps(value), "author": author, "at": at},
    )


def _delete(bind: sa.Connection, keys: Sequence[str], *, author: str | None = None) -> None:
    if author is None:
        bind.execute(sa.text("DELETE FROM settings WHERE key = ANY(:keys)"), {"keys": list(keys)})
    else:
        bind.execute(
            sa.text("DELETE FROM settings WHERE key = ANY(:keys) AND updated_by = :author"),
            {"keys": list(keys), "author": author},
        )


def _iso(at: datetime) -> str:
    return at.astimezone(UTC).isoformat()


def _storable(value: Any) -> Any:
    """``value`` with every number canonical JSON refuses (a float, an integer beyond
    ±(2^53 − 1)) as its text: the legacy rows were stored without validation (2.8.2 (g)), and
    the seed copies the status row's ``from`` into the audit entry, whose details are canonical
    (a refused entry would stop every later upgrade at the seed)."""
    if isinstance(value, float) or (
        isinstance(value, int) and not isinstance(value, bool) and abs(value) > MAX_SAFE_INTEGER
    ):
        return str(value)
    if isinstance(value, dict):
        return {key: _storable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_storable(item) for item in value]
    return value


def upgrade() -> None:
    bind = op.get_bind()
    rows = _rows(bind, [*LEGACY_KEYS, KEY])
    legacy = {key: rows[key] for key in LEGACY_KEYS if key in rows}
    administrator = {key: row for key, row in legacy.items() if row[1] != SEED}
    current = rows.get(KEY)
    if administrator and (current is None or current[1] == SEED):
        _convert(bind, legacy, administrator)
    _delete(bind, LEGACY_KEYS, author=SEED)


def _convert(
    bind: sa.Connection,
    legacy: Mapping[str, tuple[Any, str, datetime]],
    administrator: Mapping[str, tuple[Any, str, datetime]],
) -> None:
    values = {
        key: legacy[key][0] if key in legacy else default
        for key, default in LEGACY_DEFAULTS.items()
    }
    _value, latest_by, latest_at = max(administrator.values(), key=lambda row: row[2])
    exits, dropped = legacy_egresses(
        values["nat.public_ips"], values["nat.wan_transip"], values["lookup.port_hint"]
    )
    failed = False
    try:
        exits = validate_egresses(exits)
    except RefusedError as refused:
        log.warning(
            "0018_nat_egresses: the converted internet exits break the rule %s; nat.egresses "
            "is left empty (one implicit double-NAT exit) and R20-nat:conversion-failed asks "
            "for the exits to be entered",
            refused.code,
        )
        exits, failed = {}, True
    _upsert(bind, KEY, exits, MIGRATION, latest_at)
    status = {
        "migration": revision,
        "from": _storable(values),
        "new": exits,
        "dropped": dropped,
        "conversion_failed": failed,
        "audited": False,
    }
    _upsert(bind, MIGRATION_KEY, status, MIGRATION, datetime.now(UTC))
    changes = {
        name: {
            "fingerprint": fingerprint(exit_),
            "changed_at": _iso(latest_at),
            "changed_by": latest_by,
            "deleted": False,
        }
        for name, exit_ in exits.items()
    }
    _upsert(bind, CHANGES_KEY, changes, MIGRATION, datetime.now(UTC))


def downgrade() -> None:
    bind = op.get_bind()
    current = _rows(bind, [KEY]).get(KEY)
    if current is not None and not current[1].startswith(":"):
        _rebuild_legacy(bind, current[0])
    _delete(bind, [KEY, MIGRATION_KEY, CHANGES_KEY])


def _rebuild_legacy(bind: sa.Connection, value: Any) -> None:
    """The legacy rows from exits an administrator changed after the upgrade, where they allow
    it (the previous version reads only these); everything else is named in the log."""
    try:
        exits = validate_egresses(value)
    except RefusedError as refused:
        log.warning(
            "0018_nat_egresses: nat.egresses breaks the rule %s; not converted back", refused.code
        )
        return
    public_ips: list[str] = []
    wan_transip = ""
    port_hint = False
    lost: list[str] = []
    for name, exit_ in sorted(exits.items()):
        port_hint = port_hint or exit_["port_preservation"] == "preserved"
        mode, public, transips = exit_["mode"], exit_["public"], exit_["transips"]
        if mode == "direct":
            public_ips += [a for a in public if "/" not in a and a not in public_ips]
            if any("/" in a for a in public):
                lost.append(f"{name} (public networks)")
            continue
        wan = (
            mode == "isp_nat"
            and len(transips) == 1
            and "/" not in transips[0]
            and not exit_["interfaces"]
            and not wan_transip
        )
        if wan:
            wan_transip = transips[0]
        elif not (mode == "isp_nat" and not transips):  # the catch-all is the implicit default
            lost.append(f"{name} ({mode})")
            continue
        if public:
            lost.append(f"{name} (public)")
    if lost:
        log.warning(
            "0018_nat_egresses: exits the previous version cannot read were dropped: %s",
            ", ".join(lost),
        )
    now = datetime.now(UTC)
    for key, legacy_value in (
        ("nat.public_ips", public_ips),
        ("nat.wan_transip", wan_transip),
        ("lookup.port_hint", port_hint),
    ):
        _upsert(bind, key, legacy_value, MIGRATION, now)
