"""The narrow editor of arcilogger.toml (ADR-0014; contract 2.7.3)."""

from __future__ import annotations

import difflib
import tomllib
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from arcilogger_core.admin.tomledit import (
    MANAGED_BEGIN,
    MANAGED_END,
    SetSources,
    SetValue,
    TomlEditError,
    _installer_sources,
    _installer_string,
    edit,
    expected,
    source_table,
    toml_string,
)
from arcilogger_core.config import SourceConfig, parse_config

EXAMPLE = Path(__file__).parents[2] / "deploy" / "windows" / "arcilogger.example.toml"
WHERE = Path("C:/ProgramData/ARCILOGGER/arcilogger.toml")
SWITCH = SourceConfig(name="switch-2", type="mikrotik", address="172.20.10.22")
APS = SourceConfig(
    name="ap", type="generic", address="172.20.20.0/24", heartbeat_minutes=30, expected=False
)


def example() -> bytes:
    return EXAMPLE.read_bytes()


def changed_lines(old: bytes, new: bytes) -> tuple[list[str], list[str]]:
    """The removed and the added lines of an edit."""
    diff = list(difflib.ndiff(old.decode().splitlines(), new.decode().splitlines()))
    return (
        [line[2:] for line in diff if line.startswith("- ")],
        [line[2:] for line in diff if line.startswith("+ ")],
    )


# -- golden edits of the shipped example -----------------------------------------------------------


def test_no_edit_gives_the_identical_bytes() -> None:
    old = example()
    assert edit(old, [], path=WHERE) is old
    crlf = example().replace(b"\n", b"\r\n")
    assert edit(crlf, [], path=WHERE) == crlf


def test_the_site_name_is_replaced_in_place_with_its_comment() -> None:
    new = edit(example(), [SetValue("site_name", 'Örnek "A.Ş." \\ Holding')], path=WHERE)
    removed, added = changed_lines(example(), new)
    assert removed == [
        'site_name = ""                 # the organisation\'s name (reports, e-mails); required: '
        "-SiteName"
    ]
    assert added == [
        'site_name = "Örnek \\"A.Ş.\\" \\\\ Holding"                 # the organisation\'s name '
        "(reports, e-mails); required: -SiteName"
    ]
    assert tomllib.loads(new.decode())["site_name"] == 'Örnek "A.Ş." \\ Holding'


@pytest.mark.parametrize(
    ("edits", "removed", "added"),
    [
        (
            [SetValue("clock.ntp_server", "172.20.10.1"), SetValue("clock.alert_offset_ms", 500)],
            ['ntp_server = ""', "alert_offset_ms = 1000"],
            ['ntp_server = "172.20.10.1"', "alert_offset_ms = 500"],
        ),
        (
            [SetValue("tsa.url", "http://zd.kamusm.gov.tr"), SetValue("tsa.timeout_seconds", 90)],
            ['url = "http://tzd.kamusm.gov.tr"'],
            ['url = "http://zd.kamusm.gov.tr"', "timeout_seconds = 90"],
        ),
        (
            [SetValue("tsa.legal_root_fingerprints", ["a1" * 32, "b2" * 32])],
            ["legal_root_fingerprints = []"],
            [f'legal_root_fingerprints = ["{"a1" * 32}", "{"b2" * 32}"]'],
        ),
        (
            [SetValue("smtp.host", "smtp.ornek.example"), SetValue("smtp.enabled", False)],
            [],
            ['host = "smtp.ornek.example"'],
        ),
        (
            [SetValue("replica.target", "\\\\NAS-01\\arcilogger-worm")],
            ['target = "//<NAS>/arcilogger-worm"   # a share on a NAS or another server '
             "(placeholder)"],
            ['target = "\\\\\\\\NAS-01\\\\arcilogger-worm"   # a share on a NAS or another server '
             "(placeholder)"],
        ),
        (
            [SetValue("retention.enabled", True), SetValue("retention.days", 800)],
            ["enabled = false                # keep everything during the first two years",
             "days = 760                     # minimum 730"],
            ["enabled = true                # keep everything during the first two years",
             "days = 800                     # minimum 730"],
        ),
        (
            [SetValue("backup.keep_daily", 30), SetValue("backup.enabled", False)],
            ["enabled = true", "keep_daily = 14                # keep the newest dump of each of "
             "the last 14 days ..."],
            ["enabled = false", "keep_daily = 30                # keep the newest dump of each of "
             "the last 14 days ..."],
        ),
    ],
)  # fmt: skip
def test_golden_edits_of_each_section(
    edits: list[SetValue], removed: list[str], added: list[str]
) -> None:
    for newline in (b"\n", b"\r\n"):
        old = example().replace(b"\n", newline)
        new = edit(old, edits, path=WHERE)
        assert changed_lines(old, new) == (removed, added)
        assert new.count(b"\n") == new.count(newline)  # the newline style is kept
        assert new.count(b"#") == old.count(b"#")  # every comment stays


