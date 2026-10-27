"""The search guides (docs/ARAMA.md, docs/SEARCH.md) match the search API they describe.

The operator learns the query language from these two documents, so they must list exactly the
fields and aliases of the field catalogue (``search/fields.py``, served by ``GET
/search/fields``), every record kind, every error code with its HTTP status, every audit action
the search API writes, the settings with their real defaults and the measured performance
(contract 2.23, 3.10).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

import pytest

from arcilogger_api.search.fields import catalogue_fields, catalogue_kinds
from arcilogger_api.search.messages import MESSAGES, STATUS
from arcilogger_api.security import ALL_PERMISSIONS
from arcilogger_db.settings import DEFAULT_SETTINGS

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
ROUTER = ROOT / "api" / "src" / "arcilogger_api" / "routers" / "search.py"
#: (document, heading of the field section, heading of the kinds section, of the errors,
#: of the audit, of the performance).
GUIDES: Final = {
    "ARAMA.md": (
        "## 3. Alanlar",
        "## 6. Kayıt türleri",
        "## 14. Hata kodları",
        "## 11. Kim ne görür",
        "## 13. Performans",
    ),
    "SEARCH.md": (
        "## 3. Fields",
        "## 6. Record kinds",
        "## 14. Error codes",
        "## 11. Who sees what",
        "## 13. Performance",
    ),
}


def _doc(name: str) -> str:
    return (DOCS / name).read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """From ``heading`` to the next heading of the same level (or the end)."""
    start = text.index(heading)
    level = heading.split(" ", 1)[0] + " "
    end = text.find("\n" + level, start + len(heading))
    return text[start : end if end >= 0 else len(text)]


def _rows(section: str) -> list[list[str]]:
    """The cells of the table rows of ``section`` (header and separator rows left out)."""
    rows = []
    for line in section.splitlines():
        if not line.startswith("| ") or set(line) <= {"|", "-", " "}:
            continue
        rows.append([cell.strip() for cell in line.strip().strip("|").split(" | ")])
    return rows[1:]  # the header


@pytest.mark.parametrize("name", GUIDES)
def test_the_guides_list_exactly_the_fields_and_their_aliases(name: str) -> None:
    section = _section(_doc(name), GUIDES[name][0])
    documented: dict[str, list[str]] = {}
    for cells in _rows(section):
        found = re.fullmatch(r"`([a-z_]+)`(?: \(((?:`[a-z_]+`(?:, )?)+)\))?", cells[0])
        assert found is not None, cells[0]
        assert found.group(1) not in documented, f"{found.group(1)} twice"
        documented[found.group(1)] = re.findall(r"`([a-z_]+)`", found.group(2) or "")
    catalogue = {field["name"]: list(field["aliases"]) for field in catalogue_fields()}
    assert documented == catalogue
    assert list(documented) == list(catalogue)  # the order of the syntax help


@pytest.mark.parametrize("name", GUIDES)
def test_the_guides_list_every_record_kind(name: str) -> None:
    rows = _rows(_section(_doc(name), GUIDES[name][1]))
    kinds = catalogue_kinds()
    assert [cells[0] for cells in rows] == [f"`{kind['key']}`" for kind in kinds]
    label = "label_tr" if name == "ARAMA.md" else "label_en"
    assert [cells[1] for cells in rows] == [kind[label] for kind in kinds]


@pytest.mark.parametrize("name", GUIDES)
def test_the_guides_list_every_error_code_with_its_status(name: str) -> None:
    rows = _rows(_section(_doc(name), GUIDES[name][2]))
    documented = {cells[0].strip("`"): int(cells[1]) for cells in rows}
    assert documented == dict(STATUS)
    assert set(documented) == set(MESSAGES)


@pytest.mark.parametrize("name", GUIDES)
def test_the_guides_list_every_audit_action_of_the_search_api(name: str) -> None:
    actions = set(re.findall(r'action="(search\.[a-z_]+)"', ROUTER.read_text(encoding="utf-8")))
    assert actions >= {"search.run", "search.event", "search.refs", "search.export"}
    section = _section(_doc(name), GUIDES[name][3])
    documented = set(re.findall(r"`(search\.[a-z_]+)`", section))
    assert documented == actions


@pytest.mark.parametrize("name", GUIDES)
def test_the_guides_give_the_settings_with_their_defaults(name: str) -> None:
    """Every ``search.*``/``live.*`` name is a setting, a permission, an audit action or the
    summary table's job; a number in brackets after a setting is its default (``(100.000)``,
    ``(100,000)``)."""
    text = _doc(name)
    named = set(re.findall(r"`((?:search|live)\.[a-z_]+)`", text))
    actions = set(re.findall(r'action="(search\.[a-z_]+)"', ROUTER.read_text(encoding="utf-8")))
    known = {*DEFAULT_SETTINGS, *ALL_PERMISSIONS, *actions, "live.open", "live.close"}
    assert named <= known | {"search.counts_backfill"}, named - known
    for key, number in re.findall(r"`((?:search|live)\.[a-z_]+)` \((\d[\d.,]*)\)", text):
        assert int(re.sub(r"[.,]", "", number)) == DEFAULT_SETTINGS[key], key


@pytest.mark.parametrize("name", GUIDES)
def test_the_guides_record_the_measured_performance(name: str) -> None:
    section = _section(_doc(name), GUIDES[name][4])
    assert "{{" not in section, "performance numbers not filled in"
    rows = _rows(section)
    queries = {cells[0] for cells in rows}
    assert {"Q1", "Q2", "Q5", "Q8", "Q10", "S1"} <= queries, queries
    for cells in rows:
        assert re.search(r"\d+ ms", " ".join(cells)), cells


def test_both_guides_link_each_other_and_the_contract() -> None:
    arama, search = _doc("ARAMA.md"), _doc("SEARCH.md")
    assert "(SEARCH.md)" in arama
    assert "(ARAMA.md)" in search
    for text in (arama, search):
        assert "(MVP-CONTRACTS.md)" in text
        assert "## 17." in _doc("MVP-CONTRACTS.md")
