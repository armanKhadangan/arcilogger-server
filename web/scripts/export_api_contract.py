"""Write the API contract the web app is checked against (run from the repository root).

    uv run python web/scripts/export_api_contract.py          # regenerate
    uv run python web/scripts/export_api_contract.py --check  # exit 1 when out of date

Two files under ``web/src/generated/`` (both committed, both generated — never edit by hand):

* ``api-schemas.json`` — the ``components.schemas`` and the paths of the API's OpenAPI document.
  ``web/src/lib/api/types.test.ts`` compares every TypeScript type in ``web/src/lib/api/types.ts``
  with it (field names, required/optional, null, enums, nested types), so a renamed or retyped
  field in a Pydantic model fails the web tests instead of a page at run time.
* ``roles.json`` — roles, their labels and permissions (``arcilogger_api.security``) and the
  permissions that require the KVKK notice. The web app reads it for the permission-filtered menu
  and the read-only role matrix, so the matrix shown is the one the server enforces.
* ``audit-vectors.json`` — golden audit-log entries as ``GET /api/v1/audit`` returns them, with the
  canonical JSON and hash computed by the server's own code (``arcilogger_api.audit``). The web
  app recomputes both in the browser (the detail drawer of "Denetim Kaydı"); its unit tests and
  the mocked E2E API use these vectors, so the browser's canonical form is checked against the
  server's (escapes, key order by UTF-16 code units, times, nulls), not against itself.

The web CI job has no Python; run ``--check`` in the Python job (or before committing an API
change) so the snapshot cannot drift from the API.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from arcilogger_api.app import create_app
from arcilogger_api.audit import GENESIS_HASH, canonical_entry, entry_hash
from arcilogger_api.auth.accounts import PRIVACY_PERMISSIONS
from arcilogger_api.routers.audit import AuditEntry
from arcilogger_api.security import (
    PERMISSIONS,
    RECENT_AUTH_MINUTES,
    ROLE_LABELS,
    ROLE_PERMISSIONS,
)
from arcilogger_core.canonical import canonical_json

OUT_DIR = Path(__file__).resolve().parent.parent / "src" / "generated"
#: Number of golden audit entries (more than one API page of 100, for "daha fazla yükle").
AUDIT_VECTOR_COUNT = 140


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


#: (actor_id, actor, action, target, details, ip) of the first entries: the edge cases of the
#: canonical form. The rest repeat ordinary actions.
_SPECIAL: list[tuple[int | None, str | None, str, str | None, dict[str, Any], str | None]] = [
    (None, "system", "setup.token_create", None, {"os_user": "Administrator"}, None),
    (
        None,
        "system",
        "setup.admin_create",
        "user:1",
        {"role": "admin", "username": "tolga"},
        "172.20.10.37",
    ),
    (1, "tolga", "auth.login", "user:1", {"next": "totp_setup", "rehashed": False}, "172.20.10.37"),
    (
        1,
        "tolga",
        "auth.totp_enrol",
        "user:1",
        {"recovery_codes": 10, "session": "4f2a9c1b7e3d"},
        "172.20.10.37",
    ),
    (
        None,
        "system",
        "auth.login_failed",
        None,
        {"reason": "unknown_user"},
        "2001:db8::10",
    ),
    (
        1,
        "tolga",
        "user.create",
        "user:2",
        {"active": True, "role": "auditor", "username": "ayse.yilmaz"},
        "172.20.10.37",
    ),
    (
        1,
        "tolga",
        "user.update",
        "user:2",
        {
            "changes": {
                "display_name": {"new": "Ayşe Yılmaz — Denetim", "old": "Ayşe Yılmaz"},
                "email": {"new": None, "old": "ayse@ornek.com.tr"},
            },
        },
        "172.20.10.37",
    ),
    (
        1,
        "tolga",
        "settings.change",
        "setting:site.name",
        {
            # Escapes and key order of RFC 8785: quote, backslash, control characters,
            # U+2028, non-BMP characters (UTF-16 order differs from code point order).
            "new": 'Örnek "A.Ş."\\ İzmir\n\t\u0001\u2028',
            "old": None,
            "z": [1, -2, 9007199254740991, True, False, None, {}, []],
            "\u00e9": "é",
            "\U0001f600": "smile",
            "\uff61": "halfwidth",
            "A": {"b": 1, "B": 2, "a": 3},
        },
        "172.20.10.37",
    ),
    (2, "ayse.yilmaz", "auth.reauth_failed", "user:2", {"reason": "bad_code"}, "172.20.42.165"),
    (
        None,
        "system",
        "auth.lockout",
        "user:2",
        {"attempts": 5, "lock": 1, "minutes": 15, "step": "reauth", "username": "ayse.yilmaz"},
        "172.20.42.165",
    ),
]
_ROUTINE: list[tuple[int | None, str | None, str, str | None, dict[str, Any], str | None]] = [
    (1, "tolga", "auth.login", "user:1", {"next": "totp", "rehashed": False}, "172.20.10.37"),
    (1, "tolga", "auth.totp", "user:1", {"method": "totp", "session": "9b1c"}, "172.20.10.37"),
    (2, "ayse.yilmaz", "auth.logout", "user:2", {"session": "77aa"}, "172.20.42.165"),
    (1, "tolga", "user.unlock", "user:2", {"was_locked": True}, "172.20.10.37"),
    (2, "ayse.yilmaz", "privacy.accept", "user:2", {"version": "1"}, "172.20.42.165"),
]


def audit_vectors() -> list[dict[str, Any]]:
    """Golden entries, oldest first: API form + the server's canonical JSON and hash."""
    start = datetime(2026, 10, 12, 5, 0, tzinfo=UTC)
    vectors: list[dict[str, Any]] = []
    prev_hash = GENESIS_HASH
    for index in range(AUDIT_VECTOR_COUNT):
        actor_id, actor, action, target, details, ip = (
            _SPECIAL[index] if index < len(_SPECIAL) else _ROUTINE[index % len(_ROUTINE)]
        )
        # Whole seconds for some entries: the API then omits the fraction ("…:07Z").
        micro = 0 if index % 4 == 0 else (index * 7919) % 1_000_000
        time = start + timedelta(seconds=index * 137, microseconds=micro)
        entry_id = index + 1
        entry = canonical_entry(
            entry_id=entry_id,
            time=time,
            actor_id=actor_id,
            actor=actor,
            action=action,
            target=target,
            details=details,
            ip=ip,
        )
        hashed = entry_hash(prev_hash, entry)
        api = AuditEntry(
            id=entry_id,
            time=time,
            actor_id=actor_id,
            actor=actor,
            action=action,
            target=target,
            target_masked=False,
            details=details,
            ip=ip,
            prev_hash=prev_hash,
            hash=hashed,
        ).model_dump(mode="json")
        vectors.append({"api": api, "canonical": canonical_json(entry).decode("utf-8")})
        prev_hash = hashed
    return vectors