def test_a_missing_key_goes_after_the_last_key_of_its_table() -> None:
    new = edit(example(), [SetValue("smtp.host", "smtp.ornek.example")], path=WHERE).decode()
    smtp = new[new.index("[smtp]") :]
    assert smtp.index("timeout_seconds = 30") < smtp.index('host = "smtp.ornek.example"')
    assert smtp.index('host = "smtp.ornek.example"') < smtp.index("# ca_file")


def test_a_removed_key_loses_its_line_only() -> None:
    old = example().replace(b'# username = "<user>"', b'username = "arcilogger"')
    new = edit(old, [SetValue("smtp.username", None)], path=WHERE)
    removed, added = changed_lines(old, new)
    assert removed == ['username = "arcilogger"          # remove when the server needs no '
                       "authentication"]  # fmt: skip
    assert added == []
    assert edit(example(), [SetValue("smtp.username", None)], path=WHERE) == example()


def test_a_missing_table_is_appended() -> None:
    old = b'data_dir = "D:/ARCILOGGER"\n'
    new = edit(old, [SetValue("smtp.host", "smtp.ornek.example")], path=WHERE)
    assert new == b'data_dir = "D:/ARCILOGGER"\n\n[smtp]\nhost = "smtp.ornek.example"\n'
    no_newline = edit(b'data_dir = "D:/ARCILOGGER"', [SetValue("site_name", "x")], path=WHERE)
    assert no_newline == b'data_dir = "D:/ARCILOGGER"\nsite_name = "x"\n'


def test_a_multi_line_array_is_written_back_on_one_line() -> None:
    old = b'data_dir = "D:/x"\n\n[tsa]\njava = [\n  "java", # the default\n]\nprovider = "mock"\n'
    new = edit(
        old, [SetValue("tsa.java", ["C:/Program Files/Java/jre1.8/bin/java.exe"])], path=WHERE
    )
    assert new == (
        b'data_dir = "D:/x"\n\n[tsa]\njava = ["C:/Program Files/Java/jre1.8/bin/java.exe"]\n'
        b'provider = "mock"\n'
    )


@pytest.mark.parametrize(
    "value", ['quote " and backslash \\', "]", "[sources]", "Ç Ğ İ Ö Ş Ü ç ğ ı ö ş ü", "# x", "'"]
)
def test_strings_are_escaped(value: str) -> None:
    new = edit(b'data_dir = "D:/x"\n', [SetValue("site_name", value)], path=WHERE)
    assert tomllib.loads(new.decode())["site_name"] == value
    assert new.count(b"\n") == 2  # one line each
    assert _installer_string(new.decode(), "", "site_name") == value


