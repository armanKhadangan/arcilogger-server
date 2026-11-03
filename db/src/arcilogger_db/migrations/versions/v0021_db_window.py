"""The 30-day database window: ``db.retention_days`` 30, daily partitions, ``archive_reloads``.

Revision ID: 0021_db_window
Revises: 0020_fg_traffic_transip_time
Create Date: 2026-11-02

Wave 5 contract §5, owner decision "son 30 olsun" (G1, G2, G4; issue #110):

* ``db.retention_days`` becomes the database window, default 30 (was 400), at least 7. A stored
  400 (the untouched old default) becomes 30, a stored value below 7 becomes 7, every other
  value is kept. A row of the seed stays the seed's (it follows the shipped default from then
  on); an administrator's changed row is written with ``updated_by = ':migration'``, and the log
  names the old and the new value. A change, of either row, also leaves the status row
  ``db.retention_days_migration`` (author ``:migration``, not in ``DEFAULT_SETTINGS``: the old
  and the new value, who had set the old one, ``audited: false``); the window job ``db.window``
  turns it into the one ``settings.change`` audit entry (actor ``system``) on its first run,
  before it drops anything, because no revision writes audit entries (the audit chain is
  application code). The new key ``db.reload_keep_days`` (7) is inserted by the seed, as every
  new key.
* ``archive_reloads``: the requests to bring sealed days older than the window back from the
  vault ("arşivden yeniden yükle", G4), loaded by ArciIngest one at a time (the partial unique
  index allows one ``loading`` row).
* Plain PostgreSQL: the time-series tables get **daily** partitions after the current month's
  monthly partition (``arcilogger_db.partitions``, run by ``arcilogger-db upgrade`` right after
  this revision): one per UTC day, as TimescaleDB's chunks; the first one covers only the three
  hours from the month's end (local midnight, 21:00 UTC) to the next UTC midnight. The monthly
  partitions ``<table>_pYYYYMM`` created ahead by earlier versions for the months after the
  current one are dropped when they are empty; one that holds rows (a device clock in the
  future) is kept, never split, and the daily partitions beside it cover only the rest of their
  UTC day. Monthly partitions of the current and earlier months stay until the window job drops
  them, each with the daily partition after it, once the UTC day it ends in has ended by its
  cutoff. Nothing changes on TimescaleDB (1-day chunks already).

The downgrade (developers and the migration tests; ``Install-Arcilogger.ps1 -Rollback`` calls it
from #122): the daily partitions are merged back into monthly ones (local months; a daily
partition's rows split by local month, since a UTC day holds the first three hours of the next
site day; the default partition's rows of those months moved too, then attached), since the
previous version knows monthly partitions only. A transaction holds a lock on every object it
drops or creates until its commit, and in one transaction the whole window and the days ahead
(46 UTC days) need about 6,700 or more, beyond the 6,400 of PostgreSQL's defaults
(``max_locks_per_transaction`` 64, ``max_connections`` 100), which a rollback never changes. So
the merge is done in steps, one local month of one table each (:func:`_merge_month`, some 500
locks), and the downgrade commits each step itself (:func:`_commit`; manager answer 3 on #121).
It first estimates the largest step (:func:`locks_needed`) and refuses, changing nothing, when
even that one can never fit (:func:`check_lock_table`). A step is atomic, and the steps are
planned from the partitions there are: a run interrupted part way leaves the months merged so
far monthly and the rest as this version has them (a merged month's last UTC day stays as the
next month's change-over slice, W5-G-12), alembic's version at ``0021_db_window``, and the next
run goes on with the rest (the way on after an interruption is to run the downgrade again; an
upgrade instead keeps the merged months monthly). Each step takes the partitions' advisory lock,
as every other change to them does (:func:`_lock_partitions`), and the version moves only when,
under that lock, no daily partition is left (:func:`_check_merged`: one made between two steps
by a service still running makes the run refuse, the next one merges it). Only after the last
step, in the transaction in which alembic moves its version (the caller's), ``archive_reloads``,
``db.reload_keep_days`` and the status rows ``db.window_dropped_before`` and
``db.retention_days_migration`` are dropped (an audit entry already written from the latter
stays), and a ``db.retention_days`` of 30 written by the seed or by this revision goes back to
400 (lossy: a value raised to 7 stays 7, which the previous version accepts; no revision writes
audit entries, #122's rollback step audits it, manager answer 4). On TimescaleDB there is nothing
to merge: the downgrade is one transaction.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021_db_window"
down_revision: str | Sequence[str] | None = "0020_fg_traffic_transip_time"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

log = logging.getLogger("alembic.runtime.migration")

TZ = postgresql.TIMESTAMP(timezone=True)
TABLE = "archive_reloads"
KEY = "db.retention_days"
KEEP_KEY = "db.reload_keep_days"
#: The status row of the window job's applied cutoff (``arcilogger_db.partitions.DROPPED_KEY``).
DROPPED_KEY = "db.window_dropped_before"
#: The status row of the G1 change, audited by the window job (frozen copy of
#: ``arcilogger_worker.db_window.CHANGE_KEY``; a test keeps them equal).
CHANGE_KEY = "db.retention_days_migration"
SEED = ":seed"
MIGRATION = ":migration"
AUTHORS = [SEED, MIGRATION]
#: G1: the old shipped default, the new one and the new lower bound.
OLD_DEFAULT = 400
NEW_DEFAULT = 30
MINIMUM = 7
#: Frozen copies of ``models.ARCHIVE_RELOAD_REASONS`` and ``ARCHIVE_RELOAD_STATUSES``.
REASONS = ("lookup", "search")
STATUSES = ("queued", "loading", "ready", "failed", "expired")
#: Frozen copy of ``models.TIME_SERIES_TABLES`` and of the site time zone
#: (``arcilogger_core.timeutil``): migrations never import application code.
TIME_SERIES_TABLES = (
    "messages",
    "fg_traffic",
    "fg_web",
    "fg_dns",
    "fg_app",
    "fg_ips",
    "fg_virus",
    "fg_event",
    "mt_dhcp",
    "mt_arp",
    "mt_fw",
    "mt_account",
    "mt_interface",
    "mt_config",
    "mt_event",
    "fg_dhcp",
)
SITE_TZ = ZoneInfo("Europe/Istanbul")
#: Locks of the downgrade besides the time-series tables' objects (the settings, the
#: ``archive_reloads`` table and its indexes, alembic's version table, the system catalogs).
LOCK_ALLOWANCE = 100
#: The advisory lock every change to the partitions takes (``arcilogger_db.partitions``'
#: ``PARTITION_LOCK_KEY``, "ARCIPART", frozen here: a revision imports no application code).
PARTITION_LOCK_KEY = int.from_bytes(b"ARCIPART", "big") - 2**63
ONE_DAY = timedelta(days=1)
MONTHLY = re.compile(r"^(?P<table>[a-z_]+)_p(?P<year>\d{4})(?P<month>\d{2})$")
DAILY = re.compile(r"^(?P<table>[a-z_]+)_p(?P<year>\d{4})(?P<month>\d{2})(?P<day>\d{2})$")


def _in(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted})"


def _month(day: date) -> date:
    return day.replace(day=1)


def _next_month(day: date) -> date:
    return date(day.year + day.month // 12, day.month % 12 + 1, 1)


def _midnight(day: date) -> str:
    """Local midnight of a site day as a DDL literal (computed here, never input)."""
    return f"'{datetime.combine(day, time(0, 0), tzinfo=SITE_TZ).isoformat()}'"


def _partitions(bind: sa.Connection) -> list[tuple[str, str]]:
    """``(parent, partition)`` of every natively partitioned time-series table."""
    rows = bind.execute(
        sa.text(
            "SELECT p.relname, c.relname FROM pg_partitioned_table t "
            "JOIN pg_class p ON p.oid = t.partrelid "
            "JOIN pg_inherits i ON i.inhparent = p.oid JOIN pg_class c ON c.oid = i.inhrelid "
            "WHERE p.relnamespace = CAST(current_schema() AS regnamespace) "
            "AND p.relname = ANY(:tables) ORDER BY p.relname, c.relname"
        ),
        {"tables": list(TIME_SERIES_TABLES)},
    ).all()
    return [(parent, name) for parent, name in rows]


# -- upgrade --------------------------------------------------------------------------------------


def upgrade() -> None:
    bind = op.get_bind()
    _window_setting(bind)
    op.create_table(
        TABLE,
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("requested_by", sa.Text(), nullable=False),
        sa.Column("requested_at", TZ, server_default=sa.text("now()"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("lookup_id", sa.BigInteger(), nullable=True),
        sa.Column("first_day", sa.Date(), nullable=False),
        sa.Column("last_day", sa.Date(), nullable=False),
        sa.Column("status", sa.Text(), server_default="queued", nullable=False),
        sa.Column("bytes_total", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("bytes_done", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("ready_at", TZ, nullable=True),
        sa.Column("expires_at", TZ, nullable=True),
        sa.CheckConstraint(_in("reason", REASONS), name=op.f("ck_archive_reloads_reason")),
        sa.CheckConstraint(_in("status", STATUSES), name=op.f("ck_archive_reloads_status")),
        sa.CheckConstraint("last_day >= first_day", name=op.f("ck_archive_reloads_range")),
        sa.CheckConstraint(
            "btrim(requested_by) <> ''", name=op.f("ck_archive_reloads_requested_by")
        ),
        sa.CheckConstraint(
            "lookup_id IS NULL OR reason = 'lookup'", name=op.f("ck_archive_reloads_lookup")
        ),
        sa.CheckConstraint(
            "bytes_total >= 0 AND bytes_done >= 0", name=op.f("ck_archive_reloads_bytes")
        ),
        sa.CheckConstraint(
            "(status = 'failed') = (error_code IS NOT NULL)",
            name=op.f("ck_archive_reloads_error_code"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('ready', 'expired') "
            "OR (ready_at IS NOT NULL AND expires_at IS NOT NULL)",
            name=op.f("ck_archive_reloads_ready"),
        ),
        sa.ForeignKeyConstraint(
            ["lookup_id"],
            ["lookups.id"],
            name=op.f("fk_archive_reloads_lookup_id_lookups"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_archive_reloads")),
        comment="Archive reloads: sealed days loaded back into the database on request.",
    )
    op.create_index("ix_archive_reloads_status_id", TABLE, ["status", "id"], unique=False)
    op.create_index(
        "uq_archive_reloads_loading",
        TABLE,
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'loading'"),
    )
    _drop_future_months(bind)


def _window_setting(bind: sa.Connection) -> None:
    """G1: 400 → 30, below 7 → 7, every other value kept (module docstring)."""
    row = bind.execute(
        sa.text("SELECT value, updated_by FROM settings WHERE key = :key"), {"key": KEY}
    ).one_or_none()
    if row is None:
        return  # a new database: the seed inserts the new default
    value, author = row
    if not isinstance(value, int) or isinstance(value, bool):
        return  # not a number: the readers fall back to the default, nothing to convert
    if value == OLD_DEFAULT:
        new = NEW_DEFAULT
    elif value < MINIMUM:
        new = MINIMUM
    else:
        return
    by = SEED if author == SEED else MIGRATION
    bind.execute(
        sa.text(
            "UPDATE settings SET value = CAST(:value AS jsonb), updated_by = :by, "
            "updated_at = now() WHERE key = :key"
        ),
        {"value": str(new), "by": by, "key": KEY},
    )
    change = {
        "old": value,
        "new": new,
        "was_set_by": author,
        "migration": revision,
        "audited": False,
    }
    bind.execute(
        sa.text(
            "INSERT INTO settings (key, value, updated_by, updated_at) "
            "VALUES (:key, CAST(:value AS jsonb), :by, now()) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, "
            "updated_by = EXCLUDED.updated_by, updated_at = EXCLUDED.updated_at"
        ),
        {"key": CHANGE_KEY, "value": json.dumps(change), "by": MIGRATION},
    )
    log.info("%s: %s -> %s (was set by %s)", KEY, value, new, author)


def _drop_future_months(bind: sa.Connection) -> None:
    """The empty monthly partitions of the months after the current one (G2)."""
    today = datetime.now(UTC).astimezone(SITE_TZ).date()
    first = _next_month(today.replace(day=1))
    for parent, name in _partitions(bind):
        match = MONTHLY.match(name)
        if match is None or match.group("table") != parent:
            continue
        if date(int(match.group("year")), int(match.group("month")), 1) < first:
            continue
        # The lock first: no row can arrive between the check and the drop.
        bind.execute(sa.text(f'LOCK TABLE "{parent}" IN ACCESS EXCLUSIVE MODE'))
        exists = sa.text(f'SELECT EXISTS (SELECT 1 FROM "{name}")')  # noqa: S608
        if bind.execute(exists).scalar_one():
            log.info("%s: holds rows, kept as a monthly partition", name)
            continue
        bind.execute(sa.text(f'DROP TABLE "{name}"'))


# -- downgrade ------------------------------------------------------------------------------------


def downgrade() -> None:
    bind = op.get_bind()
    check_lock_table(bind, locks_needed(bind))
    daily, planned, defaults = _merge_plan(bind)
    for parent, month, names in _steps(daily, planned):
        default = f"{parent}_default" if parent in defaults else None
        _merge_month(bind, parent, month, names, _next_month(month) in planned[parent], default)
        _commit(bind)
    # The rest goes with alembic's version, in the caller's transaction, and only when no daily
    # partition is left (one made between two steps, which commit and release their locks).
    _check_merged(bind)
    op.drop_index("uq_archive_reloads_loading", table_name=TABLE)
    op.drop_index("ix_archive_reloads_status_id", table_name=TABLE)
    op.drop_table(TABLE)
    bind.execute(
        sa.text("DELETE FROM settings WHERE key = ANY(:keys)"),
        {"keys": [KEEP_KEY, DROPPED_KEY, CHANGE_KEY]},
    )
    bind.execute(
        sa.text(
            "UPDATE settings SET value = CAST(:old AS jsonb), updated_at = now() "
            "WHERE key = :key AND value = CAST(:new AS jsonb) AND updated_by = ANY(:authors)"
        ),
        {"old": str(OLD_DEFAULT), "new": str(NEW_DEFAULT), "key": KEY, "authors": AUTHORS},
    )


def _merge_plan(
    bind: sa.Connection,
) -> tuple[dict[str, list[tuple[str, date]]], dict[str, list[date]], set[str]]:
    """Per table its daily partitions (name, UTC day) and the local months that get a monthly
    partition (:func:`_merge_month`), and the tables with a default partition. A month that
    still has its monthly partition (or got it from an earlier, interrupted run) gets none."""
    daily: dict[str, list[tuple[str, date]]] = {}
    monthly: dict[str, set[date]] = {}
    partitions = _partitions(bind)
    defaults = {parent for parent, name in partitions if name == f"{parent}_default"}
    for parent, name in partitions:
        if (match := DAILY.match(name)) is not None and match.group("table") == parent:
            day = date(int(match.group("year")), int(match.group("month")), int(match.group("day")))
            daily.setdefault(parent, []).append((name, day))
        elif (match := MONTHLY.match(name)) is not None and match.group("table") == parent:
            month = date(int(match.group("year")), int(match.group("month")), 1)
            monthly.setdefault(parent, set()).add(month)
    months = {
        parent: sorted(
            {month for _name, day in names for month in (_month(day), _month(day + ONE_DAY))}
            - monthly.get(parent, set())
        )
        for parent, names in daily.items()
    }
    return daily, months, defaults


def _steps(
    daily: dict[str, list[tuple[str, date]]], planned: dict[str, list[date]]
) -> list[tuple[str, date, list[tuple[str, date]]]]:
    """The steps of the merge in order, by table and month: the daily partitions that can hold
    rows of the month (the UTC days from the one it begins in, at 21:00 UTC, to the one it ends
    in). The UTC day a month begins in is the previous month's last one too: the previous
    month's step leaves it holding this month's rows only."""
    return [
        (
            parent,
            month,
            [(name, day) for name, day in names if month in (_month(day), _month(day + ONE_DAY))],
        )
        for parent, names in sorted(daily.items())
        for month in planned[parent]
    ]


def locks_needed(bind: sa.Connection) -> int:
    """About how many objects the largest step of the downgrade (:func:`_merge_month`) holds a
    lock on until its commit. Dropping a daily partition locks everything that goes with it
    (what depends on it in ``pg_depend``: its indexes, TOAST table, row types, constraints,
    column defaults), creating the monthly one as much again (a daily partition's average);
    ``LOCK TABLE`` takes every other partition of the table, and the default partition's
    indexes take its rows' moves. Plus :data:`LOCK_ALLOWANCE`; 0 when there is nothing to
    merge."""
    daily, planned, _defaults = _merge_plan(bind)
    needed = 0
    for table, _step, names in _steps(daily, planned):
        objects, others = bind.execute(
            sa.text(
                "WITH RECURSIVE o(classid, objid) AS ("
                "SELECT CAST('pg_class' AS regclass), CAST(name AS regclass) "
                "FROM unnest(CAST(:names AS text[])) AS name "
                "UNION SELECT d.classid, d.objid FROM pg_depend d JOIN o "
                "ON d.refclassid = o.classid AND d.refobjid = o.objid "
                "WHERE d.deptype IN ('a', 'i')) "
                "SELECT (SELECT count(*) FROM o), "
                "(SELECT count(*) FROM pg_class c WHERE c.oid = CAST(:table AS regclass) "
                "OR c.oid IN (SELECT inhrelid FROM pg_inherits "
                "WHERE inhparent = CAST(:table AS regclass)) "
                "OR c.oid IN (SELECT indexrelid FROM pg_index "
                "WHERE indrelid = CAST(:default AS regclass))) "
                "- cardinality(CAST(:names AS text[]))"
            ),
            {
                "table": table,
                "names": [name for name, _day in names],
                "default": f"{table}_default",
            },
        ).one()
        per_partition = -(-int(objects) // len(names))
        needed = max(needed, LOCK_ALLOWANCE + int(objects) + int(others) + per_partition)
    return needed


def check_lock_table(bind: sa.Connection, needed: int) -> None:
    """Refuse the downgrade before it changes anything when the locks of its largest step can
    never fit into PostgreSQL's shared lock table (room for ``max_locks_per_transaction`` times
    (``max_connections`` + ``max_prepared_transactions``) objects, shared by every session): the
    step would fail with "out of shared memory" and be rolled back. A step fits into the
    defaults many times over; the downgrade never changes PostgreSQL's configuration."""
    per, connections, prepared = bind.execute(
        sa.text(
            "SELECT CAST(current_setting('max_locks_per_transaction') AS integer), "
            "CAST(current_setting('max_connections') AS integer), "
            "CAST(current_setting('max_prepared_transactions') AS integer)"
        )
    ).one()
    room = per * (connections + prepared)
    if needed > room:
        raise RuntimeError(
            f"0021_db_window geri alınamadı: en büyük adımı (bir tablonun bir ayı, kendi "
            f"işleminde) yaklaşık {needed} kilit gerektiriyor, PostgreSQL'in kilit tablosunda "
            f"{room} yer var (max_locks_per_transaction {per}, max_connections {connections}). "
            "Bu çalıştırma hiçbir şeyi değiştirmedi; önceki bir çalıştırmanın birleştirdiği "
            "aylar birleştirilmiş kalır. PostgreSQL'in varsayılanları (64 ve 100) bir adıma "
            "fazlasıyla yeter: bu ayarları denetleyin. / "
            f"0021_db_window was not downgraded: its largest step (one month of one table, in "
            f"its own transaction) needs about {needed} locks, PostgreSQL's lock table has room "
            f"for {room} (max_locks_per_transaction {per}, max_connections {connections}). This "
            "run changed nothing; the months an earlier run merged stay merged. PostgreSQL's "
            "defaults (64 and 100) are ample for a step: check these settings."
        )


