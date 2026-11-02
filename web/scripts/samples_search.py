"""The Arama samples (``web/e2e/samples/search.json``), captured from the real search API.

``capture_api_samples.py`` calls :func:`capture` on its main site after the other pages' samples,
with the capture (the app, its database and data directory, the request helpers ``json``,
``error``, ``file``) and three signed-in clients: an administrator, an auditor and an
administrator who has not accepted the KVKK notice. It returns ``{sample file name: data}``; a
file whose data is empty is not written (and ``--check`` expects none).

What ``search.json`` holds (contract 2.10, 2.22.2), all on the site day 09.10.2026 of the main
capture (the example chain's DHCP, DNS, web and traffic lines, the ARP conflict):

* ``fields``, ``parse`` (a typed and a free-text query, refused ones with their spans);
* ``search``: runs by the administrator (typed rows, free text, browsing with a next page, an
  empty result, a scan the request budget cut with its frontier cursor, a saved search) and by
  the auditor;
* ``stats`` in the three modes (``rollup`` over the counted day, ``hybrid`` with partial edge
  hours, ``direct`` for an hour) and one the statistics budget cut;
* ``event`` (the drawer of a traffic, a DHCP and a ``messages``-only row), ``refs``, ``export``
  (CSV, Excel and a file cut at the row limit: headers, size and SHA-256, no content) and
  ``saved`` (create, list, rename, delete);
* ``errors``: every refusal the page shows (query errors with ``details``, range, cursor,
  expired search, KVKK notice, permissions, re-authentication, busy, rate limits, the export
  lock, saved-search conflicts, 504 ``search_timeout``).

The main capture writes no ``messages`` rows: :func:`site_day_ingested` loads them from the
site day's files with ingest's own code and fills ``search_counts`` for the time of the capture.
The search ids are random: they are replaced by ``ornek-arama-NN``
everywhere in the file, so a mock that checks the follow-up bodies still matches.
"""

from __future__ import annotations

import gzip
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, insert, text

from arcilogger_api.rawlines import locate
from arcilogger_api.routers.search import SearchState, state_of
from arcilogger_core.timeutil import SITE_TZ, day_window
from arcilogger_db.models import Message, SearchCount
from arcilogger_db.searchcounts import rebuild_stream
from arcilogger_db.settings import DEFAULT_SETTINGS
from arcilogger_ingest.prepare import StreamInfo, prepare_chunk

if TYPE_CHECKING:
    from capture_api_samples import Capture

#: Keys this module's files compare differently, added to the script's defaults:
#: ``{file: (data-map keys, enum keys)}`` (``capture_api_samples.MAP_KEYS``, ``ENUM_KEYS``).
#: ``fields`` of the event drawer are a row's columns (per table); the row kinds, the drivers, the
#: facet and notice codes and the export's truncation are what the page maps to labels.
SHAPE_KEYS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "search": (
        frozenset({"fields", "extra"}),
        frozenset({"driver", "truncated", "table", "field"}),
    )
}

API = "/api/v1/search"
#: The main capture's site day and addresses (``capture_api_samples``: the PC of the example
#: chain on VLAN 42 and the staff phone on the Wi-Fi).
SITE_DAY = date(2026, 10, 9)
PC = "172.20.42.57"
PHONE_HOST = "www.google.com"
#: The settings this module changes; put back to their defaults at the end.
CHANGED = (
    "search.runs_per_minute",
    "search.request_budget_ms",
    "search.stats_budget_ms",
    "search.export_max_rows",
    "search.export_max_per_hour",
    "search.saved_max_per_user",
)


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(SITE_DAY.year, SITE_DAY.month, SITE_DAY.day, hour, minute, tzinfo=SITE_TZ)


def _range(start: datetime, end: datetime) -> dict[str, str]:
    return {"from": start.isoformat(), "to": end.isoformat()}


DAY = _range(*day_window(SITE_DAY))


class FakeClock:
    """A budget clock that moves ``step`` seconds on every reading (a scan the budget cuts)."""

    def __init__(self, step: float) -> None:
        self.step = step
        self.now = 0.0

    def __call__(self) -> float:
        self.now += self.step
        return self.now


@contextmanager
def _tuned(state: SearchState, **values: Any) -> Iterator[None]:
    """``state``'s clock or window hook replaced inside the block."""
    saved = {name: getattr(state, name) for name in values}
    for name, value in values.items():
        setattr(state, name, value)
    try:
        yield
    finally:
        for name, value in saved.items():
            setattr(state, name, value)


def _slow_after_the_first(calls: list[datetime]) -> Any:
    """A window hook: every window after the first sleeps past the deadline."""

    def hook(session: Any, lower: datetime, upper: datetime) -> None:
        calls.append(lower)
        if len(calls) > 1:
            session.execute(text("SELECT pg_sleep(5)"))

    return hook