@pytest.mark.parametrize("value", ["line\nbreak", "tab\there", "nul\x00", "del\x7f", "\r\n"])
def test_control_characters_are_escaped_like_the_installer_does(value: str) -> None:
    text = toml_string(value)
    assert "\n" not in text
    assert "\r" not in text
    assert tomllib.loads(f"k = {text}")["k"] == value
    assert _installer_string(f"k = {text}\n", "", "k") == value


def test_toml_string_matches_convertto_arcitomlstring() -> None:
    assert toml_string('a"b\\c\td\x7f') == '"a\\"b\\\\c\\u0009d\\u007F"'


# -- the managed block of the sources --------------------------------------------------------------


def test_the_first_source_goes_before_the_first_table_and_keeps_the_examples() -> None:
    new = edit(example(), [SetSources((SWITCH, APS))], path=WHERE).decode()
    block = new[new.index(MANAGED_BEGIN) : new.index(MANAGED_END) + len(MANAGED_END)]
    assert block == "\n".join(
        [
            MANAGED_BEGIN,
            "[[sources]]",
            'name = "switch-2"',
            'type = "mikrotik"',
            'address = "172.20.10.22"',
            "",
            "[[sources]]",
            'name = "ap"',
            'type = "generic"',
            'address = "172.20.20.0/24"',
            "heartbeat_minutes = 30",
            "expected = false",
            MANAGED_END,
        ]
    )
    assert new.index("#[[sources]]") < new.index(MANAGED_BEGIN) < new.index("\n[collector]")
    assert new.count("#[[sources]]") == 3  # the commented-out examples stay
    sources = parse_config(new.encode(), WHERE).sources
    assert sources == (SWITCH, APS)
    assert _installer_sources(new) == (2, ["172.20.10.22", "172.20.20.0/24"])


def test_the_block_is_replaced_in_place_and_can_be_emptied() -> None:
    first = edit(example(), [SetSources((SWITCH,))], path=WHERE)
    disabled = SWITCH.model_copy(update={"enabled": False, "transport": "tcp"})
    second = edit(first, [SetSources((disabled,))], path=WHERE)
    removed, added = changed_lines(first, second)
    assert removed == []
    assert added == ['transport = "tcp"', "enabled = false"]
    empty = edit(second, [SetSources(())], path=WHERE).decode()
    assert f"{MANAGED_BEGIN}\n{MANAGED_END}\n" in empty
    assert "sources" not in tomllib.loads(empty)
    again = edit(empty.encode(), [SetSources((SWITCH,))], path=WHERE)
    assert again == first


def test_existing_source_tables_become_the_block() -> None:
    old = (
        b'site_name = "x"\ndata_dir = "D:/x"\n\n# my firewall\n[[sources]]\nname = "fortigate"\n'
        b'type = "fortigate"   # the main one\naddress = "172.31.255.1"\nheartbeat_minutes = 15\n'
        b'\n[[sources]]\nname = "core-r1"\ntype = "mikrotik"\naddress = "172.20.10.1"\n'
        b'\n# --- the collector\n[collector]\nbind = "0.0.0.0"\n'
    )
    current = parse_config(old, WHERE).sources
    new = edit(old, [SetSources((*current, SWITCH))], path=WHERE).decode()
    assert new.startswith('site_name = "x"\ndata_dir = "D:/x"\n\n# my firewall\n' + MANAGED_BEGIN)
    assert "heartbeat_minutes" not in new  # the default is left out
    assert "# --- the collector\n[collector]" in new
    assert parse_config(new.encode(), WHERE).sources == (*current, SWITCH)


