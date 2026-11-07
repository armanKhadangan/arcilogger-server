"""The Windows icons of ``deploy/windows/desktop/icons`` (Wave 5 addendum C, C1-C2), written by
``tools/icons/build-icons.mjs``: one for the program (Setup, its uninstaller, the shortcuts) and
one per level of the status window's notification-area icon."""

from __future__ import annotations

import re
import struct
from pathlib import Path

import pytest

from arcilogger_core.desktop.status import COLOURS

ROOT = Path(__file__).parents[1]
ICONS = ROOT / "deploy" / "windows" / "desktop" / "icons"
SCRIPT = ROOT / "tools" / "icons" / "build-icons.mjs"
PNG = b"\x89PNG\r\n\x1a\n"


def _images(path: Path) -> list[tuple[int, bytes]]:
    """(size, image bytes) of every image of an .ico, after checking its directory."""
    data = path.read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    assert (reserved, kind) == (0, 1), f"{path.name} is not an icon file"
    images = []
    for index in range(count):
        width, height, colours, _, planes, bits, length, offset = struct.unpack_from(
            "<BBBBHHII", data, 6 + 16 * index
        )
        assert width == height, path.name
        assert (colours, planes, bits) == (0, 1, 32), path.name
        assert offset + length <= len(data), f"{path.name}: image {index} beyond the file"
        images.append((width or 256, data[offset : offset + length]))
    return images


def _check_bmp(name: str, size: int, image: bytes) -> None:
    header, width, height, planes, bits = struct.unpack_from("<IiiHH", image, 0)
    assert (header, width, height, planes, bits) == (40, size, 2 * size, 1, 32), name
    mask_row = (size + 31) // 32 * 4
    assert len(image) == 40 + size * size * 4 + mask_row * size, name
    alphas = image[40 + 3 : 40 + size * size * 4 : 4]
    # Transparent corners (rounded square), opaque inside: the alpha channel is used.
    assert 0 in alphas, f"{name}: {size} px has no transparent pixel"
    assert 255 in alphas, f"{name}: {size} px has no opaque pixel"


def test_program_icon() -> None:
    images = _images(ICONS / "arcilogger.ico")
    assert [size for size, _ in images] == [16, 20, 24, 32, 40, 48, 64, 256]
    for size, image in images:
        if size == 256:
            assert image.startswith(PNG), "256 px is a PNG, as Explorer expects"
        else:
            _check_bmp("arcilogger.ico", size, image)


@pytest.mark.parametrize("level", sorted(COLOURS))
def test_tray_icons(level: str) -> None:
    images = _images(ICONS / f"tray-{level}.ico")
    assert [size for size, _ in images] == [16, 20, 24, 32, 40, 48, 64]
    for size, image in images:
        _check_bmp(f"tray-{level}.ico", size, image)


def test_only_these_icons() -> None:
    expected = {"arcilogger.ico"} | {f"tray-{level}.ico" for level in COLOURS}
    assert {p.name for p in ICONS.iterdir()} == expected


def test_script_draws_the_status_window_colours() -> None:
    """The tray icons are drawn in the window's own colours: change both together."""
    script = SCRIPT.read_text(encoding="utf-8")
    levels = re.search(r"const LEVELS = \{([^}]*)\}", script)
    assert levels is not None
    drawn = dict(re.findall(r"(\w+): '(#[0-9a-fA-F]{6})'", levels.group(1)))
    assert drawn == dict(COLOURS)
