"""CSV imports (spec 9.2): dry run and transactional apply, per-line Turkish/English errors,
limits, encodings and separators, the round trip of an export with Turkish letters, templates,
the multipart reader and the audit entries (applies only)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api import csvfiles, csvimport
from arcilogger_api.csvimport import parse_time
from arcilogger_api.directory_testing import add_device, add_person
from arcilogger_api.errors import ApiError
from arcilogger_api.lookup_testing import accept_privacy
from arcilogger_api.testing import LoggedIn
from arcilogger_api.uploads import multipart_file
from arcilogger_core.timeutil import SITE_TZ
from arcilogger_db.assignments import lock_devices
from arcilogger_db.models import AuditLog, Device, DeviceAssignment, Person

Login = Callable[..., LoggedIn]
API = "/api/v1"
PC, PHONE, PRINTER = "f0:1f:af:00:00:01", "02:00:5e:00:53:46", "a4:83:e7:00:00:60"
TURKISH = "ı İ ş ğ ü ö ç Ş Ğ Ü Ö Ç"


@pytest.fixture
def factory(app_session_factory: sessionmaker[Session]) -> sessionmaker[Session]:
    return app_session_factory


@pytest.fixture
def admin(client: TestClient, login_as: Login, factory: sessionmaker[Session]) -> LoggedIn:
    who = login_as(client, "admin", username="yonetici")
    accept_privacy(factory, who.user_id)
    return who


def _upload(
    client: TestClient, kind: str, data: bytes | str, *, dry_run: bool | None = None
) -> Any:
    body = data.encode("utf-8") if isinstance(data, str) else data
    params = {} if dry_run is None else {"dry_run": str(dry_run).lower()}
    return client.post(
        f"{API}/imports/{kind}",
        params=params,
        files={"file": ("import.csv", body, "text/csv")},
    )


def _audit(factory: sessionmaker[Session], action: str) -> list[AuditLog]:
    with factory() as session:
        rows = list(
            session.scalars(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id))
        )
        for row in rows:
            session.expunge(row)
    return rows


def _persons(factory: sessionmaker[Session]) -> dict[str, Person]:
    with factory() as session:
        rows = list(session.scalars(select(Person)))
        for row in rows:
            session.expunge(row)
    return {row.name: row for row in rows}


def _lines(*rows: str) -> str:
    return "\r\n".join(rows) + "\r\n"


# -- persons --------------------------------------------------------------------------------------


def test_persons_dry_run_then_apply(
    client: TestClient, admin: LoggedIn, factory: sessionmaker[Session]
) -> None:
    with factory() as session:
        add_person(session, "Eski Ad", employee_no="1001", department="Planlama")
        add_person(session, "Aynı Ad")
        session.commit()
    data = _lines(
        "name;department;title;email;employee_no;active;notes",
        "Yeni Ad;idari işler;Uzman;yeni@example.com;1001;EVET;",
        "Aynı Ad;;;;;;",
        'Gül Şen;FİNANS;;;2002;HAYIR;"çok\nsatırlı"',
        ";;;;;;",
    )
    preview = _upload(client, "persons", data)  # dry run by default
    assert preview.status_code == 200, preview.text
    result = preview.json()
    assert (result["kind"], result["dry_run"], result["rows"]) == ("persons", True, 3)
    assert (result["created"], result["updated"], result["unchanged"]) == (2, 1, 0)
    assert result["errors"] == []
    actions = [(p["line"], p["action"], p["duplicate_name"]) for p in result["preview"]]
    assert actions == [(2, "update", False), (3, "create", True), (4, "create", False)]
    assert set(_persons(factory)) == {"Eski Ad", "Aynı Ad"}  # nothing written
    assert _audit(factory, "import.persons") == []  # dry runs are not audited
    applied = _upload(client, "persons", data, dry_run=False)
    assert applied.status_code == 200, applied.text
    assert applied.json()["dry_run"] is False
    persons = _persons(factory)
    assert persons["Yeni Ad"].employee_no == "1001"
    assert persons["Yeni Ad"].title == "Uzman"
    assert persons["Gül Şen"].active is False
    assert persons["Gül Şen"].notes == "çok\nsatırlı"
    (entry,) = _audit(factory, "import.persons")
    assert entry.details["rows"] == 3
    assert (entry.details["created"], entry.details["updated"]) == (2, 1)
    assert len(entry.details["file_sha256"]) == 64
    assert "Gül" not in str(entry.details)
    # The same file again: the employee numbers match, nothing changes for them.
    again = _upload(client, "persons", data).json()
    assert (again["updated"], again["unchanged"]) == (0, 2)


def test_persons_errors_per_line_and_nothing_applied(
    client: TestClient, admin: LoggedIn, factory: sessionmaker[Session]
) -> None:
    data = _lines(
        "name,department,email,employee_no,active",  # comma separated
        "İyi Satır,,,5001,",
        ",,,5002,",
        "Kötü E-posta,,abc,5003,",
        "Yok Departman,Hayali,,5004,",
        "Tekrar,,,5001,",
        "Belirsiz,,,5005,belki",
        "x" * 201 + ",,,5006,",
        "Kontrol‮,,,5007,",
    )
    response = _upload(client, "persons", data, dry_run=False)
    assert response.status_code == 200
    result = response.json()
    assert [e["line"] for e in result["errors"]] == [3, 4, 5, 6, 7, 8, 9]
    assert result["errors"][0]["message_tr"] == "name boş olamaz."
    assert result["errors"][0]["message_en"] == "name must not be empty."
    assert "Hayali" in result["errors"][2]["message_tr"]
    assert "2. satırında" in result["errors"][3]["message_tr"]
    assert "line 2" in result["errors"][3]["message_en"]
    assert _persons(factory) == {}  # all or nothing
    assert _audit(factory, "import.persons") == []


def test_export_imports_unchanged_with_turkish_letters(
    client: TestClient, admin: LoggedIn, factory: sessionmaker[Session]
) -> None:
    created = client.post(
        f"{API}/persons",
        json={
            "name": f"Işık Çağrı {TURKISH}",
            "employee_no": "7001",
            "title": "=Şef",  # neutralised in the export, restored on import
            "notes": 'ilk satır\nikinci; satır, "tırnak"',
            "department_id": None,
        },
    )
    assert created.status_code == 201, created.text
    exported = client.get(f"{API}/persons/export.csv")
    assert exported.status_code == 200
    assert "'=Şef" in exported.content.decode("utf-8")
    result = _upload(client, "persons", exported.content).json()
    assert result["errors"] == []
    assert (result["created"], result["updated"], result["unchanged"]) == (0, 0, 1)
    # Without BOM and with LF line ends too.
    plain = exported.content.decode("utf-8").removeprefix("﻿").replace("\r\n", "\n")
    assert _upload(client, "persons", plain).json()["unchanged"] == 1


def test_an_export_imports_as_updates_also_without_employee_numbers(
    client: TestClient, admin: LoggedIn, factory: sessionmaker[Session]
) -> None:
    """The export's ``id`` column identifies the person: a person without an employee number is
    updated, not created again (which would duplicate them and leave their devices behind)."""
    for body in (
        {"name": "Numarasız Kişi", "title": "Uzman"},
        {"name": "Numaralı Kişi", "employee_no": "7002", "title": "Uzman"},
    ):
        assert client.post(f"{API}/persons", json=body).status_code == 201
    ids = {name: person.id for name, person in _persons(factory).items()}
    exported = client.get(f"{API}/persons/export.csv").content.decode("utf-8")
    same = _upload(client, "persons", exported).json()
    assert same["errors"] == []
    assert (same["created"], same["updated"], same["unchanged"]) == (0, 0, 2)
    edited = exported.replace(";Uzman;", ";Şef;")
    preview = _upload(client, "persons", edited).json()
    assert preview["errors"] == []
    assert (preview["created"], preview["updated"], preview["unchanged"]) == (0, 2, 0)
    shown = sorted((p["person_id"], p["action"], p["changes"]) for p in preview["preview"])
    assert shown == sorted(
        [
            (ids["Numarasız Kişi"], "update", ["title"]),
            (ids["Numaralı Kişi"], "update", ["title"]),
        ]
    )
    assert _upload(client, "persons", edited, dry_run=False).json()["errors"] == []
    persons = _persons(factory)
    assert set(persons) == {"Numarasız Kişi", "Numaralı Kişi"}  # nobody duplicated
    assert {person.title for person in persons.values()} == {"Şef"}
    assert persons["Numarasız Kişi"].id == ids["Numarasız Kişi"]
    # Without the id column a line without an employee number still adds a person.
    plain = _lines("name;title", "Numarasız Kişi;Şef")
    assert _upload(client, "persons", plain).json()["preview"][0]["duplicate_name"] is True


def test_person_id_errors_per_line(
    client: TestClient, admin: LoggedIn, factory: sessionmaker[Session]
) -> None:
    with factory() as session:
        first = add_person(session, "Bir", employee_no="1").id
        second = add_person(session, "İki", employee_no="2").id
        third = add_person(session, "Üç").id
        fourth = add_person(session, "Dört", employee_no="4").id
        session.commit()
    data = _lines(
        "id;name;employee_no",
        "999999;Yok;",  # 2 unknown id
        "abc;Harf;",  # 3 not an id
        f"{first};Bir;4",  # 4 the employee number of another person
        f"{third};Üç;",  # 5 fine, unchanged
        f"{third};Üç Tekrar;",  # 6 the same person again
        f"{second};İki;3",  # 7 fine: a new number for an existing person
        ";Yeni;2",  # 8 that person's old number: the same person again
        ";Boş id;",  # 9 fine: a new person
    )
    result = _upload(client, "persons", data, dry_run=False).json()
    texts = {e["line"]: e for e in result["errors"]}
    assert sorted(texts) == [2, 3, 4, 6, 8]
    assert texts[2]["message_tr"].startswith("id 999999 olan kişi yok")
    assert "id column" in texts[3]["message_en"]
    assert f"(id {fourth})" in texts[4]["message_tr"]
    assert "5. satırında" in texts[6]["message_tr"]
    assert "line 7" in texts[8]["message_en"]
    assert (result["created"], result["updated"], result["unchanged"]) == (1, 1, 1)
    assert _persons(factory)["İki"].employee_no == "2"  # all or nothing
    good = _lines("id;name;employee_no", f"{second};İki;3", f"{third};Üç;", ";Boş id;")
    applied = _upload(client, "persons", good, dry_run=False).json()
    assert applied["errors"] == []
    persons = _persons(factory)
    assert (persons["İki"].id, persons["İki"].employee_no) == (second, "3")
    assert set(persons) == {"Bir", "İki", "Üç", "Dört", "Boş id"}


@pytest.mark.parametrize(
    ("data", "status", "code"),
    [
        ("Ad;Soyad\r\nA;B\r\n", 422, "csv_header"),  # unknown column
        ("department\r\nX\r\n", 422, "csv_header"),  # missing name
        ("name;name\r\nA;B\r\n", 422, "csv_header"),  # repeated column
        ("name\r\n", 422, "csv_empty"),
        ("", 422, "csv_empty"),
        ('name\r\n"open quote\r\n', 200, None),  # an open quote runs to the end of the file
    ],
)
def test_file_level_problems(
    client: TestClient, admin: LoggedIn, data: str, status: int, code: str | None
) -> None:
    response = _upload(client, "persons", data)
    assert response.status_code == status, response.text
    if code is not None:
        assert response.json()["error"]["code"] == code


def test_encoding_size_and_row_limits(
    client: TestClient, admin: LoggedIn, monkeypatch: pytest.MonkeyPatch
) -> None:
    windows = "name\r\nIşık\r\n".encode("cp1254")
    response = _upload(client, "persons", windows)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "csv_encoding"
    assert "CSV UTF-8" in response.json()["error"]["message_tr"]
    big = b"name\r\n" + b"x\r\n" * (csvfiles.MAX_BYTES // 3 + 10)
    response = _upload(client, "persons", big)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "csv_too_large"
    # A body above the limit plus the multipart framing is refused while it streams in.
    huge = _upload(client, "persons", b"name\r\n" + b"x" * (csvfiles.MAX_BYTES + 70_000))
    assert huge.status_code == 413
    assert huge.json()["error"]["code"] == "csv_too_large"
    monkeypatch.setattr(csvfiles, "MAX_ROWS", 3)
    many = _lines("name", "A", "B", "C", "D", "E")
    response = _upload(client, "persons", many)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "csv_too_many_rows"
    assert "5 satır" in response.json()["error"]["message_tr"]


def test_upload_must_be_multipart_with_a_file_field(client: TestClient, admin: LoggedIn) -> None:
    raw = client.post(
        f"{API}/imports/persons", content=b"name\r\nA\r\n", headers={"content-type": "text/csv"}
    )
    assert raw.status_code == 422
    assert raw.json()["error"]["code"] == "upload_invalid"
    other = client.post(
        f"{API}/imports/persons", files={"upload": ("x.csv", b"name\r\nA\r\n", "text/csv")}
    )
    assert other.json()["error"]["code"] == "upload_invalid"
    assert (
        client.post(
            f"{API}/imports/people", files={"file": ("x.csv", b"name\r\nA\r\n", "text/csv")}
        ).status_code
        == 422
    )


def test_multipart_reader_returns_the_exact_bytes() -> None:
    content = "name\r\nIşık\r\n\r\n--not-the-boundary\r\n".encode()
    body = (
        b"--XyZ\r\n"
        b'Content-Disposition: form-data; name="other"\r\n\r\nvalue\r\n'
        b"--XyZ\r\n"
        b'Content-Disposition: form-data; name="file"; filename="a.csv"\r\n'
        b"Content-Type: text/csv\r\n\r\n" + content + b"\r\n--XyZ--\r\n"
    )
    assert multipart_file('multipart/form-data; boundary="XyZ"', body) == content
    assert multipart_file("multipart/form-data; boundary=XyZ", body) == content
    with pytest.raises(ApiError):
        multipart_file("multipart/form-data", body)
    with pytest.raises(ApiError):
        multipart_file("multipart/form-data; boundary=XyZ", body, field="missing")


def test_csv_reader_details() -> None:
    table = csvfiles.read_csv(
        '﻿Name ; EMAIL\r\n\'=a;b\r\n\r\n"x\ny";z\r\nshort\r\n'.encode(),
        required=("name",),
        known=("email",),
    )
    assert table.columns == ("name", "email")
    assert [(row.line, row.values) for row in table.rows] == [
        (2, {"name": "=a", "email": "b"}),
        (4, {"name": "x\ny", "email": "z"}),
        (6, {"name": "short"}),
    ]
    # An export's key column is kept, its other columns dropped; only the template's columns are
    # named when a header is refused.
    keyed = csvfiles.read_csv(
        b"id;name;created_at\r\n7;A;x\r\n",
        required=("name",),
        known=("email",),
        ignored=("created_at",),
        export_keys=("id",),
    )
    assert [row.values for row in keyed.rows] == [{"id": "7", "name": "A"}]
    with pytest.raises(ApiError) as refused:
        csvfiles.read_csv(
            b"name;nope\r\nA;B\r\n", required=("name",), known=("email",), export_keys=("id",)
        )
    assert refused.value.message_en.endswith("Expected columns: name, email.")
    assert csvfiles.detect_separator('"a;b",c,d') == ","
    assert csvfiles.detect_separator("a;b,c") == ";"
    assert csvfiles.parse_bool("EVET") is True
    assert csvfiles.parse_bool("HAYIR") is False
    assert csvfiles.parse_bool("Pasİf") is False
    assert csvfiles.parse_bool("belki") is None


def test_parse_time_accepts_iso_and_turkish_dates() -> None:
    assert parse_time("2026-10-01T09:00:00+03:00") == datetime(2026, 10, 1, 6, 0, tzinfo=UTC)
    assert parse_time("2026-10-01 09:00") == datetime(2026, 10, 1, 9, 0, tzinfo=SITE_TZ)
    assert parse_time("01.10.2026 09:30") == datetime(2026, 10, 1, 9, 30, tzinfo=SITE_TZ)
    assert parse_time("01.10.2026 09:30:15") == datetime(2026, 10, 1, 9, 30, 15, tzinfo=SITE_TZ)
    assert parse_time("01.10.2026") == datetime(2026, 10, 1, tzinfo=SITE_TZ)
    with pytest.raises(ValueError, match="yarın"):
        parse_time("yarın")


# -- assignments ----------------------------------------------------------------------------------


@pytest.fixture
def inventory(factory: sessionmaker[Session]) -> dict[str, int]:
    with factory() as session:
        ayse = add_person(session, "Ayşe Işık", employee_no="1001", department="Finans")
        mehmet = add_person(session, "Mehmet Öz", employee_no="1002")
        add_person(session, "İki Kişi")
        add_person(session, "İki Kişi")
        gone = add_person(session, "Ayrıldı", employee_no="0999", active=False)
        for mac in (PC, PHONE, PRINTER):
            add_device(session, mac)
        session.commit()
        return {"ayse": ayse.id, "mehmet": mehmet.id, "gone": gone.id}


def _history(
    factory: sessionmaker[Session], mac: str
) -> list[tuple[int, datetime, datetime | None]]:
    with factory() as session:
        rows = session.scalars(
            select(DeviceAssignment)
            .where(DeviceAssignment.mac == mac)
            .order_by(DeviceAssignment.valid_from)
        )
        return [(row.person_id, row.valid_from, row.valid_to) for row in rows]


def test_assignments_build_history_in_file_order(
    client: TestClient, admin: LoggedIn, factory: sessionmaker[Session], inventory: dict[str, int]
) -> None:
    data = _lines(
        "mac;employee_no;person_name;valid_from;note",
        "F0-1F-AF-00-00-01;1001;;01.09.2026 09:00;ilk zimmet",
        "f01f.af00.0001;;Mehmet Öz;2026-10-01T08:00:00+03:00;devir",
        "02:00:5e:00:53:46;1001;Ayşe Işık;;",
    )
    preview = _upload(client, "assignments", data).json()
    assert preview["errors"] == []
    assert (preview["rows"], preview["created"], preview["updated"]) == (3, 2, 1)
    assert [(p["line"], p["action"], p["mac"]) for p in preview["preview"]] == [
        (2, "assign", PC),
        (3, "reassign", PC),
        (4, "assign", PHONE),
    ]
    assert preview["preview"][1]["previous_person_name"] == "Ayşe Işık"
    assert _history(factory, PC) == []
    applied = _upload(client, "assignments", data, dry_run=False).json()
    assert applied["errors"] == []
    september = datetime(2026, 9, 1, 9, 0, tzinfo=SITE_TZ)
    october = datetime(2026, 10, 1, 8, 0, tzinfo=SITE_TZ)
    assert _history(factory, PC) == [
        (inventory["ayse"], september, october),
        (inventory["mehmet"], october, None),
    ]
    with factory() as session:
        assert session.get_one(Device, PC).person_id == inventory["mehmet"]
        assert session.get_one(Device, PHONE).person_id == inventory["ayse"]
        note = session.scalars(
            select(DeviceAssignment.note).where(DeviceAssignment.valid_from == september)
        ).one()
        assert note == "ilk zimmet"
    (entry,) = _audit(factory, "import.assignments")
    assert (entry.details["created"], entry.details["updated"], entry.details["rows"]) == (2, 1, 3)
    # Applying the last line again changes nothing.
    again = _upload(
        client, "assignments", _lines("mac;employee_no", f"{PHONE};1001"), dry_run=False
    )
    assert again.json()["unchanged"] == 1
    assert len(_audit(factory, "import.assignments")) == 2


def test_assignment_errors_per_line(
    client: TestClient, admin: LoggedIn, factory: sessionmaker[Session], inventory: dict[str, int]
) -> None:
    future = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    data = _lines(
        "mac;employee_no;person_name;valid_from;note",
        "zz:zz;1001;;;",  # 2 invalid MAC
        "02:00:00:00:00:99;1001;;;",  # 3 never seen
        f"{PC};4040;;;",  # 4 unknown number
        f"{PC};;Kimse;;",  # 5 unknown name
        f"{PC};;İki Kişi;;",  # 6 ambiguous
        f"{PC};1001;Mehmet Öz;;",  # 7 mismatch
        f"{PC};0999;;;",  # 8 inactive
        f"{PC};1001;;dün;",  # 9 unreadable time
        f"{PC};1001;;{future};",  # 10 future
        f"{PRINTER};;;;",  # 11 no person
        f"{PHONE};1002;;01.10.2026 10:00;",  # 12 fine
        f"{PHONE};1001;;01.09.2026 10:00;",  # 13 before the line above
        ";1001;;;",  # 14 no MAC
    )
    result = _upload(client, "assignments", data, dry_run=False).json()
    lines = [e["line"] for e in result["errors"]]
    assert lines == [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 14]
    texts = {e["line"]: e for e in result["errors"]}
    assert "henüz görülmedi" in texts[3]["message_tr"]
    assert "employee_no" in texts[6]["message_en"]
    assert "pasif" in texts[8]["message_tr"]
    assert "gelecekte" in texts[10]["message_tr"]
    assert "yalnızca ileriye" in texts[13]["message_tr"]
    assert "01.10.2026 10:00:00" in texts[13]["message_tr"]
    assert result["created"] == 1  # what line 12 would have done
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(DeviceAssignment)) == 0


def test_a_deactivation_before_the_apply_locks_is_seen(
    client: TestClient,
    admin: LoggedIn,
    factory: sessionmaker[Session],
    inventory: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A person deactivated after the import read the persons but before it locked them: the
    locked, reloaded row is checked again, so the inactive person gets no device."""
    real = csvimport.lock_persons

    def deactivate_first(db: Session, ids: Any) -> None:
        with factory() as other:  # another connection, committed before the lock is taken
            other.get_one(Person, inventory["ayse"]).active = False
            other.commit()
        real(db, ids)

    monkeypatch.setattr(csvimport, "lock_persons", deactivate_first)
    data = _lines("mac;employee_no", f"{PC};1001", f"{PHONE};1002")
    result = _upload(client, "assignments", data, dry_run=False).json()
    assert [e["line"] for e in result["errors"]] == [2]
    assert "pasif" in result["errors"][0]["message_tr"]
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(DeviceAssignment)) == 0
    assert _audit(factory, "import.assignments") == []


