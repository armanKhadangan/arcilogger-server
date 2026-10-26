"""Search: the hourly summary table, saved searches, the counted-from line, the domain index.

Revision ID: 0016_search
Revises: 0015_admin_requests
Create Date: 2026-10-26

ADR-0015 (Arama, Canlı Akış, Kaynaklar):

* ``search_counts``: exact line counts per hour, spool file (``source``, ``raw_day``), kind,
  typed table (``tbl``, ``''`` for none), ``action`` and private /24 (``net``). The ingest loader
  adds a batch's counts in the transaction that loads its rows; the statistics of a broad query
  come from here instead of counting millions of rows. The unique key treats ``NULL`` as a value
  (``NULLS NOT DISTINCT``, PostgreSQL 15+) so the loader's upsert finds a key without
  ``action``/``net``. Indexes: per source and hour (the statistics, the Kaynaklar page) and per
  file (``raw_day``, ``source``: a file's rebuild).
* ``saved_searches``: personal saved searches (name unique per owner ignoring case, a range
  preset or a fixed range, never both); deleted with their owner.
* ``ingest_offsets.counts_from_line``: a file whose lines were loaded before this revision is not
  in ``search_counts`` yet. Each such row gets ``line_number + 1`` ("lines before this are not
  counted"); the worker job ``search.counts_backfill`` recounts the file once it is finished and
  clears the value. ``NULL`` (every file of a new installation): counted from its first line.
* ``ix_fg_web_rhost_time`` on ``fg_web (reverse(lower(hostname)), time)``: "this domain and its
  subdomains" becomes a prefix range of the reversed host name. On native partitions the index of
  the parent reaches every partition. On TimescaleDB it is a plain ``CREATE INDEX`` on the
  hypertable, inside the upgrade's one transaction (``timescaledb.transaction_per_chunk`` cannot
  run in a transaction and is not used). TimescaleDB 2.30's source creates such an index on every
  chunk and checks a compressed chunk apart only for a unique index; on a compressed chunk the
  index holds just the rows not compressed (compressed rows are read by decompression). That the
  statement is accepted with compressed chunks present is not yet shown: the Linux
  TimescaleDB CI job runs ``db/tests/test_migration_0016.py``, and this revision is not released
  before that job is green. The build reads all of ``fg_web`` once: the installer stops the
  services for an upgrade and the spool keeps every incoming line meanwhile.

No partition and no hypertable is created: the new tables are small (``search_counts`` about
450 keys an hour for 150 users, about 4 M rows over 400 days).

The downgrade drops everything above.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016_search"
down_revision: str | Sequence[str] | None = "0015_admin_requests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TZ = postgresql.TIMESTAMP(timezone=True)
PRESETS = ("15m", "1h", "24h", "today", "yesterday", "7d", "30d")
KEY = ("hour", "source", "raw_day", "kind", "tbl", "action", "net")


def _in(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted})"


def upgrade() -> None:
    op.create_table(
        "search_counts",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("hour", TZ, nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("raw_day", sa.Date(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("tbl", sa.Text(), server_default="", nullable=False),
        sa.Column("action", sa.Text(), nullable=True),
        sa.Column("net", postgresql.CIDR(), nullable=True),
        sa.Column("n", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("n > 0", name=op.f("ck_search_counts_n")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_search_counts")),
        sa.UniqueConstraint(*KEY, name="uq_search_counts_key", postgresql_nulls_not_distinct=True),
        comment="Exact hourly line counts per source file, kind, table, action and /24.",
    )
    op.create_index(
        "ix_search_counts_source_hour", "search_counts", ["source", "hour"], unique=False
    )
    op.create_index(
        "ix_search_counts_raw_day_source", "search_counts", ["raw_day", "source"], unique=False
    )

    op.create_table(
        "saved_searches",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("range_preset", sa.Text(), nullable=True),
        sa.Column("range_from", TZ, nullable=True),
        sa.Column("range_to", TZ, nullable=True),
        sa.Column("created_at", TZ, server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", TZ, server_default=sa.text("now()"), nullable=False),
        sa.Column("last_run_at", TZ, nullable=True),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 80", name=op.f("ck_saved_searches_name")),
        sa.CheckConstraint("length(query) <= 1000", name=op.f("ck_saved_searches_query")),
        sa.CheckConstraint(
            _in("range_preset", PRESETS), name=op.f("ck_saved_searches_range_preset")
        ),
        sa.CheckConstraint(
            "(range_preset IS NOT NULL AND range_from IS NULL AND range_to IS NULL) "
            "OR (range_preset IS NULL AND range_from IS NOT NULL AND range_to > range_from)",
            name=op.f("ck_saved_searches_range"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_saved_searches_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_saved_searches")),
        comment="Personal saved searches: name, query text, preset or fixed time range.",
    )
    op.create_index(
        "uq_saved_searches_owner_name",
        "saved_searches",
        ["owner_id", sa.text("lower(name)")],
        unique=True,
    )

    op.add_column("ingest_offsets", sa.Column("counts_from_line", sa.Integer(), nullable=True))
    op.execute("UPDATE ingest_offsets SET counts_from_line = line_number + 1 WHERE line_number > 0")

    op.create_index(
        "ix_fg_web_rhost_time",
        "fg_web",
        [sa.text("reverse(lower(hostname))"), "time"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_fg_web_rhost_time", table_name="fg_web")
    op.drop_column("ingest_offsets", "counts_from_line")
    op.drop_index("uq_saved_searches_owner_name", table_name="saved_searches")
    op.drop_table("saved_searches")
    op.drop_index("ix_search_counts_raw_day_source", table_name="search_counts")
    op.drop_index("ix_search_counts_source_hour", table_name="search_counts")
    op.drop_table("search_counts")
