"""Test helpers: realistic raw spool lines plus the typed rows that point at them.

:class:`Evidence` writes each message exactly as the collector would (``rawlog.format_line`` into
``spool/<source>/<day>.log``) and inserts the parsed row with the raw reference of that line
(``raw_day``, ``raw_line``, ``raw_offset``), so tests can check that an attribution chain cites
the right raw line. Like the ingest service it advances ``ingest_offsets`` (source, raw day) in
the same transaction as the row (late-evidence detection relies on it). The wire formats follow
the FortiGate and MikroTik format samples of the parser tests; every value is fictional. Used
by the worker tests and by later packages that need lease/traffic data (API lookup tests, demo
data).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from arcilogger_core.layout import Layout
from arcilogger_core.rawlog import format_line
from arcilogger_core.timeutil import site_day, to_site
from arcilogger_db.models import (
    FgApp,
    FgDns,
    FgTraffic,
    FgWeb,
    IngestOffset,
    Message,
    MtArp,
    MtDhcp,
)

TR = timezone(timedelta(hours=3))
ROUTER = "172.20.10.1"
FIREWALL = "172.31.255.1"


def tr(day: date, hour: int, minute: int = 0, second: int = 0) -> datetime:
    """A moment in Europe/Istanbul (UTC+03:00)."""
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=TR)


class Evidence:
    def __init__(self, session: Session, layout: Layout) -> None:
        self.session = session
        self.layout = layout
        self._lines: dict[tuple[str, date], int] = {}

    def _write(
        self,
        source: str,
        sender: str,
        time: datetime,
        message: str,
        received: datetime | None = None,
    ) -> dict[str, Any]:
        received = time + timedelta(milliseconds=3) if received is None else received
        day = site_day(received)
        path = self.layout.spool_file(source, day)
        path.parent.mkdir(parents=True, exist_ok=True)
        offset = path.stat().st_size if path.exists() else 0
        data = format_line(received, sender, message.encode("utf-8"))
        known = self._lines.get((source, day))
        if known is None:  # a file another Evidence wrote: number on after its lines
            known = path.read_bytes().count(b"\n") if offset else 0
        with path.open("ab") as handle:
            handle.write(data)
        line = known + 1
        self._lines[(source, day)] = line
        progress = {"byte_offset": offset + len(data), "line_number": line}
        self.session.execute(
            insert(IngestOffset)
            .values(source=source, day=day, **progress)
            .on_conflict_do_update(index_elements=["source", "day"], set_=progress)
        )
        return {
            "time": time.astimezone(UTC),
            "time_received": received.astimezone(UTC),
            "source": source,
            "sender": sender,
            "raw_day": day,
            "raw_line": line,
            "raw_offset": offset,
        }

    def _add(self, row: Any) -> int:
        self.session.add(row)
        self.session.flush()
        return int(row.id)

    @staticmethod
    def _mikrotik_header(time: datetime, topics: str) -> str:
        stamp = to_site(time).isoformat(timespec="milliseconds")
        return f"<30>{stamp} CORE-R1 {topics}"

    def dhcp(
        self,
        time: datetime,
        action: str,
        ip: str,
        mac: str,
        *,
        server: str = "dhcp43",
        hostname: str | None = None,
        vlan: int | None = None,
    ) -> int:
        host = f" {hostname}" if hostname else ""
        message = (
            f"{self._mikrotik_header(time, 'dhcp,info')} {server} {action} {ip} for "
            f"{mac.upper()}{host}"
        )
        common = self._write("core-r1", ROUTER, time, message)
        return self._add(
            MtDhcp(
                **common,
                server=server,
                vlan=vlan,
                action=action,
                ip=ip,
                mac=mac.lower(),
                hostname=hostname,
            )
        )

    def heartbeat(
        self, time: datetime, *, source: str = "core-r1", received: datetime | None = None
    ) -> int:
        """A loaded line of ``source`` without typed row (``messages`` only): how far ingest has
        loaded that source (``ARCI-HEARTBEAT`` of the CORE-R1 scheduler, or any FortiGate line).
        ``received``: the collector's receive time (default: the device time + 3 ms; later for
        a device clock that runs behind)."""
        sender = ROUTER if source == "core-r1" else FIREWALL
        message = f"{self._mikrotik_header(time, 'script,info')} ARCI-HEARTBEAT CORE-R1"
        common = self._write(source, sender, time, message, received)
        return self._add(Message(**common, type="heartbeat", text="ARCI-HEARTBEAT"))

    def arp(self, time: datetime, ip: str, mac: str, interface: str = "vlan43") -> int:
        message = (
            f"{self._mikrotik_header(time, 'script,info')} ARP-LOG {ip} {mac.upper()} {interface}"
        )
        common = self._write("core-r1", ROUTER, time, message)
        return self._add(MtArp(**common, ip=ip, mac=mac.lower(), interface=interface))

    def traffic(
        self,
        time: datetime,
        srcip: str,
        srcport: int,
        dstip: str = "203.0.113.10",
        dstport: int = 443,
        *,
        transip: str | None = "100.64.12.9",
        transport: int | None = None,
        duration: int | None = 10,
        sessionid: int | None = None,
        proto: int = 6,
        interim: bool = False,
        trandisp: str | None = "snat",
        action: str = "accept",
        sentbyte: int | None = None,
        rcvdbyte: int | None = None,
        sentdelta: int | None = None,
        rcvddelta: int | None = None,
        durationdelta: int | None = None,
        app: str | None = None,
        appcat: str | None = None,
        dstinetsvc: str | None = None,
        service: str | None = None,
        srcintf: str | None = None,
        dstintf: str | None = None,
        srcmac: str | None = None,
    ) -> int:
        """A traffic log; byte counters, deltas, application and ISDB service are optional
        (the usage golden datasets set them; the lookup tests do not need them).

        ``srcintf``/``dstintf`` (the session's FortiGate interfaces, e.g. ``vlan42``/``wan1``)
        and ``srcmac`` (device identification on the source interface, ADR-0016) are written
        only when given, so every line written without them stays byte for byte as before;
        ``srcmac`` goes into the raw line only."""
        local = to_site(time)
        logid = "0000000020" if interim else "0000000013"
        nat = (
            f' trandisp="{trandisp}" transip={transip} transport={transport or srcport}'
            if transip
            else ""
        )
        optional = {
            "dstinetsvc": dstinetsvc,
            "service": service,
            "app": app,
            "appcat": appcat,
            "sentbyte": sentbyte,
            "rcvdbyte": rcvdbyte,
            "sentdelta": sentdelta,
            "rcvddelta": rcvddelta,
            "durationdelta": durationdelta,
        }
        extra = "".join(
            f" {key}={value}" if isinstance(value, int) else f' {key}="{value}"'
            for key, value in optional.items()
            if value is not None
        )
        source_side = f' srcintf="{srcintf}"' if srcintf is not None else ""
        destination_side = f' dstintf="{dstintf}"' if dstintf is not None else ""
        device = f' srcmac="{srcmac}"' if srcmac is not None else ""
        message = (
            f'<189>date={local:%Y-%m-%d} time={local:%H:%M:%S} devname="FIREWALL-01" '
            f'devid="FGT0000000000000" eventtime={int(time.timestamp() * 1e9)} tz="+0300" '
            f'logid="{logid}" type="traffic" subtype="forward" level="notice" vd="root" '
            f"srcip={srcip} srcport={srcport}{source_side} dstip={dstip} dstport={dstport}"
            f"{destination_side} "
            f'sessionid={sessionid or srcport} proto={proto} action="{action}"{nat} '
            f"duration={duration or 0}{extra}{device}"
        )
        common = self._write("fortigate", FIREWALL, time, message)
        return self._add(
            FgTraffic(
                **common,
                logid=logid,
                subtype="forward",
                action=action,
                sentbyte=sentbyte,
                rcvdbyte=rcvdbyte,
                sentdelta=sentdelta,
                rcvddelta=rcvddelta,
                durationdelta=durationdelta,
                app=app,
                appcat=appcat,
                dstinetsvc=dstinetsvc,
                service=service,
                sessionid=sessionid if sessionid is not None else srcport,
                proto=proto,
                srcip=srcip,
                srcport=srcport,
                srcintf=srcintf,
                dstip=dstip,
                dstport=dstport,
                dstintf=dstintf,
                transip=transip,
                transport=(transport or srcport) if transip else None,
                trandisp=trandisp if transip else None,
                duration=duration,
                is_interim=interim,
            )
        )

    def web(
        self,
        time: datetime,
        srcip: str,
        sessionid: int,
        hostname: str,
        dstip: str,
        *,
        url: str | None = None,
        cat: int | None = None,
        catdesc: str | None = None,
        action: str | None = None,
    ) -> int:
        optional = {"url": url, "cat": cat, "catdesc": catdesc, "action": action}
        extra = "".join(
            f" {key}={value}" if isinstance(value, int) else f' {key}="{value}"'
            for key, value in optional.items()
            if value is not None
        )
        message = (
            f'<190>logid="0316013056" type="utm" subtype="webfilter" srcip={srcip} '
            f'dstip={dstip} sessionid={sessionid} hostname="{hostname}"{extra}'
        )
        common = self._write("fortigate", FIREWALL, time, message)
        return self._add(
            FgWeb(
                **common,
                sessionid=sessionid,
                srcip=srcip,
                dstip=dstip,
                hostname=hostname,
                url=url,
                cat=cat,
                catdesc=catdesc,
                action=action,
            )
        )

    def app(
        self,
        time: datetime,
        srcip: str,
        sessionid: int,
        hostname: str | None,
        dstip: str,
        *,
        app: str | None = None,
        appcat: str | None = None,
        action: str | None = None,
    ) -> int:
        optional = {"app": app, "appcat": appcat, "action": action, "hostname": hostname}
        extra = "".join(f' {key}="{value}"' for key, value in optional.items() if value)
        message = (
            f'<190>logid="1059028704" type="utm" subtype="app-ctrl" srcip={srcip} '
            f"dstip={dstip} sessionid={sessionid}{extra}"
        )
        common = self._write("fortigate", FIREWALL, time, message)
        return self._add(
            FgApp(
                **common,
                sessionid=sessionid,
                srcip=srcip,
                dstip=dstip,
                hostname=hostname,
                app=app,
                appcat=appcat,
                action=action,
            )
        )

    def dns(
        self,
        time: datetime,
        srcip: str,
        qname: str,
        answers: Sequence[str],
        *,
        cat: int | None = None,
        catdesc: str | None = None,
        action: str | None = None,
    ) -> int:
        optional = {"cat": cat, "catdesc": catdesc, "action": action}
        extra = "".join(
            f" {key}={value}" if isinstance(value, int) else f' {key}="{value}"'
            for key, value in optional.items()
            if value is not None
        )
        message = (
            f'<189>logid="1501054802" type="utm" subtype="dns" srcip={srcip} qname="{qname}" '
            f'ipaddr="{", ".join(answers)}"{extra}'
        )
        common = self._write("fortigate", FIREWALL, time, message)
        return self._add(
            FgDns(
                **common,
                srcip=srcip,
                qname=qname,
                answers=list(answers) or None,
                cat=cat,
                catdesc=catdesc,
                action=action,
            )
        )

    def raw_line(self, source: str, day: date, line: int, offset: int) -> bytes:
        """The spool line at byte ``offset``; ``ValueError`` unless it is line number ``line``."""
        data = self.layout.spool_file(source, day).read_bytes()
        if data[:offset].count(b"\n") != line - 1:
            raise ValueError(f"byte {offset} is not the start of line {line}")
        end = data.index(b"\n", offset)
        return data[offset : end + 1]
