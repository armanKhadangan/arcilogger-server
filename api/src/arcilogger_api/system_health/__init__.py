"""``GET /api/v1/system/health`` — the health part of the Sistem page (Wave 4 contract 2.6).

* :mod:`.models` — the answer (mirrored verbatim in ``web/src/lib/system-health-types.ts``);
* :mod:`.tasks` — the eleven scheduled tasks of ``deploy/windows/tasks/``, each judged by the
  newest evidence it leaves under the data folder (or ApplySystem's ``admin\\system``);
* :mod:`.jobs` — the worker jobs of :func:`arcilogger_worker.service.default_jobs`, from
  ``state/worker-status.json`` and one bounded ``job_runs`` probe per job;
* :mod:`.routes` — the router, the services, the agent, ApplySystem and the time block.

Everything comes from files ArciApi may already read and from ``job_runs`` (contract D9): no
Service Control Manager or Task Scheduler query, no device asked. The router is not mounted
here: ``routers/system_health.py`` (package W4-F) mounts it.
"""
