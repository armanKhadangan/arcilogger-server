"""Encrypted secret store."""

from __future__ import annotations

from pathlib import Path

import pytest

from arcilogger_core.secretstore import KAMUSM_PASSWORD, SecretStore, SecretStoreError


def _store(tmp_path: Path) -> SecretStore:
    SecretStore.create_key(tmp_path / "secret.key")
    return SecretStore(tmp_path / "secret.key", tmp_path / "secrets.json")


def test_round_trip_and_ciphertext_only_on_disk(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.set(KAMUSM_PASSWORD, "çok-gizli-parola")
    assert store.get(KAMUSM_PASSWORD) == "çok-gizli-parola"
    assert "gizli" not in (tmp_path / "secrets.json").read_text(encoding="utf-8")
    assert store.names() == [KAMUSM_PASSWORD]
    assert store.get("missing") is None


def test_delete(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.set("a", "1")
    assert store.delete("a") is True
    assert store.delete("a") is False
    assert store.names() == []


def test_key_is_never_overwritten(tmp_path: Path) -> None:
    _store(tmp_path)
    with pytest.raises(FileExistsError):
        SecretStore.create_key(tmp_path / "secret.key")


def test_wrong_or_missing_key_is_reported(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.set("a", "1")
    other = tmp_path / "other"
    other.mkdir()
    SecretStore.create_key(other / "secret.key")
    with pytest.raises(SecretStoreError, match="wrong key"):
        SecretStore(other / "secret.key", tmp_path / "secrets.json").get("a")
    with pytest.raises(SecretStoreError, match="not found"):
        SecretStore(tmp_path / "nope.key", tmp_path / "secrets.json").set("a", "1")


def test_empty_secret_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="empty"):
        _store(tmp_path).set("a", "")
