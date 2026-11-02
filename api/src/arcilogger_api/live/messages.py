"""Every text of Canlı Akış, Turkish first, English beside it (contract 2.20).

:data:`MESSAGES` holds the error codes of the live endpoints with their HTTP status
(:data:`STATUS`); :data:`END_MESSAGES` the reasons an open stream ends with (``end`` event);
:data:`DROPPED_REASONS` why lines were not shown (``dropped`` event); :data:`GAP_MESSAGE` the text
of a ``gap`` event. Placeholders in braces have the same names in both languages (a test checks
it). An ``end`` for a session reason reuses the texts of the sign-in (``session_idle``,
``privacy_notice_required`` …), so the web app shows what every other page shows.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from arcilogger_api.auth.messages import AUTH_MESSAGES
from arcilogger_api.errors import MESSAGES as API_MESSAGES
from arcilogger_api.errors import ApiError

#: code → (Turkish, English).
MESSAGES: Final[Mapping[str, tuple[str, str]]] = {
    "live_stream_limit": (
        "Aynı anda en fazla {per_user} canlı akış açabilirsiniz (sunucuda toplam {total}); "
        "başka bir sekmedeki akışı kapatın.",
        "You can have at most {per_user} live tails open at a time (server total {total}); "
        "close the one in another tab.",
    ),
    "live_stream_not_found": (
        "Canlı akış bulunamadı (kapatılmış ya da sunucu yeniden başlamış olabilir).",
        "Live tail not found (it was closed or the server restarted).",
    ),
    "live_unavailable": (
        "Ham kayıt klasörü okunamıyor; canlı akış başlatılamadı. Sistem yöneticisine bildirin.",
        "The raw spool folder cannot be read; the live tail cannot start. Tell the administrator.",
    ),
}

#: code → HTTP status of the error.
STATUS: Final[Mapping[str, int]] = {
    "live_stream_limit": 429,
    "live_stream_not_found": 404,
    "live_unavailable": 503,
}

#: Why a stream ends (``end {code, message_tr, message_en}``); the session reasons below.
END_MESSAGES: Final[Mapping[str, tuple[str, str]]] = {
    "time_limit": (
        "Canlı akış {minutes} dakika sonra durduruldu (gözetimsiz ekran). Sürdürmek için "
        "“Sürdür”e basın.",
        "The live tail stopped after {minutes} minutes (unattended screen). Press “Resume” to "
        "continue.",
    ),
    "slow_client": (
        "Bağlantı satırlara yetişemedi; akış durduruldu. Filtreyi daraltıp yeniden başlatın.",
        "The connection could not keep up; the stream stopped. Narrow the filter and start again.",
    ),
    "permission_changed": (
        "Canlı akış yetkiniz kaldırıldı.",
        "Your live tail permission was removed.",
    ),
    "server_stopping": (
        "Sunucu yeniden başlatılıyor; canlı akış birazdan yeniden bağlanır.",
        "The server is restarting; the live tail reconnects shortly.",
    ),
}
#: Session reasons an ``end`` reuses from the sign-in's texts.
SESSION_END_CODES: Final = frozenset(
    {
        "session_invalid",
        "session_revoked",
        "session_expired",
        "session_idle",
        "user_inactive",
        "mfa_required",
        "password_change_required",
        "privacy_notice_required",
    }
)

#: ``dropped``: "{n} lines were not shown: {reason}." with the reasons joined.
DROPPED_MESSAGE: Final = (
    "{n} satır gösterilmedi: {reason}.",
    "{n} lines were not shown: {reason}.",
)
DROPPED_REASONS: Final[Mapping[str, tuple[str, str]]] = {
    "rate_limited": (
        "saniyede en fazla {per_second} satır gösterilir",
        "at most {per_second} lines a second are shown",
    ),
    "slow_client": ("bağlantı yetişemedi", "the connection could not keep up"),
    "parse_budget": (
        "sunucu bu kadar satırı ayrıştıramadı; alan filtresi uygulanamadı",
        "the server could not parse this many lines; the field filter could not be applied",
    ),
    "behind": ("dosya çok hızlı büyüdü", "the file grew too fast"),
}

GAP_MESSAGE: Final = (
    "Aradaki satırlar gösterilemedi; bu aralığı Arama'da açabilirsiniz.",
    "The lines in between could not be shown; you can open this interval in Search.",
)


def live_error(code: str, **params: Any) -> ApiError:
    """The API error of ``code`` with its texts and status."""
    message_tr, message_en = MESSAGES[code]
    return ApiError(STATUS[code], code, message_tr.format(**params), message_en.format(**params))


def end_texts(code: str, **params: Any) -> tuple[str, str]:
    """The Turkish and English text of an ``end`` code."""
    if code in END_MESSAGES:
        message_tr, message_en = END_MESSAGES[code]
        return message_tr.format(**params), message_en.format(**params)
    texts = AUTH_MESSAGES.get(code) or API_MESSAGES.get(code)
    if code not in SESSION_END_CODES or texts is None:  # pragma: no cover - a programming error
        raise KeyError(code)
    return texts


def dropped_texts(counts: Mapping[str, int], per_second: int) -> tuple[str, str]:
    """The text of a ``dropped`` event: the total and every reason with a count, in order."""
    reasons = [reason for reason in DROPPED_REASONS if counts.get(reason)]
    total = sum(counts.get(reason, 0) for reason in reasons)
    parts = [DROPPED_REASONS[reason] for reason in reasons]
    reason_tr = "; ".join(text.format(per_second=per_second) for text, _ in parts)
    reason_en = "; ".join(text.format(per_second=per_second) for _, text in parts)
    return (
        DROPPED_MESSAGE[0].format(n=f"{total:,}".replace(",", "."), reason=reason_tr),
        DROPPED_MESSAGE[1].format(n=f"{total:,}", reason=reason_en),
    )
