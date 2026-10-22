"""Test support: a fixed, realistic report data object (schema 1 of the usage engine's
``build_report_data``, group 2 contract section 3) for the renderers and the delivery tests.

One working day (Monday 19.10.2026) at a fictional company (:data:`SITE_NAME`, addresses of the
fictional example network): 17 persons in three departments and one without a department, two
devices without a person, background traffic that nobody can be linked to, blocked attempts of
every source, notable changes against the 7-day average. Every number is derived from the
per-person tables below, so the company totals, the department rows and the detail lists of the
workbook agree with each other, exactly as the engine's output must.

The golden files of the renderers (``worker/tests/snapshots``) are made from this object, not
from the engine's own ``sample_report_data()``, so they do not change when that sample does.
Never used by the services.
"""

from __future__ import annotations

import copy
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Final

from arcilogger_core.timeutil import SITE_TZ

DAY: Final = datetime(2026, 10, 19, tzinfo=SITE_TZ)
GENERATED_AT: Final = datetime(2026, 10, 20, 7, 0, 4, tzinfo=SITE_TZ)
#: The fictional organisation of the tests that need a name (``[site] site_name`` is empty
#: by default: every rendering must also cope without one).
SITE_NAME: Final = "Örnek A.Ş."
MB: Final = 1024 * 1024

