"""The root certificates the product ships (0.3.1): TÜRKTRUST's H7, from the bundle's ``roots\\``
into ``kamusm-roots\\`` on every install and upgrade. These checks need no PowerShell; the real
install on Windows (``test_installer_scripts.py``, end to end) checks the file lands."""

from __future__ import annotations

import hashlib
from pathlib import Path

from arcilogger_core.config import TURKTRUST_ROOT_FINGERPRINTS
from arcilogger_core.tsa import certparse

REPO = Path(__file__).parents[1]
ROOTS = REPO / "deploy" / "windows" / "roots"
INSTALLER = REPO / "deploy" / "windows" / "installer"
FIXTURE = REPO / "core" / "tests" / "data" / "turktrust" / "h7-root.crt"


def test_every_shipped_root_is_a_built_in_legal_root() -> None:
    files = sorted(ROOTS.glob("*.crt"))
    assert [f.name for f in files] == ["TURKTRUST_Elektronik_Sertifika_Hizmet_Saglayicisi_h7.crt"]
    for file in files:
        digest = hashlib.sha256(file.read_bytes()).hexdigest()
        assert digest in TURKTRUST_ROOT_FINGERPRINTS
        (certificate,) = certparse.load_certificates(file.read_bytes())
        assert certparse.fingerprint(certificate) == digest
        assert certparse.is_self_signed(certificate)


def test_the_shipped_root_is_the_one_the_tests_use() -> None:
    assert (ROOTS / "TURKTRUST_Elektronik_Sertifika_Hizmet_Saglayicisi_h7.crt").read_bytes() == (
        FIXTURE.read_bytes()
    )


def test_the_bundle_carries_the_roots_and_the_installer_installs_them() -> None:
    build = (INSTALLER / "Build-ArciBundle.ps1").read_text(encoding="utf-8-sig")
    assert "Copy-Tree (Join-Path $Windows 'roots') (Join-Path $stage 'roots') '*.crt'" in build
    install = (INSTALLER / "Install-Arcilogger.ps1").read_text(encoding="utf-8-sig")
    step = install.index("Invoke-Step 'roots.install'")
    assert (
        install.index("Invoke-Step 'secrets.file'") < step < install.index("Invoke-AdminSteps\n}")
    )
    body = install[install.index("function Install-BundledRoots") :]
    body = body[: body.index("\n}\n")]
    assert "Copy-Item" in body
    # Never overwritten or removed: no -Force, no Remove-Item.
    assert "-Force" not in body
    assert "Remove-Item" not in body
