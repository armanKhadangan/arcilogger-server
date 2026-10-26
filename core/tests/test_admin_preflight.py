"""Whether the Seal task could start with a configuration (``tsa.preflight``; ADR-0014, contract
2.4.7 ``seal_ready`` and 2.6 ``tsa_would_break`` / ``tsa_not_ready``)."""

from __future__ import annotations

import hashlib
import io
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import NameOID

from arcilogger_core import seal
from arcilogger_core.admin.contract import FALLBACK_POLICY
from arcilogger_core.config import Config, parse_config
from arcilogger_core.execpolicy import TrustAnchors
from arcilogger_core.secretstore import KAMUSM_CUSTOMER, KAMUSM_PASSWORD, SecretStore
from arcilogger_core.tsa.preflight import (
    EXECUTABLE_CAUSE,
    executables_trusted,
    java_present,
    production_missing,
    roots_present,
    seal_preflight,
)

CREDENTIALS = frozenset({KAMUSM_CUSTOMER, KAMUSM_PASSWORD})


def root_pem() -> bytes:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Örnek Test Kök")])
    now = datetime.now(UTC)
    return (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
        .sign(key, hashes.SHA256())
        .public_bytes(Encoding.PEM)
    )


class Site:
    """An installation folder with a Java, a client and a root that tests switch on."""

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "ProgramData"
        self.root.mkdir()
        self.programs = tmp_path / "Program Files"
        self.java = self.programs / "Java" / "jre1.8.0_412" / "bin" / "java"
        self.java.parent.mkdir(parents=True)
        self.java.write_bytes(b"#!/bin/sh\n")
        self.jar = self.root / "zamane" / "tss-client-console-3.1.33.jar"
        self.jar.parent.mkdir()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n")
        self.jar.write_bytes(buffer.getvalue())
        self.roots = self.root / "kamusm-roots"
        self.roots.mkdir()
        self.policy = FALLBACK_POLICY.model_copy(update={"executable_roots": (str(self.programs),)})
        SecretStore.create_key(self.root / "secret.key")
        (self.root / "secrets.json").write_text("{}\n", encoding="utf-8")

    @property
    def store(self) -> SecretStore:
        return SecretStore(self.root / "secret.key", self.root / "secrets.json")

    @property
    def anchors(self) -> TrustAnchors:
        return TrustAnchors(self.root, self.policy)

    def record_jar(self) -> None:
        digest = hashlib.sha256(self.jar.read_bytes()).hexdigest()
        (self.jar.parent / "zamane-jar.sha256").write_text(
            f"{digest}  {self.jar.name}\n", encoding="utf-8"
        )

    def add_root(self) -> None:
        (self.roots / "kok.pem").write_bytes(root_pem())

    def config(self, tsa: str) -> Config:
        text = f'data_dir = "{(self.root / "data").as_posix()}"\n[tsa]\n{tsa}\n'
        return parse_config(text.encode("utf-8"), self.root / "arcilogger.toml")

    def zamane(self, *, jar: bool = True) -> Config:
        lines = ['provider = "zamane"', f'java = ["{self.java.as_posix()}"]',
                 f'roots_dir = "{self.roots.as_posix()}"']  # fmt: skip
        if jar:
            lines.append(f'zamane_jar = "{self.jar.as_posix()}"')
        return self.config("\n".join(lines))

    def preflight(self, config: Config, stored: frozenset[str] | None = None) -> list[str]:
        return seal_preflight(config, anchors=self.anchors, secrets=self.store, stored=stored)


@pytest.fixture
def site(tmp_path: Path) -> Site:
    return Site(tmp_path)


def test_the_mock_provider_needs_nothing(site: Site) -> None:
    assert site.preflight(site.config('provider = "mock"')) == []


def test_an_rfc3161_server_needs_a_pinned_root(site: Site) -> None:
    config = site.config(f'provider = "rfc3161"\nroots_dir = "{site.roots.as_posix()}"')
    assert site.preflight(config) == ["roots"]
    site.add_root()
    assert site.preflight(config) == []
    assert site.preflight(site.config('provider = "rfc3161"')) == ["roots"]  # no roots_dir


def test_the_causes_come_in_the_order_of_the_seal_task(site: Site) -> None:
    assert site.preflight(site.zamane(jar=False)) == ["zamane-jar", "roots"]
    assert site.preflight(site.zamane()) == ["credentials", "roots"]
    site.store.set(KAMUSM_CUSTOMER, "4812")
    site.store.set(KAMUSM_PASSWORD, "örnek-parola")
    assert site.preflight(site.zamane()) == [EXECUTABLE_CAUSE, "roots"]  # the jar is unknown
    site.record_jar()
    assert site.preflight(site.zamane()) == ["roots"]
    site.add_root()
    assert site.preflight(site.zamane()) == []
    assert set(seal.CONFIG_CAUSES) | {EXECUTABLE_CAUSE} >= {
        "zamane-jar",
        "credentials",
        "roots",
        "secret-store",
    }


def test_a_store_the_key_does_not_open(site: Site) -> None:
    site.record_jar()
    site.add_root()
    site.store.set(KAMUSM_CUSTOMER, "4812")
    site.store.set(KAMUSM_PASSWORD, "örnek-parola")
    (site.root / "secret.key").unlink()
    SecretStore.create_key(site.root / "secret.key")  # another key: the names stay readable
    assert site.preflight(site.zamane()) == ["secret-store"]
    (site.root / "secrets.json").write_text("{not json", encoding="utf-8")
    assert site.preflight(site.zamane()) == ["secret-store"]


def test_a_candidate_is_checked_with_the_secret_names_it_would_have(site: Site) -> None:
    site.record_jar()
    site.add_root()
    (site.root / "secrets.json").write_text("{not json", encoding="utf-8")
    assert site.preflight(site.zamane(), stored=CREDENTIALS) == []  # the store is not read
    assert site.preflight(site.zamane(), stored=frozenset({KAMUSM_CUSTOMER})) == ["credentials"]


def test_java_and_the_client_must_be_trusted(site: Site, monkeypatch: pytest.MonkeyPatch) -> None:
    config = site.zamane()
    assert not executables_trusted(config, site.anchors)
    site.record_jar()
    assert executables_trusted(config, site.anchors)
    assert java_present(config, site.anchors)
    untrusted = TrustAnchors(site.root, FALLBACK_POLICY)  # Java outside C:\Program Files
    assert not executables_trusted(config, untrusted)
    assert not java_present(config, untrusted)
    monkeypatch.setenv("PATH", str(site.root / "nowhere"))
    bare = site.config('provider = "zamane"')  # java = ["java"], looked up in PATH
    assert not java_present(bare, site.anchors)
    assert not executables_trusted(bare, site.anchors)  # no client configured


def test_what_production_still_needs(site: Site, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(site.root / "nowhere"))
    missing = production_missing(
        site.config('provider = "zamane"'), anchors=site.anchors, stored=frozenset(), roots=0
    )
    assert missing == ["credentials", "zamane_jar", "java", "roots"]
    site.record_jar()
    assert (
        production_missing(site.zamane(), anchors=site.anchors, stored=CREDENTIALS, roots=1) == []
    )


def test_roots_present_reads_the_folder(site: Site) -> None:
    config = site.config(f'provider = "zamane"\nroots_dir = "{site.roots.as_posix()}"')
    assert not roots_present(config)
    site.add_root()
    assert roots_present(config)
    (site.roots / "bozuk.pem").write_bytes(b"not a certificate")
    assert not roots_present(config)  # the Seal task refuses such a folder too
