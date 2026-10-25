"""Alerts (spec 11: R15 log source silent, R20 ARCILOGGER system) and their e-mail (ADR-0010).

Four worker jobs (:mod:`arcilogger_worker.service`):

==================  ======================================================================
``alerts.import``   every minute: new lines of ``state/alerts.jsonl`` (written by the
                    database-free evidence tasks and the collector) into ``alerts``, each line
                    once (:mod:`.importer`)
``alerts.r15``      every minute: an enabled, expected, configured source that sent nothing
                    for longer than its heartbeat (:mod:`.r15`)
``alerts.system``   every 5 minutes: the R20 checks — disk, sealing, verification, credit,
                    replica, parse failures, failing jobs, database backup (:mod:`.system`)
``alerts.notify``   every minute: e-mail through ``[smtp]`` to ``alerts.email_recipients``
                    (:mod:`.notify`, content: :mod:`.texts`, SMTP: :mod:`.mail`)
==================  ======================================================================

The licence alerts (``R20-license…``: paid modules ending or off, a limit crossed, the
maintenance) are raised by the job ``license.check`` (:mod:`.license`).

Every check keeps **one active alert per dedup key** (``R15:<source>``, ``R20-disk:<drive>``,
``R20-seal:<day>``, …; :mod:`.store`) and resolves it itself once the condition is gone
(``details.auto_resolved``). A person's changes of state and notes (the Security page, through
the API) and every automatic change are ``alert_events`` rows written by :mod:`.store`
(ADR-0010 amendment). The rule engine of M5 and webhooks are not part of this package yet.
docs/ALERTS.md lists every rule, its setting and what to do.
"""
