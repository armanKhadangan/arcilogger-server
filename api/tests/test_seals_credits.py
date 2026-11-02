"""``GET /api/v1/credits``: the Kamu SM credit history written by the Credit task, and
``supported`` for a provider without a credit query (Wave 5 contract 2.4, C3)."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from arcilogger_api import deps
from arcilogger_api.testing import LoggedIn
from arcilogger_core.config import KAMUSM_PRODUCTION_HOST, Config
from arcilogger_core.credit import check_credit
from arcilogger_core.layout import Layout
from arcilogger_core.tsa.base import StampResult, TsaUnavailableError
from arcilogger_core.tsa.mock import MockTsa, MockTsaProvider

API = "/api/v1/credits"
Login = Callable[..., LoggedIn]


class Unreachable:
    name = "zamane"
    legal_grade = False

    def stamp(self, file_path: Path) -> StampResult:  # pragma: no cover - never stamps
        raise TsaUnavailableError("down")

    def credits(self) -> int | None:
        raise TsaUnavailableError("zd.kamusm.gov.tr unreachable")


def test_credit_history_and_threshold(client: TestClient, login_as: Login, layout: Layout) -> None:
    login_as(client, "auditor")
    empty = client.get(API).json()
    assert empty["history"] == []
    assert empty["latest"] is None
    assert empty["remaining"] is None
    assert empty["below_threshold"] is False
    assert empty["threshold"] == 500
    assert empty["provider"] == "mock"
    assert empty["legal_grade"] is False
    assert empty["supported"] is True

    tsa = MockTsa(layout.state / "mock-tsa", key_type="ec")
    provider = MockTsaProvider(tsa)
    tsa.credits = 9634
    check_credit(provider, layout, 500, clock=lambda: datetime(2026, 10, 10, 6, tzinfo=UTC))
    tsa.credits = 420
    check_credit(provider, layout, 500, clock=lambda: datetime(2026, 10, 11, 6, tzinfo=UTC))
    check_credit(Unreachable(), layout, 500, clock=lambda: datetime(2026, 10, 12, 6, tzinfo=UTC))
    with layout.credit_log.open("ab") as handle:
        handle.write(b'not json\n\n{"time": "no time"}\n')

    body = client.get(API).json()
    assert [entry["credits"] for entry in body["history"]] == [9634, 420, None]
    assert body["history"][0]["time"] == "2026-10-10T09:00:00+03:00"
    assert body["latest"]["error"] == "zd.kamusm.gov.tr unreachable"
    assert body["latest"]["provider"] == "zamane"
    assert body["remaining"] == 420  # the newest known number
    assert body["below_threshold"] is True
    assert body["unreadable_lines"] == 2


def test_only_seal_readers_see_the_credit(client: TestClient, login_as: Login) -> None:
    login_as(client, "viewer")
    response = client.get(API)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def use_tsa(app: FastAPI, config: Config, **tsa: Any) -> None:
    """Serve ``config`` with other ``[tsa]`` values (not validated: ``turktrust`` reads as W5-T
    will give it)."""
    changed = config.model_copy(update={"tsa": config.tsa.model_copy(update=tsa)})
    app.dependency_overrides[deps.get_config] = lambda: changed


def write_lines(layout: Layout, *lines: dict[str, Any]) -> None:
    layout.credit_log.parent.mkdir(parents=True, exist_ok=True)
    with layout.credit_log.open("a", encoding="utf-8") as handle:
        for line in lines:
            handle.write(json.dumps(line) + "\n")


@pytest.mark.parametrize(
    "tsa",
    [{}, {"provider": "zamane", "url": f"https://{KAMUSM_PRODUCTION_HOST}"}],
    ids=["mock", "zamane"],
)
def test_credits_supported_for_a_provider_with_a_credit_query(
    app: FastAPI,
    api_config: Config,
    client: TestClient,
    login_as: Login,
    layout: Layout,
    tsa: dict[str, Any],
) -> None:
    use_tsa(app, api_config, **tsa)
    write_lines(
        layout, {"time": "2026-10-10T09:00:00+03:00", "provider": "zamane", "credits": 9634}
    )
    login_as(client, "auditor")
    body = client.get(API).json()
    assert body["supported"] is True
    assert body["remaining"] == 9634


def test_credits_for_turktrust_are_not_supported(
    app: FastAPI, api_config: Config, client: TestClient, login_as: Login
) -> None:
    """The credit card for ``turktrust`` (contract 3): no number and no threshold warning."""
    use_tsa(app, api_config, provider="turktrust", url="https://198.51.100.20/")
    login_as(client, "auditor")
    body = client.get(API).json()
    assert body["provider"] == "turktrust"
    assert body["supported"] is False
    assert body["remaining"] is None
    assert body["below_threshold"] is False


def test_credits_unsupported_line_from_the_credit_task(
    client: TestClient, login_as: Login, layout: Layout
) -> None:
    """The Credit task records ``{"supported": false}`` (2.4); a later check that returns a
    number, or a check of another provider, does not keep it."""
    unsupported = {"time": "2026-10-11T09:00:00+03:00", "provider": "mock", "supported": False}
    write_lines(layout, unsupported)
    login_as(client, "auditor")
    body = client.get(API).json()
    assert body["supported"] is False
    assert body["remaining"] is None
    assert body["below_threshold"] is False
    assert body["history"][-1]["credits"] is None
    assert "supported" not in body["history"][-1]  # CreditCheck keeps its shape

    write_lines(layout, {**unsupported, "time": "2026-10-12T09:00:00+03:00", "provider": "zamane"})
    assert client.get(API).json()["supported"] is True  # written before a provider change
    write_lines(layout, {"time": "2026-10-13T09:00:00+03:00", "provider": "mock", "credits": 12})
    assert client.get(API).json()["supported"] is True
    # a line that names no provider is the configured provider's
    write_lines(layout, {"time": "2026-10-14T09:00:00+03:00", "supported": False})
    assert client.get(API).json()["supported"] is False
