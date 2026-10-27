"""The query language (contract 2.2, 3.1) and its texts (2.20): every form, every refusal with
its span, every limit; any input parses or raises ``SearchQueryError`` only; the normalised text
parses again to the same tree; every code has Turkish and English texts with the same
placeholders."""

from __future__ import annotations

import ipaddress
import re
import string

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from arcilogger_api.search import FIELDS, LIMITS, Query, SearchQueryError, parse_query
from arcilogger_api.search.fields import (
    ContainsValue,
    DomainValue,
    IntValue,
    IpValue,
    MacValue,
    PatternValue,
    Present,
    SeverityValue,
    TokenValue,
)
from arcilogger_api.search.grammar import MAX_CHARS, MAX_CLAUSES, MAX_TERMS, MAX_VALUES
from arcilogger_api.search.messages import (
    FORMATS,
    HINTS,
    MESSAGES,
    NOTICES,
    REASONS,
    STATUS,
    error_item,
    notice,
    placeholders,
    query_error,
    texts,
)


def _refused(text: str) -> SearchQueryError:
    with pytest.raises(SearchQueryError) as caught:
        parse_query(text)
    return caught.value


# -- forms ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "normalized"),
    [
        ("", ""),
        ("   ", ""),
        (
            "srcip:172.20.41.105 type:web action:blocked",
            "srcip:172.20.41.105 type:web action:blocked",
        ),
        ("domain:example.com -action:passthrough", "domain:example.com -action:passthrough"),
        ('"login failure" source:core-r1', '"login failure" source:core-r1'),
        ("vlan:41 dstport:80,443", "vlan:41 dstport:80,443"),
        ("type:dhcp mac:00-00-5E-00-53-21", "type:dhcp mac:00:00:5e:00:53:21"),
        ("mac:0000.5e00.5321", "mac:00:00:5e:00:53:21"),
        ("error:fortigate.*", "error:fortigate.*"),
        # field names: any case, Turkish letters folded, aliases
        ("Kaynak:fortigate KAYNAKİP:172.20.*", "source:fortigate srcip:172.20.0.0/16"),
        ("tür:web TİP:dns", "type:web type:dns"),
        ("gönderen:172.20.10.2 önem:err", "sender:172.20.10.2 severity:err"),
        ("alanadı:example.com ülke:Reserved", "domain:example.com country:Reserved"),
        ("kullanıcı:admin arayüz:ether1 saldırı:x*", "user:admin interface:ether1 attack:x*"),
        ("istemci:172.20.41.5 hedefip:203.0.113.10", "srcip:172.20.41.5 dstip:203.0.113.10"),
        ("transip:198.51.100.7 transport:40123", "natip:198.51.100.7 natport:40123"),
        ("eylem:deny,BLOCKED oturum:42", "action:deny,blocked sessionid:42"),
        ("site:Example.com. alttur:forward", "domain:example.com subtype:forward"),
        ("metin:youtube cihaz:core-r1 konu:dhcp*", "text:youtube host:core-r1 program:dhcp*"),
        # negation, NOT, AND
        ("NOT foo AND bar", "-foo bar"),
        ("-action:accept", "-action:accept"),
        ('NOT "a phrase"', '-"a phrase"'),
        ("AND", ""),
        # presence
        ("mac:* -user:*", "mac:* -user:*"),
        # quoting
        (r'"say \"hi\""', r'"say \"hi\""'),
        (r'"a\\b"', r"a\b"),  # a backslash is literal outside quotes too
        ('user:"*admin"', 'user:"*admin"'),  # quoted: literal, no wildcard
        ("user:*admin*", "user:*admin*"),
        ("url:*login*", "url:login"),
        ('department:"İdari İşler"', 'department:"İdari İşler"'),
        # value forms
        ("srcip:172.20.41.0/24", "srcip:172.20.41.0/24"),
        ("srcip:172.20.41.5/24", "srcip:172.20.41.0/24"),
        ("srcip:172.20.41.5/32", "srcip:172.20.41.5"),
        ("srcip:172.*", "srcip:172.0.0.0/8"),
        ("ip:fe80::1 ip:2001:db8::/32", "ip:fe80::1 ip:2001:db8::/32"),
        ("dstport:>1024 srcport:<=1023", "dstport:>=1025 srcport:<=1023"),
        ("dstport:1000-2000 sessionid:>=1", "dstport:1000-2000 sessionid:>=1"),
        ("proto:TCP proto:17", "proto:tcp proto:udp"),
        ("proto:47", "proto:47"),
        ("mac:00:00:5E:*", "mac:00:00:5e:*"),
        ("domain:*.Example.COM", "domain:*.example.com"),
        ("domain:bücher.example", "domain:xn--bcher-kva.example"),
        ("qname:WWW.example.com.", "qname:www.example.com"),
        ("qtype:AAAA level:Warning logid:0000000013", "qtype:aaaa level:warning logid:0000000013"),
        ("severity:warning severity:<=err", "severity:warning severity:<=err"),
        (
            "severity:>=notice severity:2-4 severity:7",
            "severity:>=notice severity:2-4 severity:debug",
        ),
        ("severity:0-7 severity:<3", "severity:<=debug severity:<=crit"),
        ("type:WEB,Dhcp", "type:web,dhcp"),
        ("policy:12 policy:Internet*", "policy:12 policy:Internet*"),
        ('policy:"12" policy:"Internet"', 'policy:"12" policy:Internet'),  # a name of digits
        ("vlan:41,42 department:Finans department:3", "vlan:41,42 department:Finans department:3"),
        ("source:Core-*", "source:core-*"),
        ("--foo", '-"-foo"'),
        ('"NOT"', '"NOT"'),
        ("*abc*", "abc"),
        ("a,b,c", '"a,b,c"'),
    ],
)
def test_forms_and_their_normalised_text(text: str, normalized: str) -> None:
    query = parse_query(text)
    assert query.normalized == normalized
    assert parse_query(normalized, max_chars=None) == query


