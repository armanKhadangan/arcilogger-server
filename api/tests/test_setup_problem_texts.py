"""The setup problems of ``GET /admin/config`` in Turkish and English (Wave 4 contract 2.6.2).

``config.setup_problems`` writes each problem as "Türkçe [English]"; the API splits it for the
wizard summary and Ayarlar. The ``sources`` problem names the TOML table ``[[sources]]`` in both
parts, which the split must accept inside the English part.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from arcilogger_api.routers.admin_config import _BILINGUAL
from arcilogger_api.testing import LoggedIn
from arcilogger_core.config import Config, setup_problems

pytestmark = pytest.mark.usefixtures("installed")

Login = Callable[..., LoggedIn]


def test_sources_problem_splits_into_turkish_and_english(
    client: TestClient, login_as: Login
) -> None:
    """The test installation has no ``[[sources]]``: its warning comes in two languages."""
    login_as(client, "admin")
    response = client.get("/api/v1/admin/config")
    assert response.status_code == 200, response.text
    (problem,) = (p for p in response.json()["problems"] if p["key"] == "sources")
    assert problem["level"] == "warning"
    assert problem["message_tr"].startswith("Log kaynağı ([[sources]]) tanımlanmamış: ")
    assert problem["message_tr"].endswith("hiçbir kaynağı izlemez")
    assert problem["message_en"].startswith("no log source ([[sources]]) is configured: ")
    assert problem["message_en"].endswith("watches no source")
    assert "[no log source" not in problem["message_tr"]
    assert "tanımlanmamış" not in problem["message_en"]


def test_every_setup_problem_splits_back_into_its_message(tmp_path: Path) -> None:
    """Each problem a production configuration can have splits, and nothing is lost."""
    config = Config.model_validate(
        {"data_dir": str(tmp_path / "data"), "secrets_dir": str(tmp_path)}
    )
    no_server = config.model_copy(
        update={"clock": config.clock.model_copy(update={"fallback_server": ""})}
    )
    problems = setup_problems(config) + setup_problems(no_server)
    assert {problem.key for problem in problems} == {
        "site_name",
        "clock.ntp_server",
        "clock.fallback_server",
        "sources",
    }
    for problem in problems:
        match = _BILINGUAL.match(problem.message)
        assert match is not None, problem.key
        assert f"{match['tr']} [{match['en']}]" == problem.message
        assert match["tr"] != match["en"], problem.key


@pytest.mark.parametrize(
    "message",
    [
        "Yalnızca Türkçe",
        "Türkçe [English [nested] text]",
        "Türkçe [English [[sources] text]",
    ],
)
def test_a_message_without_a_clean_english_part_is_not_split(message: str) -> None:
    """Only ``[[name]]`` may stand inside the English part; anything else is left whole."""
    assert _BILINGUAL.match(message) is None
