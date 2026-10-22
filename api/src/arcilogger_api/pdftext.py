"""PDF text extraction for tests, under its old import path (moved to
:mod:`arcilogger_worker.pdf.text`, which the usage report tests of the worker use too)."""

from __future__ import annotations

from arcilogger_worker.pdf.text import normalized, pdf_text

__all__ = ["normalized", "pdf_text"]
