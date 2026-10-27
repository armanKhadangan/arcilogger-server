"""SQL safety of Arama (contract 2.5.1, 3.2): safe by construction, and checked.

* **Injection property:** queries built from the grammar with adversarial values (``';--``,
  ``$$``, ``\\``, ``%``, ``_``, bidi characters, 256-character values) compile to SQL that holds
  none of them: every value is a bound parameter, the only literal is ``ESCAPE '\\'``, and every
  word of the SQL text is an identifier of the models, a label of the search module or SQL.
* **Lint:** no ``text(``, ``literal_column``, ``exec_driver_sql`` and no f-string SQL in
  ``arcilogger_api/search/``; the router's only SQL text is the ``SET TRANSACTION`` constant.
* **Read-only:** a write inside a search transaction fails, through the API too; the setting of
  that transaction (``plan_cache_mode`` on TimescaleDB) ends with it, on any storage mode.
* **Execution property:** every query the parser accepts answers 200, a known 4xx or 504 —
  never 500.
"""

from __future__ import annotations

import ast
import ipaddress
import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import Select, func, insert, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.routers import search as search_router
from arcilogger_api.search import FIELDS, SearchQueryError, parse_query, sql
from arcilogger_api.search.cursor import Cursor, bounds
from arcilogger_api.search.fields import DepartmentEntry, SiteNetwork
from arcilogger_api.search.messages import STATUS
from arcilogger_api.search.planner import plan_query
from arcilogger_api.search.testing import Lines, sign_in
from arcilogger_api.search.windows import Storage
from arcilogger_api.security import CurrentUser
from arcilogger_api.testing import LoggedIn
from arcilogger_db.models import Base, Message, SearchCount
from arcilogger_db.settings import get_settings, set_setting

SEARCH_PACKAGE = Path(search_router.__file__).resolve().parents[1] / "search"
DIALECT = postgresql.psycopg.dialect()  # type: ignore[no-untyped-call]
NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
SITE = SiteNetwork(
    vlans={
        41: ipaddress.ip_network("172.20.41.0/24"),
        42: ipaddress.ip_network("172.20.42.0/24"),
        60: ipaddress.ip_network("172.20.60.0/22"),
    },
    departments=(DepartmentEntry(1, "Finans", (41,)), DepartmentEntry(2, "İdari İşler", (42, 60))),
    network_sources=frozenset({"ap"}),
)


@pytest.fixture
def searcher(client: TestClient, app_session_factory: sessionmaker[Session]) -> LoggedIn:
    """An administrator with the KVKK notice accepted; searches are not rate-limited here."""
    with app_session_factory() as session:
        set_setting(session, "search.runs_per_minute", 600, updated_by="test")
        session.commit()
    return sign_in(client, app_session_factory)


@pytest.fixture
def filled(app_session_factory: sessionmaker[Session]) -> None:
    """A few lines of several kinds in the last day."""
    lines = Lines(app_session_factory)
    lines.add(
        NOW - timedelta(hours=2),
        "fg_web",
        srcip="172.20.41.5",
        hostname="www.example.com",
        action="blocked",
        text="url=/a%_b",
    )
    lines.add(
        NOW - timedelta(hours=3),
        "fg_traffic",
        srcip="172.20.42.7",
        dstip="203.0.113.10",
        dstport=443,
        action="accept",
    )
    lines.add(
        NOW - timedelta(hours=4),
        "mt_dhcp",
        source="core-r1",
        ip="172.20.60.9",
        mac="00:00:5e:00:53:09",
    )
    lines.add(NOW - timedelta(hours=5), None, text="login failure İşlem ';--", host="ap-kat2")


# -- the injection property -----------------------------------------------------------------------

#: Values that would break out of a string literal, a LIKE pattern or an identifier.
ADVERSARIAL = (
    "';--",
    "$$",
    "\\",
    "%",
    "_",
    "\u202eevil",
    "a'); DROP TABLE messages; --",
    '" OR 1=1 --',
    "x" * 240,
    "İı Şş",
    "/*",
    "*/",
    "::text",
)
MARKER = "zqmark"