#: domain → FortiGuard category (``None``: not rated, shown as "Bilinmiyor").
SITES: Final[dict[str, str | None]] = {
    "outlook.office365.com": "Web-based Email",
    "teams.microsoft.com": "Business",
    "www.youtube.com": "Streaming Media and Download",
    "web.whatsapp.com": "Instant Messaging",
    "www.linkedin.com": "Social Networking",
    "www.instagram.com": "Social Networking",
    "ebeyanname.gib.gov.tr": "Government and Legal Organizations",
    "www.turkiye.gov.tr": "Government and Legal Organizations",
    "internetsubesi.isbank.com.tr": "Finance and Banking",
    "www.sahibinden.com": "Shopping",
    "www.trendyol.com": "Shopping",
    "www.hurriyet.com.tr": "News and Media",
    "www.ntv.com.tr": "News and Media",
    "open.spotify.com": "Streaming Media and Download",
    "drive.google.com": "File Sharing and Storage",
    "stackoverflow.com": "Information Technology",
    "www.google.com": "Search Engines and Portals",
    "translate.google.com": "Search Engines and Portals",
    "static.cdn-ornek.net": None,
}
#: Department → the sites its people use most, most used first.
PROFILES: Final[dict[str | None, tuple[str, ...]]] = {
    "Finans": (
        "ebeyanname.gib.gov.tr",
        "internetsubesi.isbank.com.tr",
        "outlook.office365.com",
        "www.turkiye.gov.tr",
        "teams.microsoft.com",
        "www.google.com",
        "www.youtube.com",
    ),
    "Planlama": (
        "drive.google.com",
        "teams.microsoft.com",
        "outlook.office365.com",
        "stackoverflow.com",
        "www.linkedin.com",
        "translate.google.com",
        "www.instagram.com",
        "open.spotify.com",
    ),
    "İdari İşler": (
        "outlook.office365.com",
        "www.sahibinden.com",
        "www.hurriyet.com.tr",
        "teams.microsoft.com",
        "www.trendyol.com",
        "web.whatsapp.com",
        "www.ntv.com.tr",
        "www.google.com",
    ),
    None: ("www.google.com", "www.youtube.com", "web.whatsapp.com", "translate.google.com"),
}
#: (name, department, first activity hh:mm, last hh:mm, active minutes, off-hours minutes,
#: download MB, upload MB, sessions, blocked attempts, device name, MAC, IP).
PERSONS: Final = (
    ("Elif Kaya", "Finans", "08:02", "17:31", 401, 0, 1250, 330, 2045, 1, "PC-07",
     "f0:1f:af:00:00:01", "172.20.41.120"),
    ("Ahmet Yıldız", "Finans", "08:15", "18:47", 377, 22, 1104, 288, 1888, 0, "MUHASEBE-02",
     "f0:1f:af:00:11:02", "172.20.41.121"),
    ("Gülşen Çetin", "Finans", "09:03", "17:12", 255, 0, 610, 95, 1102, 0, "MUHASEBE-03",
     "f0:1f:af:00:11:03", "172.20.41.122"),
    ("Mehmet Çelik", "Finans", "07:05", "16:10", 142, 0, 3890, 120, 980, 6, "MUHASEBE-04",
     "f0:1f:af:00:11:04", "172.20.41.123"),
    ("Ümit Aksoy", "Finans", "07:12", "15:55", 118, 0, 2760, 88, 812, 3, "MUHASEBE-05",
     "f0:1f:af:00:11:05", "172.20.41.124"),
    ("Ayşe Polat", "Finans", "07:30", "16:22", 97, 0, 1540, 61, 640, 2, "MUHASEBE-06",
     "f0:1f:af:00:11:06", "172.20.41.125"),
    ("Can Öztürk", "Planlama", "07:48", "20:15", 468, 95, 6120, 2410, 2990, 0, "PLANLAMA-01",
     "3c:52:82:00:12:01", "172.20.42.165"),
    ("Selin Aydın", "Planlama", "08:30", "17:59", 392, 0, 4380, 1930, 2210, 2, "PLANLAMA-02",
     "3c:52:82:00:12:02", "172.20.42.166"),
    ("Burak Şahin", "Planlama", "09:12", "18:20", 301, 12, 2205, 610, 1543, 0, "PLANLAMA-03",
     "3c:52:82:00:12:03", "172.20.42.167"),
    ("Deniz Koç", "Planlama", "08:20", "18:35", 355, 15, 2950, 480, 2402, 3, "PLANLAMA-04",
     "3c:52:82:00:12:04", "172.20.42.168"),
    ("Ece Doğan", "Planlama", "08:44", "17:40", 331, 0, 3410, 520, 2155, 5, "PLANLAMA-05",
     "3c:52:82:00:12:05", "172.20.42.169"),
    ("Oğuz Kılıç", "Planlama", "10:05", "21:48", 276, 118, 5280, 390, 1980, 4, "PLANLAMA-06",
     "3c:52:82:00:12:06", "172.20.42.170"),
    ("Murat Ekinci", "İdari İşler", "08:41", "19:52", 312, 46, 1840, 212, 1630, 0,
     "MURAT-LAPTOP", "f0:1f:af:00:10:01", "172.20.43.21"),
    ("Zeynep Arslan", "İdari İşler", "08:55", "18:04", 286, 0, 960, 140, 1210, 0, "ZEYNEP-PC",
     "f0:1f:af:00:10:02", "172.20.43.22"),
    ("Hakan Güneş", "İdari İşler", "08:10", "17:25", 268, 0, 1320, 150, 1407, 2, "OFIS-01",
     "00:15:5d:00:14:01", "172.20.43.30"),
    ("Şule Erdoğan", "İdari İşler", "08:33", "16:58", 219, 0, 870, 98, 1150, 0, "OFIS-02",
     "00:15:5d:00:14:02", "172.20.43.31"),
    ("İrem Yalçın", "İdari İşler", "08:58", "17:05", 244, 0, 1490, 205, 1366, 1, "OFIS-03",
     "00:15:5d:00:14:03", "172.20.43.32"),
    ("Kerem Tunç", None, "09:40", "16:30", 184, 0, 990, 74, 905, 1, "STAJYER-LAPTOP",
     "02:00:5e:00:53:61", "172.20.60.60"),
)  # fmt: skip
#: Devices with traffic but no person (label, MAC, IP, VLAN, active minutes, down MB, up MB).
UNASSIGNED: Final = (
    ("TOPLANTI-ODASI", "00:0c:29:00:17:01", "172.20.43.90", 43, 52, 640, 35),
    ("02:4b:1e:00:17:02", "02:4b:1e:00:17:02", "172.20.60.77", 60, 38, 410, 22),
)
#: Blocked attempts: (hh:mm:ss, person, domain, category, action, source); the person's
#: count in PERSONS matches.
BLOCKED: Final = (
    ("09:14:02", "Elif Kaya", "www.bet365.com", "Gambling", "blocked", "web"),
    ("10:41:37", "Selin Aydın", "thepiratebay.org", "Peer-to-peer File Sharing", "blocked", "web"),
    ("15:02:11", "Selin Aydın", "thepiratebay.org", "Peer-to-peer File Sharing", "blocked", "dns"),
    ("09:30:00", "Deniz Koç", "www.tiktok.com", "Social Networking", "blocked", "app"),
    ("12:15:44", "Deniz Koç", "www.tiktok.com", "Social Networking", "blocked", "app"),
    ("16:48:09", "Deniz Koç", "free-proxy.example.org", "Proxy Avoidance", "blocked", "web"),
    ("11:02:56", "Ece Doğan", "www.bet365.com", "Gambling", "blocked", "web"),
    ("11:03:10", "Ece Doğan", "www.bet365.com", "Gambling", "blocked", "web"),
    ("13:20:31", "Ece Doğan", "www.tiktok.com", "Social Networking", "blocked", "app"),
    ("13:22:05", "Ece Doğan", "www.tiktok.com", "Social Networking", "blocked", "app"),
    ("17:01:42", "Ece Doğan", "torrent.example.net", "Peer-to-peer File Sharing", "blocked", "dns"),
    ("19:12:30", "Oğuz Kılıç", "www.bet365.com", "Gambling", "blocked", "web"),
    ("20:05:11", "Oğuz Kılıç", "www.bet365.com", "Gambling", "blocked", "web"),
    ("20:44:58", "Oğuz Kılıç", "free-proxy.example.org", "Proxy Avoidance", "blocked", "web"),
    ("21:30:16", "Oğuz Kılıç", "malware-c2.example.com", "Malicious Websites", "blocked", "dns"),
    ("14:10:20", "İrem Yalçın", "www.tiktok.com", "Social Networking", "blocked", "app"),
    ("10:05:05", "Hakan Güneş", "www.bet365.com", "Gambling", "blocked", "web"),
    ("15:45:45", "Hakan Güneş", "free-proxy.example.org", "Proxy Avoidance", "blocked", "web"),
    ("08:02:12", "Mehmet Çelik", "torrent.example.net", "Peer-to-peer File Sharing", "blocked",
     "dns"),
    ("09:17:40", "Mehmet Çelik", "torrent.example.net", "Peer-to-peer File Sharing", "blocked",
     "dns"),
    ("10:33:03", "Mehmet Çelik", "www.tiktok.com", "Social Networking", "blocked", "app"),
    ("12:01:19", "Mehmet Çelik", "www.tiktok.com", "Social Networking", "blocked", "app"),
    ("12:44:27", "Mehmet Çelik", "www.bet365.com", "Gambling", "blocked", "web"),
    ("14:58:50", "Mehmet Çelik", "malware-c2.example.com", "Malicious Websites", "blocked",
     "dns"),
    ("09:05:00", "Ümit Aksoy", "www.tiktok.com", "Social Networking", "blocked", "app"),
    ("11:11:11", "Ümit Aksoy", "www.tiktok.com", "Social Networking", "blocked", "app"),
    ("13:13:13", "Ümit Aksoy", "torrent.example.net", "Peer-to-peer File Sharing", "blocked",
     "dns"),
    ("10:20:00", "Ayşe Polat", "www.bet365.com", "Gambling", "blocked", "web"),
    ("14:40:00", "Ayşe Polat", "www.tiktok.com", "Social Networking", "blocked", "app"),
    ("11:50:25", "Kerem Tunç", "free-proxy.example.org", "Proxy Avoidance", "blocked", "web"),
)  # fmt: skip
#: Active seconds of background traffic that no person could be linked to.
UNATTRIBUTED_SECONDS: Final = 5400


