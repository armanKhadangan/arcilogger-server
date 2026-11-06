"""Canlı Durum (Wave 5 contract §6, Addendum B): who is sending and receiving data now, from
the FortiGate traffic logs (``GET /activity/devices``, W5-M), and the internet line's speed
over SNMP (``GET /activity/wan``, W5-N).

Not under ``routers/``, so the API does not discover it on its own; ``routers/activity.py``
mounts it.
"""