def _quoted(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


@st.composite
def hostile_values(draw: st.DrawFn) -> str:
    parts = draw(st.lists(st.sampled_from(ADVERSARIAL), min_size=1, max_size=3))
    return (MARKER + "".join(parts))[:250]


@st.composite
def hostile_queries(draw: st.DrawFn) -> str:
    """Fields with free values carry a marked hostile value; the others valid ones."""
    free = [
        name
        for name, entry in FIELDS.items()
        if entry.form in ("text", "url", "freetext", "token", "department", "source")
    ]
    clauses = []
    for _ in range(draw(st.integers(1, 4))):
        name = draw(st.sampled_from(free))
        value = _quoted(draw(hostile_values()))
        if FIELDS[name].form == "source":
            value = MARKER + "core"
        sign = draw(st.sampled_from(["", "-"]))
        clauses.append(f"{sign}{name}:{value}" if name != "text" else f"{sign}{value}")
    clauses.append(
        draw(
            st.sampled_from(
                [
                    "",
                    "srcip:172.20.41.0/24",
                    "type:web",
                    "vlan:41",
                    "department:Finans",
                    "domain:example.com",
                ]
            )
        )
    )
    return " ".join(clause for clause in clauses if clause)


#: Words SQL itself contributes (keywords, functions, types).
SQL_WORDS = {
    "select",
    "from",
    "where",
    "and",
    "or",
    "not",
    "as",
    "on",
    "join",
    "left",
    "outer",
    "order",
    "by",
    "desc",
    "asc",
    "limit",
    "union",
    "all",
    "in",
    "is",
    "null",
    "cast",
    "inet",
    "cidr",
    "macaddr",
    "integer",
    "varchar",
    "text",
    "bigint",
    "date",
    "timestamp",
    "with",
    "time",
    "zone",
    "lower",
    "upper",
    "reverse",
    "coalesce",
    "false",
    "true",
    "like",
    "ilike",
    "escape",
    "between",
    "case",
    "when",
    "then",
    "else",
    "end",
    "count",
    "sum",
    "grouping",
    "group",
    "sets",
    "date_bin",
    "network",
    "set_masklen",
    "unnest",
    "array",
    "interval",
}
#: Labels and aliases the search module gives.
LABELS = {
    "b",
    "bucket",
    "kind",
    "action",
    "net",
    "n",
    "g",
    "page",
    "rows",
    "newest",
    "split",
    "counted",
    "typed",
    "m",
    "k",
    "client",
    "hour",
    "anon_1",
    "anon_2",
}


def _identifiers() -> set[str]:
    names = set(Base.metadata.tables)
    for table in Base.metadata.tables.values():
        names |= {column.name for column in table.columns}
    return names


ALLOWED = SQL_WORDS | LABELS | _identifiers()
#: A bound parameter (``IN`` lists are expanded into parameters when the statement runs).
_PLACEHOLDER = re.compile(
    r"%\(\w+\)s(?:::(?:TIMESTAMP WITH TIME ZONE|[A-Z]+)(?:\[\])?)?|__\[POSTCOMPILE_\w+\]"
)


def _values(params: dict[str, Any]) -> Iterator[str]:
    for value in params.values():
        if isinstance(value, list | tuple):
            yield from (str(item) for item in value)
        else:
            yield str(value)


def check_compiled(statement: Select[Any]) -> None:
    compiled = statement.compile(dialect=DIALECT)
    text_ = compiled.string
    assert MARKER not in text_
    for fragment in ADVERSARIAL:
        if fragment not in ("_", "%", "\\") and len(fragment) > 2:
            assert fragment not in text_
    bare = _PLACEHOLDER.sub(" ", text_.replace("ESCAPE '\\'", " "))
    assert "'" not in bare, "a literal in the SQL text"
    assert "$" not in bare
    assert "--" not in bare
    assert "/*" not in bare
    words = {word.lower() for word in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", bare)}
    assert words <= ALLOWED, words - ALLOWED


def statements(text_: str) -> list[Select[Any]]:
    plan = plan_query(parse_query(text_), SITE)
    start, end = NOW - timedelta(days=2), NOW
    found = [
        sql.page_statement(plan, start, bounds(None, end, plan.ranks), 101),
        sql.page_statement(
            plan, start, bounds(Cursor.last(NOW, max(plan.ranks), 7), end, plan.ranks), 11
        ),
        sql.page_statement(  # TimescaleDB: each branch split at its compression horizon
            plan,
            start,
            bounds(None, end, plan.ranks),
            101,
            dict.fromkeys(plan.tables, NOW - timedelta(days=1)),
        ),
        sql.count_statement(plan, start, end, 3600),
        sql.count_statement(plan, start, end, 3600, keys=True, limit=1000),
    ]
    if plan.rollup is not None:
        found.append(sql.rollup_statement(plan.rollup, start, end, 3600))
    return found


@settings(max_examples=400, deadline=None)
@given(hostile_queries())
def test_hostile_values_are_only_ever_parameters(text_: str) -> None:
    try:
        statements_ = statements(text_)
    except SearchQueryError:
        return  # refused before any SQL exists
    for statement in statements_:
        check_compiled(statement)
        compiled = statement.compile(dialect=DIALECT)
        assert any(MARKER in value for value in _values(compiled.params))


@pytest.mark.parametrize(
    "text_",
    [
        "",
        "srcip:172.20.41.105 type:web action:blocked",
        'domain:*.example.com "youtube" -severity:<=err',
        "mac:00:00:5e:* type:dhcp",
        "ip:203.0.113.10 dstport:80,443 proto:tcp",
        "department:Finans vlan:42 -action:accept",
        "source:ap error:fortigate.*",
        "policy:12 policy:Internet* sessionid:>=5",
        "qname:www.example.com qtype:aaaa",
    ],
)
def test_every_kind_of_condition_compiles_safely(text_: str) -> None:
    for statement in statements(text_):
        check_compiled(statement)


def test_like_patterns_are_escaped() -> None:
    plan = plan_query(parse_query('user:"50%_\\\\x"'), SITE)
    compiled = sql.page_statement(
        plan, NOW - timedelta(hours=1), bounds(None, NOW, plan.ranks), 5
    ).compile(dialect=DIALECT)
    assert "50%_\\x" not in compiled.string
    assert "50%_\\x" in _values(compiled.params)
    plan = plan_query(parse_query("user:50%_*"), SITE)
    compiled = sql.page_statement(
        plan, NOW - timedelta(hours=1), bounds(None, NOW, plan.ranks), 5
    ).compile(dialect=DIALECT)
    assert "50\\%\\_%" in _values(compiled.params)


# -- the lint test --------------------------------------------------------------------------------

SQL_KEYWORDS = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE|WHERE|FROM|SET|DROP)\b", re.IGNORECASE)
FORBIDDEN_CALLS = {"text", "literal_column", "exec_driver_sql", "TextClause"}


def _problems(path: Path, allowed_text: set[str]) -> list[str]:
    tree = ast.parse(path.read_text("utf-8"))
    found = []
    constants: dict[str, Any] = {}
    for node in ast.walk(tree):
        target = node.targets[0] if isinstance(node, ast.Assign) else getattr(node, "target", None)
        value = getattr(node, "value", None)
        if (
            isinstance(node, ast.Assign | ast.AnnAssign)
            and isinstance(target, ast.Name)
            and isinstance(value, ast.Constant)
        ):
            constants[target.id] = value.value
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else getattr(node.func, "id", "")
            )
            if name in FORBIDDEN_CALLS:
                argument = node.args[0] if node.args else None
                if not (isinstance(argument, ast.Name) and argument.id in allowed_text):
                    found.append(f"{path.name}:{node.lineno}: {name}(…)")
        if isinstance(node, ast.JoinedStr):
            literal = "".join(
                part.value
                for part in node.values
                if isinstance(part, ast.Constant) and isinstance(part.value, str)
            )
            if SQL_KEYWORDS.search(literal):
                found.append(f"{path.name}:{node.lineno}: f-string SQL")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "format"
            and isinstance(node.func.value, ast.Constant)
            and isinstance(node.func.value.value, str)
            and SQL_KEYWORDS.search(node.func.value.value)
        ):
            found.append(f"{path.name}:{node.lineno}: str.format SQL")
    for name in allowed_text:
        assert str(constants.get(name, "")).startswith("SET TRANSACTION"), name
    return found