def test_an_apply_locks_the_persons_before_the_devices(
    client: TestClient,
    admin: LoggedIn,
    factory: sessionmaker[Session],
    inventory: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Like ``POST /devices/assign``: the persons named are locked ``FOR SHARE`` (in id order)
    before the devices, so a deactivation cannot commit until the assignments have."""
    real = lock_devices
    blocked: list[bool] = []

    def try_to_deactivate(db: Session, macs: Any) -> dict[str, int | None]:
        with factory() as other:
            other.execute(text("SET LOCAL lock_timeout = '200ms'"))
            try:
                other.execute(
                    update(Person).where(Person.id == inventory["ayse"]).values(active=False)
                )
            except OperationalError:
                blocked.append(True)
            else:
                blocked.append(False)
            other.rollback()
        return real(db, macs)

    monkeypatch.setattr(csvimport, "lock_devices", try_to_deactivate)
    data = _lines("mac;employee_no", f"{PC};1001")
    result = _upload(client, "assignments", data, dry_run=False).json()
    assert result["errors"] == []
    assert blocked == [True]
    with factory() as session:
        assert session.get_one(Person, inventory["ayse"]).active is True
        assert session.get_one(Device, PC).person_id == inventory["ayse"]


def test_assignment_header_and_templates(
    client: TestClient, admin: LoggedIn, inventory: dict[str, int]
) -> None:
    missing = _upload(client, "assignments", _lines("mac;note", f"{PC};x"))
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "csv_header"
    for kind, header in (
        ("persons", "name;department;title;email;employee_no;active;notes"),
        ("assignments", "mac;employee_no;person_name;valid_from;note"),
    ):
        response = client.get(f"{API}/imports/{kind}/template.csv")
        assert response.status_code == 200
        text = response.content.decode("utf-8")
        assert text.startswith("﻿" + header + "\r\n")
        assert len(text.strip().splitlines()) == 2
        # A template imports cleanly as a dry run (once its example person exists).
    persons = _upload(client, "persons", client.get(f"{API}/imports/persons/template.csv").content)
    assert persons.json()["errors"] == []
    assert client.get(f"{API}/imports/devices/template.csv").status_code == 422


def test_many_errors_are_capped(client: TestClient, admin: LoggedIn) -> None:
    data = _lines("name;email", *[f"A{i};bad" for i in range(250)])
    errors = _upload(client, "persons", data).json()["errors"]
    assert len(errors) == 201
    assert errors[-1]["line"] == 202
    assert "50 hata daha" in errors[-1]["message_tr"]
