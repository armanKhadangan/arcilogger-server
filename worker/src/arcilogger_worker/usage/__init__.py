"""Usage analytics ("Kullanım Raporları", spec 10, ADR-0011): rollups, queries, report data.

* :mod:`.config` — the ``usage.*`` settings; :mod:`.domains` — registrable domains (Public
  Suffix List) and the background traffic rules; :mod:`.subjects` — who an address belonged to
  (the 5651 attribution chain at every moment it can change);
* :mod:`.engine` — one site day from the logs: sessions → 1-minute buckets → metrics;
  :mod:`.store` — writing a day; :mod:`.jobs` — the worker jobs and range rebuilds;
* :mod:`.query` — reading the rollups (the API's ``/usage`` endpoints and the reports);
  :mod:`.pseudonyms` — the optional pseudonyms; :mod:`.report_data` — the data of the
  scheduled reports (area R renders it).

Every duration is an **estimate** derived from connection logs, never an exact figure
(docs/KULLANIM.md / docs/USAGE.md).
"""

from __future__ import annotations
