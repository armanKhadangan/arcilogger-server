"""The browser administration with the Windows rights of NT SERVICE\\ArciApi (contract 2.2).

On Windows the API runs as NT SERVICE\\ArciApi with the rights of the installer's ACL plan
(``Get-ArciAclPlan``, ``deploy/windows/installer/ArciInstall.psm1``). It has none on
``install\\`` (the installer's own state, with ``firewall-state.json`` that ApplySystem reads
as SYSTEM), ``caddy\\``, the agent's own folders (``admin\\work``, ``pending``, ``state``,
``versions``), ``admin\\keys`` (but its own private key and ``keys.json``), nor on
``state\\requests`` and ``backups`` of the data root. Opening a file there fails with "access
denied", and Python 3.12's ``Path.is_file``, ``is_dir`` and ``exists`` raise that error instead
of answering False. These tests run as root on Linux, so :class:`ApiRights` makes the API's own
file access fail the same way while it answers a request; the agent, ApplySystem and the tasks
run between the requests with their own rights.

The steps of the Windows end-to-end job (``_e2e_browser_administration``: the settings page, the
site name, a log source, "Şimdi mühürle", "Kontör sorgula") need nothing the API may not read:
the firewall comes from ApplySystem's report, never from ``install\\``. A file the API finds but
cannot read is a problem of ``GET /admin/config`` with both texts, never a server error.
"""

from __future__ import annotations

import builtins
import errno
import io
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from arcilogger_api.admin_channel import TEXTS
from arcilogger_api.testing import LoggedIn
from arcilogger_core.admin.testing import FakeAgent, FakeSystem, Installed
from arcilogger_core.config import Config
from arcilogger_core.timeutil import site_day, utc_now
from arcilogger_worker.vaulttesting import SealedVault, at_site, make_vault

pytestmark = pytest.mark.usefixtures("installed")

Login = Callable[..., LoggedIn]
V = "/api/v1"
SOURCE = {
    "name": "firewall-01",
    "type": "fortigate",
    "address": "172.31.255.1",
    "heartbeat_minutes": 15,
    "expected": False,
    "transport": "any",
    "enabled": True,
}


class ApiRights:
    """The file access of NT SERVICE\\ArciApi on Windows, while :meth:`as_api` is active.

    A denied folder can still be looked at from its parent (``stat``: the parent grants the
    listing), but it cannot be listed, and nothing below it can be looked at or opened, except
    a file with its own entry for the API (:attr:`granted`: its private key and ``keys.json``
    in the owners-only ``admin\\keys``). ``os.lstat`` stays open: Windows resolves a path
    (``Path.resolve``) without any right on it. Every refusal is recorded in :attr:`refused`."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, installed: Installed) -> None:
        admin, data = installed.layout, installed.data_dir
        self.folders = [
            installed.root / "install",
            installed.root / "caddy",
            admin.work,
            admin.pending,
            admin.state,
            admin.versions,
            admin.keys,
            data / "state" / "requests",
            data / "backups",
        ]
        self.granted = [admin.keys / "api-ed25519.key", admin.keys / "keys.json"]
        self.active = False
        self.refused: list[str] = []
        real_stat, real_scandir, real_listdir = os.stat, os.scandir, os.listdir
        real_open = io.open

        def stat(path: Any, *args: Any, **kwargs: Any) -> Any:
            self._check(path, look=True)
            return real_stat(path, *args, **kwargs)

        def scandir(path: Any = ".") -> Any:
            self._check(path, listing=True)
            return real_scandir(path)

        def listdir(path: Any = ".") -> Any:
            self._check(path, listing=True)
            return real_listdir(path)

        def open_(file: Any, *args: Any, **kwargs: Any) -> Any:
            self._check(file)
            return real_open(file, *args, **kwargs)

        monkeypatch.setattr(os, "stat", stat)
        monkeypatch.setattr(os, "scandir", scandir)
        monkeypatch.setattr(os, "listdir", listdir)
        monkeypatch.setattr(io, "open", open_)
        monkeypatch.setattr(builtins, "open", open_)

    def deny(self, folder: Path) -> None:
        """Another folder the API has no right on (one an administrator chose by hand)."""
        self.folders.append(folder)

    @contextmanager
    def as_api(self) -> Iterator[None]:
        self.active = True
        try:
            yield
        finally:
            self.active = False

    def _check(self, path: Any, *, look: bool = False, listing: bool = False) -> None:
        if not self.active or isinstance(path, int):
            return
        target = str(Path(os.fsdecode(path)).absolute())  # lexical: no stat of its own
        if target in map(str, self.granted):
            return
        for folder in map(str, self.folders):
            inside = target.startswith(folder + os.sep)
            if inside or (target == folder and (listing or not look)):
                self._refuse(target)

    def _refuse(self, target: str) -> None:
        self.refused.append(target)
        raise PermissionError(errno.EACCES, "Access is denied", target)


@pytest.fixture
def rights(monkeypatch: pytest.MonkeyPatch, installed: Installed) -> ApiRights:
    return ApiRights(monkeypatch, installed)


def unreadable(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The problems of ``GET /admin/config`` that name a file the API could not read."""
    message_en = TEXTS["admin_file_unreadable"][1]
    return {
        problem["key"]: problem
        for problem in document["problems"]
        if problem["message_en"] == message_en
    }


