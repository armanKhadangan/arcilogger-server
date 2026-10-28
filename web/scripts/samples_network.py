"""The observed panel of the internet exits editor (``web/e2e/samples/settings-observed.json``).

Captured from the real ``GET /settings/fortigate/observed`` (contract 2.12):
``capture_api_samples.py`` calls :func:`capture` on its main site after every other page's samples
(it is the last of the sample modules), with the capture (the app, its database and data directory, the request helpers
``json`` and ``error``) and three signed-in clients: an administrator, an auditor and an
administrator who has not accepted the KVKK notice. It returns ``{sample file name: data}``.

The module writes a dual-WAN day of the firewall before the capture's "now" (13.10.2026 07:00):
session records as real spool lines and rows (:class:`arcilogger_worker.testing.Evidence`), so
the counts and times are what the endpoint makes of them:

* ``100.64.12.9`` on ``wan1``: the NAT address of the site's exit ``wan`` (claimed);
* ``100.64.13.9`` on ``wan2``: the second ISP router's line, not yet an exit (unmapped);
* ``172.20.99.1`` on ``dmz``: the source NAT towards the DMZ (unmapped);
* ``198.51.100.20`` with ``trandisp="snat-pool"``: a NAT type ``nat.snat_trandisp`` does not
  list (``unknown_trandisp``);
* in the last hour, sessions with ``srcmac`` on ``vlan42`` (one address per MAC, listed in
  ``lookup.srcmac_interfaces``) and on ``vlan60`` (one MAC on several IPv4 addresses: a router
  behind the interface, not listed).

What ``settings-observed.json`` holds: ``observed`` (``hours=24``), ``observed_exits_invalid``
(the same with a stored ``nat.egresses`` the lookup cannot parse: every row ``egress: null``,
``exits_invalid: true``) and ``errors`` (an auditor's 403, ``hours`` out of range, the 504 of a
count past its deadline). The 5-minute cache of the counts is off for the capture, so every answer
reads the database; ``nat.egresses`` and ``lookup.srcmac_interfaces`` are put back at the end.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any
from unittest import mock

from sqlalchemy import text

from arcilogger_api.routers import settings as settings_router
from arcilogger_core.timeutil import utc_now
from arcilogger_db.settings import DEFAULT_SETTINGS, get_setting
from arcilogger_worker.testing import Evidence

if TYPE_CHECKING:
    from capture_api_samples import Capture

#: Keys this module's file compares differently, added to the script's defaults:
#: ``{file: (data-map keys, enum keys)}`` (``capture_api_samples.MAP_KEYS``, ``ENUM_KEYS``).
SHAPE_KEYS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "settings-observed": (frozenset(), frozenset()),
}

API = "/api/v1"
URL = f"{API}/settings/fortigate/observed"
#: The NAT groups of the day: ``(transip, dstintf, trandisp, sessions, the first and the last
#: session's hours before "now")``; the clients and destinations are of the example world.
NAT_GROUPS = (
    ("100.64.12.9", "wan1", "snat", 30, 22.5, 0.2),
    ("100.64.13.9", "wan2", "snat", 14, 21.0, 0.4),
    ("172.20.99.1", "dmz", "snat", 6, 18.0, 3.0),
    ("198.51.100.20", "wan1", "snat-pool", 3, 9.0, 7.5),
)
#: The srcmac interfaces of the last hour: ``(srcintf, [(client, MAC)], sessions per client)``.
SRCMAC_CLIENTS = (
    (
        "vlan42",
        [("172.20.42.81", "00:00:5e:00:53:81"), ("172.20.42.82", "00:00:5e:00:53:82")],
        3,
    ),
    (
        "vlan60",
        [(f"172.20.60.{n}", "00:00:5e:00:53:a0") for n in (21, 22, 23)],
        2,
    ),
)
#: An internet exit value the lookup cannot parse (a NAT address that is no address), written
#: around the validator as ``capture_lookups`` does for its 503.
UNPARSABLE = {"wan": {"mode": "isp_nat", "transips": ["100.64.12"]}}
#: ``lookup.srcmac_interfaces`` while the samples are captured (put back to its default).
LISTED = ("vlan42",)
#: The production deadline of the counting (the 504's text names it).
DEADLINE = settings_router.OBSERVED_DEADLINE_SECONDS


def write_day(capture: Capture) -> None:
    """The firewall's sessions of :data:`NAT_GROUPS` and :data:`SRCMAC_CLIENTS` (one Evidence
    writer, so the raw line numbers follow the lines already written)."""
    now = utc_now()
    with capture.factory() as session:
        evidence = Evidence(session, capture.layout)
        for index, (transip, dstintf, trandisp, count, first, last) in enumerate(NAT_GROUPS):
            step = (first - last) / max(count - 1, 1)
            client = f"172.20.4{1 + index % 3}.{30 + index}"
            for n in range(count):
                evidence.traffic(
                    now - timedelta(hours=first - n * step),
                    client,
                    53000 + 100 * index + n,
                    transip=transip,
                    dstintf=dstintf,
                    trandisp=trandisp,
                )
        for srcintf, clients, sessions in SRCMAC_CLIENTS:
            for k, (client, mac) in enumerate(clients):
                for n in range(sessions):
                    evidence.traffic(
                        now - timedelta(minutes=50 - 10 * k - n),
                        client,
                        54000 + 10 * k + n,
                        transip="100.64.12.9",
                        srcintf=srcintf,
                        dstintf="wan1",
                        srcmac=mac,
                    )
        session.commit()


def capture(capture: Capture, admin: Any, auditor: Any, newcomer: Any) -> dict[str, Any]:
    """The observed panel's samples."""
    out: dict[str, Any] = {"errors": {}}
    errors = out["errors"]
    write_day(capture)
    cache = settings_router.ObservedState()
    cache.ttl = 0.0  # every answer counts again
    capture.app.state.arci_observed = cache
    with capture.factory() as session:
        exits = get_setting(session, "nat.egresses")
    capture.setting("lookup.srcmac_interfaces", list(LISTED))
    try:
        out["observed"] = capture.json(admin.get(URL), 200)
        capture.setting("nat.egresses", UNPARSABLE)
        out["observed_exits_invalid"] = capture.json(admin.get(URL), 200)
        if out["observed_exits_invalid"]["nat"].get("exits_invalid") is not True:
            raise SystemExit("observed: an unparsable nat.egresses gave no exits_invalid")
    finally:
        capture.setting("nat.egresses", exits)
    errors["forbidden"] = capture.error(auditor.get(URL), 403, "forbidden")
    errors["validation_error"] = capture.error(
        admin.get(URL, params={"hours": 73}), 422, "validation_error"
    )

    def slow_counts(db: Any, *_args: Any, **_kwargs: Any) -> Any:
        # Longer than the deadline: PostgreSQL cancels the statement.
        db.execute(text("SELECT pg_sleep(:seconds)"), {"seconds": DEADLINE + 1})
        raise SystemExit("observed: the deadline did not stop a slow count")

    with mock.patch.object(settings_router, "observed_counts", slow_counts):
        errors["fortigate_observed_timeout"] = capture.error(
            admin.get(URL), 504, "fortigate_observed_timeout"
        )
    capture.setting("lookup.srcmac_interfaces", DEFAULT_SETTINGS["lookup.srcmac_interfaces"])
    capture.app.state.arci_observed = None
    return {"settings-observed": out}