def _always_slow(session: Any, lower: datetime, upper: datetime) -> None:
    session.execute(text("SELECT pg_sleep(5)"))


@contextmanager
def site_day_ingested(capture: Capture) -> Iterator[None]:
    """The site day as ingest leaves it, for the block: the main capture writes the typed rows
    and the spool lines (``worker.testing.Evidence``) but no ``messages`` rows, so its files are
    prepared again by ingest's own code (``prepare_chunk``) and their ``messages`` rows inserted,
    then ``search_counts`` is filled (``rebuild_stream``, the counts step). Both are removed
    afterwards: the next sample modules see the site as it was."""
    types = {source.name: source.type for source in capture.config.sources}
    with capture.factory() as session:
        ids: list[int] = []
        for source in sorted(types):
            found = locate(capture.layout, source, SITE_DAY)
            if found is None:
                continue
            origin, path = found
            data = path.read_bytes()
            if origin == "vault":
                data = gzip.decompress(data)
            chunk = prepare_chunk(StreamInfo(source, types[source], SITE_DAY), 0, 0, data)
            rows = [
                values for line in chunk.lines for table, values in line.rows if table == "messages"
            ]
            if rows:
                ids += session.scalars(insert(Message).values(rows).returning(Message.id)).all()
            rebuild_stream(session.connection(), source, SITE_DAY)
        if not ids:
            raise SystemExit(f"no spool line of {SITE_DAY} to search")
        session.commit()
    try:
        yield
    finally:
        with capture.factory() as session:
            session.execute(delete(SearchCount).where(SearchCount.raw_day == SITE_DAY))
            session.execute(delete(Message).where(Message.id.in_(ids)))
            session.commit()


def capture(capture: Capture, admin: Any, auditor: Any, newcomer: Any) -> dict[str, Any]:
    """The search samples (see the module text)."""
    with site_day_ingested(capture):
        data = _capture(capture, admin, auditor, newcomer)
    return {"search": _stable_ids(data)}


