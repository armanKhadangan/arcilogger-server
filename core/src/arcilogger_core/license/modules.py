"""The module registry: what a licence can switch on (licensing contract 1.1).

``core_5651`` (5651 Temel) is in every licence and every state. The other modules are features
without a 5651 role (usage analytics, scheduled reports, bulk CSV import); everything the 5651
obligations need stays in ``core_5651`` and is never gated. A licence may list keys a newer
version adds: an older version keeps and ignores them.

Prices are not in the code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

CORE_MODULE: Final = "core_5651"


@dataclass(frozen=True)
class Module:
    key: str
    name_tr: str
    name_en: str
    description_tr: str
    description_en: str
    requires: tuple[str, ...] = ()
    always: bool = False


MODULES: Final[tuple[Module, ...]] = (
    Module(
        key=CORE_MODULE,
        name_tr="5651 Temel",
        name_en="5651 Core",
        description_tr=(
            "Log toplama, mühürleme, saklama, 5651 sorgusu, kanıt paketleri, cihazlar ve kişiler, "
            "uyarılar, denetim kaydı."
        ),
        description_en=(
            "Log collection, sealing, retention, 5651 lookups, evidence packages, devices and "
            "persons, alerts, audit log."
        ),
        always=True,
    ),
    Module(
        key="usage_analytics",
        name_tr="Kullanım analizi",
        name_en="Usage analytics",
        description_tr=(
            "Kişi, cihaz, site ve kategori bazında tahmini kullanım süreleri ve dışa aktarma."
        ),
        description_en="Estimated usage time per person, device, site and category, with exports.",
    ),
    Module(
        key="usage_reports",
        name_tr="Zamanlanmış kullanım raporları",
        name_en="Scheduled usage reports",
        description_tr=(
            "Kullanım raporlarının zamanlanmış olarak üretilip e-postayla gönderilmesi "
            "(Kullanım analizi gerekir)."
        ),
        description_en=(
            "Scheduled usage reports made and e-mailed by the server (needs Usage analytics)."
        ),
        requires=("usage_analytics",),
    ),
    Module(
        key="bulk_import",
        name_tr="Toplu içe aktarma (CSV)",
        name_en="Bulk import (CSV)",
        description_tr=(
            "Kişilerin ve cihaz atamalarının CSV dosyasından toplu içe aktarılması (elle giriş "
            "her zaman açıktır)."
        ),
        description_en=(
            "Bulk import of persons and device assignments from CSV files (manual entry is "
            "always available)."
        ),
    ),
)

_BY_KEY: Final = {item.key: item for item in MODULES}


def module(key: str) -> Module:
    """The registry entry of ``key``; ``KeyError`` for a key this version does not know."""
    return _BY_KEY[key]


def module_keys() -> tuple[str, ...]:
    """Every registry key, in registry order."""
    return tuple(item.key for item in MODULES)
