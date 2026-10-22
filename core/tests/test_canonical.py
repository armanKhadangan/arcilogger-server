"""RFC 8785 canonical JSON."""

from __future__ import annotations

import json

import pytest

from arcilogger_core.canonical import CanonicalJsonError, canonical_json, is_canonical


def test_keys_are_sorted_by_utf16_code_units() -> None:
    # RFC 8785 section 3.2.3 example (string values only).
    value = {
        "€": "Euro Sign",
        "\r": "Carriage Return",
        "\ufb33": "Hebrew Letter Dalet With Dagesh",
        "1": "One",
        "😂": "Smiley",
        "\u0080": "Control",
        "ö": "Latin Small Letter O With Diaeresis",
    }
    expected = (
        '{"\\r":"Carriage Return","1":"One","\u0080":"Control",'
        '"ö":"Latin Small Letter O With Diaeresis","€":"Euro Sign","😂":"Smiley",'
        '"\ufb33":"Hebrew Letter Dalet With Dagesh"}'
    )
    assert canonical_json(value) == expected.encode("utf-8")


def test_string_escaping_matches_ecmascript() -> None:
    value = {"s": '\u20ac$\u000f\nA\'B"\\\\"/'}
    assert canonical_json(value) == b'{"s":"\xe2\x82\xac$\\u000f\\nA\'B\\"\\\\\\\\\\"/"}'


def test_nested_structures_and_literals() -> None:
    value = {"b": [1, True, None, {"z": False, "a": "x"}], "a": -5}
    assert canonical_json(value) == b'{"a":-5,"b":[1,true,null,{"a":"x","z":false}]}'


@pytest.mark.parametrize("bad", [1.5, 2**53, {"a": 0.1}, {1: "x"}, b"bytes", {"s": "\ud800"}])
def test_unsupported_values_are_rejected(bad: object) -> None:
    with pytest.raises(CanonicalJsonError):
        canonical_json(bad)


def test_is_canonical() -> None:
    data = canonical_json({"b": 1, "a": [2, 3]})
    assert is_canonical(data)
    assert not is_canonical(json.dumps({"b": 1, "a": [2, 3]}).encode())
    assert not is_canonical(b'{"a":1.5}')
    assert not is_canonical(b"not json")