@pytest.mark.parametrize(
    "text",
    [
        # another table between two source tables
        b'data_dir = "D:/x"\n[[sources]]\nname = "a"\ntype = "generic"\naddress = "172.20.1.1"\n'
        b'[collector]\nbind = "0.0.0.0"\n[[sources]]\nname = "b"\ntype = "generic"\n'
        b'address = "172.20.1.2"\n',
        # a source table outside the managed block
        f'data_dir = "D:/x"\n{MANAGED_BEGIN}\n{MANAGED_END}\n[collector]\nbind = "0.0.0.0"\n'
        '[[sources]]\nname = "a"\ntype = "generic"\naddress = "172.20.1.1"\n'.encode(),
        # an inline array of tables
        b'data_dir = "D:/x"\nsources = [{name = "a", type = "generic", address = "172.20.1.1"}]\n',
        # a dotted key inside a source table
        b'data_dir = "D:/x"\n[[sources]]\nname = "a"\ntype = "generic"\naddress = "172.20.1.1"\n'
        b"x.y = 1\n",
    ],
)
def test_source_layouts_the_editor_does_not_handle_are_refused(text: bytes) -> None:
    with pytest.raises(TomlEditError) as caught:
        edit(text, [SetSources((SWITCH,))], path=WHERE)
    assert caught.value.code in ("config_unsupported_format", "validation_failed")


# -- refusals inside an edited table ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "item"),
    [
        (b'data_dir = "D:/x"\nclock.ntp_server = "a"\n', SetValue("clock.ntp_server", "b")),
        (b'data_dir = "D:/x"\nclock = {ntp_server = "a"}\n', SetValue("clock.ntp_server", "b")),
        (b'data_dir = "D:/x"\n[clock]\nntp_server = """a"""\n', SetValue("clock.ntp_server", "b")),
        (b"data_dir = \"D:/x\"\n[clock]\nntp_server = '''a'''\n",
         SetValue("clock.ntp_server", "b")),
        (b'data_dir = "D:/x"\n[smtp]\nhost = "a"\n[clock]\nx = 1\n[smtp]\nport = 1\n',
         SetValue("smtp.host", "b")),
        (b'data_dir = "D:/x"\n[tsa]\nprovider = "mock"\n[tsa.extra]\nx = 1\n',
         SetValue("tsa.port", 81)),
        (b'data_dir = "D:/x"\nsite_name = """multi\nline"""\n', SetValue("site_name", "b")),
    ],
)  # fmt: skip
def test_forms_the_editor_cannot_change_safely_are_refused(text: bytes, item: SetValue) -> None:
    with pytest.raises(TomlEditError) as caught:
        edit(text, [item], path=WHERE)
    assert caught.value.code in ("config_unsupported_format", "validation_failed")


def test_other_tables_may_use_any_form() -> None:
    old = (
        b'data_dir = "D:/x"\n[collector]\nbind = "0.0.0.0"\n'
        b"[seal]\nretry_minutes = [\n  5, # first\n  15,\n]\n[database]\nname = '''arcilogger'''\n"
    )
    new = edit(old, [SetValue("site_name", "Örnek")], path=WHERE)
    assert tomllib.loads(new.decode())["seal"]["retry_minutes"] == [5, 15]


def test_data_dir_and_secrets_dir_are_never_written() -> None:
    for path in ("data_dir", "secrets_dir"):
        with pytest.raises(TomlEditError):
            edit(example(), [SetValue(path, "E:/elsewhere")], path=WHERE)


def test_an_invalid_result_is_validation_failed() -> None:
    with pytest.raises(TomlEditError) as caught:
        edit(example(), [SetValue("retention.days", 10)], path=WHERE)
    assert caught.value.code == "validation_failed"
    assert "10" not in caught.value.reason.split(":")[-1] or "days" in caught.value.reason


def test_the_installer_patterns_read_what_tomllib_reads() -> None:
    new = edit(
        example(),
        [
            SetValue("site_name", "Örnek A.Ş."),
            SetValue("clock.ntp_server", "172.20.10.1"),
            SetSources((SWITCH,)),
        ],
        path=WHERE,
    ).decode()
    assert _installer_string(new, "", "site_name") == "Örnek A.Ş."
    assert _installer_string(new, "", "data_dir") == "D:/ARCILOGGER"
    assert _installer_string(new, "collector", "bind") == "0.0.0.0"  # noqa: S104
    assert _installer_string(new, "clock", "ntp_server") == "172.20.10.1"
    assert _installer_sources(new) == (1, ["172.20.10.22"])


