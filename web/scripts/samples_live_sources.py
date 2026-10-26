"""The Canlı Akış and Kaynaklar samples (``web/e2e/samples/live.json``, ``sources.json``).

Captured from the real live tail and sources API: ``capture_api_samples.py`` calls
:func:`capture` on its main site after the other pages' samples, with the capture (the app, its
database and data directory with the raw spool, the request helpers ``json``, ``error``,
``file``) and three signed-in clients: an administrator, an auditor and an administrator who has
not accepted the KVKK notice. It returns ``{sample file name: data}``; a file whose data is empty
is not written (and ``--check`` expects none).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from capture_api_samples import Capture

#: Keys this module's files compare differently, added to the script's defaults:
#: ``{file: (data-map keys, enum keys)}`` (``capture_api_samples.MAP_KEYS``, ``ENUM_KEYS``).
SHAPE_KEYS: dict[str, tuple[frozenset[str], frozenset[str]]] = {}


def capture(capture: Capture, admin: Any, auditor: Any, newcomer: Any) -> dict[str, Any]:
    """The live tail and sources samples: none until their API exists."""
    return {}
