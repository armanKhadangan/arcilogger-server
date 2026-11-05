"""Sealed files are read only through :mod:`arcilogger_ingest.files`.

:class:`~arcilogger_ingest.files.VaultReader` is the one reader of a sealed file's lines: it
opens the file with delete sharing (:func:`~arcilogger_ingest.files.open_shared`), so vault
retention on Windows is never blocked by a read, and it is the one place that knows the vault's
compression formats. A second reader elsewhere in ``ingest/src`` (its own ``gzip.GzipFile`` or
``lzma`` stream) would break the day the vault's format changes, and only for the files of that
format: the reload's ``received_outside`` did, until review of W5-G. So no other module of
``ingest/src`` uses a compression module at all (``gzip``, ``zlib``, ``lzma``, ``bz2``, under any
name). The raw bytes of a file (hashed against the manifest) are read through ``open_shared``
too, never a plain ``open(..., "rb")``.
"""

from __future__ import annotations

import ast
import gzip
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

import pytest

from arcilogger_core.rawlog import format_line
from arcilogger_core.timeutil import SITE_TZ
from arcilogger_ingest import service as service_module
from arcilogger_ingest.files import VaultReader
from arcilogger_ingest.service import HASH_CHUNK, received_outside
from arcilogger_ingest.testing import DAY

SRC = Path(__file__).resolve().parents[1] / "src"
#: The module that owns reading sealed files.
OWNER = SRC / "arcilogger_ingest" / "files.py"
#: The compression modules of the standard library: no module but :data:`OWNER` uses one in any
#: form (an import under any name, ``from … import``, ``importlib``), so no other reader can
#: decompress a sealed file, whatever it read the bytes with.
COMPRESSION = frozenset({"gzip", "zlib", "lzma", "bz2"})
#: The calls that take the mode as their second argument (``open(path, "rb")``).
SECOND_MODE = frozenset({("io", "open"), ("os", "fdopen")})


def _binary_open(node: ast.Call) -> bool:
    """Whether ``node`` opens a file for reading in binary mode without ``open_shared``:
    ``open(..., "rb")``, ``io.open(..., "rb")``, ``os.fdopen(..., "rb")`` or ``<path>.open("rb")``
    (a reading mode with ``b``, given by position or as ``mode=``). Writing (``"ab"``, ``"wb"``)
    is not reading a sealed file; an ``open`` of a compression module is counted as that
    module's use."""
    func = node.func
    if isinstance(func, ast.Attribute):
        owner = func.value.id if isinstance(func.value, ast.Name) else None
        if owner in COMPRESSION:
            return False
        if (owner, func.attr) in SECOND_MODE:
            modes = node.args[1:2]
        elif func.attr == "open":
            modes = node.args[:1]
        else:
            return False
    elif isinstance(func, ast.Name) and func.id == "open":
        modes = node.args[1:2]
    else:
        return False
    modes += [keyword.value for keyword in node.keywords if keyword.arg == "mode"]
    return any(
        isinstance(mode, ast.Constant)
        and isinstance(mode.value, str)
        and {"r", "b"} <= set(mode.value)
        for mode in modes
    )


def _dynamic_import(node: ast.Call) -> str | None:
    """The compression module ``node`` imports by name (``importlib.import_module("gzip")``,
    ``__import__("zlib")``), else ``None``."""
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    if name not in ("import_module", "__import__") or not node.args:
        return None
    first = node.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        module = first.value.split(".")[0]
        return module if module in COMPRESSION else None
    return None


def _spelling(node: ast.AST) -> str | None:
    """What ``node`` reaches that only :data:`OWNER` may (module text), else ``None``."""
    if isinstance(node, ast.Call):
        if _binary_open(node):
            return "binary open()"
        module = _dynamic_import(node)
        return None if module is None else f"import of {module}"
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        if node.value.id in COMPRESSION:
            return f"{node.value.id}.{node.attr}"
        return None
    if isinstance(node, ast.Import):
        names = [
            alias.name + (f" as {alias.asname}" if alias.asname else "")
            for alias in node.names
            if alias.name.split(".")[0] in COMPRESSION
        ]
        return f"import {', '.join(names)}" if names else None
    if (
        isinstance(node, ast.ImportFrom)
        and node.level == 0
        and node.module is not None
        and node.module.split(".")[0] in COMPRESSION
    ):
        return f"from {node.module} import {', '.join(a.name for a in node.names)}"
    return None


def _own_readers(path: Path, root: Path = SRC.parent) -> list[str]:
    """Where ``path`` uses a compression module (any import of ``gzip``, ``zlib``, ``lzma`` or
    ``bz2``, or an attribute of one) or opens a file for reading in binary mode without
    ``open_shared`` (``file:line: spelling``, in walk order). ``read_bytes()`` is not counted: the
    service reads its JSON state and manifests so, and without a compression module the bytes of
    a sealed file are no lines."""
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), str(path))):
        spelled = _spelling(node)
        if spelled is not None:
            found.append(f"{path.relative_to(root)}:{getattr(node, 'lineno', 0)}: {spelled}")
    return found