def test_source_tables_omit_defaults_and_keep_model_order() -> None:
    assert source_table(SWITCH) == {
        "name": "switch-2",
        "type": "mikrotik",
        "address": "172.20.10.22",
    }
    full = SourceConfig(
        name="x", type="generic", address="172.20.1.1", heartbeat_minutes=5, expected=False,
        transport="udp", enabled=False,
    )  # fmt: skip
    assert list(source_table(full)) == [
        "name", "type", "address", "heartbeat_minutes", "expected", "transport", "enabled",
    ]  # fmt: skip


# -- the postcondition holds for random configurations and edits -----------------------------------

#: A site name the configuration accepts (no control or format characters).
_TEXT = st.text(st.characters(exclude_categories=("Cc", "Cf", "Cs", "Zl", "Zp")), max_size=40)
#: A host name the configuration accepts (a label never ends with a hyphen).
_HOSTS = st.from_regex(r"\A[a-z]([a-z0-9-]{0,9}[a-z0-9])?(\.[a-z]{2,5}){0,2}\Z")
_EDITS = st.one_of(
    st.builds(SetValue, st.just("site_name"), _TEXT),
    st.builds(SetValue, st.just("clock.ntp_server"), _HOSTS),
    st.builds(SetValue, st.just("clock.alert_offset_ms"), st.integers(1, 60_000)),
    st.builds(SetValue, st.just("tsa.credit_alert_threshold"), st.integers(0, 10**6)),
    st.builds(SetValue, st.just("smtp.host"), st.one_of(st.none(), _HOSTS)),
    st.builds(SetValue, st.just("smtp.username"), st.one_of(st.none(), _HOSTS)),
    st.builds(SetValue, st.just("retention.days"), st.integers(730, 36_500)),
    st.builds(SetValue, st.just("backup.enabled"), st.booleans()),
    st.builds(
        SetValue, st.just("tsa.legal_root_fingerprints"),
        st.lists(st.from_regex(r"\A[0-9a-f]{64}\Z"), max_size=3),
    ),
    st.builds(
        SetSources,
        st.lists(
            st.builds(
                SourceConfig,
                name=st.from_regex(r"\A[a-z][a-z0-9-]{0,8}\Z"),
                type=st.sampled_from(["fortigate", "mikrotik", "generic"]),
                address=st.integers(1, 250).map(lambda n: f"172.20.30.{n}"),
                heartbeat_minutes=st.integers(1, 1440),
                expected=st.booleans(),
                transport=st.sampled_from(["any", "udp", "tcp"]),
                enabled=st.booleans(),
            ),
            max_size=4,
            unique_by=(lambda s: s.name, lambda s: s.address),
        ).map(tuple),
    ),
)  # fmt: skip


def _base(minimal: bool, crlf: bool) -> bytes:
    text = b'data_dir = "D:/ARCILOGGER"\n' if minimal else example()
    return text.replace(b"\n", b"\r\n") if crlf else text


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    minimal=st.booleans(),
    crlf=st.booleans(),
    rounds=st.lists(st.lists(_EDITS, min_size=1, max_size=4), min_size=1, max_size=3),
)
def test_random_edits_satisfy_the_postcondition(
    minimal: bool, crlf: bool, rounds: list[list[Any]]
) -> None:
    current = _base(minimal, crlf)
    for edits in rounds:
        # one value per path, as the rules give them
        unique: dict[str, Any] = {}
        for item in edits:
            unique[item.path if isinstance(item, SetValue) else "sources"] = item
        edits = list(unique.values())
        before = tomllib.loads(current.decode())
        new = edit(current, edits, path=WHERE)
        assert tomllib.loads(new.decode()) == expected(before, edits)
        if crlf:
            assert b"\n" not in new.replace(b"\r\n", b"")
        current = new
