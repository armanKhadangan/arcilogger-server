"""The internet line's speed over read-only SNMPv3 (Wave 5 contract §6, W5-N).

The worker job ``snmp.poll`` reads the WAN interface counters of the SNMP-enabled log sources
(:mod:`arcilogger_core.snmp`, :mod:`.settings`, :mod:`.poller`) and keeps the rates in
``state/wan-rates.json`` (:mod:`.state`) for ``GET /activity/wan``; :mod:`.job` also raises
``R20-snmp``. This is the first time ARCILOGGER sends a request to a device: only to a
configured single-address source's own address, UDP to its SNMP port, authPriv only.
"""
