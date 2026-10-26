"""Test helpers of Arama (``api/tests/test_search_*.py``, ``web/scripts/samples_search.py``).

:class:`Lines` writes loaded lines straight into the database — one ``messages`` row and at
most one typed row with the same time and raw reference, as ingest stores them — so a test
controls every value (equal times across tables, empty columns, odd text) without the parsers.
Raw references are numbered per spool file ``(source, receive day)`` like the collector's
lines; nothing is written to the spool. The search API never reads the spool, only the raw-line
drawer does (``GET /raw``), which these tests do not open.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Final

from fastapi.testclient import TestClient
from sqlalchemy import insert
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.auth.accounts import accept_privacy, privacy_version
from arcilogger_api.testing import LoggedIn, login, make_user
from arcilogger_core.timeutil import site_day
from arcilogger_db.models import Base

#: ``(type, subtype)`` of the ``messages`` row of each typed table (``mt_event``: by event).
MESSAGE_TYPES: Final[Mapping[str, tuple[str, str | None]]] = {
    "fg_traffic": ("traffic", "forward"),
    "fg_web": ("utm", "webfilter"),
    "fg_dns": ("utm", "dns"),
    "fg_app": ("utm", "app-ctrl"),
    "fg_ips": ("utm", "ips"),
    "fg_virus": ("utm", "virus"),
    "fg_event": ("event", "system"),
    "mt_dhcp": ("dhcp", None),
    "mt_arp": ("arp", None),
    "mt_fw": ("fw", None),
    "mt_account": ("account", "login"),
    "mt_interface": ("interface", None),
    "mt_config": ("config", None),
}
MT_EVENT_TYPES: Final[Mapping[str, str]] = {
    "dhcp_offering": "dhcp",
    "dhcp_pool_empty": "dhcp",
    "dhcp_failed": "dhcp",
    "wifi_connect": "wifi",
    "wifi_disconnect": "wifi",
    "heartbeat": "heartbeat",
    "log": "log",
}
#: Required columns of the typed tables without a natural default.
REQUIRED: Final[Mapping[str, Mapping[str, Any]]] = {
    "mt_dhcp": {
        "server": "dhcp41",
        "action": "assigned",
        "ip": "172.20.41.5",
        "mac": "00:00:5e:00:53:05",
    },
    "mt_arp": {"ip": "172.20.41.5", "mac": "00:00:5e:00:53:05"},
    "mt_account": {"action": "login"},
    "mt_interface": {"interface": "ether1", "state": "up"},
    "mt_event": {"event": "heartbeat"},
}
SENDERS: Final = {"fortigate": "172.31.255.1", "core-r1": "172.20.10.1"}
LINE_BYTES: Final = 400


@dataclass(frozen=True)
class Written:
    """One line as written: its table, row id and raw reference."""

    table: str | None
    id: int | None
    message_id: int
    time: datetime
    source: str
    day: date
    line: int
    offset: int


@dataclass
class Lines:
    """Writes lines into a database (see the module text)."""

    factory: sessionmaker[Session]
    _lines: dict[tuple[str, date], int] = field(default_factory=dict)
    written: list[Written] = field(default_factory=list)

    def add(self, time: datetime, table: str | None = None, **values: Any) -> Written:
        """One line of ``source`` at device time ``time``; ``table`` and the typed values: its
        typed row (``None``: a ``messages`` row only). See :meth:`write`."""
        with self.factory() as session:
            written = self.write(session, time, table, **values)
            session.commit()
        return written

    def many(
        self, start: datetime, count: int, step: timedelta, table: str | None, **values: Any
    ) -> list[Written]:
        """``count`` lines ``step`` apart (newest last), in one transaction."""
        with self.factory() as session:
            found = [self.write(session, start + i * step, table, **values) for i in range(count)]
            session.commit()
        return found

    def write(
        self,
        session: Session,
        time: datetime,
        table: str | None = None,
        *,
        source: str = "fortigate",
        sender: str | None = None,
        received: datetime | None = None,
        type: str | None = None,
        subtype: str | None = None,
        text: str = "",
        host: str | None = None,
        severity: int | None = None,
        program: str | None = None,
        parse_error: str | None = None,
        **typed: Any,
    ) -> Written:
        """One line in ``session`` (the caller commits)."""
        received = received or time
        day = site_day(received)
        number = self._lines.get((source, day), 0) + 1
        self._lines[(source, day)] = number
        if type is None:
            if table == "mt_event":
                type, subtype = MT_EVENT_TYPES[typed.get("event", "heartbeat")], None
            elif table is not None:
                type, default_subtype = MESSAGE_TYPES[table]
                subtype = subtype if subtype is not None else default_subtype
            else:
                type = "syslog"
        common = {
            "time": time,
            "time_received": received,
            "source": source,
            "sender": sender or SENDERS.get(source, "172.20.10.9"),
            "raw_day": day,
            "raw_line": number,
            "raw_offset": (number - 1) * LINE_BYTES,
        }
        messages = Base.metadata.tables["messages"]
        message_id = session.execute(
            insert(messages)
            .values(
                **common,
                type=type,
                subtype=subtype,
                text=text,
                host=host,
                severity=severity,
                program=program,
                parse_error=parse_error,
            )
            .returning(messages.c.id)
        ).scalar_one()
        row_id = None
        if table is not None:
            values = {**REQUIRED.get(table, {}), **typed, **common}
            found = Base.metadata.tables[table]
            row_id = session.execute(
                insert(found).values(**values).returning(found.c.id)
            ).scalar_one()
        written = Written(
            table, row_id, message_id, time, source, day, number, common["raw_offset"]
        )
        self.written.append(written)
        return written


def sign_in(
    client: TestClient,
    factory: sessionmaker[Session],
    role: str = "admin",
    *,
    notice: bool = True,
    **user: Any,
) -> LoggedIn:
    """A user of ``role`` signed in on ``client`` (``notice``: the KVKK notice accepted)."""
    who = login(client, factory, make_user(factory, role, **user))
    if notice:
        with factory() as session:
            accept_privacy(session, who.user_id, privacy_version(session), ip=None)
            session.commit()
    return who
