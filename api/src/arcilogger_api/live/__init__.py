"""Canlı Akış: the live tail of the raw spool (ADR-0015, contract 2.14).

* :mod:`~arcilogger_api.live.hub` — one reader per API process: follows today's spool files,
  parses within a budget, keeps the newest lines and hands each stream what its query matches;
* :mod:`~arcilogger_api.live.stream` — one open ``GET /live/streams/{id}``: its events, the
  session check every 30 s, resume and the ``live.close`` audit entry;
* :mod:`~arcilogger_api.live.events` — the buffered line and the server-sent event frames;
* :mod:`~arcilogger_api.live.schemas`, :mod:`~arcilogger_api.live.messages` — shapes and texts.

The endpoints are in :mod:`arcilogger_api.routers.live`.
"""
