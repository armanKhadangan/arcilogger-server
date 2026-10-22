"""The FortiGate ``key=value`` tokenizer (spec 7.2): edge cases, fast = reference, round trip."""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from arcilogger_ingest.parsers.kv import (
    Tokens,
    _tokenize_fast,
    format_pairs,
    quote,
    tokenize,
    tokenize_reference,
)

FORMAT = Path(__file__).parents[2] / "tests" / "fixtures" / "format"
FORTIGATE = [
    line.split(">", 1)[1]
    for line in (FORMAT / "fortigate-sample.log").read_text("utf-8").splitlines()
]
WANINFO = (
    "name=wan1,bytes=7340032000/912680550,packets=6120400/2890310;name=wan2,bytes=0/0,packets=0/0;"
)


def both(text: str) -> Tokens:
    """Tokenize with the public function and check the reference agrees."""
    result = tokenize(text)
    assert result == tokenize_reference(text)
    return result


def test_sample_perf_stats_line_keeps_waninfo_with_equals_commas_and_semicolons() -> None:
    tokens = both(FORTIGATE[0])
    assert tokens.fields["waninfo"] == WANINFO
    assert tokens.fields["bandwidth"] == "118/245"  # a quoted value with "/"
    assert tokens.fields["msg"] == (
        "Performance statistics: average CPU: 3, memory:  31, concurrent sessions:  74, "
        "setup-rate: 2"
    )  # double spaces inside quotes survive
    assert tokens.duplicates == {}
    assert tokens.strays == ()
    assert tokens.unterminated is None
    assert len(tokens.fields) == 25


@pytest.mark.parametrize("line", FORTIGATE)
def test_sample_lines_take_the_fast_path(line: str) -> None:
    fast = _tokenize_fast(line)
    assert fast is not None
    assert fast == tokenize_reference(line)


def test_sample_traffic_line_field_count_and_quoting() -> None:
    tokens = both(FORTIGATE[6])
    assert len(tokens.fields) == 49
    assert tokens.fields["devname"] == "FIREWALL-01"  # quotes removed
    assert tokens.fields["srcip"] == "172.20.42.76"  # unquoted
    assert tokens.fields["dstcountry"] == "Ireland"
    assert tokens.fields["eventtime"] == "1790145897482913706"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (r'msg="say \"hi\" now" x=1', {"msg": 'say "hi" now', "x": "1"}),
        (r'p="C:\\temp\\" q=2', {"p": "C:\\temp\\", "q": "2"}),
        (r'p="C:\Users\x"', {"p": r"C:\Users\x"}),  # other backslashes stay literal
        (r'a="\"" b="\\"', {"a": '"', "b": "\\"}),
        ('a= b="" c=3', {"a": "", "b": "", "c": "3"}),
        ("x=a=b y==", {"x": "a=b", "y": "="}),  # unquoted values may contain "="
        ('k="a b=c, d; e"', {"k": "a b=c, d; e"}),
        ('user="Ayşe Öz" dept="Finans"', {"user": "Ayşe Öz", "dept": "Finans"}),
        ("  a=1 \t b=2  ", {"a": "1", "b": "2"}),  # any ASCII whitespace separates
        ('a="x"b=1', {"a": "x", "b": "1"}),  # glued to a closing quote: still a pair
        ('a=x"y z=1', {"a": 'x"y', "z": "1"}),  # quote inside an unquoted value
        ("", {}),
    ],
)
def test_values(text: str, expected: dict[str, str]) -> None:
    tokens = both(text)
    assert tokens.fields == expected
    assert tokens.strays == ()
    assert tokens.unterminated is None


def test_duplicate_keys_keep_the_first_and_list_all() -> None:
    tokens = both('a=1 b=2 a=3 c="x" a="4" c=y')
    assert tokens.fields == {"a": "1", "b": "2", "c": "x"}
    assert tokens.duplicates == {"a": ["1", "3", "4"], "c": ["x", "y"]}


def test_trailing_garbage_is_kept_as_strays() -> None:
    tokens = both('a=1 b="x" junk "more stuff =oops')
    assert tokens.fields == {"a": "1", "b": "x"}
    assert tokens.strays == ("junk", '"more', "stuff", "=oops")
    assert tokens.unterminated is None