def test_sealed_files_are_read_only_through_the_vault_reader() -> None:
    found = [
        hit for path in sorted(SRC.rglob("*.py")) if path != OWNER for hit in _own_readers(path)
    ]
    assert found == [], (
        "Sealed files are read through arcilogger_ingest.files.VaultReader (open_shared, every "
        "vault format) and opened with open_shared, never with an own gzip/lzma reader or a "
        "plain binary open(): " + "; ".join(found)
    )


def test_the_guard_sees_what_it_forbids(tmp_path: Path) -> None:
    """The scan itself: every spelling it must catch is caught (aliases included), the shared
    reader, ``open_shared``, text opens and writes are not."""
    module = tmp_path / "reader.py"
    module.write_text(
        "import gzip\n"
        "import lzma\n"
        "import gzip as g\n"
        "import os, zlib\n"
        "import bz2 as b\n"
        "from gzip import GzipFile\n"
        "from gzip import decompress as unpack\n"
        "from lzma import LZMAFile\n"
        "import importlib\n"
        "from arcilogger_ingest.files import VaultReader, open_shared\n"
        "a = gzip.GzipFile(fileobj=raw, mode='rb')\n"
        "b = gzip.open(path)\n"
        "c = lzma.LZMAFile(path)\n"
        "d = zlib.decompressobj(31)\n"
        "e = importlib.import_module('gzip')\n"
        "f = __import__('zlib')\n"
        "with path.open('rb') as f: pass\n"
        "with open(path, mode='rb') as f: pass\n"
        "with io.open(path, 'rb') as f: pass\n"
        "with os.fdopen(descriptor, 'rb') as f: pass\n"
        "r = VaultReader(path, 0)\n"
        "with open_shared(path) as f: pass\n"
        "text = path.open(encoding='utf-8')\n"
        "with open(path, encoding='utf-8') as f: pass\n"
        "with path.open('ab') as f: pass\n"
        "with io.open(path, 'wb') as f: pass\n"
        "state = importlib.import_module('json')\n",
        encoding="utf-8",
    )
    found = sorted(_own_readers(module, tmp_path), key=lambda hit: int(hit.split(":")[1]))
    assert [hit.split(": ", 1)[1] for hit in found] == [
        "import gzip",
        "import lzma",
        "import gzip as g",
        "import zlib",
        "import bz2 as b",
        "from gzip import GzipFile",
        "from gzip import decompress",
        "from lzma import LZMAFile",
        "gzip.GzipFile",
        "gzip.open",
        "lzma.LZMAFile",
        "zlib.decompressobj",
        "import of gzip",
        "import of zlib",
        "binary open()",
        "binary open()",
        "binary open()",
        "binary open()",
    ]


def test_received_outside_reads_through_the_vault_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reload's scan of a sealed file (:func:`received_outside`) is a :class:`VaultReader`
    of the service, closed afterwards; a file of several reads gives every line once, also the
    one received on another day after the first read and the last one without ``\\n``."""
    in_day = datetime.combine(DAY, time(9), tzinfo=SITE_TZ)
    stray = datetime(2001, 1, 1, 9, 0, 30, tzinfo=SITE_TZ)
    filler = format_line(in_day, "172.20.10.1", b"<13>" + b"x" * 200)
    count = 2 * HASH_CHUNK // len(filler)
    lines = [filler] * count + [format_line(stray, "172.20.10.1", b"<13>2001"), filler]
    lines.append(format_line(in_day + timedelta(days=2), "172.20.10.1", b"<13>ahead")[:-1])
    path = tmp_path / "core-r1.log.gz"
    path.write_bytes(gzip.compress(b"".join(lines)))
    readers: list[Recording] = []

    class Recording(VaultReader):
        def __init__(self, path: Path, offset: int) -> None:
            super().__init__(path, offset)
            self.reads, self.closed = 0, False
            readers.append(self)

        def read(self, max_bytes: int) -> bytes:
            self.reads += 1
            return super().read(max_bytes)

        def close(self) -> None:
            self.closed = True
            super().close()

    monkeypatch.setattr(service_module, "VaultReader", Recording)
    assert received_outside(path, DAY) == [
        datetime(2001, 1, 1, 9, 0, tzinfo=SITE_TZ).astimezone(UTC),
        (in_day + timedelta(days=2)).astimezone(UTC),
    ]
    assert [(r.path, r.reads > 2, r.closed) for r in readers] == [(path, True, True)]