def system_agent(installed: Installed, **system: Any) -> FakeAgent:
    """The agent with ApplySystem (``install\\firewall-state.json``: a managed firewall),
    after ApplySystem's first report."""
    fake = FakeAgent(installed, system=FakeSystem(installed, **system))
    fake.start()
    fake.process()
    return fake


def test_the_settings_page_needs_nothing_the_api_may_not_read(
    client: TestClient, login_as: Login, installed: Installed, rights: ApiRights
) -> None:
    """The real installation's 500 of ``GET /admin/config``: the API looked at
    ``install\\firewall-state.json`` for whether the firewall is managed."""
    system_agent(installed, strict_outbound=True)
    assert (installed.root / "install" / "firewall-state.json").is_file()
    login_as(client, "admin")
    with rights.as_api():
        config = client.get(f"{V}/admin/config")
        status = client.get(f"{V}/admin/status")
    assert config.status_code == 200, config.text
    assert status.status_code == 200, status.text
    assert rights.refused == []
    assert unreadable(config.json()) == {}
    firewall = status.json()["system"]["firewall"]
    assert (firewall["managed"], firewall["strict_outbound"]) == (True, True)


def test_the_strict_outbound_mode_comes_from_applysystem(
    client: TestClient, login_as: Login, installed: Installed, rights: ApiRights
) -> None:
    """A new time server needs the firewall first in strict outbound mode (rules 2.6): the API
    knows the mode from ApplySystem's report, not from the installer's file it may not read."""
    system_agent(installed, strict_outbound=True)
    login_as(client, "admin")
    with rights.as_api():
        tag = client.get(f"{V}/admin/config").json()["etags"]["general"]
        checked = client.post(
            f"{V}/admin/config/general/check",
            json={"etag": tag, "clock": {"ntp_server": "172.20.10.2"}},
        )
    assert checked.status_code == 200, checked.text
    assert "system.firewall" in checked.json()["effects"]
    assert "probe_skipped" in [warning["code"] for warning in checked.json()["warnings"]]
    assert rights.refused == []


def test_before_applysystem_reports_a_source_waits_for_the_firewall(
    client: TestClient, login_as: Login, installed: Installed, rights: ApiRights
) -> None:
    text = installed.config_path.read_text(encoding="utf-8")
    installed.config_path.write_text(
        text + '\n[[sources]]\nname = "core-r1"\ntype = "mikrotik"\naddress = "172.31.255.1"\n',
        encoding="utf-8",
    )
    FakeSystem(installed)  # managed (the installer's file), but no report yet
    login_as(client, "admin")
    with rights.as_api():
        response = client.get(f"{V}/admin/config")
    assert response.status_code == 200, response.text
    (source,) = response.json()["sections"]["sources"]
    assert source["firewall"] == "pending"
    assert rights.refused == []


TODAY = site_day(utc_now())
SEALED = [TODAY - timedelta(days=offset) for offset in (7, 6, 5, 4)]
FAILED = TODAY - timedelta(days=3)


@pytest.fixture
def vault(api_config: Config) -> Iterator[SealedVault]:
    """Sealed days and the oldest due one, whose last attempt failed ("Şimdi mühürle")."""
    sealed = make_vault(api_config.data_dir, tsa_now=at_site(SEALED[0] - timedelta(days=30)))
    sealed.seal_days(SEALED)
    sealed.write_day(FAILED)
    assert sealed.fail_seal(FAILED).status == "pending"
    yield sealed
    sealed.unlock_all()