def test_the_tree_has_spans_and_values() -> None:
    text = 'srcip:172.20.41.0/24 -action:deny,blocked  "login failure" mac:*'
    query = parse_query(text)
    srcip, action, phrase, mac = query.clauses
    assert (srcip.field, srcip.negated, srcip.free) == ("srcip", False, False)
    assert srcip.values == (IpValue(ipaddress.ip_network("172.20.41.0/24"), exact=False),)
    assert text[srcip.start : srcip.end] == "srcip:172.20.41.0/24"
    assert (action.negated, action.values) == (True, (TokenValue("deny"), TokenValue("blocked")))
    assert text[action.start : action.end] == "-action:deny,blocked"
    assert (phrase.field, phrase.free, phrase.op) == ("text", True, "text")
    assert phrase.values == (ContainsValue("login failure"),)
    assert text[phrase.start : phrase.end] == '"login failure"'
    assert (mac.op, mac.values) == ("present", (Present(),))
    assert action.op == "match"
    assert action.value_texts() == ["deny", "blocked"]
    assert query.terms == ("login failure",)
    assert not query.empty
    assert parse_query("").empty


def test_values_carry_their_form() -> None:
    (port, mac, domain, user, sev) = parse_query(
        "dstport:<10 mac:00:00:5e:* domain:*.example.com user:adm* severity:<=err"
    ).clauses
    assert port.values == (IntValue(None, 9),)
    assert mac.values == (MacValue("00:00:5e:00:00:00", "00:00:5e:ff:ff:ff", 3),)
    assert domain.values == (DomainValue("example.com", subdomains_only=True),)
    assert user.values == (PatternValue("adm", starts=True),)
    assert sev.values == (SeverityValue(0, 3),)


def test_a_quoted_number_is_a_policy_name() -> None:
    number, name = parse_query('policy:12 policy:"12"').clauses
    assert number.values == (IntValue(12, 12),)  # policyid
    assert name.values == (PatternValue("12"),)  # policyname
    assert name.render() == 'policy:"12"'


def test_terms_are_the_positive_free_text() -> None:
    query = parse_query('foo -bar "baz qux" text:one,two')
    assert query.terms == ("foo", "baz qux")  # a list is OR: no term every match contains
    assert parse_query("text:three abc").terms == ("three", "abc")


