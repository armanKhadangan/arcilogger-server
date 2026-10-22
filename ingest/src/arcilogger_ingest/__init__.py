"""ARCILOGGER ingest (Windows service ``ArciIngest``).

Milestone M2: tails the raw spool files with a persisted read offset, parses FortiGate,
MikroTik and generic syslog, and bulk-loads normalised rows into PostgreSQL/TimescaleDB. Every
row keeps a reference (source, day, line number) to its raw line. If the database is down the
offset simply stops advancing; nothing is lost because the raw file is the queue.
"""

__version__ = "0.1.0"