def test_the_end_to_end_steps_with_the_rights_of_the_api(
    client: TestClient,
    login_as: Login,
    installed: Installed,
    rights: ApiRights,
    vault: SealedVault,
) -> None:
    """``_e2e_browser_administration`` of the Windows job, each answer of the API given with
    its Windows rights: the site name, a log source through the firewall, "Şimdi mühürle" and
    "Kontör sorgula", each followed to its end."""
    agent = system_agent(installed)
    login_as(client, "admin")

    def api(method: str, path: str, body: Any = None) -> dict[str, Any]:
        with rights.as_api():
            response = client.request(method, path, json=body)
        assert response.status_code in (200, 202), (method, path, response.text)
        found: dict[str, Any] = response.json()
        return found

    def etag(section: str) -> str:
        found: str = api("GET", f"{V}/admin/config")["etags"][section]
        return found

    def follow(accepted: dict[str, Any]) -> dict[str, Any]:
        agent.process()
        agent.process()  # ApplySystem runs after the agent wrote the file
        return api("GET", accepted["status_url"])

    site = "Örnek A.Ş. (Windows CI)"
    done = follow(
        api("PUT", f"{V}/admin/config/general", {"etag": etag("general"), "site_name": site})
    )
    assert done["state"] == "active", done
    assert api("GET", f"{V}/auth/me")["site_name"] == site

    accepted = api("POST", f"{V}/admin/config/sources", {"etag": etag("sources"), "source": SOURCE})
    done = follow(accepted)
    assert done["state"] == "active", done
    steps = {step["step"]: step["state"] for step in done["steps"]}
    assert steps["system.firewall"] == "done", steps
    (source,) = api("GET", f"{V}/admin/config")["sections"]["sources"]
    assert (source["name"], source["firewall"]) == ("firewall-01", "open")

    done = follow(api("POST", f"{V}/seals/{FAILED.isoformat()}/seal"))
    assert (done["state"], done["result"]["status"]) == ("succeeded", "sealed"), done

    done = follow(api("POST", f"{V}/credits/query"))
    assert done["state"] == "succeeded", done
    assert done["result"]["credits"] >= 0, done
    assert unreadable(api("GET", f"{V}/admin/config")) == {}
    assert rights.refused == []


def test_a_file_the_api_cannot_read_is_a_problem_not_a_server_error(
    client: TestClient, login_as: Login, installed: Installed, rights: ApiRights, tmp_path: Path
) -> None:
    """Kamu SM roots, a Zamane client and a Java chosen by hand in a folder NT SERVICE\\ArciApi
    may not read: the page answers, names each unreadable path with both texts and shows what
    the switch to production still needs."""
    protected = tmp_path / "protected"
    roots, jar = protected / "kamusm", protected / "zamane" / "tss-client-console-3.1.33.jar"
    java = protected / "java" / "bin" / "java.exe"
    for path in (roots / "root.pem", jar, java):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not read")
    text = installed.config_path.read_text(encoding="utf-8")
    installed.config_path.write_text(
        text.replace(
            'provider = "mock"',
            f'provider = "mock"\nroots_dir = "{roots.as_posix()}"\n'
            f'zamane_jar = "{jar.as_posix()}"\njava = ["{java.as_posix()}"]',
            1,
        ),
        encoding="utf-8",
    )
    rights.deny(protected)
    login_as(client, "admin")
    with rights.as_api():
        response = client.get(f"{V}/admin/config")
    assert response.status_code == 200, response.text
    document = response.json()
    found = unreadable(document)
    assert set(found) == {str(roots), str(jar)}
    for problem in found.values():
        assert (problem["level"], problem["message_tr"]) == (
            "warning",
            TEXTS["admin_file_unreadable"][0],
        )
    tsa = document["sections"]["tsa"]
    assert tsa["roots"] == []
    assert tsa["zamane"]["present"] is False
    assert {"zamane_jar", "java", "roots"} <= set(tsa["readiness"]["missing"])
    assert rights.refused  # the API did try, and got "access denied"


def test_the_new_text_has_both_languages() -> None:
    message_tr, message_en = TEXTS["admin_file_unreadable"]
    assert "NT SERVICE\\ArciApi" in message_tr
    assert "NT SERVICE\\ArciApi" in message_en
    assert "Install-Arcilogger.ps1" in message_tr
    assert message_tr != message_en