def _at(hhmm: str, seconds: int = 0) -> datetime:
    hours, minutes = (int(part) for part in hhmm.split(":")[:2])
    return DAY + timedelta(hours=hours, minutes=minutes, seconds=seconds)


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def _split(total: int, weights: list[int]) -> list[int]:
    """``total`` in parts proportional to ``weights`` (the last part takes the rest)."""
    whole = sum(weights)
    parts = [total * weight // whole for weight in weights[:-1]]
    return [*parts, total - sum(parts)]


def _person_sites(index: int, person: tuple[Any, ...]) -> list[dict[str, Any]]:
    name, department = person[0], person[1]
    active, down, up, sessions = person[4] * 60, person[6] * MB, person[7] * MB, person[8]
    domains = list(PROFILES[department])
    if index % 3 == 0:
        domains.append("static.cdn-ornek.net")
    # The first site gets the most time, then each one less (a shifting pattern per person).
    weights = [(len(domains) - rank) * (3 + (index + rank) % 4) for rank in range(len(domains))]
    seconds = _split(active, weights)
    # Every fourth person downloads most from the sites they use least (updates, large files).
    downloads = _split(down, list(reversed(weights)) if index % 4 == 3 else weights)
    uploads = _split(up, weights)
    counts = _split(sessions, weights)
    rows = [
        {
            "label": name,
            "subject": f"person:{index + 1}",
            "department": department,
            "domain": domain,
            "category": SITES[domain],
            "seconds": seconds[rank],
            "download_bytes": downloads[rank],
            "upload_bytes": uploads[rank],
            "sessions": counts[rank],
        }
        for rank, domain in enumerate(domains)
    ]
    return sorted(rows, key=lambda row: (-row["seconds"], row["domain"]))


def _person_hours(person: tuple[Any, ...]) -> list[dict[str, Any]]:
    first, last = _at(person[2]), _at(person[3])
    hours = []
    moment = first.replace(minute=0)
    while moment <= last:
        hours.append(moment)
        moment += timedelta(hours=1)
    weights = [2 if moment.hour in (12,) else 5 + (moment.hour % 3) for moment in hours]
    seconds = _split(person[4] * 60, weights)
    downloads = _split(person[6] * MB, weights)
    uploads = _split(person[7] * MB, weights)
    return [
        {
            "label": person[0],
            "hour": _iso(moment),
            "active_seconds": min(seconds[i], 3600),
            "download_bytes": downloads[i],
            "upload_bytes": uploads[i],
        }
        for i, moment in enumerate(hours)
    ]


def _top(rows: list[dict[str, Any]], key: str, limit: int) -> list[tuple[str, int]]:
    totals: dict[str, int] = defaultdict(int)
    for row in rows:
        totals[row[key] or "Bilinmiyor"] += row["seconds"]
    return sorted(totals.items(), key=lambda item: (-item[1], item[0]))[:limit]


def report_sample() -> dict[str, Any]:
    """A deep copy of the fixed sample (callers may change it)."""
    return copy.deepcopy(_SAMPLE)


def _build() -> dict[str, Any]:
    person_sites: list[dict[str, Any]] = []
    person_hours: list[dict[str, Any]] = []
    persons: list[dict[str, Any]] = []
    devices: list[dict[str, Any]] = []
    for index, person in enumerate(PERSONS):
        (name, department, first, last, active, off, down, up, sessions, blocked, device) = person[
            :11
        ]
        sites = _person_sites(index, person)
        person_sites += sites
        person_hours += _person_hours(person)
        persons.append(
            {
                "label": name,
                "subject": f"person:{index + 1}",
                "department": department,
                "first_activity": _iso(_at(first)),
                "last_activity": _iso(_at(last)),
                "online_seconds": int((_at(last) - _at(first)).total_seconds()),
                "active_seconds": active * 60,
                "download_bytes": down * MB,
                "upload_bytes": up * MB,
                "sessions": sessions,
                "top_sites": [
                    {"domain": row["domain"], "seconds": row["seconds"]} for row in sites[:20]
                ],
                "top_categories": [
                    {"category": category, "seconds": seconds}
                    for category, seconds in _top(sites, "category", 10)
                ],
                "blocked": blocked,
                "off_hours_active_seconds": off * 60,
            }
        )
        devices.append(
            {
                "label": device,
                "mac": person[11],
                "ip": person[12],
                "vlan": int(person[12].split(".")[2]),
                "person": name,
                "active_seconds": active * 60,
                "download_bytes": down * MB,
                "upload_bytes": up * MB,
            }
        )
    for label, mac, ip, vlan, active, down, up in UNASSIGNED:
        devices.append(
            {
                "label": label,
                "mac": mac,
                "ip": ip,
                "vlan": vlan,
                "person": None,
                "active_seconds": active * 60,
                "download_bytes": down * MB,
                "upload_bytes": up * MB,
            }
        )
    subjects = {entry["label"]: entry["subject"] for entry in persons}
    blocked_rows = sorted(
        (
            {
                "time": _iso(_at(when[:5], int(when[6:]))),
                "label": label,
                "subject": subjects[label],
                "domain": domain,
                "category": category,
                "action": action,
                "source": source,
            }
            for when, label, domain, category, action, source in BLOCKED
        ),
        key=lambda row: row["time"],
        reverse=True,
    )
    for entry in persons:
        count = sum(1 for row in blocked_rows if row["label"] == entry["label"])
        if count != entry["blocked"]:
            raise ValueError(f"sample: {entry['label']} has {count} blocked attempts listed")
    unassigned_active = sum(item[4] * 60 for item in UNASSIGNED)
    unassigned_down = sum(item[5] * MB for item in UNASSIGNED)
    unassigned_up = sum(item[6] * MB for item in UNASSIGNED)
    active_total = sum(p["active_seconds"] for p in persons) + unassigned_active
    active_total += UNATTRIBUTED_SECONDS
    down_total = sum(p["download_bytes"] for p in persons) + unassigned_down
    up_total = sum(p["upload_bytes"] for p in persons) + unassigned_up
    site_totals: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in person_sites:
        for key in ("seconds", "download_bytes", "upload_bytes"):
            site_totals[row["domain"]][key] += row[key]
    top_sites = sorted(site_totals.items(), key=lambda item: (-item[1]["seconds"], item[0]))[:20]
    departments: dict[str, dict[str, Any]] = {}
    for entry in persons:
        name = entry["department"] or "Bilinmiyor"  # the engine's name of the group
        row = departments.setdefault(
            name,
            {
                "name": name,
                "active_users": 0,
                "active_seconds": 0,
                "download_bytes": 0,
                "upload_bytes": 0,
                "blocked": 0,
            },
        )
        row["active_users"] += 1
        for key in ("active_seconds", "download_bytes", "upload_bytes", "blocked"):
            row[key] += entry[key]
    return {
        "schema": 1,
        "kind": "full_day",
        "period": {"from": _iso(DAY), "to": _iso(DAY + timedelta(days=1))},
        "generated_at": _iso(GENERATED_AT),
        "person_level": True,
        "pseudonymized": False,
        "computed_until": _iso(DAY + timedelta(days=1)),
        "company": {
            "active_users": len(persons),
            "online_seconds": sum(p["online_seconds"] for p in persons),
            "active_seconds": active_total,
            "download_bytes": down_total,
            "upload_bytes": up_total,
            "sessions": sum(p["sessions"] for p in persons) + 1330,
            "blocked": len(blocked_rows),
            "off_hours_active_seconds": sum(p["off_hours_active_seconds"] for p in persons),
            "top_sites": [
                {
                    "domain": domain,
                    "category": SITES[domain],
                    "seconds": totals["seconds"],
                    "share": round(totals["seconds"] / active_total, 4),
                    "download_bytes": totals["download_bytes"],
                    "upload_bytes": totals["upload_bytes"],
                }
                for domain, totals in top_sites
            ],
            "top_categories": [
                {
                    "category": category,
                    "seconds": seconds,
                    "share": round(seconds / active_total, 4),
                }
                for category, seconds in _top(person_sites, "category", 20)
            ],
        },
        "departments": sorted(departments.values(), key=lambda d: -d["active_seconds"]),
        "persons": sorted(persons, key=lambda p: -p["active_seconds"]),
        "changes": [
            {
                "scope": "company",
                "metric": "category:Streaming Media and Download",
                "value": 30120,
                "baseline_7d": 10757,
                "change_percent": 180.0,
                "text_tr": "Video ve müzik akışı 7 günlük ortalamanın %180 üzerinde.",
                "text_en": "Streaming is 180 % above the 7-day average.",
            },
            {
                "scope": "department:Planlama",
                "metric": "download_bytes",
                "value": 24345 * MB,
                "baseline_7d": 12485 * MB,
                "change_percent": 95.0,
                "text_tr": "Planlama indirmesi 7 günlük ortalamanın %95 üzerinde.",
                "text_en": "Planning downloads are 95 % above the 7-day average.",
            },
            {
                "scope": "department:İdari İşler",
                "metric": "active_seconds",
                "value": 79740,
                "baseline_7d": 209842,
                "change_percent": -62.0,
                "text_tr": "İdari İşler aktif süresi 7 günlük ortalamanın %62 altında.",
                "text_en": "Administration active time is 62 % below the 7-day average.",
            },
        ],
        "data_quality": {
            "unattributed_seconds": UNATTRIBUTED_SECONDS,
            "notes_tr": [
                "2 cihaz bir kişiye atanmamış; süreleri kişi tablosunda yok (Cihazlar sayfası).",
                "1 sa 30 dk arka plan trafiği hiçbir cihaza bağlanamadı.",
            ],
            "notes_en": [
                "2 devices are not assigned to a person; their time is not in the person table.",
                "1 h 30 min of background traffic could not be linked to any device.",
            ],
        },
        "person_sites": person_sites,
        "person_hours": person_hours,
        "blocked": blocked_rows,
        "blocked_total": len(blocked_rows),
        "devices": devices,
    }


_SAMPLE: Final = _build()