def test_unterminated_quote_takes_the_rest_of_the_line() -> None:
    tokens = both('a=1 msg="cut off here x=2')
    assert tokens.fields == {"a": "1", "msg": "cut off here x=2"}
    assert tokens.unterminated == "msg"
    escaped_end = both('a="ends with backslash\\')
    assert escaped_end.fields == {"a": "ends with backslash\\"}
    assert escaped_end.unterminated == "a"


def test_literal_backslashes_mode() -> None:
    """``escapes=False``: every backslash is an ordinary character (FortiOS escaping is a
    verify-on-site item; the FortiGate parser retries an unterminated line this way)."""
    # Both a trailing backslash and escaped quotes: neither reading is clean (both have strays,
    # which the parser reports as fortigate.unparsed).
    text = 'filename="C:\\dir\\" b=1 msg="say \\"hi\\""'
    escaped = tokenize_reference(text)
    assert escaped.fields == {"filename": 'C:\\dir" b=1 msg='}
    assert escaped.strays == ("say", '\\"hi\\""')
    assert escaped.unterminated is None
    literal = tokenize_reference(text, escapes=False)
    assert literal.fields == {"filename": "C:\\dir\\", "b": "1", "msg": "say \\"}
    assert literal.strays == ('hi\\""',)
    windows = tokenize_reference('path="C:\\Users\\x\\" next=1', escapes=False)
    assert windows == Tokens({"path": "C:\\Users\\x\\", "next": "1"}, {})
    assert tokenize_reference('path="C:\\Users\\x\\" next=1').unterminated == "path"


def test_nul_in_text_does_not_confuse_the_fast_path() -> None:
    # The fast path uses NUL as a marker; such lines go to the reference loop.
    tokens = both('a="x\x00" b=\x00')
    assert tokens.fields == {"a": "x\x00", "b": "\x00"}


@pytest.mark.parametrize(
    "text",
    [
        'a="x" "y"',  # quoted value without key
        'a="x""y"',  # two quoted values glued together
        'a=b="c"',  # quote inside an unquoted value
        '"lead a=1',  # stray quote first
        "a=1 a=2",  # duplicate
        "=v a=1",  # empty key
    ],
)
def test_unusual_lines_fall_back_to_the_reference(text: str) -> None:
    assert _tokenize_fast(text) is None
    both(text)


def test_quote_and_format_pairs() -> None:
    assert quote('a "b" \\c') == '"a \\"b\\" \\\\c"'
    assert format_pairs([("x", "1"), ("msg", 'say "hi"')]) == 'x="1" msg="say \\"hi\\""'


# -- properties ---------------------------------------------------------------------------------

TRICKY = st.text(alphabet=' =",;\\\t\x00abcAB1\u00e7\u0131\u00a0', max_size=80)
KEYS = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789_-.", min_size=1, max_size=12)
VALUES = st.text(max_size=40)


@settings(max_examples=1000, deadline=None)
@given(text=TRICKY)
@example('a="x" b=1 c="y z"')
@example('a="x"\x00')
@example('a=\x00 b="c"')
def test_fast_path_equals_reference(text: str) -> None:
    assert tokenize(text) == tokenize_reference(text)
    fast = _tokenize_fast(text)
    if fast is not None:
        assert fast == tokenize_reference(text)


@settings(max_examples=500, deadline=None)
@given(pairs=st.dictionaries(KEYS, VALUES, max_size=30))
def test_round_trip(pairs: dict[str, str]) -> None:
    tokens = tokenize(format_pairs(pairs.items()))
    assert tokens.fields == pairs
    assert tokens.duplicates == {}
    assert tokens.strays == ()
    assert tokens.unterminated is None


@settings(max_examples=300, deadline=None)
@given(
    pairs=st.dictionaries(
        KEYS, st.text(alphabet=st.characters(blacklist_characters=' \t\n\r\f\v"\\')), max_size=20
    )
)
def test_round_trip_unquoted(pairs: dict[str, str]) -> None:
    """Values without whitespace, quotes or backslashes may also be sent unquoted."""
    text = " ".join(f"{key}={value}" for key, value in pairs.items())
    assert both(text).fields == pairs