def contract_files() -> dict[str, str]:
    """File name → content (deterministic: sorted keys, LF line ends)."""
    openapi = create_app().openapi()
    schemas = {
        "info": "generated by web/scripts/export_api_contract.py; do not edit",
        "paths": {path: sorted(methods) for path, methods in openapi["paths"].items()},
        "schemas": openapi["components"]["schemas"],
    }
    roles = {
        "info": "generated by web/scripts/export_api_contract.py; do not edit",
        "roles": [
            {
                "id": role,
                "label_tr": ROLE_LABELS[role][0],
                "label_en": ROLE_LABELS[role][1],
                "permissions": sorted(ROLE_PERMISSIONS[role]),
            }
            for role in ROLE_LABELS
        ],
        "permissions": [
            {"id": name, "label_tr": texts[0], "label_en": texts[1]}
            for name, texts in PERMISSIONS.items()
        ],
        "privacy_permissions": sorted(PRIVACY_PERMISSIONS),
        "recent_auth_minutes": RECENT_AUTH_MINUTES,
    }
    vectors = {
        "info": "generated by web/scripts/export_api_contract.py; do not edit",
        "entries": audit_vectors(),
    }
    return {
        "api-schemas.json": _dump(schemas),
        "roles.json": _dump(roles),
        "audit-vectors.json": _dump(vectors),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="only compare, write nothing")
    args = parser.parse_args(argv)
    stale = []
    for name, content in contract_files().items():
        path = OUT_DIR / name
        current = path.read_bytes().decode("utf-8") if path.exists() else None
        if current == content:
            continue
        stale.append(name)
        if not args.check:
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content.encode("utf-8"))
    if args.check and stale:
        print(f"out of date: {', '.join(stale)} - run web/scripts/export_api_contract.py")
        return 1
    print("written: " + ", ".join(stale) if stale else "up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
