"""Executable policy at run time (ADR-0014; contract 2.8.4)."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from arcilogger_core.admin.contract import (
    AdminLayout,
    AdminPolicy,
    ApprovedJar,
    JarsFile,
    TrustedExecutable,
    dump_document,
)
from arcilogger_core.config import Config
from arcilogger_core.execpolicy import (
    ExecutableRefusedError,
    ZamanePaths,
    check_executable,
    check_java,
    check_zamane_jar,
)
from arcilogger_core.timeutil import SITE_TZ, utc_now
from arcilogger_core.tsa import zamane_releases
from arcilogger_core.tsa.zamane_releases import KNOWN_RELEASES, ZamaneRelease, find_release


@dataclass
class Machine:
    """A program folder (``executable_roots``), a folder outside it, a ProgramData folder."""

    roots: Path
    outside: Path
    config: Config

    def policy(self, **changes: Any) -> AdminPolicy:
        fields: dict[str, Any] = {
            "enabled": True,
            "executable_roots": (str(self.roots),),
            "written_at": utc_now().astimezone(SITE_TZ),
            "written_by": "installer",
        }
        fields.update(changes)
        return AdminPolicy(**fields)

    @property
    def zamane(self) -> ZamanePaths:
        return ZamanePaths.of(self.config)


def program(path: Path, content: bytes = b"#!program\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
    roots = tmp_path / "Program Files"
    outside = tmp_path / "Tools"
    config = Config(data_dir=tmp_path / "data", secrets_dir=tmp_path / "ProgramData")
    return Machine(roots=roots, outside=outside, config=config)


def refusal(call: Any, *args: Any, **kwargs: Any) -> str:
    with pytest.raises(ExecutableRefusedError) as caught:
        call(*args, **kwargs)
    assert caught.value.code == "executable_refused"
    return caught.value.reason


# -- tsa.java -------------------------------------------------------------------------------------


@pytest.mark.parametrize("java", [("java",), ("java.exe",), ("JAVA.EXE",)])
def test_the_default_java_from_the_system_path_is_accepted(machine: Machine, java: Any) -> None:
    check_java(java, machine.policy())


@pytest.mark.parametrize(
    "java",
    [("cmd",), ("powershell",), ("cmd.exe",), ("bin/java",), ("..\\java.exe",), ("",), ()],
)
def test_other_bare_names_and_relative_paths_are_refused(machine: Machine, java: Any) -> None:
    refusal(check_java, java, machine.policy())


def test_java_under_a_trusted_root_is_accepted(machine: Machine) -> None:
    java = program(machine.roots / "Eclipse Adoptium" / "jre-8.0.432.6-hotspot" / "bin" / "java")
    check_java((str(java),), machine.policy())


def test_java_outside_the_roots_needs_a_trusted_hash(machine: Machine) -> None:
    java = program(machine.outside / "java" / "bin" / "java.exe")
    assert "trusted SHA-256" in refusal(check_java, (str(java),), machine.policy())
    trusted = TrustedExecutable(path=str(java), sha256=sha(java))
    check_java((str(java),), machine.policy(trusted_executables=(trusted,)))
    java.write_bytes(b"#!changed since the installer recorded it\n")
    refusal(check_java, (str(java),), machine.policy(trusted_executables=(trusted,)))


def test_a_second_java_element_is_refused_unless_the_whole_command_was_recorded(
    machine: Machine,
) -> None:
    java = program(machine.roots / "Java" / "bin" / "java.exe")
    for extra in ("-XX:OnError=cmd /c calc", "-Xmx256m", "-jar"):
        assert "extra arguments" in refusal(check_java, (str(java), extra), machine.policy())
    recorded = TrustedExecutable(path=str(java), sha256=sha(java), argv=(str(java), "-Xmx256m"))
    policy = machine.policy(trusted_executables=(recorded,))
    check_java((str(java), "-Xmx256m"), policy)
    refusal(check_java, (str(java), "-Xmx512m"), policy)
    refusal(check_java, (str(java), "-Xmx256m", "-XX:OnError=calc"), policy)


def test_a_recorded_command_still_needs_its_program_trusted(machine: Machine) -> None:
    java = program(machine.outside / "bin" / "java.exe")
    recorded = TrustedExecutable(path=str(java), sha256="0" * 64, argv=(str(java), "-Xmx256m"))
    refusal(check_java, (str(java), "-Xmx256m"), machine.policy(trusted_executables=(recorded,)))


def test_another_program_under_a_trusted_root_is_not_java(machine: Machine) -> None:
    other = program(machine.roots / "Something" / "cmd.exe")
    assert "is not one of" in refusal(check_java, (str(other),), machine.policy())


def test_a_link_out_of_the_trusted_root_is_refused(machine: Machine) -> None:
    """A junction or link inside Program Files that leads elsewhere counts as elsewhere."""
    target = program(machine.outside / "evil" / "java.exe")
    inside = machine.roots / "Java" / "bin"
    inside.mkdir(parents=True)
    refusal(check_java, (str(link(inside / "java.exe", target)),), machine.policy())
    folder = link(machine.roots / "Linked", target.parent)
    refusal(check_java, (str(folder / "java.exe"),), machine.policy())


def test_a_missing_or_relative_java_is_refused(machine: Machine) -> None:
    assert "does not exist" in refusal(
        check_java, (str(machine.roots / "Java" / "bin" / "java.exe"),), machine.policy()
    )
    refusal(check_java, ("Program Files/Java/bin/java.exe",), machine.policy())
    folder = machine.roots / "Java" / "java.exe"
    folder.mkdir(parents=True)
    assert "not a file" in refusal(check_java, (str(folder),), machine.policy())


# -- backup tools ---------------------------------------------------------------------------------


def test_backup_tools_need_an_absolute_trusted_path(machine: Machine) -> None:
    pg_dump = program(machine.roots / "PostgreSQL" / "16" / "bin" / "pg_dump.exe")
    check_executable(str(pg_dump), machine.policy(), what="backup.pg_dump")
    refusal(check_executable, "pg_dump", machine.policy(), what="backup.pg_dump")
    elsewhere = program(machine.outside / "pg_dump.exe")
    refusal(check_executable, str(elsewhere), machine.policy())
    trusted = TrustedExecutable(path=str(elsewhere), sha256=sha(elsewhere))
    check_executable(str(elsewhere), machine.policy(trusted_executables=(trusted,)))


def test_the_refusal_names_the_setting(machine: Machine) -> None:
    with pytest.raises(ExecutableRefusedError, match=re.escape("backup.pg_restore: ")) as caught:
        check_executable("pg_restore", machine.policy(), what="backup.pg_restore")
    assert caught.value.what == "backup.pg_restore"


# -- the Zamane client ----------------------------------------------------------------------------


def installed_jar(machine: Machine, content: bytes = b"PK\x03\x04 zamane client") -> Path:
    """A jar copied into zamane\\ and recorded by the installer (``-ZamaneJar``)."""
    jar = program(machine.zamane.root / "tss-client-console-3.1.33.jar", content)
    record = f"{sha(jar)}  {jar.name}\n# confirmed 2026-10-25T07:00:00Z by Administrator\n"
    machine.zamane.installer_record.write_text(record, encoding="utf-8")
    return jar


def test_the_installer_recorded_jar_is_accepted(machine: Machine) -> None:
    jar = installed_jar(machine)
    assert check_zamane_jar(jar, machine.config, machine.policy()) == sha(jar)


def test_an_unrecorded_jar_is_refused(machine: Machine) -> None:
    jar = installed_jar(machine)
    other = program(machine.zamane.root / "other.jar", b"PK\x03\x04 another client")
    assert check_zamane_jar(jar, machine.config, machine.policy())
    assert "not known" in refusal(check_zamane_jar, other, machine.config, machine.policy())
    jar.write_bytes(b"PK\x03\x04 replaced after the installation")
    refusal(check_zamane_jar, jar, machine.config, machine.policy())


def test_a_jar_outside_the_zamane_folder_is_refused_even_when_trusted(machine: Machine) -> None:
    jar = program(machine.outside / "tss-client-console-3.1.33.jar")
    trusted = TrustedExecutable(path=str(jar), sha256=sha(jar))
    assert "not an installed client" in refusal(
        check_zamane_jar, jar, machine.config, machine.policy(trusted_executables=(trusted,))
    )


def test_a_staged_upload_never_runs(machine: Machine, monkeypatch: pytest.MonkeyPatch) -> None:
    staged = program(machine.zamane.staging / f"{'a' * 64}.jar", b"PK\x03\x04 uploaded")
    monkeypatch.setattr(zamane_releases, "KNOWN_RELEASES", {sha(staged): ZamaneRelease("3.1.33")})
    record = f"{sha(staged)}  {staged.name}\n"
    machine.zamane.installer_record.write_text(record, encoding="utf-8")
    refusal(check_zamane_jar, staged, machine.config, machine.policy())
    # Nor through a link inside zamane\ that points into the staging folder.
    client = link(machine.zamane.root / "client.jar", staged)
    refusal(check_zamane_jar, client, machine.config, machine.policy())


def test_a_jar_approved_by_applysystem_is_accepted(machine: Machine) -> None:
    jar = program(machine.zamane.approved / "c3.jar", b"PK\x03\x04 promoted client")
    refusal(check_zamane_jar, jar, machine.config, machine.policy())
    approved = ApprovedJar(
        sha256=sha(jar), path=str(jar), approved_at=utc_now(), approved_by="hold"
    )
    jars = AdminLayout(machine.config.admin_dir).jars
    jars.parent.mkdir(parents=True)
    jars.write_bytes(dump_document(JarsFile(approved=(approved,))))
    assert check_zamane_jar(jar, machine.config, machine.policy()) == sha(jar)
    jars.write_bytes(b'{"schema": "arcilogger.admin-jars/1", "approved": [}')
    refusal(check_zamane_jar, jar, machine.config, machine.policy())


def test_a_known_release_is_accepted(machine: Machine, monkeypatch: pytest.MonkeyPatch) -> None:
    jar = program(machine.zamane.root / "tss-client-console-3.1.33.jar", b"PK\x03\x04 known")
    refusal(check_zamane_jar, jar, machine.config, machine.policy())
    monkeypatch.setattr(zamane_releases, "KNOWN_RELEASES", {sha(jar): ZamaneRelease("3.1.33")})
    assert check_zamane_jar(jar, machine.config, machine.policy()) == sha(jar)


def test_a_policy_trusted_jar_is_accepted(machine: Machine) -> None:
    jar = program(machine.zamane.root / "tss-client-console-3.1.33.jar", b"PK\x03\x04 recorded")
    trusted = TrustedExecutable(path=str(jar), sha256=sha(jar))
    assert check_zamane_jar(jar, machine.config, machine.policy(trusted_executables=(trusted,)))


def test_a_missing_jar_is_refused(machine: Machine) -> None:
    missing = machine.zamane.root / "tss-client-console-3.1.33.jar"
    assert "does not exist" in refusal(check_zamane_jar, missing, machine.config, machine.policy())


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
