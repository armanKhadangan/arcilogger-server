"""Encrypted secret store."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from arcilogger_core.secretstore import (
    KAMUSM_CUSTOMER,
    KAMUSM_PASSWORD,
    REPLICA_PASSWORD,
    SMTP_PASSWORD,
    SecretStore,
    SecretStoreError,
    SecretValueError,
    check_secret_value,
)


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


# -- tokens of the browser administration (ADR-0014) -----------------------------------------------


def test_a_token_is_a_value_of_the_store_and_is_checked_once(tmp_path: Path) -> None:
    store = _store(tmp_path)
    token = store.encrypt_value("smtp-parola-1")
    assert "smtp-parola-1" not in token
    assert token.startswith("gAAAAA")
    store.set_encrypted(SMTP_PASSWORD, token)
    assert store.get(SMTP_PASSWORD) == "smtp-parola-1"
    assert json.loads((tmp_path / "secrets.json").read_text(encoding="utf-8")) == {
        SMTP_PASSWORD: token
    }


@pytest.mark.parametrize(
    ("name", "value"),
    [
        (KAMUSM_PASSWORD, "x" * 129),
        (KAMUSM_CUSTOMER, "12 34"),
        (SMTP_PASSWORD, "satır\nsonu"),
        (SMTP_PASSWORD, "sıfır​genişlik"),
        (REPLICA_PASSWORD, "x" * 257),
    ],
)
def test_a_token_whose_value_breaks_a_rule_is_refused(
    tmp_path: Path, name: str, value: str
) -> None:
    store = _store(tmp_path)
    with pytest.raises(SecretValueError) as caught:
        store.set_encrypted(name, store.encrypt_value(value))
    assert caught.value.code == "secret_invalid"
    assert value not in str(caught.value)
    assert store.names() == []


def test_a_token_of_another_key_is_refused(tmp_path: Path) -> None:
    store = _store(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    foreign = _store(other).encrypt_value("parola")
    with pytest.raises(SecretStoreError, match="does not decrypt"):
        store.render_with({SMTP_PASSWORD: foreign})


def test_render_with_keeps_the_other_ciphertexts_and_writes_nothing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.set(KAMUSM_CUSTOMER, "123456")
    store.set(KAMUSM_PASSWORD, "kamu-parola")
    before = (tmp_path / "secrets.json").read_bytes()
    kept = json.loads(before)[KAMUSM_CUSTOMER]
    token = store.encrypt_value("yeni-parola")
    rendered = store.render_with({KAMUSM_PASSWORD: token, SMTP_PASSWORD: None})
    assert (tmp_path / "secrets.json").read_bytes() == before
    assert json.loads(rendered) == {KAMUSM_CUSTOMER: kept, KAMUSM_PASSWORD: token}
    assert rendered.endswith(b"\n")
    deleted = store.render_with({KAMUSM_CUSTOMER: None})
    assert json.loads(deleted) == {KAMUSM_PASSWORD: json.loads(before)[KAMUSM_PASSWORD]}
    assert store.render_with({}) == before


def test_secret_value_limits() -> None:
    check_secret_value(KAMUSM_CUSTOMER, "1" * 64)
    check_secret_value(SMTP_PASSWORD, "boşluklu parola ğüşiöç")
    for name, value in ((KAMUSM_CUSTOMER, "1" * 65), (KAMUSM_PASSWORD, ""), ("x.y", "a\x00b")):
        with pytest.raises(SecretValueError):
            check_secret_value(name, value)


def test_readable_never_raises(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.set(SMTP_PASSWORD, "parola")
    assert store.readable(SMTP_PASSWORD)
    assert not store.readable(REPLICA_PASSWORD)
    other = tmp_path / "other"
    other.mkdir()
    SecretStore.create_key(other / "secret.key")
    assert not SecretStore(other / "secret.key", tmp_path / "secrets.json").readable(SMTP_PASSWORD)