# -- refusals -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "code", "span", "params"),
    [
        ("a OR b", "search_term_too_short", (0, 1), {}),
        ("deny OR blocked", "search_or_unsupported", (5, 7), {}),
        ("(srcip:1.2.3.4)", "search_or_unsupported", (0, 1), {}),
        ("srcip:1.2.3.4)", "search_or_unsupported", (13, 14), {}),
        ("abc(def", "search_or_unsupported", (3, 4), {}),
        ("foo:bar", "search_unknown_field", (0, 7), {"field": "foo"}),
        ("1abc:x", "search_unknown_field", (0, 6), {"field": "1abc"}),
        ("action:", "search_syntax", (6, 7), {"reason": "empty_value", "position": 7}),
        ("action:a,", "search_syntax", (8, 9), {"reason": "empty_value", "position": 9}),
        ("action:a, b", "search_syntax", (8, 9), {"reason": "empty_value", "position": 9}),
        ("action:,a", "search_syntax", (6, 7), {"reason": "empty_value", "position": 7}),
        ('action:""', "search_syntax", (7, 9), {"reason": "empty_value", "position": 8}),
        ('"abc', "search_syntax", (0, 4), {"reason": "unclosed_quote", "position": 1}),
        (r'"a\nb"', "search_syntax", (2, 4), {"reason": "bad_escape", "position": 3}),
        ('x"y"', "search_syntax", (1, 2), {"reason": "unexpected_quote", "position": 2}),
        ('"abc"def', "search_syntax", (5, 6), {"reason": "unexpected_quote", "position": 6}),
        ("NOT", "search_syntax", (0, 3), {"reason": "missing_clause", "position": 1}),
        ("abc NOT  ", "search_syntax", (4, 7), {"reason": "missing_clause", "position": 5}),
        ("- foo", "search_syntax", (0, 1), {"reason": "missing_clause", "position": 1}),
        (":foo", "search_syntax", (0, 1), {"reason": "missing_field", "position": 1}),
        ("abc\x00def", "search_syntax", (3, 4), {"reason": "control_character", "position": 4}),
        ("abc\ud800", "search_syntax", (3, 4), {"reason": "control_character", "position": 4}),
        ("mac:*,00:00:5e:00:53:21", "search_syntax", (0, 23), {"reason": "star_alone"}),
        ("ab", "search_term_too_short", (0, 2), {}),
        ('"ab"', "search_term_too_short", (0, 4), {}),
        ("text:ab", "search_term_too_short", (5, 7), {}),
        ("**", "search_term_too_short", (0, 2), {}),
        ("srcip:172.20.41", "search_value_invalid", (6, 15), {"field": "srcip"}),
        ("srcip:fe80::1%eth0", "search_value_invalid", (6, 18), {"field": "srcip"}),
        ("dstport:70000", "search_value_invalid", (8, 13), {"format": "port"}),
        ("dstport:<0", "search_value_invalid", (8, 10), {}),
        ("dstport:2000-1000", "search_value_invalid", (8, 17), {}),
        ("dstport:٣", "search_value_invalid", (8, 9), {}),  # ASCII digits only
        ("proto:gre", "search_value_invalid", (6, 9), {"format": "proto"}),
        ("mac:00:00:5e", "search_value_invalid", (4, 12), {"format": "mac"}),
        ("domain:exa*mple.com", "search_value_invalid", (7, 19), {"format": "domain"}),
        ("domain:..", "search_value_invalid", (7, 9), {}),
        ("qname:*.example.com", "search_value_invalid", (6, 19), {"format": "name"}),
        ("user:a*b", "search_value_invalid", (5, 8), {"format": "text"}),
        ("source:*core", "search_value_invalid", (7, 12), {"format": "source"}),
        ("type:mail", "search_value_invalid", (5, 9), {"format": "kind"}),
        ("type:*", "search_value_invalid", (5, 6), {}),
        ("vlan:*", "search_value_invalid", (5, 6), {"format": "vlan"}),
        ("-vlan:*", "search_value_invalid", (6, 7), {"format": "vlan"}),
        ("department:*", "search_value_invalid", (11, 12), {"format": "department"}),
        ("severity:loud", "search_value_invalid", (9, 13), {"format": "severity"}),
        ("severity:<emerg", "search_value_invalid", (9, 15), {}),
        ("severity:٣-4", "search_value_invalid", (9, 12), {}),
        ("error:bad/name", "search_value_invalid", (6, 14), {"format": "error"}),
        ("vlan:5000", "search_value_invalid", (5, 9), {"format": "vlan"}),
        ("vlan:x", "search_value_invalid", (5, 6), {}),
        ("url:**", "search_value_invalid", (4, 6), {"format": "url"}),
    ],
)
def test_refusals_point_at_the_offending_part(
    text: str, code: str, span: tuple[int, int], params: dict[str, object]
) -> None:
    error = _refused(text)
    assert (error.code, (error.start, error.end)) == (code, span)
    assert params.items() <= error.params.items()


