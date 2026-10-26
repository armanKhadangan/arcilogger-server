"""Key files of the administration channel (ADR-0014; contract 2.4.2)."""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from arcilogger_core.admin import keys
from arcilogger_core.admin.contract import AdminLayout, KeysFile, b64url_decode, parse_document
from arcilogger_core.admin.keys import (
    KEY_FILES,
    KeysError,
    init_keys,
    key_file,
    keys_file,
    load_keys,
    load_signing_key,
)
from arcilogger_core.config import Config


def keys_dir(tmp_path: Path) -> Path:
    config = Config(data_dir=tmp_path / "data", secrets_dir=tmp_path)
    return AdminLayout(config.admin_dir).keys


def test_init_creates_both_keys_and_the_public_list(tmp_path: Path) -> None:
    folder = keys_dir(tmp_path)
    assert folder == tmp_path / "admin" / "keys"
    created = init_keys(folder)
    assert sorted(path.name for path in folder.iterdir()) == [
        "agent-ed25519.key",
        "api-ed25519.key",
        "keys.json",
    ]
    assert parse_document(KeysFile, keys_file(folder).read_bytes()) == created
    assert [key.role for key in created.keys] == ["api", "agent"]
    for role in ("api", "agent"):
        text = key_file(folder, role).read_text(encoding="ascii")
        assert text.endswith("\n")
        assert text.count("\n") == 1, "one line"
        assert len(base64.b64decode(text.strip(), validate=True)) == 32, "the raw private key"
        signing = load_signing_key(folder, role)
        public = signing.private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        entry = next(key for key in created.keys if key.role == role)
        assert b64url_decode(entry.public, 32) == public
        assert signing.key_id == entry.id == f"{role}-{hashlib.sha256(public).hexdigest()[:8]}"
    document = json.loads(keys_file(folder).read_text(encoding="utf-8"))
    assert document["schema"] == "arcilogger.admin-keys/1"
    assert set(document) == {"schema", "instance", "keys"}


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes (the installer sets ACLs)")
def test_private_keys_are_readable_by_their_owner_only(tmp_path: Path) -> None:
    folder = keys_dir(tmp_path)
    init_keys(folder)
    for name in KEY_FILES.values():
        assert (folder / name).stat().st_mode & 0o077 == 0


def test_init_is_idempotent_and_never_overwrites_a_key(tmp_path: Path) -> None:
    folder = keys_dir(tmp_path)
    first = init_keys(folder)
    before = {path.name: path.read_bytes() for path in folder.iterdir()}
    assert init_keys(folder) == first
    assert {path.name: path.read_bytes() for path in folder.iterdir()} == before


def test_a_lost_public_list_is_rebuilt_from_the_private_keys(tmp_path: Path) -> None:
    folder = keys_dir(tmp_path)
    first = init_keys(folder)
    keys_file(folder).unlink()
    again = init_keys(folder)
    assert again.keys == first.keys
    assert again.instance != first.instance, "a new installation id: pending requests expire"


def test_a_lost_private_key_is_replaced_and_listed_keeping_the_instance(tmp_path: Path) -> None:
    folder = keys_dir(tmp_path)
    first = init_keys(folder)
    api_before = key_file(folder, "api").read_bytes()
    key_file(folder, "agent").unlink()
    again = init_keys(folder)
    assert again.instance == first.instance
    assert again.keys[0] == first.keys[0]
    assert again.keys[1] != first.keys[1]
    assert key_file(folder, "api").read_bytes() == api_before
    assert load_signing_key(folder, "agent").key_id == again.keys[1].id


def test_an_invalid_public_list_is_not_replaced_silently(tmp_path: Path) -> None:
    folder = keys_dir(tmp_path)
    init_keys(folder)
    keys_file(folder).write_text('{"schema": "arcilogger.admin-keys/1"}', encoding="utf-8")
    with pytest.raises(KeysError, match="remove it"):
        init_keys(folder)
    with pytest.raises(KeysError, match="unreadable"):
        load_keys(folder)


def test_a_key_id_that_does_not_belong_to_its_key_is_refused(tmp_path: Path) -> None:
    folder = keys_dir(tmp_path)
    init_keys(folder)
    document = json.loads(keys_file(folder).read_text(encoding="utf-8"))
    document["keys"][0]["id"] = "api-00000000"
    keys_file(folder).write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(KeysError, match="does not belong"):
        load_keys(folder)


def test_a_signing_key_not_listed_is_refused(tmp_path: Path) -> None:
    """A private key keys.json does not list would sign requests the agent refuses as possible
    tampering: the API reports the broken installation instead (admin_channel_key_missing)."""
    folder = keys_dir(tmp_path)
    init_keys(folder)
    other = tmp_path / "other"
    init_keys(other)
    key_file(folder, "api").write_bytes(key_file(other, "api").read_bytes())
    with pytest.raises(KeysError, match="not listed"):
        load_signing_key(folder, "api")


@pytest.mark.parametrize("content", [b"", b"not base64\n", base64.b64encode(b"short") + b"\n"])
def test_a_damaged_private_key_is_reported(tmp_path: Path, content: bytes) -> None:
    folder = keys_dir(tmp_path)
    init_keys(folder)
    key_file(folder, "api").write_bytes(content)
    with pytest.raises(KeysError, match="unreadable"):
        load_signing_key(folder, "api")
    with pytest.raises(KeysError, match="unreadable"):
        init_keys(folder)


def test_missing_files_are_reported(tmp_path: Path) -> None:
    folder = keys_dir(tmp_path)
    with pytest.raises(KeysError, match="keys init"):
        load_keys(folder)
    with pytest.raises(KeysError, match="keys init"):
        load_signing_key(folder, "agent")


def test_a_key_file_is_created_exclusively(tmp_path: Path) -> None:
    """The creation itself refuses an existing file (O_EXCL), whatever the caller checked."""
    folder = keys_dir(tmp_path)
    init_keys(folder)
    path = key_file(folder, "api")
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        keys._create_private(path)
    assert path.read_bytes() == before