def test_no_sql_text_in_the_search_package() -> None:
    files = sorted(SEARCH_PACKAGE.glob("*.py"))
    assert {"planner.py", "sql.py", "windows.py", "stats.py", "export.py"} <= {
        f.name for f in files
    }
    problems = [problem for path in files for problem in _problems(path, set())]
    assert problems == []


def test_the_router_sends_only_the_read_only_statement() -> None:
    path = Path(search_router.__file__)
    assert _problems(path, {"READ_ONLY"}) == []
    assert search_router.READ_ONLY == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"


# -- read-only ------------------------------------------------------------------------------------


def _user(user_id: int = 1) -> CurrentUser:
    return CurrentUser(
        id=user_id,
        username="test",
        display_name="Test",
        role="admin",
        permissions=frozenset({"search.read"}),
        must_change_password=False,
        mfa_passed=True,
        session_token_hash="0" * 64,
        reauthenticated_at=None,
    )


def test_a_write_inside_the_search_transaction_fails(
    app_session_factory: sessionmaker[Session],
) -> None:
    state = search_router.SearchState()
    with app_session_factory() as session:
        settings_ = get_settings(session, prefix="search.")
        with (
            search_router.reading(session, state, _user(), settings_),
            pytest.raises(DBAPIError) as refused,
        ):
            session.execute(
                insert(SearchCount).values(
                    hour=NOW, source="x", raw_day=NOW.date(), kind="web", tbl="", n=1
                )
            )
        assert getattr(refused.value.orig, "sqlstate", None) == "25006"  # read_only_sql_transaction
        assert session.scalar(select(SearchCount.id)) is None


