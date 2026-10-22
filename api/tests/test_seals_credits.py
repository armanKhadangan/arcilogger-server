"""``GET /api/v1/credits``: the Kamu SM credit history written by the Credit task."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from arcilogger_api.testing import LoggedIn
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
