"""CSV imports of persons and device assignments (``POST /imports/{kind}``, spec 9.2).

A **dry run** (the default) checks every line and shows what would change; an **apply** runs the
same checks against the locked current state and writes all lines in one transaction — or, if any
line has an error, nothing (the answer then lists the errors, ``created`` etc. are what *would*
have changed). Each error names its file line (the header is line 1) in Turkish and English;
at most :data:`MAX_ERRORS` are listed, then one line says how many more there are. ``preview``
lists at most :data:`PREVIEW_ROWS` of the lines that change something.

``persons`` — columns ``name;department;title;email;employee_no;active;notes`` (``name``
required) and ``id``. A line with an ``id`` (the persons export writes it) updates the person
with that id, which must exist — also a person without an employee number; a line without one
but with an ``employee_no`` updates the person with that number, or creates one; a line with
neither creates a person (the preview flags a name that already exists: ``duplicate_name``). An
update writes only the columns present in the file; an empty cell clears that field (an
``employee_no`` too, when the line has an ``id``). An ``id`` whose line names the employee
number of another person, and a person on two lines, are line errors. ``department`` is a
department name (Turkish letters folded) that must exist; ``active`` is evet/hayır (also
true/false, 1/0; empty: unchanged, active for a new person). The other columns of the persons
export (``device_count``, ``created_at``, ``updated_at``) are accepted and ignored, so an
exported file imports unchanged.

``assignments`` — columns ``mac;employee_no;person_name;valid_from;note`` (``mac`` and one of
``employee_no`` / ``person_name`` required). The person is found by ``employee_no``, else by the
exact, unique name; inactive persons get no devices (an apply locks the persons named, ``FOR
SHARE`` in id order before the devices — the order of ``POST /devices/assign`` — and checks again,
so a concurrent deactivation is either seen or waits). The device must have been seen (it is in
the device list): a MAC is never created from a file, so a typo cannot add a device. ``valid_from``
(default: now) is ISO-8601 (``2026-10-01T09:00:00+03:00``; without offset: Europe/Istanbul) or
``01.10.2026 09:00[:00]`` / ``01.10.2026``. Lines are applied in file order with the rules of
:class:`arcilogger_db.assignments.Planner`, so one file can move a device through several
persons (oldest line first). ``created`` counts devices that had no owner, ``updated`` devices
that change owner, ``unchanged`` lines that change nothing.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final, Literal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from arcilogger_api.csvfiles import CsvRow, csv_error, parse_bool, read_csv
from arcilogger_api.textfold import fold
from arcilogger_core.fmt import format_datetime
from arcilogger_core.timeutil import SITE_TZ
from arcilogger_db.assignments import AssignmentError, Change, Planner, load_history, lock_devices
from arcilogger_db.models import Department, Device, Person
from arcilogger_worker.lookup import normalize_mac

ImportKind = Literal["persons", "assignments"]
MAX_ERRORS: Final = 200
PREVIEW_ROWS: Final = 50
PERSON_COLUMNS: Final = ("name", "department", "title", "email", "employee_no", "active", "notes")
#: Written by the persons export: ``id`` identifies the person, the others are ignored.
PERSON_EXPORT_ID: Final = "id"
PERSON_EXPORT_ONLY: Final = ("device_count", "created_at", "updated_at")
ASSIGNMENT_COLUMNS: Final = ("mac", "employee_no", "person_name", "valid_from", "note")
#: Header and one example row of each template (``GET /imports/{kind}/template.csv``).
TEMPLATES: Final[dict[str, tuple[tuple[str, ...], tuple[str, ...]]]] = {
    "persons": (
        PERSON_COLUMNS,
        (
            "Ayşe Yılmaz",
            "Finans",
            "Muhasebe Uzmanı",
            "ayse.yilmaz@example.com",
            "1001",
            "evet",
            "",
        ),
    ),
    "assignments": (
        ASSIGNMENT_COLUMNS,
        ("f0:1f:af:00:00:01", "1001", "Ayşe Yılmaz", "2026-10-01T09:00:00+03:00", "Zimmet formu"),
    ),
}
_LIMITS: Final = {"name": 200, "title": 200, "employee_no": 64, "notes": 2000, "note": 500}
_EMAIL: Final = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_LOCAL_FORMATS: Final = ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y")
_ID: Final = re.compile(r"^[0-9]{1,18}$")
#: Rows locked per statement (keeps the ``IN`` list of one statement bounded).
_CHUNK: Final = 1000

LINE_MESSAGES: Final = {
    "required": ("{column} boş olamaz.", "{column} must not be empty."),
    "too_long": (
        "{column} en fazla {limit} karakter olabilir.",
        "{column} may have at most {limit} characters.",
    ),
    "control": (
        "{column} kontrol karakteri içeremez.",
        "{column} must not contain control characters.",
    ),
    "email": ("E-posta adresi geçersiz ({value}).", "The e-mail address is invalid ({value})."),
    "active": (
        "active değeri evet ya da hayır olmalıdır ({value}).",
        "active must be evet or hayır, true or false ({value}).",
    ),
    "department": (
        "'{value}' adlı departman yok; önce Departmanlar listesine ekleyin.",
        "There is no department '{value}'; add it to the departments first.",
    ),
    "employee_no_repeated": (
        "employee_no {value} dosyanın {first}. satırında da var.",
        "employee_no {value} also appears on line {first} of the file.",
    ),
    "person_unknown_id": (
        "id {value} olan kişi yok; dışa aktarılan dosyanın id sütununu değiştirmeyin (yeni kişi "
        "için boş bırakın).",
        "There is no person with id {value}; do not change the id column of an exported file "
        "(leave it empty for a new person).",
    ),
    "person_repeated": (
        "Bu kişi (id {value}) dosyanın {first}. satırında da var.",
        "This person (id {value}) also appears on line {first} of the file.",
    ),
    "id_mismatch": (
        "employee_no {number} başka bir kişiye ait (id {other}); bu satır id {value} kişisinin.",
        "employee_no {number} belongs to another person (id {other}); this line is the person "
        "with id {value}.",
    ),
    "mac": (
        "MAC adresi geçersiz ({value}); örnek: aa:bb:cc:dd:ee:ff.",
        "Invalid MAC address ({value}); example: aa:bb:cc:dd:ee:ff.",
    ),
    "device_unknown": (
        "{value} cihazı henüz görülmedi (cihaz listesinde yok); cihaz ağa bağlanıp "
        "göründükten sonra tekrar içe aktarın.",
        "Device {value} has not been seen yet (it is not in the device list); import it again "
        "once the device has been on the network.",
    ),
    "person_missing": (
        "Kişi belirtilmemiş: employee_no veya person_name gerekli.",
        "No person given: employee_no or person_name is required.",
    ),
    "person_unknown_no": (
        "employee_no {value} olan kişi yok.",
        "There is no person with employee_no {value}.",
    ),
    "person_unknown_name": ("'{value}' adlı kişi yok.", "There is no person named '{value}'."),
    "person_ambiguous": (
        "'{value}' adında {count} kişi var; employee_no ile belirtin.",
        "{count} persons are named '{value}'; give the employee_no.",
    ),
    "person_mismatch": (
        "employee_no {number} '{name}' kişisine ait; person_name '{value}' bununla uyuşmuyor.",
        "employee_no {number} belongs to '{name}'; person_name '{value}' does not match.",
    ),
    "person_inactive": (
        "'{value}' pasif; pasif bir kişiye cihaz atanamaz.",
        "'{value}' is inactive; devices cannot be assigned to an inactive person.",
    ),
    "valid_from": (
        "valid_from okunamadı ({value}); örnek: 2026-10-01T09:00:00+03:00 veya 01.10.2026 09:00.",
        "valid_from cannot be read ({value}); example: 2026-10-01T09:00:00+03:00 or "
        "01.10.2026 09:00.",
    ),
    "in_future": (
        "valid_from gelecekte olamaz ({value}).",
        "valid_from must not be in the future ({value}).",
    ),
    "overlap": (
        "valid_from ({value}), {mac} cihazının önceki atamasından ({limit}) önce; atama geçmişi "
        "değiştirilemez, yalnızca ileriye doğru eklenir.",
        "valid_from ({value}) is before the previous assignment of {mac} ({limit}); the "
        "assignment history cannot be rewritten, only extended.",
    ),
    "more_errors": (
        "… ve {count} hata daha (önce bunları düzeltin).",
        "… and {count} more errors (fix these first).",
    ),
}


class ImportLineError(BaseModel):
    #: File line (the header is line 1).
    line: int
    message_tr: str
    message_en: str


class ImportResult(BaseModel):
    kind: ImportKind
    dry_run: bool
    #: Data rows in the file (empty lines not counted).
    rows: int
    created: int
    updated: int
    unchanged: int
    errors: list[ImportLineError]
    #: At most 50 of the lines that change something (keys depend on ``kind``).
    preview: list[dict[str, Any]]


class LineProblemError(Exception):
    """One error of one line (raised by the field checks)."""

    def __init__(self, code: str, **params: object) -> None:
        self.code = code
        self.params = params
        super().__init__(code)


@dataclass
class Outcome:
    kind: ImportKind
    rows: int
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    errors: list[tuple[int, LineProblemError]] = field(default_factory=list)
    preview: list[dict[str, Any]] = field(default_factory=list)

    def add_preview(self, item: dict[str, Any]) -> None:
        if len(self.preview) < PREVIEW_ROWS:
            self.preview.append(item)

    def result(self, *, dry_run: bool) -> ImportResult:
        errors = []
        for line, problem in self.errors[:MAX_ERRORS]:
            text_tr, text_en = LINE_MESSAGES[problem.code]
            errors.append(
                ImportLineError(
                    line=line,
                    message_tr=text_tr.format(**problem.params),
                    message_en=text_en.format(**problem.params),
                )
            )
        if len(self.errors) > MAX_ERRORS:
            text_tr, text_en = LINE_MESSAGES["more_errors"]
            rest = len(self.errors) - MAX_ERRORS
            errors.append(
                ImportLineError(
                    line=self.errors[MAX_ERRORS][0],
                    message_tr=text_tr.format(count=rest),
                    message_en=text_en.format(count=rest),
                )
            )
        return ImportResult(
            kind=self.kind,
            dry_run=dry_run,
            rows=self.rows,
            created=self.created,
            updated=self.updated,
            unchanged=self.unchanged,
            errors=errors,
            preview=self.preview,
        )


# -- field checks ---------------------------------------------------------------------------------


def _text(
    row: CsvRow, column: str, *, required: bool = False, multiline: bool = False
) -> str | None:
    value = row.get(column)
    if multiline:
        value = value.replace("\r\n", "\n")
    if not value:
        if required:
            raise LineProblemError("required", column=column)
        return None
    if len(value) > _LIMITS.get(column, 200):
        raise LineProblemError("too_long", column=column, limit=_LIMITS.get(column, 200))
    allowed = "\n\t" if multiline else ""
    if any(unicodedata.category(c) in {"Cc", "Cf", "Cs"} and c not in allowed for c in value):
        raise LineProblemError("control", column=column)
    return value


def parse_time(text: str) -> datetime:
    """ISO-8601 (without offset: site time) or ``dd.mm.yyyy[ HH:MM[:SS]]`` (site time)."""
    value = text.strip()
    moment: datetime | None = None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        for pattern in _LOCAL_FORMATS:
            try:
                moment = datetime.strptime(value, pattern)  # noqa: DTZ007 - site time below
                break
            except ValueError:
                continue
    if moment is None:
        raise ValueError(value)
    if moment.tzinfo is None or moment.utcoffset() is None:
        moment = moment.replace(tzinfo=SITE_TZ)
    return moment


def _collect(
    outcome: Outcome, rows: Sequence[CsvRow], check: Callable[[CsvRow], Any]
) -> list[tuple[CsvRow, Any]]:
    """``check`` every row; the problems become errors, the rest is returned with its value."""
    good: list[tuple[CsvRow, Any]] = []
    for row in rows:
        try:
            good.append((row, check(row)))
        except LineProblemError as problem:
            outcome.errors.append((row.line, problem))
    return good


# -- persons --------------------------------------------------------------------------------------


@dataclass
class PersonLine:
    values: dict[str, Any]
    #: The person with the line's ``id``, else with its ``employee_no`` (an update), else
    #: ``None`` (a new person).
    person: Person | None = None
    changes: list[str] = field(default_factory=list)


def import_persons(db: Session, data: bytes, *, apply: bool) -> Outcome:
    table = read_csv(
        data,
        required=("name",),
        known=PERSON_COLUMNS,
        ignored=PERSON_EXPORT_ONLY,
        export_keys=(PERSON_EXPORT_ID,),
    )
    outcome = Outcome(kind="persons", rows=len(table.rows))
    departments = {fold(row.name): row for row in db.scalars(select(Department))}
    persons = list(db.scalars(select(Person)))
    by_id = {row.id: row for row in persons}
    by_number = {row.employee_no: row for row in persons if row.employee_no}
    names = {fold(row.name) for row in persons}
    present = set(table.columns)
    seen_numbers: dict[str, int] = {}
    seen_persons: dict[int, int] = {}

    def known_person(row: CsvRow) -> Person | None:
        """The person of the line's ``id`` cell (the export's), ``None`` for an empty one."""
        text = row.get(PERSON_EXPORT_ID)
        if not text:
            return None
        person = by_id.get(int(text)) if _ID.match(text) else None
        if person is None:
            raise LineProblemError("person_unknown_id", value=text[:40])
        return person

    def check(row: CsvRow) -> PersonLine:
        values: dict[str, Any] = {"name": _text(row, "name", required=True)}
        if "department" in present:
            name = _text(row, "department")
            department = None if name is None else departments.get(fold(name))
            if name is not None and department is None:
                raise LineProblemError("department", value=name)
            values["department_id"] = None if department is None else department.id
        for column in ("title", "employee_no"):
            if column in present:
                values[column] = _text(row, column)
        if "email" in present:
            email = _text(row, "email")
            if email is not None and (len(email) > 254 or not _EMAIL.match(email)):
                raise LineProblemError("email", value=email)
            values["email"] = email
        if "notes" in present:
            values["notes"] = _text(row, "notes", multiline=True)
        if "active" in present and row.get("active"):
            active = parse_bool(row.get("active"))
            if active is None:
                raise LineProblemError("active", value=row.get("active"))
            values["active"] = active
        number = values.get("employee_no")
        if number is not None:
            first = seen_numbers.setdefault(number, row.line)
            if first != row.line:
                raise LineProblemError("employee_no_repeated", value=number, first=first)
        numbered = by_number.get(number) if number else None
        person = known_person(row)
        if person is None:
            person = numbered
        elif numbered is not None and numbered is not person:
            # The number is another person's: updating would take it from them.
            raise LineProblemError("id_mismatch", number=number, other=numbered.id, value=person.id)
        if person is not None:
            first = seen_persons.setdefault(person.id, row.line)
            if first != row.line:
                raise LineProblemError("person_repeated", value=person.id, first=first)
        line = PersonLine(values=values, person=person)
        if line.person is not None:
            line.changes = [k for k, v in values.items() if getattr(line.person, k) != v]
        return line

    lines = _collect(outcome, table.rows, check)
    for row, line in lines:
        if line.person is None:
            outcome.created += 1
            outcome.add_preview(
                {
                    "line": row.line,
                    "action": "create",
                    "person_id": None,
                    "name": line.values["name"],
                    "employee_no": line.values.get("employee_no"),
                    "changes": sorted(line.values),
                    "duplicate_name": fold(line.values["name"]) in names,
                }
            )
        elif line.changes:
            outcome.updated += 1
            outcome.add_preview(
                {
                    "line": row.line,
                    "action": "update",
                    "person_id": line.person.id,
                    "name": line.values["name"],
                    "employee_no": line.values.get("employee_no"),
                    "changes": line.changes,
                    "duplicate_name": False,
                }
            )
        else:
            outcome.unchanged += 1
    if apply and not outcome.errors:
        for _, line in lines:
            if line.person is None:
                db.add(Person(**{"active": True, **line.values}))
            else:
                for name in line.changes:
                    setattr(line.person, name, line.values[name])
        db.flush()
    return outcome


# -- assignments ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class AssignmentLine:
    mac: str
    person: Person
    valid_from: datetime | None
    note: str | None


def lock_persons(db: Session, ids: Collection[int]) -> None:
    """Lock the ``persons`` rows of ``ids`` ``FOR SHARE`` in id order and reload them (a change
    committed since they were read is seen; a deactivation from now on waits for this
    transaction). Taken before :func:`~arcilogger_db.assignments.lock_devices`, like
    ``POST /devices/assign`` (person, then devices), so the two never deadlock."""
    ordered = sorted(set(ids))
    for start in range(0, len(ordered), _CHUNK):
        db.scalars(
            select(Person)
            .where(Person.id.in_(ordered[start : start + _CHUNK]))
            .order_by(Person.id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        ).all()


def import_assignments(
    db: Session, data: bytes, *, apply: bool, now: datetime
) -> tuple[Outcome, list[Change]]:
    """Check (and with ``apply`` lock and plan) the lines; the caller writes the changes."""
    table = read_csv(data, required=("mac",), known=ASSIGNMENT_COLUMNS)
    if "employee_no" not in table.columns and "person_name" not in table.columns:
        raise csv_error(
            422,
            "csv_header",
            detail="missing column employee_no or person_name",
            columns=", ".join(ASSIGNMENT_COLUMNS),
        )
    outcome = Outcome(kind="assignments", rows=len(table.rows))
    persons = list(db.scalars(select(Person)))
    by_id = {row.id: row for row in persons}
    by_number = {row.employee_no: row for row in persons if row.employee_no}
    by_name: dict[str, list[Person]] = {}
    for person in persons:
        by_name.setdefault(person.name, []).append(person)

    def check(row: CsvRow) -> AssignmentLine:
        text = row.get("mac")
        if not text:
            raise LineProblemError("required", column="mac")
        try:
            mac = normalize_mac(text)
        except ValueError:
            raise LineProblemError("mac", value=text[:40]) from None
        number, name = row.get("employee_no"), row.get("person_name")
        if number:
            person = by_number.get(number)
            if person is None:
                raise LineProblemError("person_unknown_no", value=number)
            if name and name != person.name:
                raise LineProblemError(
                    "person_mismatch", number=number, name=person.name, value=name
                )
        elif name:
            found = by_name.get(name, [])
            if not found:
                raise LineProblemError("person_unknown_name", value=name)
            if len(found) > 1:
                raise LineProblemError("person_ambiguous", value=name, count=len(found))
            person = found[0]
        else:
            raise LineProblemError("person_missing")
        if not person.active:
            raise LineProblemError("person_inactive", value=person.name)
        valid_from = None
        if row.get("valid_from"):
            try:
                valid_from = parse_time(row.get("valid_from"))
            except ValueError:
                raise LineProblemError("valid_from", value=row.get("valid_from")[:60]) from None
        return AssignmentLine(mac, person, valid_from, _text(row, "note", multiline=True))

    lines = _collect(outcome, table.rows, check)
    if apply:
        lock_persons(db, {line.person.id for _, line in lines})
        for row, line in lines:
            if not line.person.active:  # deactivated since the persons were read
                outcome.errors.append(
                    (row.line, LineProblemError("person_inactive", value=line.person.name))
                )
        lines = [(row, line) for row, line in lines if line.person.active]
    macs = {line.mac for _, line in lines}
    if apply:
        known = lock_devices(db, macs)
    else:
        query = select(Device.mac, Device.person_id).where(Device.mac.in_(sorted(macs)))
        known = dict(db.execute(query).all())
    planner = Planner(load_history(db, known), now=now, owners=dict(known))
    for row, line in lines:
        if line.mac not in known:
            outcome.errors.append((row.line, LineProblemError("device_unknown", value=line.mac)))
            continue
        try:
            change = planner.assign(line.mac, line.person.id, line.valid_from, note=line.note)
        except AssignmentError as exc:
            shown = format_datetime(line.valid_from or now)
            if exc.code == "in_future":
                problem = LineProblemError("in_future", value=shown)
            else:
                limit = format_datetime(exc.limit) if exc.limit else "—"
                problem = LineProblemError("overlap", value=shown, mac=line.mac, limit=limit)
            outcome.errors.append((row.line, problem))
            continue
        if change is None:
            outcome.unchanged += 1
            continue
        if change.previous is None:
            outcome.created += 1
        else:
            outcome.updated += 1
        previous = by_id.get(change.previous) if change.previous is not None else None
        outcome.add_preview(
            {
                "line": row.line,
                "action": "assign" if previous is None else "reassign",
                "mac": change.mac,
                "person_id": line.person.id,
                "person_name": line.person.name,
                "previous_person_id": None if previous is None else previous.id,
                "previous_person_name": None if previous is None else previous.name,
                "valid_from": change.valid_from.isoformat(),
            }
        )
    outcome.errors.sort(key=lambda item: item[0])
    return outcome, planner.changes
