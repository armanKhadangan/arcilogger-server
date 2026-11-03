"""The web's exit-rule error texts equal the server's (#40 item 15).

The browser checks ``nat.egresses`` with its own mirror of the exit rules and shows the code's
text from ``network.egress.codes`` in the ``network`` locale area; the server refuses with
``SETTING_ERRORS`` (``db/src/arcilogger_db/settings.py``). The administrator must read the same
sentence from either side, so every ``setting_egress_*`` code and ``setting_ipv6_not_supported``
has the server's Turkish and English text in the web, placeholders mapped from the server's
``{name}`` to i18next's ``{{name}}``.
"""

from __future__ import annotations

import json
import re
import string
from pathlib import Path
from typing import Final

from arcilogger_db.settings import SETTING_ERRORS

ROOT = Path(__file__).resolve().parents[1]
LOCALES = ROOT / "web" / "src" / "locales" / "areas" / "network"
LANGUAGES: Final = ("tr", "en")
#: An i18next placeholder, ``{{name}}`` (``{{ name }}`` too).
WEB_PLACEHOLDER: Final = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def _is_exit_code(code: str) -> bool:
    return code.startswith("setting_egress_") or code == "setting_ipv6_not_supported"


def _web_codes(language: str) -> dict[str, str]:
    """``network.egress.codes`` of the web's ``network`` locale area in ``language``."""
    area = json.loads((LOCALES / f"{language}.json").read_text(encoding="utf-8"))
    codes: dict[str, str] = area["network"]["egress"]["codes"]
    return codes


def _server_placeholders(text: str) -> set[str]:
    return {field for _, field, _, _ in string.Formatter().parse(text) if field is not None}


def _from_web(text: str) -> str:
    """The web text in the server's placeholder syntax: ``{{name}}`` becomes ``{name}``."""
    return WEB_PLACEHOLDER.sub(r"{\1}", text)


def test_placeholders_are_read_in_both_syntaxes() -> None:
    # No exit-rule text has a placeholder today; a later one must still compare.
    assert _from_web("En çok {{limit}} öğe, {{ kind }}.") == "En çok {limit} öğe, {kind}."
    assert set(WEB_PLACEHOLDER.findall("{{expected_en}} expected")) == {"expected_en"}
    assert _server_placeholders("Değer türü yanlış: {expected_tr} bekleniyor.") == {"expected_tr"}


def test_the_server_has_exit_codes() -> None:
    # Guards the comparisons below against an empty selection (a renamed prefix).
    assert {"setting_egress_address", "setting_ipv6_not_supported"} <= set(SETTING_ERRORS)


def test_every_exit_code_of_the_server_has_its_text_in_the_web() -> None:
    web = {language: _web_codes(language) for language in LANGUAGES}
    differences: list[str] = []
    for code, texts in sorted(SETTING_ERRORS.items()):
        if not _is_exit_code(code):
            continue
        for language, server_text in zip(LANGUAGES, texts, strict=True):
            web_text = web[language].get(code)
            if web_text is None:
                differences.append(f"{code} ({language}): missing in the web")
                continue
            web_names = set(WEB_PLACEHOLDER.findall(web_text))
            if web_names != _server_placeholders(server_text):
                differences.append(
                    f"{code} ({language}): placeholders {sorted(web_names)} in the web, "
                    f"{sorted(_server_placeholders(server_text))} on the server"
                )
            elif _from_web(web_text) != server_text:
                differences.append(
                    f"{code} ({language}):\n  web:    {web_text!r}\n  server: {server_text!r}"
                )
    assert not differences, "the web's exit-rule texts differ from SETTING_ERRORS:\n" + "\n".join(
        differences
    )


def test_the_web_has_no_exit_code_the_server_lacks() -> None:
    unknown = sorted(
        f"{code} ({language})"
        for language in LANGUAGES
        for code in _web_codes(language)
        if code.startswith("setting_egress_") and code not in SETTING_ERRORS
    )
    assert not unknown, f"setting_egress_* codes in the web that SETTING_ERRORS lacks: {unknown}"