def _merge_month(
    bind: sa.Connection,
    parent: str,
    month: date,
    names: list[tuple[str, date]],
    rest: bool,
    default: str | None,
) -> None:
    """One step of the downgrade: a local month of a table becomes one monthly partition, as the
    previous version creates them. The month's rows of its daily partitions (``names``, see
    :func:`_steps`; a daily partition is a UTC day, ``[D 00:00 UTC, D+1 00:00 UTC)``, or the
    part of it beside a monthly partition) and of the default partition go into it, and the
    daily partitions are dropped. The UTC day the month ends in also holds the first three
    hours of the next month: when that month gets a monthly partition too (``rest``), the day
    stays with those rows only, detached and attached again as the next month's change-over
    slice (as this version makes it), for the next step. Every statement is in the step's
    transaction (:func:`_commit` after it): it is done completely or not at all."""
    monthly_name = f"{parent}_p{month:%Y%m}"
    following = _next_month(month)
    _lock_partitions(bind)
    bind.execute(sa.text(f'LOCK TABLE "{parent}" IN ACCESS EXCLUSIVE MODE'))
    bind.execute(
        sa.text(
            f'CREATE TABLE "{monthly_name}" (LIKE "{parent}" INCLUDING DEFAULTS '
            "INCLUDING CONSTRAINTS INCLUDING STORAGE)"
        )
    )
    last, last_day = names[-1]
    slice_ = last if rest and _month(last_day + ONE_DAY) == following else None
    for name, _day in names:
        bind.execute(
            sa.text(
                f'INSERT INTO "{monthly_name}" SELECT * FROM "{name}" '  # noqa: S608
                f"WHERE {_in_month(month)}"
            )
        )
        # Every row went to the month or stays for the next one (the partition holds rows of
        # these months only): a row left over would go with the drop.
        kept = [month, following] if name == slice_ else [month]
        moved = " OR ".join(_in_month(each) for each in kept)
        left = sa.text(f'SELECT EXISTS (SELECT 1 FROM "{name}" WHERE NOT ({moved}))')  # noqa: S608
        if bind.execute(left).scalar_one():  # pragma: no cover - see the comment above
            raise RuntimeError(f"{name}: rows outside the months it is merged into")
        if name == slice_:
            bind.execute(sa.text(f'ALTER TABLE "{parent}" DETACH PARTITION "{name}"'))
            bind.execute(sa.text(f'DELETE FROM "{name}" WHERE {_in_month(month)}'))  # noqa: S608
        else:
            bind.execute(sa.text(f'DROP TABLE "{name}"'))
    if default is not None:
        bind.execute(
            sa.text(
                f'INSERT INTO "{monthly_name}" SELECT * FROM "{default}" '  # noqa: S608
                f"WHERE {_in_month(month)}"
            )
        )
        bind.execute(sa.text(f'DELETE FROM "{default}" WHERE {_in_month(month)}'))  # noqa: S608
    values = f"FROM ({_midnight(month)}) TO ({_midnight(following)})"
    bind.execute(
        sa.text(f'ALTER TABLE "{parent}" ATTACH PARTITION "{monthly_name}" FOR VALUES {values}')
    )
    if slice_ is not None:
        end = datetime.combine(last_day + ONE_DAY, time(0, 0), tzinfo=UTC).isoformat()
        values = f"FROM ({_midnight(following)}) TO ('{end}')"
        bind.execute(
            sa.text(f'ALTER TABLE "{parent}" ATTACH PARTITION "{slice_}" FOR VALUES {values}')
        )
    log.info("%s: daily partitions merged into %s", parent, monthly_name)