def test_a_word_with_a_colon_gets_a_hint() -> None:
    mac = _refused("00:00:5E:00:53:21")
    assert mac.code == "search_unknown_field"
    assert mac.params == {
        "field": "00",
        "hint": "mac",
        "suggestion": "mac:00:00:5e:00:53:21",
    }
    for word in ("http://example.com/x", "fe80::1", "::1", "[2001:db8::1]"):
        error = _refused(word)
        assert (error.code, error.params["hint"]) == ("search_unknown_field", "quote"), word
        assert error.params["suggestion"] == f'"{word}"'
    assert _refused("::1").params["field"] == "::1"
    assert "hint" not in _refused("foo:bar").params
    message_tr, message_en = texts("search_unknown_field", mac.params)
    assert message_tr.endswith("MAC adresi aramak için: mac:00:00:5e:00:53:21")
    assert message_en.endswith("To search for a MAC address: mac:00:00:5e:00:53:21")


def test_a_quoted_value_with_a_colon_is_text() -> None:
    query = parse_query('"http://example.com/x" url:http://example.com/login')
    assert query.clauses[0].values == (ContainsValue("http://example.com/x"),)
    assert query.clauses[1].values == (ContainsValue("http://example.com/login"),)


def test_every_limit() -> None:
    assert LIMITS == {
        "max_chars": 1000,
        "max_clauses": 16,
        "max_values": 20,
        "max_terms": 5,
    }
    longest = " " * (MAX_CHARS - 10) + "vlan:41,42"
    assert len(parse_query(longest).clauses) == 1  # 1,000 characters parse …
    error = _refused(" " + longest)  # … 1,001 do not
    assert (error.code, error.start, error.end) == ("search_too_complex", 0, MAX_CHARS + 1)
    clauses = " ".join(f"vlan:{n}" for n in range(1, MAX_CLAUSES + 2))
    error = _refused(clauses)
    assert error.code == "search_too_complex"
    assert clauses[error.start : error.end] == f"vlan:{MAX_CLAUSES + 1}"
    assert len(parse_query(clauses.rsplit(" ", 1)[0]).clauses) == MAX_CLAUSES
    values = "vlan:" + ",".join(str(n) for n in range(1, MAX_VALUES + 2))
    error = _refused(values)
    assert (error.code, values[error.start : error.end]) == ("search_too_complex", "21")
    assert len(parse_query(values.rsplit(",", 1)[0]).clauses[0].values) == MAX_VALUES
    terms = " ".join(f"term{n}" for n in range(MAX_TERMS + 1))
    error = _refused(terms)
    assert (error.code, terms[error.start : error.end]) == ("search_too_complex", "term5")
    assert _refused("text:aaa,bbb,ccc " + terms.rsplit(" ", 3)[0]).code == "search_too_complex"
    value = "a" * 257
    error = _refused(f"user:{value}")
    assert (error.code, error.start, error.end) == ("search_too_complex", 5, 262)
    assert _refused(value).code == "search_too_complex"
    assert parse_query("user:" + "a" * 256).clauses[0].values == (PatternValue("a" * 256),)


def test_the_value_limit_is_counted_unescaped() -> None:
    quoted = '"' + '\\"' * 256 + '"'
    assert parse_query(f"user:{quoted}").clauses[0].values == (PatternValue('"' * 256),)


def test_the_length_limit_can_be_lifted_for_normalised_text() -> None:
    domains = ",".join(f"ışık{n}.example" for n in range(20))
    query = parse_query(f"domain:{domains}")
    assert len(query.normalized) > len(f"domain:{domains}")  # punycode is longer
    assert parse_query(query.normalized, max_chars=None) == query


# -- properties -----------------------------------------------------------------------------------