@pytest.mark.parametrize("timescale", [True, False])
def test_the_custom_plans_last_as_long_as_the_search_transaction(
    app_session_factory: sessionmaker[Session], timescale: bool
) -> None:
    """The statements after a search on its pooled connection are planned as before."""
    state = search_router.SearchState()
    state.storage.get_or_compute(lambda: Storage(timescale, {}))  # as on TimescaleDB, or not
    show = select(func.current_setting("plan_cache_mode"))
    with app_session_factory() as session:
        settings_ = get_settings(session, prefix="search.")
        before = session.scalar(show)
        with search_router.reading(session, state, _user(), settings_):
            inside = session.scalar(show)
            pooled = session.connection().connection.dbapi_connection
        assert pooled is not None
        after = pooled.execute("SELECT current_setting('plan_cache_mode')").fetchone()
        pooled.rollback()  # back to the pool as it was
        again = session.scalar(show)
    assert before == "auto"
    assert inside == ("force_custom_plan" if timescale else "auto")
    assert after == ("auto",)
    assert again == "auto"


def test_a_write_through_the_api_never_lands(
    app: FastAPI,
    client: TestClient,
    searcher: LoggedIn,
    app_session_factory: sessionmaker[Session],
) -> None:
    Lines(app_session_factory).add(NOW - timedelta(minutes=5), "fg_web", srcip="172.20.41.5")

    def write(session: Session, _lower: datetime, _upper: datetime) -> None:
        session.execute(
            insert(Message).values(
                time=NOW,
                time_received=NOW,
                source="x",
                sender="172.20.41.9",
                type="syslog",
                raw_day=NOW.date(),
                raw_line=1,
                raw_offset=0,
            )
        )

    search_router.state_of(app).hook = write
    with TestClient(app, base_url="http://testserver", raise_server_exceptions=False) as quiet:
        quiet.cookies.update(client.cookies)
        quiet.headers.update(client.headers)
        response = quiet.post(
            "/api/v1/search",
            json={"q": "", "from": (NOW - timedelta(hours=1)).isoformat(), "to": NOW.isoformat()},
        )
    assert response.status_code == 500
    with app_session_factory() as session:
        assert session.scalar(select(Message.id).where(Message.source == "x")) is None


# -- the execution property -----------------------------------------------------------------------

KNOWN = {200, 504} | {status for code, status in STATUS.items()} | {422}


@st.composite
def any_queries(draw: st.DrawFn) -> str:
    plain = st.text(alphabet=st.sampled_from(list(' "\\,:()-*.<>=/_aqsrcip0145İş')), max_size=40)
    return draw(
        st.one_of(
            hostile_queries(),
            plain,
            st.sampled_from(
                [
                    "srcip:172.20.41.5",
                    "vlan:99",
                    "department:Yok",
                    "type:web domain:example.com",
                    "mac:00:00:5e:*",
                ]
            ),
        )
    )


@settings(
    max_examples=120,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(text_=any_queries())
def test_every_accepted_query_answers_without_a_server_error(
    client: TestClient, searcher: LoggedIn, filled: None, text_: str
) -> None:
    body = {"q": text_[:1000], "from": (NOW - timedelta(days=1)).isoformat(), "to": NOW.isoformat()}
    response = client.post("/api/v1/search", json=body)
    assert response.status_code in KNOWN, response.text
    assert response.status_code != 500
    if response.status_code == 200:
        stats = client.post(
            "/api/v1/search/stats", json={"search_id": response.json()["search_id"]}
        )
        assert stats.status_code == 200, stats.text
