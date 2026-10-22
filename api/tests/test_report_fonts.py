"""Bundled report fonts (DejaVu): Turkish coverage, recorded source checksums, licence, and the
text extraction the report tests rely on (ToUnicode maps of the embedded subsets)."""

from __future__ import annotations

import io
import re

import pytest
from reportlab.pdfgen.canvas import Canvas  # type: ignore[import-untyped]

from arcilogger_api.pdftext import normalized, pdf_text
from arcilogger_api.reports.fonts import (
    FILES,
    FONT_DIR,
    MONO,
    SANS,
    SANS_BOLD,
    TURKISH,
    font_fingerprints,
    missing_glyphs,
    register_fonts,
)

#: Every letter of the Turkish alphabet, upper and lower case (plus the circumflex vowels).
ALPHABET = "abcçdefgğhıijklmnoöprsştuüvyzABCÇDEFGĞHIİJKLMNOÖPRSŞTUÜVYZâÂîÎûÛ"
SAMPLE = "Iğdır'da ılık şubat; İSTANBUL ÇİĞDEM ÖĞÜŞ — çöğüşıİ 0123456789 #012 \\x9f"


@pytest.mark.parametrize("font", [SANS, SANS_BOLD, MONO])
def test_every_turkish_letter_has_a_glyph(font: str) -> None:
    assert set(TURKISH) <= set(ALPHABET)
    assert missing_glyphs(font, ALPHABET) == []
    assert missing_glyphs(font, "—·→±“”") == []  # punctuation the report uses


def test_a_missing_glyph_is_detected() -> None:
    assert missing_glyphs(SANS, "a中") == ["中"]  # no CJK in DejaVu Sans


def test_font_files_match_the_recorded_source() -> None:
    readme = (FONT_DIR / "README.md").read_text(encoding="utf-8")
    recorded = dict(re.findall(r"\| `([\w.-]+\.ttf)` \| [^|]+ \| `([0-9a-f]{64})` \|", readme))
    assert recorded == font_fingerprints()
    assert set(recorded) == set(FILES.values())
    assert "dejavu-fonts-ttf` 2.37.3" in readme
    licence = (FONT_DIR / "LICENSE-DejaVu.txt").read_text(encoding="utf-8")
    assert "Bitstream Vera Fonts Copyright" in licence
    assert "DejaVu changes are in public domain" in licence


def test_embedded_text_extracts_with_turkish_letters_intact() -> None:
    register_fonts()
    buffer = io.BytesIO()
    canvas = Canvas(buffer, invariant=1, initialFontName=SANS)
    for index, (font, text) in enumerate(
        [(SANS, ALPHABET), (SANS_BOLD, SAMPLE), (MONO, SAMPLE), (SANS, "(parantez) \\ ters")]
    ):
        canvas.setFont(font, 10)
        canvas.drawString(40, 800 - 20 * index, text)
    canvas.showPage()
    canvas.save()
    pdf = buffer.getvalue()
    assert b"/ToUnicode" in pdf
    assert b"Helvetica" not in pdf  # no unembedded standard font (it has no "ı", "ş", "ğ")
    lines = pdf_text(pdf).splitlines()
    assert lines == [ALPHABET, SAMPLE, SAMPLE, "(parantez) \\ ters"]
    assert normalized(" a \n b  ") == "a b"


def test_text_extraction_of_arrays_ranges_and_escapes() -> None:
    """The parts of the PDF syntax reportlab may use besides ``(…) Tj`` (hand-made file)."""
    cmap = (
        b"begincmap\n1 beginbfchar\n<41> <0130>\nendbfchar\n"
        b"1 beginbfrange\n<61> <63> <015F>\nendbfrange\nendcmap"
    )
    content = (
        b"BT /F9 12 Tf [(A) -120 (abc)] TJ T* (\\101\\\n\\(\\)\\n\\q) Tj ET\nBT /F9 12 Tf (b) Tj ET"
    )
    pdf = b"\n".join(
        [
            b"%PDF-1.4",
            b"1 0 obj << /Type /Page /Contents [ 3 0 R ] /Resources << /Font << /F9 2 0 R >> >> >>"
            b" endobj",
            b"2 0 obj << /Type /Font /ToUnicode 4 0 R >> endobj",
            b"3 0 obj << /Length %d >> stream\n" % len(content) + content + b"\nendstream endobj",
            b"4 0 obj << /Length %d >> stream\n" % len(cmap) + cmap + b"\nendstream endobj",
            b"%%EOF",
        ]
    )
    # bfchar A → İ; bfrange a-c → ş Š š; octal \101 = A; "\" + line feed continues the string
    assert pdf_text(pdf) == "İşŠš\nİ()\nq\nŠ"