def _capture(capture: Capture, admin: Any, auditor: Any, newcomer: Any) -> dict[str, Any]:
    capture.setting("search.runs_per_minute", 600)
    state = state_of(capture.app)
    out: dict[str, Any] = {}
    errors: dict[str, Any] = {}

    def run(client: Any, q: str, span: dict[str, str] = DAY, **extra: Any) -> dict[str, Any]:
        found: dict[str, Any] = capture.json(client.post(API, json={"q": q, **span, **extra}), 200)
        return found

    def stats(client: Any, search_id: str) -> dict[str, Any]:
        found: dict[str, Any] = capture.json(
            client.post(f"{API}/stats", json={"search_id": search_id}), 200
        )
        return found

    # -- the query language
    out["fields"] = capture.json(admin.get(f"{API}/fields"), 200)
    parse = f"{API}/parse"
    out["parse"] = {
        "typed": capture.json(admin.post(parse, json={"q": f"srcip:{PC} type:traffic"}), 200),
        "text": capture.json(admin.post(parse, json={"q": '"PLN-PC14" -dhcp42'}), 200),
        "refused": capture.json(admin.post(parse, json={"q": "srcip:172.20.42.999"}), 200),
        "unknown_field": capture.json(admin.post(parse, json={"q": "istemciip:172.20.42.57"}), 200),
    }

    # -- runs and pages
    runs: dict[str, Any] = {}
    runs["typed"] = run(admin, f"srcip:{PC}")
    runs["text"] = run(admin, PHONE_HOST)
    runs["first_page"] = run(admin, "", limit=5)
    runs["next_page"] = capture.json(
        admin.post(
            f"{API}/page",
            json={
                "search_id": runs["first_page"]["search_id"],
                "cursor": runs["first_page"]["next_cursor"],
            },
        ),
        200,
    )
    runs["empty"] = run(admin, "srcip:172.20.42.250")
    capture.setting("search.request_budget_ms", 600)
    calls: list[datetime] = []
    with _tuned(state, hook=_slow_after_the_first(calls)):
        runs["partial"] = run(admin, f"srcip:{PC}", _range(day_window(SITE_DAY)[0], _at(11, 30)))
    capture.setting("search.request_budget_ms", DEFAULT_SETTINGS["search.request_budget_ms"])
    if runs["partial"]["scan"]["complete"] or not runs["partial"]["items"]:
        raise SystemExit("the cut scan must be partial and show the first window's rows")
    runs["auditor"] = run(auditor, f"srcip:{PC} type:web")
    out["search"] = runs

    # -- statistics
    counted: dict[str, Any] = {}
    counted["rollup"] = stats(admin, run(admin, "")["search_id"])
    hybrid = run(admin, "type:traffic", _range(_at(0, 30), _at(23, 30)))
    counted["hybrid"] = stats(admin, hybrid["search_id"])
    counted["direct"] = stats(
        admin, run(admin, f"srcip:{PC}", _range(_at(11), _at(12)))["search_id"]
    )
    capture.setting("search.stats_budget_ms", 1000)
    cut = run(admin, "srcip:*")
    with _tuned(state, clock=FakeClock(0.15)):
        counted["partial"] = stats(admin, cut["search_id"])
    capture.setting("search.stats_budget_ms", DEFAULT_SETTINGS["search.stats_budget_ms"])
    counted["auditor"] = stats(auditor, runs["auditor"]["search_id"])
    expected = {"rollup": "rollup", "hybrid": "hybrid", "direct": "direct", "partial": "direct"}
    for name, mode in expected.items():
        if counted[name]["mode"] != mode:
            raise SystemExit(f"stats {name}: expected mode {mode}, got {counted[name]['mode']}")
    if counted["partial"]["complete"]:
        raise SystemExit("the cut statistics must be partial")
    out["stats"] = counted

    # -- one row
    def event(item: dict[str, Any]) -> dict[str, Any]:
        params = {"table": item["table"], "time": item["time"], "id": item["key"].split(":")[-1]}
        found: dict[str, Any] = capture.json(admin.get(f"{API}/event", params=params), 200)
        return found

    def first(page: dict[str, Any], table: str) -> dict[str, Any]:
        for item in page["items"]:
            if item["table"] == table:
                found: dict[str, Any] = item
                return found
        raise SystemExit(f"no {table} row in the run")

    dhcp = run(admin, f"type:dhcp ip:{PC}")
    out["event"] = {
        "traffic": event(first(runs["typed"], "fg_traffic")),
        "dhcp": event(first(dhcp, "mt_dhcp")),
        "message": event(first(runs["text"], "messages")),
    }

    # -- evidence references and export
    out["refs"] = capture.json(
        admin.post(f"{API}/refs", json={"search_id": runs["typed"]["search_id"]}), 200
    )

    def exported(search_id: str, fmt: str) -> dict[str, Any]:
        response = admin.post(f"{API}/export", json={"search_id": search_id, "format": fmt})
        facts: dict[str, Any] = capture.file(response)
        for name in ("content-length", "x-arci-export-truncated"):
            if name in response.headers:
                facts["headers"][name] = response.headers[name]
        return facts

    capture.recent(admin)
    out["export"] = {
        "csv": exported(runs["typed"]["search_id"], "csv"),
        "xlsx": exported(runs["typed"]["search_id"], "xlsx"),
    }
    capture.setting("search.export_max_rows", 3)
    out["export"]["truncated"] = exported(runs["typed"]["search_id"], "csv")
    capture.setting("search.export_max_rows", DEFAULT_SETTINGS["search.export_max_rows"])

    # -- saved searches
    saved_url = f"{API}/saved"
    body = {"name": "Planlama PC trafiği", "q": f"srcip:{PC} type:traffic", "range": DAY}
    created = capture.json(admin.post(saved_url, json=body), 201)
    preset = {"name": "Son 24 saat engellenen web", "q": "type:web action:blocked"}
    second = capture.json(admin.post(saved_url, json={**preset, "range": {"preset": "24h"}}), 201)
    renamed = {**body, "name": "Planlama PC — oturumlar"}
    out["saved"] = {
        "created": created,
        "created_preset": second,
        "list": capture.json(admin.get(saved_url), 200),
        "updated": capture.json(admin.put(f"{saved_url}/{created['id']}", json=renamed), 200),
        "run": run(admin, "", {}, saved_id=created["id"]),
        "auditor_list": capture.json(auditor.get(saved_url), 200),
    }
    deleted = admin.delete(f"{saved_url}/{second['id']}")
    if deleted.status_code != 204:
        raise SystemExit(f"DELETE saved search: {deleted.status_code} {deleted.text[:500]}")
    out["saved"]["deleted_status"] = deleted.status_code
    out["saved"]["list_after_delete"] = capture.json(admin.get(saved_url), 200)

    # -- refusals
    def refused(response: Any, status: int, code: str) -> None:
        errors[code] = capture.error(response, status, code)

    refused(admin.post(API, json={"q": 'srcip:"172.20', **DAY}), 422, "search_syntax")
    refused(
        admin.post(API, json={"q": "istemciip:172.20.42.57", **DAY}), 422, "search_unknown_field"
    )
    refused(admin.post(API, json={"q": "srcip:172.20.42.999", **DAY}), 422, "search_value_invalid")
    refused(admin.post(API, json={"q": "ab", **DAY}), 422, "search_term_too_short")
    refused(
        admin.post(API, json={"q": f"srcip:{PC} OR {PHONE_HOST}", **DAY}),
        422,
        "search_or_unsupported",
    )
    many = " ".join(f"srcport:{50000 + index}" for index in range(17))
    refused(admin.post(API, json={"q": many, **DAY}), 422, "search_too_complex")
    refused(
        admin.post(API, json={"q": "type:dhcp qname:www.example.com", **DAY}),
        422,
        "search_fields_incompatible",
    )
    refused(admin.post(API, json={"q": "vlan:999", **DAY}), 422, "search_vlan_unknown")
    refused(admin.post(API, json={"q": "department:Depo", **DAY}), 422, "search_department_unknown")
    backwards = _range(_at(12), _at(11))
    refused(admin.post(API, json={"q": "", **backwards}), 422, "search_range_invalid")
    long = _range(_at(0) - timedelta(days=40), _at(0))
    refused(admin.post(API, json={"q": "", **long}), 422, "search_range_too_long")
    refused(admin.post(API, json={"q": "", **DAY, "limit": 600}), 422, "validation_error")
    page = {"search_id": runs["typed"]["search_id"], "cursor": "bozuk-imlec"}
    refused(admin.post(f"{API}/page", json=page), 422, "search_cursor_invalid")
    refused(admin.post(f"{API}/stats", json={"search_id": "suresi-dolmus"}), 409, "search_expired")
    gone = {"table": "fg_traffic", "time": _at(11).isoformat(), "id": 999_999}
    refused(admin.get(f"{API}/event", params=gone), 404, "search_event_not_found")
    refused(newcomer.post(API, json={"q": "", **DAY}), 403, "privacy_notice_required")
    forbidden = {"search_id": runs["auditor"]["search_id"], "format": "csv"}
    errors["forbidden_export"] = capture.error(
        auditor.post(f"{API}/export", json=forbidden), 403, "forbidden"
    )
    errors["forbidden_refs"] = capture.error(
        auditor.post(f"{API}/refs", json={"search_id": runs["auditor"]["search_id"]}),
        403,
        "forbidden",
    )
    capture.recent(admin, minutes_ago=10)
    export = {"search_id": runs["typed"]["search_id"], "format": "csv"}
    refused(admin.post(f"{API}/export", json=export), 403, "reauth_required")
    capture.recent(admin)
    with state.exports.hold(admin.who.user_id):
        refused(admin.post(f"{API}/export", json=export), 409, "search_export_running")
    capture.setting("search.export_max_per_hour", 3)
    refused(admin.post(f"{API}/export", json=export), 429, "search_export_rate_limited")
    gate = state.gate
    wait, gate.wait_seconds = gate.wait_seconds, 0.2
    try:
        with gate.slot(admin.who.user_id, 10), gate.slot(admin.who.user_id, 10):
            refused(admin.post(API, json={"q": "", **DAY}), 429, "search_busy")
    finally:
        gate.wait_seconds = wait
    capture.setting("search.runs_per_minute", 1)
    refused(admin.post(API, json={"q": "", **DAY}), 429, "search_rate_limited")
    capture.setting("search.runs_per_minute", 600)
    capture.setting("search.request_budget_ms", 500)
    with _tuned(state, hook=_always_slow):
        refused(admin.post(API, json={"q": f"srcip:{PC}", **DAY}), 504, "search_timeout")
    capture.setting("search.request_budget_ms", DEFAULT_SETTINGS["search.request_budget_ms"])
    refused(
        admin.post(saved_url, json=body | {"name": "planlama pc — OTURUMLAR"}),
        409,
        "saved_search_name_taken",
    )
    refused(admin.put(f"{saved_url}/999999", json=body), 404, "saved_search_not_found")
    capture.setting("search.saved_max_per_user", 1)
    refused(admin.post(saved_url, json=body | {"name": "Bir tane daha"}), 409, "saved_search_limit")
    out["errors"] = errors
    for key in CHANGED:
        capture.setting(key, DEFAULT_SETTINGS[key])
    return out


def _search_ids(value: Any, found: dict[str, str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "search_id" and isinstance(item, str) and item not in found:
                found[item] = f"ornek-arama-{len(found) + 1:02d}"
            _search_ids(item, found)
    elif isinstance(value, list):
        for item in value:
            _search_ids(item, found)


def _replace(value: Any, names: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _replace(item, names) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace(item, names) for item in value]
    if isinstance(value, str):
        return names.get(value, value)
    return value


def _stable_ids(data: dict[str, Any]) -> dict[str, Any]:
    """The random search ids replaced by ``ornek-arama-NN`` (in the order they first appear)."""
    names: dict[str, str] = {}
    _search_ids(data, names)
    replaced: dict[str, Any] = _replace(data, names)
    return replaced