#: Characters the grammar cares about, plus letters, digits and Turkish letters.
ALPHABET = st.sampled_from(list(' \t"\\,:()-*.<>=/_' + string.ascii_letters[:12] + "0159İıŞş\x00"))


@settings(max_examples=1500, deadline=None)
@given(st.text(alphabet=ALPHABET, max_size=60) | st.text(max_size=40))
def test_any_input_parses_or_is_refused_with_a_span_inside_it(text: str) -> None:
    refused: SearchQueryError | None = None
    try:
        query = parse_query(text)
    except SearchQueryError as error:  # nothing else may escape
        refused = error
    if refused is not None:
        assert 0 <= refused.start <= refused.end <= len(text)
        assert refused.code in MESSAGES
        texts(refused.code, refused.params)  # every refusal has its texts
        return
    assert isinstance(query, Query)
    assert parse_query(query.normalized, max_chars=None) == query


def _field_value(name: str) -> st.SearchStrategy[str]:
    form = FIELDS[name].form
    values: dict[str, list[str]] = {
        "ip": ["172.20.41.5", "172.20.41.0/24", "172.20.*", "203.0.113.10", "fe80::1"],
        "port": ["443", "1000-2000", ">1024", "<=10"],
        "int": ["123456", ">=1"],
        "proto": ["tcp", "UDP", "1", "47"],
        "mac": ["00:00:5e:00:53:21", "00-00-5E-00-53-21", "00:00:5e:*"],
        "domain": ["example.com", "*.example.com", "WWW.Example.com."],
        "name": ["www.example.com"],
        "token": ["deny", "AAAA", '"two words"'],
        "text": ["admin", "adm*", "*min", "*dmi*", '"*literal"', '"İdari İşler"'],
        "url": ["login", "*login*", '"/a b"'],
        "source": ["fortigate", "core-*"],
        "kind": ["web", "dhcp", "TRAFFIC"],
        "severity": ["err", "<=warning", "2-4", "7"],
        "error": ["fortigate.kv", "mikrotik.*"],
        "policy": ["12", "Internet*", '"12"'],
        "vlan": ["41", "61"],
        "department": ["Finans", '"İdari İşler"', "3"],
        "freetext": ["youtube", '"login failure"'],
    }
    return st.lists(st.sampled_from(values[form]), min_size=1, max_size=3).map(",".join)


@st.composite
def _queries(draw: st.DrawFn) -> str:
    clauses = []
    terms = 0
    for _ in range(draw(st.integers(0, 6))):
        name = draw(st.sampled_from(sorted(FIELDS)))
        value = draw(_field_value(name))
        if FIELDS[name].form == "freetext":
            count = value.count(",") + 1
            if terms + count >= MAX_TERMS:  # within the limit, one left for the trailing term
                continue
            terms += count
        alias = draw(st.sampled_from((name, *FIELDS[name].aliases)))
        sign = draw(st.sampled_from(["", "-", "NOT "]))
        clauses.append(f"{sign}{alias.upper() if draw(st.booleans()) else alias}:{value}")
    if draw(st.booleans()):
        clauses.append(draw(st.sampled_from(["youtube", '"login failure"', "-blocked"])))
    return draw(st.sampled_from([" ", "  ", " AND "])).join(clauses)


@settings(max_examples=500, deadline=None)
@given(_queries())
def test_queries_from_the_catalogue_parse_and_normalise_stably(text: str) -> None:
    query = parse_query(text)
    again = parse_query(query.normalized, max_chars=None)
    assert again == query
    assert again.normalized == query.normalized


# -- texts ----------------------------------------------------------------------------------------

