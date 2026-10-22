"""Clock offset probe (SNTP, RFC 4330) — the evidence of correct time in every manifest.

The Windows Time service keeps the server synchronised with the local time source
(``clock.ntp_server``, usually the core router), else with ``clock.fallback_server``. This probe
measures, independently, how far the server clock is from the NTP server, once an hour, and
stores the samples under ``state/clock/<day>.jsonl``; the day's manifest records the maximum.
An offset above ``clock.alert_offset_ms`` or an unreachable time source raises an alert. Without
a local time source only the fallback is asked and every sample carries a ``warning``.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from arcilogger_core.alerts import raise_alert
from arcilogger_core.config import Config
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import isoformat_site, site_day, utc_now

NTP_EPOCH_OFFSET = 2_208_988_800  # seconds between 1900-01-01 and 1970-01-01
#: Recorded in every sample taken while ``clock.ntp_server`` is empty.
NO_LOCAL_SOURCE = (
    "Yerel saat kaynağı (clock.ntp_server) ayarlanmamış; yalnızca yedek sunucu soruldu "
    "[no local time source (clock.ntp_server) is set; only the fallback server was asked]"
)


class ClockError(RuntimeError):
    pass


@dataclass(frozen=True)
class NtpResult:
    offset_s: float
    delay_s: float
    stratum: int


def _to_ntp(seconds: float) -> bytes:
    whole = int(seconds) + NTP_EPOCH_OFFSET
    fraction = int((seconds - int(seconds)) * 2**32) & 0xFFFFFFFF
    return struct.pack("!II", whole, fraction)


def _from_ntp(data: bytes) -> float:
    whole, fraction = struct.unpack("!II", data)
    return float(whole - NTP_EPOCH_OFFSET + fraction / 2**32)


def sntp_query(server: str, port: int = 123, timeout: float = 5.0) -> NtpResult:
    """One SNTP client exchange; the originate timestamp guards against stray replies."""
    sent_at = time.time()
    transmit = _to_ntp(sent_at)
    # Random low bits make the originate timestamp unpredictable.
    transmit = transmit[:4] + struct.pack(
        "!I", struct.unpack("!I", transmit[4:])[0] ^ secrets.randbits(8)
    )
    packet = b"\x23" + b"\x00" * 39 + transmit  # LI=0, VN=4, mode=3 (client)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.sendto(packet, (server, port))
            data, _ = sock.recvfrom(512)
        except OSError as exc:
            raise ClockError(f"{server}: {exc}") from exc
    received_at = time.time()
    if len(data) < 48:
        raise ClockError(f"{server}: short NTP reply")
    if data[24:32] != transmit:
        raise ClockError(f"{server}: reply does not answer our request")
    stratum = data[1]
    if stratum == 0 or data[0] & 0x07 not in (4, 5):
        raise ClockError(f"{server}: unsynchronised or not a server reply (stratum {stratum})")
    t1 = _from_ntp(transmit)
    t2 = _from_ntp(data[32:40])
    t3 = _from_ntp(data[40:48])
    t4 = received_at
    return NtpResult(
        offset_s=((t2 - t1) + (t3 - t4)) / 2, delay_s=(t4 - t1) - (t3 - t2), stratum=stratum
    )


def check_clock(
    config: Config,
    *,
    clock: Callable[[], datetime] = utc_now,
    query: Callable[[str, int], NtpResult] = lambda s, p: sntp_query(s, p),
) -> dict[str, object]:
    layout = Layout(config.data_dir)
    now = clock()
    sample: dict[str, object] = {"time": isoformat_site(now, "seconds")}
    if not config.clock.ntp_server:
        sample["warning"] = NO_LOCAL_SOURCE
    errors = []
    if not config.clock.servers:
        errors.append("saat sunucusu ayarlanmamış [no time server is configured]")
    for server in config.clock.servers:
        try:
            result = query(server, config.clock.ntp_port)
        except ClockError as exc:
            errors.append(str(exc))
            continue
        sample.update(
            server=server,
            offset_us=round(result.offset_s * 1_000_000),
            delay_us=round(result.delay_s * 1_000_000),
            stratum=result.stratum,
            ok=True,
        )
        break
    else:
        sample.update(server=None, offset_us=None, ok=False, error="; ".join(errors))
    path = layout.clock_samples(site_day(now))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        handle.write(json.dumps(sample, sort_keys=True).encode("utf-8") + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    offset = sample.get("offset_us")
    if not sample["ok"]:
        raise_alert(
            layout.alerts,
            "R20-clock",
            "high",
            "Saat sunucusuna ulaşılamadı: " + "; ".join(errors),
            {},
        )
    elif isinstance(offset, int) and abs(offset) > config.clock.alert_offset_ms * 1000:
        raise_alert(
            layout.alerts,
            "R20-clock",
            "high",
            f"Saat sapması {offset / 1000:.0f} ms (eşik {config.clock.alert_offset_ms} ms).",
            {"offset_us": offset, "server": sample.get("server")},
        )
    return sample
