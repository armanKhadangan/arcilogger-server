"""Executable policy at run time (ADR-0014; contract 2.8.4)."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from arcilogger_core.admin.contract import (
    FALLBACK_POLICY,
    AdminLayout,
    AdminPolicy,
    ApprovedJar,
    JarsFile,
    TrustedExecutable,
    dump_document,
)
from arcilogger_core.alerts import read_alerts
from arcilogger_core.cli import main as cli_main
from arcilogger_core.config import load_config
from arcilogger_core.execpolicy import (
    CheckedJar,
    ExecutableRefusedError,
    TrustAnchors,
    ZamanePaths,
    check_executable,
    check_java,
    check_zamane_jar,
)
from arcilogger_core.layout import Layout
from arcilogger_core.secretstore import KAMUSM_CUSTOMER, KAMUSM_PASSWORD, SecretStore
from arcilogger_core.timeutil import SITE_TZ, utc_now
from arcilogger_core.tsa import TsaExecutableError, make_provider, zamane_releases
from arcilogger_core.tsa.base import TsaConfigurationError
from arcilogger_core.tsa.zamane import ZamaneCliProvider
from arcilogger_core.tsa.zamane_releases import KNOWN_RELEASES, ZamaneRelease, find_release


@dataclass
class Machine:
    """A program folder (``executable_roots``), a folder outside it, the installation folder
    (``ProgramData\\ARCILOGGER``, where the configuration file lies)."""

    roots: Path
    outside: Path
    programdata: Path

    def anchors(self, **changes: Any) -> TrustAnchors:
        """The trust anchors of the installation with a policy of these values."""
        fields: dict[str, Any] = {
            "enabled": True,
            "executable_roots": (str(self.roots),),
            "written_at": utc_now().astimezone(SITE_TZ),
            "written_by": "installer",
        }
        fields.update(changes)
        return TrustAnchors(self.programdata, AdminPolicy(**fields))

    @property
    def zamane(self) -> ZamanePaths:
        return ZamanePaths(self.programdata / "zamane")


def program(path: Path, content: bytes = b"#!program\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def recorded(path: Path) -> TrustedExecutable:
    """What the installer records for a program outside the default trusted locations."""
    return TrustedExecutable(path=str(path), sha256=sha(path))


def link(path: Path, target: Path) -> Path:
    """A symbolic link (a junction resolves the same way). Windows creates one only with the
    privilege for it; without it the test is skipped."""
    try:
        path.symlink_to(target, target_is_directory=target.is_dir())
    except OSError as exc:
        pytest.skip(f"no symbolic links here: {exc}")
    return path


@pytest.fixture
def machine(tmp_path: Path) -> Machine:
    return Machine(
        roots=tmp_path / "Program Files",
        outside=tmp_path / "Tools",
        programdata=tmp_path / "ProgramData",
    )


def refusal(call: Any, *args: Any, **kwargs: Any) -> str:
    with pytest.raises(ExecutableRefusedError) as caught:
        call(*args, **kwargs)
    assert caught.value.code == "executable_refused"
    return caught.value.reason


# -- the trust anchors ----------------------------------------------------------------------------


def test_the_anchors_are_the_folder_of_the_configuration_file(machine: Machine) -> None:
    """The agent can write secrets_dir; a policy, an installer record and a jar in a folder it
    points secrets_dir at count for nothing."""
    elsewhere = machine.outside / "agent"
    attacker_java = program(elsewhere / "bin" / "java.exe", b"#!the agent's program\n")
    attacker_jar = program(elsewhere / "zamane" / "client.jar", b"PK\x03\x04 the agent's jar")
    (elsewhere / "zamane" / "zamane-jar.sha256").write_text(f"{sha(attacker_jar)}  client.jar\n")
    policy = AdminPolicy(
        enabled=True,
        trusted_executables=(recorded(attacker_java),),
        written_at=utc_now(),
        written_by="installer",
    )
    (elsewhere / "admin").mkdir()
    AdminLayout(elsewhere / "admin").policy.write_bytes(dump_document(policy))
    config_path = machine.programdata / "arcilogger.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        f'data_dir = "{(machine.outside / "data").as_posix()}"\n'
        f'secrets_dir = "{elsewhere.as_posix()}"\n'
        f'[tsa]\njava = ["{attacker_java.as_posix()}"]\nzamane_jar = "{attacker_jar.as_posix()}"\n',
        encoding="utf-8",
    )
    config = load_config(config_path)
    assert config.admin_dir == elsewhere / "admin"
    anchors = TrustAnchors.of(config_path)
    assert anchors.root == machine.programdata
    assert anchors.policy == FALLBACK_POLICY
    refusal(check_java, config.tsa.java, anchors)
    assert config.tsa.zamane_jar is not None
    assert "not an installed client" in refusal(check_zamane_jar, config.tsa.zamane_jar, anchors)


def test_a_missing_or_damaged_policy_keeps_the_defaults_running(machine: Machine) -> None:
    """No policy.json (before the installer writes it, or a bad hand edit): the default java and
    the installer-recorded jar still run, nothing else is trusted."""
    config_path = machine.programdata / "arcilogger.toml"
    jar = installed_jar(machine)
    policy_file = AdminLayout(machine.programdata / "admin").policy
    for content in (None, b'{"schema": "arcilogger.admin-policy/1", "enabled": tru'):
        if content is not None:
            policy_file.parent.mkdir(parents=True, exist_ok=True)
            policy_file.write_bytes(content)
        anchors = TrustAnchors.of(config_path)
        assert anchors.policy == FALLBACK_POLICY
        assert not anchors.policy.enabled
        assert anchors.policy.trusted_executables == ()
        assert check_java(("java",), anchors) == ("java",)
        assert check_zamane_jar(jar, anchors) == CheckedJar(jar, sha(jar))
    elsewhere = program(machine.outside / "bin" / "java.exe")
    refusal(check_java, (str(elsewhere),), TrustAnchors.of(config_path))


def test_a_written_policy_is_read_from_the_installation_folder(machine: Machine) -> None:
    java = program(machine.outside / "java" / "bin" / "java.exe")
    trusted = TrustedExecutable(path=str(java), sha256=sha(java))
    written = machine.anchors(trusted_executables=(trusted,)).policy
    policy_file = AdminLayout(machine.programdata / "admin").policy
    policy_file.parent.mkdir(parents=True)
    policy_file.write_bytes(dump_document(written))
    anchors = TrustAnchors.of(machine.programdata / "arcilogger.toml")
    assert anchors.policy == written
    assert check_java((str(java),), anchors) == (str(java),)


# -- tsa.java -------------------------------------------------------------------------------------


@pytest.mark.parametrize("java", [("java",), ("java.exe",), ("JAVA.EXE",)])
def test_the_default_java_from_the_system_path_is_accepted(machine: Machine, java: Any) -> None:
    assert check_java(java, machine.anchors()) == java


@pytest.mark.parametrize(
    "java",
    [("cmd",), ("powershell",), ("cmd.exe",), ("bin/java",), ("..\\java.exe",), ("",), ()],
)
def test_other_bare_names_and_relative_paths_are_refused(machine: Machine, java: Any) -> None:
    refusal(check_java, java, machine.anchors())


def test_java_under_a_trusted_root_is_accepted(machine: Machine) -> None:
    java = program(machine.roots / "Eclipse Adoptium" / "jre-8.0.432.6-hotspot" / "bin" / "java")
    assert check_java((str(java),), machine.anchors()) == (str(java),)


def test_java_outside_the_roots_needs_a_recorded_hash(machine: Machine) -> None:
    java = program(machine.outside / "java" / "bin" / "java.exe")
    assert "recorded with its SHA-256" in refusal(check_java, (str(java),), machine.anchors())
    trusted = TrustedExecutable(path=str(java), sha256=sha(java))
    check_java((str(java),), machine.anchors(trusted_executables=(trusted,)))
    java.write_bytes(b"#!changed since the installer recorded it\n")
    refusal(check_java, (str(java),), machine.anchors(trusted_executables=(trusted,)))


def test_a_recorded_hash_counts_only_at_its_recorded_path(machine: Machine) -> None:
    """A byte-identical copy in a folder someone else can write would load that folder's
    libraries (jvm.dll, libpq.dll)."""
    java = program(machine.outside / "java" / "bin" / "java.exe")
    anchors = machine.anchors(trusted_executables=(recorded(java),))
    copy = program(machine.outside / "logs" / "x" / "bin" / "java.exe", java.read_bytes())
    assert "recorded with its SHA-256" in refusal(check_java, (str(copy),), anchors)
    pg_dump = program(machine.outside / "pg" / "pg_dump.exe")
    anchors = machine.anchors(trusted_executables=(recorded(pg_dump),))
    copied = program(machine.outside / "logs" / "pg_dump.exe", pg_dump.read_bytes())
    refusal(check_executable, str(copied), anchors, what="backup.pg_dump")
    assert check_executable(str(pg_dump), anchors) == str(pg_dump)


@pytest.mark.parametrize(
    "path",
    [
        "\\\\fileserver.ornek.example\\tools\\java\\bin\\java.exe",
        "//fileserver.ornek.example/tools/java/bin/java.exe",
        "\\\\?\\C:\\Program Files\\Java\\bin\\java.exe",
        "\\\\.\\C:\\Program Files\\Java\\bin\\java.exe",
    ],
)
def test_network_and_device_paths_are_refused(machine: Machine, path: str) -> None:
    trusted = TrustedExecutable(path=path, sha256="0" * 64)
    assert "network or device path" in refusal(
        check_java, (path,), machine.anchors(trusted_executables=(trusted,))
    )


def test_a_second_java_element_is_refused_unless_the_whole_command_was_recorded(
    machine: Machine,
) -> None:
    java = program(machine.roots / "Java" / "bin" / "java.exe")
    for extra in ("-XX:OnError=cmd /c calc", "-Xmx256m", "-jar"):
        assert "extra arguments" in refusal(check_java, (str(java), extra), machine.anchors())
    recorded = TrustedExecutable(path=str(java), sha256=sha(java), argv=(str(java), "-Xmx256m"))
    anchors = machine.anchors(trusted_executables=(recorded,))
    assert check_java((str(java), "-Xmx256m"), anchors) == (str(java), "-Xmx256m")
    refusal(check_java, (str(java), "-Xmx512m"), anchors)
    refusal(check_java, (str(java), "-Xmx256m", "-XX:OnError=calc"), anchors)


def test_a_recorded_command_still_needs_its_program_trusted(machine: Machine) -> None:
    java = program(machine.outside / "bin" / "java.exe")
    recorded = TrustedExecutable(path=str(java), sha256="0" * 64, argv=(str(java), "-Xmx256m"))
    refusal(check_java, (str(java), "-Xmx256m"), machine.anchors(trusted_executables=(recorded,)))


def test_another_program_under_a_trusted_root_is_not_java(machine: Machine) -> None:
    other = program(machine.roots / "Something" / "cmd.exe")
    assert "is not one of" in refusal(check_java, (str(other),), machine.anchors())


def test_a_link_out_of_the_trusted_root_is_refused(machine: Machine) -> None:
    """A junction or link inside Program Files that leads elsewhere counts as elsewhere."""
    target = program(machine.outside / "evil" / "java.exe")
    inside = machine.roots / "Java" / "bin"
    inside.mkdir(parents=True)
    refusal(check_java, (str(link(inside / "java.exe", target)),), machine.anchors())
    folder = link(machine.roots / "Linked", target.parent)
    refusal(check_java, (str(folder / "java.exe"),), machine.anchors())


def test_a_link_into_the_trusted_root_is_refused(machine: Machine) -> None:
    """A junction in a folder the agent can write (logs\\) that leads into Program Files passes
    a check that follows it, and can be re-pointed before java starts: no link on the path at
    all."""
    java = program(machine.roots / "Eclipse Adoptium" / "jre" / "bin" / "java.exe")
    logs = machine.outside / "logs"
    logs.mkdir(parents=True)
    junction = link(logs / "j", java.parent.parent)
    reason = refusal(check_java, (str(junction / "bin" / "java.exe"),), machine.anchors())
    assert "resolves to" in reason
    assert check_java((str(java),), machine.anchors()) == (str(java),)


def test_a_missing_or_relative_java_is_refused(machine: Machine) -> None:
    assert "does not exist" in refusal(
        check_java, (str(machine.roots / "Java" / "bin" / "java.exe"),), machine.anchors()
    )
    refusal(check_java, ("Program Files/Java/bin/java.exe",), machine.anchors())
    folder = machine.roots / "Java" / "java.exe"
    folder.mkdir(parents=True)
    assert "not a file" in refusal(check_java, (str(folder),), machine.anchors())


# -- backup tools ---------------------------------------------------------------------------------


def test_backup_tools_need_an_absolute_trusted_path(machine: Machine) -> None:
    pg_dump = program(machine.roots / "PostgreSQL" / "16" / "bin" / "pg_dump.exe")
    assert check_executable(str(pg_dump), machine.anchors(), what="backup.pg_dump") == str(pg_dump)
    refusal(check_executable, "pg_dump", machine.anchors(), what="backup.pg_dump")
    elsewhere = program(machine.outside / "pg_dump.exe")
    refusal(check_executable, str(elsewhere), machine.anchors())
    trusted = TrustedExecutable(path=str(elsewhere), sha256=sha(elsewhere))
    check_executable(str(elsewhere), machine.anchors(trusted_executables=(trusted,)))


def test_the_refusal_names_the_setting(machine: Machine) -> None:
    with pytest.raises(ExecutableRefusedError, match=re.escape("backup.pg_restore: ")) as caught:
        check_executable("pg_restore", machine.anchors(), what="backup.pg_restore")
    assert caught.value.what == "backup.pg_restore"


# -- the Zamane client ----------------------------------------------------------------------------


def installed_jar(machine: Machine, content: bytes = b"PK\x03\x04 zamane client") -> Path:
    """A jar copied into zamane\\ and recorded by the installer (``-ZamaneJar``)."""
    jar = program(machine.zamane.root / "tss-client-console-3.1.33.jar", content)
    record = f"{sha(jar)}  {jar.name}\n# confirmed 2026-10-25T07:00:00Z by Administrator\n"
    machine.zamane.installer_record.write_text(record, encoding="utf-8")
    return jar


@pytest.mark.parametrize("denied", ["stat", "open"])
def test_a_program_that_cannot_be_read_is_refused(
    machine: Machine, monkeypatch: pytest.MonkeyPatch, denied: str
) -> None:
    """Windows: an account without a right on a file gets "access denied" from ``Path.is_file``
    (Python 3.12 raises it instead of answering False) and from hashing it. The checks refuse
    such a program or client (fail closed) instead of raising an OSError no caller expects (the
    API's settings page, the Seal task, the backup)."""
    java = program(machine.outside / "java" / "bin" / "java.exe")
    trusted = TrustedExecutable(path=str(java), sha256=sha(java))
    jar = installed_jar(machine)
    real = getattr(Path, denied)

    def refuse(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self in (java, jar):
            raise PermissionError(13, "Access is denied", str(self))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, denied, refuse)
    anchors = machine.anchors(trusted_executables=(trusted,))
    assert "cannot be read (PermissionError: Access is denied)" in refusal(
        check_java, (str(java),), anchors
    )
    assert "cannot be read" in refusal(check_zamane_jar, jar, anchors)


def test_the_installer_recorded_jar_is_accepted(machine: Machine) -> None:
    jar = installed_jar(machine)
    checked = check_zamane_jar(jar, machine.anchors())
    assert checked == CheckedJar(jar, sha(jar))
    path, digest = checked
    assert (path, digest) == (jar, sha(jar))


def test_an_unrecorded_jar_is_refused(machine: Machine) -> None:
    jar = installed_jar(machine)
    other = program(machine.zamane.root / "other.jar", b"PK\x03\x04 another client")
    assert check_zamane_jar(jar, machine.anchors())
    assert "not known" in refusal(check_zamane_jar, other, machine.anchors())
    jar.write_bytes(b"PK\x03\x04 replaced after the installation")
    refusal(check_zamane_jar, jar, machine.anchors())


def test_a_jar_outside_the_zamane_folder_is_refused_even_when_trusted(machine: Machine) -> None:
    jar = program(machine.outside / "tss-client-console-3.1.33.jar")
    trusted = TrustedExecutable(path=str(jar), sha256=sha(jar))
    assert "not an installed client" in refusal(
        check_zamane_jar, jar, machine.anchors(trusted_executables=(trusted,))
    )


def test_the_upgrade_copies_a_jar_from_outside_into_the_zamane_folder(machine: Machine) -> None:
    """Contract 2.16 (Day 0 notes): a ``tsa.zamane_jar`` configured by hand outside zamane\\
    is copied into zamane\\ and its line appended to the installer record, and the
    configuration names the copy; sealing then goes on. Recording the outside path in
    ``trusted_executables`` instead would not help: a jar runs only from zamane\\."""
    earlier = installed_jar(machine)
    outside = program(machine.outside / "Zamane" / "tss-client-console-3.1.34.jar", b"PK 3.1.34")
    trusted = TrustedExecutable(path=str(outside), sha256=sha(outside))
    refusal(check_zamane_jar, outside, machine.anchors(trusted_executables=(trusted,)))
    copy = program(machine.zamane.root / outside.name, outside.read_bytes())
    with machine.zamane.installer_record.open("a", encoding="utf-8") as record:
        record.write(f"{sha(copy)}  {copy.name}\n# copied from {outside} during the upgrade\n")
    assert check_zamane_jar(copy, machine.anchors()) == CheckedJar(copy, sha(outside))
    assert check_zamane_jar(earlier, machine.anchors()).sha256 == sha(earlier)


def test_a_staged_upload_never_runs(machine: Machine, monkeypatch: pytest.MonkeyPatch) -> None:
    staged = program(machine.zamane.staging / f"{'a' * 64}.jar", b"PK\x03\x04 uploaded")
    monkeypatch.setattr(zamane_releases, "KNOWN_RELEASES", {sha(staged): ZamaneRelease("3.1.33")})
    record = f"{sha(staged)}  {staged.name}\n"
    machine.zamane.installer_record.write_text(record, encoding="utf-8")
    refusal(check_zamane_jar, staged, machine.anchors())
    # Nor through a link inside zamane\ that points into the staging folder.
    client = link(machine.zamane.root / "client.jar", staged)
    refusal(check_zamane_jar, client, machine.anchors())


def test_a_link_into_the_zamane_folder_is_refused(machine: Machine) -> None:
    """``logs\\z`` -> ``zamane\\``: the genuine jar would be hashed, then the junction
    re-pointed before java opens the file."""
    jar = installed_jar(machine)
    logs = machine.outside / "logs"
    logs.mkdir(parents=True)
    junction = link(logs / "z", machine.zamane.root)
    assert "resolves to" in refusal(check_zamane_jar, junction / jar.name, machine.anchors())
    file_link = link(logs / "client.jar", jar)
    refusal(check_zamane_jar, file_link, machine.anchors())


@pytest.mark.parametrize("approved_by", ["known", "console:bt-yonetici"])
def test_an_approved_jar_is_accepted(machine: Machine, approved_by: str) -> None:
    jar = program(machine.zamane.approved / f"{'c3' * 32}.jar", b"PK\x03\x04 promoted client")
    refusal(check_zamane_jar, jar, machine.anchors())
    approved = ApprovedJar(
        sha256=sha(jar), path=str(jar), approved_at=utc_now(), approved_by=approved_by
    )
    jars = machine.anchors().jars
    assert jars == machine.programdata / "admin" / "system" / "jars.json"
    jars.parent.mkdir(parents=True)
    jars.write_bytes(dump_document(JarsFile(approved=(approved,))))
    assert check_zamane_jar(jar, machine.anchors()).sha256 == sha(jar)
    jars.write_bytes(b'{"schema": "arcilogger.admin-jars/1", "approved": [}')
    refusal(check_zamane_jar, jar, machine.anchors())


def test_a_known_release_is_accepted(machine: Machine, monkeypatch: pytest.MonkeyPatch) -> None:
    jar = program(machine.zamane.root / "tss-client-console-3.1.33.jar", b"PK\x03\x04 known")
    refusal(check_zamane_jar, jar, machine.anchors())
    monkeypatch.setattr(zamane_releases, "KNOWN_RELEASES", {sha(jar): ZamaneRelease("3.1.33")})
    assert check_zamane_jar(jar, machine.anchors()).sha256 == sha(jar)


def test_a_policy_trusted_jar_is_accepted_at_its_recorded_path(machine: Machine) -> None:
    jar = program(machine.zamane.root / "tss-client-console-3.1.33.jar", b"PK\x03\x04 recorded")
    trusted = TrustedExecutable(path=str(jar), sha256=sha(jar))
    assert check_zamane_jar(jar, machine.anchors(trusted_executables=(trusted,)))
    copy = program(machine.zamane.root / "other" / jar.name, jar.read_bytes())
    refusal(check_zamane_jar, copy, machine.anchors(trusted_executables=(trusted,)))


def test_a_missing_jar_is_refused(machine: Machine) -> None:
    missing = machine.zamane.root / "tss-client-console-3.1.33.jar"
    assert "does not exist" in refusal(check_zamane_jar, missing, machine.anchors())


# -- the release list -----------------------------------------------------------------------------


def test_the_release_list_is_empty_until_the_owner_fills_it() -> None:
    assert dict(KNOWN_RELEASES) == {}
    hexdigest = re.compile(r"[0-9a-f]{64}")
    for jar_sha, release in KNOWN_RELEASES.items():
        assert hexdigest.fullmatch(jar_sha)
        assert release.version
        assert release.archive_sha256 is None or hexdigest.fullmatch(release.archive_sha256)


def test_a_release_is_found_by_its_jar_or_its_archive(monkeypatch: pytest.MonkeyPatch) -> None:
    release = ZamaneRelease("3.1.33", archive_sha256="b" * 64)
    monkeypatch.setattr(zamane_releases, "KNOWN_RELEASES", {"a" * 64: release})
    assert find_release("a" * 64) is release
    assert find_release("B" * 64) is release
    assert find_release("c" * 64) is None


# -- at run time: the time-stamp provider and the tasks -----------------------------------------


def zamane_config(machine: Machine, *, java: tuple[str, ...], jar: Path) -> Path:
    """``arcilogger.toml`` of the installation for the Zamane client, credentials stored."""
    config_path = machine.programdata / "arcilogger.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    java_list = ", ".join(json.dumps(element) for element in java)
    config_path.write_text(
        f'data_dir = "{(machine.outside / "data").as_posix()}"\n'
        f'[tsa]\nprovider = "zamane"\nurl = "http://tzd.kamusm.gov.tr"\njava = [{java_list}]\n'
        f"zamane_jar = {json.dumps(str(jar))}\n",
        encoding="utf-8",
    )
    SecretStore.create_key(machine.programdata / "secret.key")
    secrets = SecretStore(machine.programdata / "secret.key", machine.programdata / "secrets.json")
    secrets.set(KAMUSM_CUSTOMER, "123456")
    secrets.set(KAMUSM_PASSWORD, "Deneme-Parola-7")  # gitleaks:allow
    return config_path


def provider_of(config_path: Path) -> Any:
    config = load_config(config_path)
    secrets = SecretStore(config.secrets_dir / "secret.key", config.secrets_dir / "secrets.json")
    anchors = TrustAnchors.of(config_path)
    return make_provider(config, Layout(config.data_dir), secrets, anchors=anchors)


def test_the_provider_runs_the_defaults_that_pass_the_policy(machine: Machine) -> None:
    """After an upgrade nothing stops sealing: ``java`` from the PATH and the installer's jar,
    with no policy.json written yet."""
    jar = installed_jar(machine)
    provider = provider_of(zamane_config(machine, java=("java",), jar=jar))
    assert isinstance(provider, ZamaneCliProvider)
    assert provider.java == ("java",)
    assert provider.jar == jar


@pytest.mark.parametrize(
    ("java", "reason"),
    [(("java", "-XX:OnError=cmd /c calc"), "extra arguments"), (("cmd",), "bare program name")],
)
def test_the_provider_refuses_a_java_the_policy_refuses(
    machine: Machine, java: tuple[str, ...], reason: str
) -> None:
    config_path = zamane_config(machine, java=java, jar=installed_jar(machine))
    with pytest.raises(TsaExecutableError, match=reason) as caught:
        provider_of(config_path)
    assert isinstance(caught.value, TsaConfigurationError)  # every caller already handles it
    assert caught.value.refusal.what == "tsa.java"


def test_the_provider_refuses_a_jar_the_policy_refuses(machine: Machine) -> None:
    installed_jar(machine)
    staged = program(machine.zamane.staging / f"{'d' * 64}.jar", b"PK\x03\x04 uploaded")
    with pytest.raises(TsaExecutableError) as caught:
        provider_of(zamane_config(machine, java=("java",), jar=staged))
    assert caught.value.refusal.what == "tsa.zamane_jar"


def test_the_seal_task_reports_a_refused_program_as_its_own_cause(
    machine: Machine, capsys: pytest.CaptureFixture[str]
) -> None:
    jar = program(machine.outside / "Zamane" / "tss-client-console-3.1.33.jar")
    config_path = zamane_config(machine, java=("java",), jar=jar)
    assert cli_main(["--config", str(config_path), "seal"]) == 1
    assert "tsa.zamane_jar" in capsys.readouterr().err
    (alert,) = read_alerts(Layout(machine.outside / "data").alerts)
    assert alert["rule"] == "R20-seal-config"
    assert alert["details"]["cause"] == "executable"
    assert "güvenilir bir konumda değil" in alert["message"]
    assert "is not in a trusted location" in alert["message"]
    assert cli_main(["--config", str(config_path), "credit"]) == 1  # the other tasks refuse too
    assert "tsa.zamane_jar" in capsys.readouterr().err