#: Contract 2.20: the search codes and their HTTP status (``search_event_timeout``: WP-B's
#: review fixes, the contract's Deviations).
CONTRACT_CODES = {
    "search_syntax": 422,
    "search_unknown_field": 422,
    "search_value_invalid": 422,
    "search_term_too_short": 422,
    "search_too_complex": 422,
    "search_or_unsupported": 422,
    "search_fields_incompatible": 422,
    "search_vlan_unknown": 422,
    "search_department_unknown": 422,
    "search_range_invalid": 422,
    "search_range_too_long": 422,
    "search_expired": 409,
    "search_cursor_invalid": 422,
    "search_timeout": 504,
    "search_busy": 429,
    "search_rate_limited": 429,
    "search_event_not_found": 404,
    "search_event_timeout": 504,
    "search_export_running": 409,
    "search_export_rate_limited": 429,
    "saved_search_not_found": 404,
    "saved_search_name_taken": 409,
    "saved_search_limit": 409,
}
CONTRACT_NOTICES = {
    "vault_only_before",
    "counts_incomplete",
    "text_case",
    "text_truncated",
    "query_not_selective",
    "department_by_vlan",
}
#: Words that would claim legal compliance (the product's wording rule).
OVERSTATEMENT = re.compile(r"uyumlu|uyumu|garanti|complian|guarantee", re.IGNORECASE)
SAMPLE_PARAMS = {
    "reason": "empty_value",
    "position": 3,
    "field": "srcip",
    "value": "x" * 100,
    "format": "ip",
    "fields": "srcip, type",
    "vlan": 41,
    "department": "Finans",
    "days": 31,
    "seconds": 4,
    "n": 10,
    "minutes": 12,
    "date": "2026-10-26",
}


def test_every_contract_code_and_notice_has_its_texts() -> None:
    assert dict(STATUS) == CONTRACT_CODES
    assert set(MESSAGES) == set(CONTRACT_CODES)
    assert set(NOTICES) == CONTRACT_NOTICES


@pytest.mark.parametrize(
    ("catalogue", "code"),
    [
        *(("errors", code) for code in MESSAGES),
        *(("notices", code) for code in NOTICES),
    ],
)
def test_texts_are_turkish_and_english_with_the_same_placeholders(
    catalogue: str, code: str
) -> None:
    message_tr, message_en = (MESSAGES if catalogue == "errors" else NOTICES)[code]
    for message in (message_tr, message_en):
        assert message
        assert message.strip() == message
        assert message.endswith(".")
    assert message_tr != message_en
    assert placeholders(message_tr) == placeholders(message_en), code
    assert placeholders(message_tr) <= set(SAMPLE_PARAMS), code
    rendered = texts(code, SAMPLE_PARAMS, catalogue=catalogue)
    for text in (*rendered, message_tr, message_en):
        assert not OVERSTATEMENT.search(text), (code, text)
    assert "{" not in "".join(rendered)  # every placeholder filled


@pytest.mark.parametrize("table", [REASONS, FORMATS, HINTS])
def test_reasons_formats_and_hints_have_both_languages(table: dict[str, tuple[str, str]]) -> None:
    for code, (text_tr, text_en) in table.items():
        assert text_tr, code
        assert text_en, code
        assert text_tr != text_en, code
        assert placeholders(text_tr) == placeholders(text_en), code


def test_every_field_form_has_a_format_text() -> None:
    forms = {entry.form for entry in FIELDS.values()} | {"port"}
    assert forms <= set(FORMATS)


def test_rendered_errors_and_notices() -> None:
    error = _refused("dstport:99999")
    api = query_error(error)
    assert (api.status_code, api.code) == (422, "search_value_invalid")
    assert api.message_tr == (
        "“dstport” alanı için “99999” geçerli değil (beklenen: 0-65535 arası port, aralık ya da "
        "karşılaştırma, ör. 443, 1000-2000, >1024)."
    )
    assert api.details == {"start": 8, "end": 13, "params": error.params}
    item = error_item(_refused('"abc'))
    assert item == {
        "code": "search_syntax",
        "message_tr": "Sorgu okunamadı: kapanmamış tırnak işareti (1. karakter).",
        "message_en": "The query cannot be read: a quotation mark is not closed (character 1).",
        "start": 0,
        "end": 4,
    }
    shown = texts("search_value_invalid", {"field": "user", "value": "v" * 200, "format": "text"})
    assert "v" * 63 + "…" in shown[0]
    assert notice("vault_only_before", date="2026-01-01") == {
        "code": "vault_only_before",
        "message_tr": "01.01.2026 öncesine ait kayıtlar veritabanında yok; o günlerin ham "
        "kayıtları varsa mühürlü arşivdedir (Kanıt Paketi ile alınabilir).",
        "message_en": "Records before 01.01.2026 are not in the database; raw lines of those "
        "days, if any, are in the sealed vault (available through an evidence package).",
        "params": {"date": "2026-01-01"},
    }
