"""ARCILOGGER syslog collector (Windows service ``ArciCollector``).

Replaces rsyslog of the original Linux design. Responsibilities (milestone M1):

* receive syslog from the configured senders on UDP/TCP 514 (optional TLS on 6514);
* write every message **first and unchanged** to
  ``<data>\\spool\\<source>\\<YYYY-MM-DD>.log`` (receive day in Europe/Istanbul), each line
  prefixed with the receive time (RFC 3339, microseconds, offset) and the sender IP;
* switch to the next day's file at local midnight on its own (no external rotation);
* count messages per source for the dashboard and the "log source silent" rule.

The spool file is also the durable queue for the ingest service, so the collector never
depends on the database and never waits for anything except the disk.
"""

__version__ = "0.1.0"
