"""Write the sealed xz day of ``tests/fixtures/vault-xz`` (Wave 5 W5-H).

A FortiGate day (the lines of ``tests/fixtures/format/fortigate-sample.log``, received every five
minutes on 2026-10-12) sealed by the product's own sealer with ``seal.compression = "xz"`` and
16 KiB streams, so the sealed file has many streams, and stamped by the mock TSA. The folder holds
the vault day as the sealer left it (``2026/10/12/``) and the mock TSA's root (``kokler/``).

Both verifiers, 7-Zip and ``xz -t`` read these bytes in ``tests/test_vault_xz_fixture.py``, and
``SHA256SUMS`` pins them: a reader that changes how it reads old days fails there. The mock TSA
makes a new key every run, so running this again gives other bytes; do it only on purpose and
commit the new ``SHA256SUMS`` with the reason.

Usage: ``uv run python tests/fixtures/vault-xz/make_fixture.py``.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from cryptography.hazmat.primitives.serialization import Encoding

from arcilogger_core import sealedfile
from arcilogger_core.config import Config
from arcilogger_core.immutability import is_locked, unlock
from arcilogger_core.layout import Layout
from arcilogger_core.rawlog import format_line
from arcilogger_core.seal import Sealer
from arcilogger_core.timeutil import SITE_TZ
from arcilogger_core.tsa.mock import MockTsa, MockTsaProvider

HERE = Path(__file__).resolve().parent
SAMPLE = HERE.parent / "format" / "fortigate-sample.log"
DAY = date(2026, 10, 12)
SENDER = "192.0.2.10"


def main() -> int:
    for old in (HERE / "2026", HERE / "kokler"):
        shutil.rmtree(old, ignore_errors=True)
    messages = SAMPLE.read_bytes().splitlines()
    now = datetime.combine(DAY + timedelta(days=1), time(0, 30), tzinfo=SITE_TZ).astimezone(UTC)
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        config = Config.model_validate(
            {
                "data_dir": str(root / "data"),
                "secrets_dir": str(root),
                "site_name": "Örnek A.Ş. Merkez",
                "sources": [{"name": "fortigate", "type": "fortigate", "address": SENDER}],
                "seal": {"quiet_seconds": 0, "compression": "xz", "first_day": DAY.isoformat()},
                "tsa": {"provider": "mock"},
            }
        )
        layout = Layout(config.data_dir)
        spool = layout.spool_file("fortigate", DAY)
        spool.parent.mkdir(parents=True)
        with spool.open("wb") as handle:
            for n in range(24 * 12):
                received = datetime.combine(DAY, time(n // 12, n % 12 * 5), tzinfo=SITE_TZ)
                handle.write(format_line(received, SENDER, messages[n % len(messages)]))
        tsa = MockTsa(layout.state / "mock-tsa", key_type="ec", clock=lambda: now)
        sealedfile.STREAM_BYTES = 16384
        sealer = Sealer(
            config,
            provider=MockTsaProvider(tsa),
            roots=[tsa.root_certificate],
            clock=lambda: now,
            clock_status=lambda: "fixture clock",
        )
        outcome = sealer.seal_day(DAY)
        if outcome.status != "sealed":
            raise SystemExit(f"sealing failed: {outcome.message}")
        target = HERE / f"{DAY:%Y}" / f"{DAY:%m}" / f"{DAY:%d}"
        target.mkdir(parents=True)
        for path in sorted(layout.vault_day(DAY).iterdir()):
            shutil.copyfile(path, target / path.name)
        for path in root.rglob("*"):  # the sealer locks what it seals
            if path.is_file() and is_locked(path):
                unlock(path)
        (HERE / "kokler").mkdir()
        (HERE / "kokler" / "mock-root.pem").write_bytes(
            tsa.root_certificate.public_bytes(Encoding.PEM)
        )
    names = sorted(
        path.relative_to(HERE).as_posix()
        for path in HERE.rglob("*")
        if path.is_file() and path.name not in {"SHA256SUMS", "make_fixture.py", "README.md"}
    )
    (HERE / "SHA256SUMS").write_text(
        "".join(
            f"{hashlib.sha256((HERE / name).read_bytes()).hexdigest()}  {name}\n" for name in names
        ),
        encoding="ascii",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
