"""All ARCILOGGER packages ship as one product with one version number."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

import arcilogger_api
import arcilogger_collector
import arcilogger_core
import arcilogger_db
import arcilogger_ingest
import arcilogger_worker


def test_all_packages_share_the_product_version() -> None:
    versions = {
        module.__name__: module.__version__
        for module in (
            arcilogger_core,
            arcilogger_db,
            arcilogger_collector,
            arcilogger_ingest,
            arcilogger_api,
            arcilogger_worker,
        )
    }
    assert set(versions.values()) == {arcilogger_core.__version__}, versions


# -- the release date (licensing contract 5.1, 9.2; test A-T15) ------------------------------------

ROOT = Path(__file__).resolve().parents[1]
CORE_INIT = ROOT / "core" / "src" / "arcilogger_core" / "__init__.py"
#: The published .NET pattern Build-ArciBundle uses (``(?<d>…)``; Python spells it ``(?P<d>…)``).
BUNDLE_PATTERN = r'(?m)^RELEASE_DATE: Final = "(?<d>\d{4}-\d{2}-\d{2})"'


def test_release_date_is_one_literal_line_the_bundle_script_can_read() -> None:
    text = CORE_INIT.read_text(encoding="utf-8")
    matches = re.findall(BUNDLE_PATTERN.replace("(?<d>", "(?P<d>"), text)
    assert matches == [arcilogger_core.RELEASE_DATE]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", arcilogger_core.RELEASE_DATE)
    assert date.fromisoformat(arcilogger_core.RELEASE_DATE).isoformat() == (
        arcilogger_core.RELEASE_DATE
    )
    lines = [line for line in text.splitlines() if "RELEASE_DATE" in line and "=" in line]
    assert lines == [f'RELEASE_DATE: Final = "{arcilogger_core.RELEASE_DATE}"']
    # __version__ stays readable by Build-ArciBundle and hatch as before.
    assert re.search(r'(?m)^__version__\s*=\s*"(?P<v>[^"]+)"', text)


def test_the_licence_package_parses_the_release_date() -> None:
    from arcilogger_core.license import release_date

    assert release_date() == date.fromisoformat(arcilogger_core.RELEASE_DATE)


def test_the_installer_scripts_read_the_release_date_with_the_published_pattern() -> None:
    """Build-ArciBundle (or ArciInstall.psm1's Get-ArciProductReleaseDate) reads RELEASE_DATE
    with exactly the published pattern, so both sides agree on the line format."""
    installer = ROOT / "deploy" / "windows" / "installer"
    scripts = [installer / "Build-ArciBundle.ps1", installer / "ArciInstall.psm1"]
    readers = [path for path in scripts if "RELEASE_DATE" in path.read_text(encoding="utf-8-sig")]
    if not readers:
        pytest.skip("the installer does not read RELEASE_DATE yet (area D)")
    assert any(BUNDLE_PATTERN in path.read_text(encoding="utf-8-sig") for path in readers)