def _lock_partitions(bind: sa.Connection) -> None:
    """Wait for, and then keep out until the commit, every other change to the partitions (the
    window job, ArciIngest's daily partitions, ``arcilogger-db upgrade``)."""
    bind.execute(sa.text("SELECT pg_advisory_xact_lock(:key)"), {"key": PARTITION_LOCK_KEY})


def _check_merged(bind: sa.Connection) -> None:
    """Refuse to move alembic's version while a daily partition is left: the steps are planned
    once, and between two of them (each commits and releases its locks) a service still running
    can make one (a day ahead, a reload's day). Checked under the partitions' advisory lock,
    which is kept until the caller's commit; the run changes nothing more, and the next one
    merges the rest."""
    _lock_partitions(bind)
    daily, _planned, _defaults = _merge_plan(bind)
    left = sorted(name for names in daily.values() for name, _day in names)
    if left:
        shown = ", ".join(left[:5]) + (" …" if len(left) > 5 else "")
        raise RuntimeError(
            f"0021_db_window geri alınamadı: geri alma sürerken günlük bölümler oluştu ({shown}); "
            "ArciIngest ve ArciWorker hizmetleri çalışıyor olabilir. Hizmetleri durdurup geri "
            "almayı yeniden çalıştırın: birleştirilen aylar birleştirilmiş kalır, sürüm "
            "0021_db_window olarak kalır. / "
            f"0021_db_window was not downgraded: daily partitions were made while the downgrade "
            f"ran ({shown}); ArciIngest and ArciWorker may be running. Stop the services and run "
            "the downgrade again: the merged months stay merged, the version stays "
            "0021_db_window."
        )


def _commit(bind: sa.Connection) -> None:
    """Commit a step of the downgrade. Alembic runs every revision in the caller's transaction
    (``env.py``; ``engine.begin()`` in the tools and the tests), which SQLAlchemy does not let a
    revision end, so the step is committed on the driver's connection; the next statement opens
    the next transaction, which the caller commits (with alembic's version) or rolls back. What
    the caller's transaction held before the downgrade (the revisions above this one, each with
    its version row) is committed with the first step, and a transaction-level advisory lock is
    released there: a caller that serialises downgrades takes a session-level one."""
    bind.connection.commit()


def _in_month(month: date) -> str:
    """The device times of a local month (DDL literals, computed here)."""
    return f"(time >= {_midnight(month)} AND time < {_midnight(_next_month(month))})"
