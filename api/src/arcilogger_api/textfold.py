"""Case-insensitive search for Turkish text, the same in Python and in SQL.

``fold`` maps ASCII capitals to lower case and the Turkish letters ``İ I ı`` → ``i``, ``Ş`` →
``ş``, ``Ğ`` → ``ğ``, ``Ü`` → ``ü``, ``Ö`` → ``ö``, ``Ç`` → ``ç`` (and ``Â Î Û``), with one fixed
character table: PostgreSQL ``translate()`` applies the same table (:func:`folded`), so the result
does not depend on the database's locale (``lower('I')`` is ``ı`` in a Turkish locale and ``i``
elsewhere; ``lower('İ')`` does nothing in the C locale). "ışık", "IŞIK" and "Işık" all fold to
"işik"; a search for any of them finds the others.
"""

from __future__ import annotations

from typing import Any, Final

from sqlalchemy import ColumnElement, SQLColumnExpression, func

_FROM: Final = "ABCDEFGHIJKLMNOPQRSTUVWXYZİıŞĞÜÖÇÂÎÛ"
_TO: Final = "abcdefghijklmnopqrstuvwxyziişğüöçâîû"
_TABLE: Final = str.maketrans(_FROM, _TO)


def fold(text: str) -> str:
    """``text`` folded for comparison (see the module text)."""
    return text.translate(_TABLE)


def folded(expression: SQLColumnExpression[Any]) -> ColumnElement[str]:
    """The SQL twin of :func:`fold` for a text expression."""
    return func.translate(expression, _FROM, _TO)
