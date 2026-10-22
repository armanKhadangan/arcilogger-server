# ADR-0008: Device-person assignment history

- Status: proposed (2026-10-19); waits for the owner's confirmation of the defaults
  [ASSUMPTIONS.md](../ASSUMPTIONS.md) D6–D13
- Deciders: the product owner; proposed with the devices and persons work package (build
  specification 9.2)

## Context

The 5651 lookup names the person behind an internal IP at a past moment T: lease (MAC) → device →
person (spec 9.4). Until now a device had one field, `devices.person_id`, the person it is linked
to **today**. A lookup for last month therefore named whoever holds the device when the lookup
runs. When a laptop is handed from one employee to another, an official report could name the
wrong person — the report said so (`person_link_at_lookup_time`), but the answer stayed wrong.

5651 requests arrive weeks or months after the event. The answer must rest on what was recorded
**for that moment**: who the device was assigned to at T. Spec 9.2 also asks for several MACs per
person (randomised phone MACs change) and for the department of usage to be the person's
department if linked, else the VLAN's.

## Decision

1. **History table** `device_assignments` (revision `0008_device_assignments`): one row per
   assignment, `[valid_from, valid_to)`, `valid_to` NULL for the current one, with `assigned_by`,
   `note` and `backfilled`. Rows are never deleted or rewritten: unassigning closes the open row;
   reassigning closes it and opens a new one **at the same moment**. The note belongs to the row
   opened, so an unassign, which opens none, refuses a note (422) instead of dropping it. A
   person may hold any number of devices; a device has at most one person at a time.
2. **The database enforces it**, not only the API: at most one open row per MAC (partial unique
   index) and no overlapping intervals of one MAC — an exclusion constraint
   `EXCLUDE USING gist (mac WITH =, tstzrange(valid_from, valid_to) WITH &&)` with `btree_gist`,
   a contrib module of the standard PostgreSQL distribution (Windows server: *verify on
   site*), "trusted" since PostgreSQL 13 (the database owner, i.e. the ARCILOGGER role the
   installer creates, may create it; tested on
   PostgreSQL 16.15 without TimescaleDB, including `pg_dump`/`pg_restore` as that role). Where the
   extension cannot be created the migration installs a trigger with the same rule, the same
   error code (`23P01`) and the same constraint name. Foreign keys are `ON DELETE RESTRICT`
   (devices are never deleted; persons are deactivated, not deleted).
3. **History is only appended to.** A new assignment may not start in the future (two minutes of
   clock slack: a start within them is stored as now, so a browser clock ahead of the server
   cannot hold back an immediate correction) and not before the current assignment's start — or,
   for a device without an owner, the end of its last assignment. A change that would rewrite
   the past is refused (409).
   One file of the CSV import may still build a device's history from the oldest line on.
4. **`devices.person_id` stays** as the denormalised current owner, written only by
   `arcilogger_db.assignments`, in the same transaction as the history. Lists, filters ("unknown
   devices") and the usage reports keep reading one column; the correlation worker never touches
   it (and writes device rows in MAC order, the order in which assignment writers lock them).
5. **Backfill:** every link that existed before the migration becomes one open row with
   `backfilled = true` and `valid_from = coalesce(first_seen, created_at)` — the real start was
   never recorded; the report says so (`person_link_backfilled`).
6. **Person step of the lookup** (`arcilogger_worker.lookup.attribution`): the row covering T
   (`valid_from <= T < coalesce(valid_to, ∞)`) names the person, `PersonInfo.link = "history"`
   with `assigned_from` / `assigned_to`. A device **with** history but no row at T names **nobody**
   (`person_unassigned_at_time`) — never the owner of another time. Only a device without any
   history (data written outside this module) falls back to its current owner,
   `link = "current"`, and keeps the old note `person_link_at_lookup_time`. The new fields have
   defaults, so stored outcomes and stored report inputs load unchanged (as `current`), and a
   report of an older lookup renders the same bytes.
7. **What is not tracked:** a person's **department history** and name changes. The department in
   a chain is the person's department today; the outcome says so for history links
   (`person_department_current`). A department change is rare and the 5651 request asks for the
   person, not the department; keeping both histories would need a second interval table and a
   second rule. VLAN changes of the department link are not historised either.

## Consequences

- A lookup for a past moment names the person recorded for that moment, and the official report
  prints the interval ("atama: 01.10.2026 09:00:00 tarihinden itibaren").
- The quality of the answer depends on the assignment being entered **when the device changes
  hands** (or backdated when it is entered, before the next change). Entering it later than the
  next change is impossible by design; a wrong past entry cannot be corrected in the web
  interface — only by a database administrator, outside the audited path (owner decision D6).
- Devices first assigned after the migration have no person before their `valid_from`; lookups
  for earlier moments name nobody unless the assignment was backdated.
- `btree_gist` is one more extension in the database; backups made with `pg_dump` contain it and
  restore as the database owner (checked). Downgrade leaves the extension in place.
- The audit log records assignments by MAC and person **id** (no names), the imports by file hash
  and counts.
