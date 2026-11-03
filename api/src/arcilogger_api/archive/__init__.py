"""Archive reload ("arşivden yeniden yükle", Wave 5 contract §5, G4 to G9): ``/archive-reloads``
(:mod:`.routes`), the database window as the API sees it (:mod:`.service`) and the models
(:mod:`.models`).

Not under ``routers/``, so the API does not discover it on its own; ``routers/archive_reloads.py``
mounts it (C1).

Codes, each with a Turkish and an English text in :data:`.service.MESSAGES`:

- answers: 409 ``archive_reload_required`` (a lookup or search needs days outside the window,
  G7/G8); the refusals of G5, 422 ``archive_reload_too_large``, ``archive_day_not_in_vault`` and
  ``archive_reload_disk_low``;
- ``error_code`` of a ``failed`` reload (set by ArciIngest while it loads): ``archive_day_not_in_vault``
  (vault retention removed a day after the request was queued) and ``archive_file_unreadable``
  (a sealed file of a day kept failing to read, or no longer matched its manifest).
"""
