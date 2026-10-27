"""Trusted senders (ADR-0016, ``lookup.trust``): an unconfigured sender or a source of another
type never gives FortiGate evidence, except an ``unknown-<ip>`` sender inside the address of an
enabled, configured FortiGate source; the row rules wait for the ``fg_dhcp`` table."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from arcilogger_db.models import Source
from arcilogger_worker.lookup import trust
from arcilogger_worker.lookup.types import LookupSettings


def _source(name: str, kind: str, address: str | None, **flags: bool) -> Source:
    return Source(name=name, type=kind, address=address, **flags)


def test_untrusted_sources(db_session: Session) -> None:
    db_session.add_all(
        [
            # Configured FortiGates: trusted, and they vouch for senders inside their address.
            _source("fortigate", "fortigate", "172.31.255.1"),
            _source("fw-ha", "fortigate", "172.31.254.0/29"),
            _source("fw6", "fortigate", "2001:db8:31::/64"),
            # A removed FortiGate keeps its own trust but vouches for nothing.
            _source("fortigate-old", "fortigate", "198.51.100.9", enabled=False, expected=False),
            # A configured source of another type is no FortiGate.
            _source("core-r1", "mikrotik", "172.20.10.1"),
            _source("ap-172.20.10.23", "generic", "172.20.10.23"),
            # Unconfigured senders.
            _source("unknown-172.31.254.3", "fortigate", "172.31.254.3", unknown=True),
            _source("unknown-172.31.254.4", "fortigate", None, unknown=True),  # name only
            _source("unknown-2001-db8-31--5", "fortigate", None, unknown=True),
            _source("unknown-172.31.254.9", "fortigate", "172.31.254.9", unknown=True),
            _source("unknown-192.0.2.7", "fortigate", "192.0.2.7", unknown=True),
            _source("unknown-198.51.100.9", "fortigate", "198.51.100.9", unknown=True),
            _source("unknown-172.20.10.1", "fortigate", "172.20.10.1", unknown=True),
            _source("unknown-garbled", "generic", None, unknown=True),
        ]
    )
    db_session.flush()
    assert trust.untrusted_sources(db_session) == {
        "core-r1",
        "ap-172.20.10.23",
        "unknown-172.31.254.9",  # outside the HA network /29
        "unknown-192.0.2.7",  # nobody vouches for it
        "unknown-198.51.100.9",  # a removed source's address vouches for nothing
        "unknown-172.20.10.1",  # only a FortiGate source vouches
        "unknown-garbled",
    }


def test_without_sources_nothing_is_untrusted(db_session: Session) -> None:
    """Rows of a source without a ``sources`` row (test helpers only) count as trusted."""
    assert trust.untrusted_sources(db_session) == frozenset()


def test_a_configured_source_without_an_address_vouches_for_nothing(db_session: Session) -> None:
    db_session.add_all(
        [
            _source("fortigate", "fortigate", None),
            _source("unknown-172.31.255.1", "fortigate", "172.31.255.1", unknown=True),
        ]
    )
    db_session.flush()
    assert trust.untrusted_sources(db_session) == {"unknown-172.31.255.1"}


def test_the_refusal_rules_are_listed_in_their_order() -> None:
    assert trust.REFUSAL_RULES == (
        "fortigate_dhcp_untrusted_source",
        "fortigate_dhcp_excluded",
        "ipv6_ignored",
        "fortigate_dhcp_unmapped",
        "fortigate_dhcp_unexpected_msg",
        "fortigate_dhcp_receive_time",
        "fortigate_dhcp_bad_mac",
    )
    assert {"fortigate_dhcp_excluded", "ipv6_ignored"} == trust.CONFIGURATION


def test_the_row_rules_need_the_fortigate_dhcp_table(db_session: Session) -> None:
    settings = LookupSettings()
    moment = datetime(2026, 10, 12, tzinfo=UTC)
    with pytest.raises(NotImplementedError):
        trust.refusal(settings, frozenset())
    with pytest.raises(NotImplementedError):
        trust.kept(settings, frozenset())
    with pytest.raises(NotImplementedError):
        trust.usable_dhcp_origins(db_session, settings, frozenset(), [], moment, moment)
    with pytest.raises(NotImplementedError):
        trust.srcmac_ip_counts(db_session, moment, moment, None, frozenset())
