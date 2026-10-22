"""Password hashing (Argon2id) and the password policy (spec 13).

**Hashing parameters** — Argon2id, ``t = 3`` passes, ``m = 64 MiB``, ``p = 4`` lanes, 16-byte
salt, 32-byte hash: the second recommended option of RFC 9106 (section 4, for environments
where 2 GiB per hash is too much). On the 4-core production server one hash takes roughly
50-100 ms and 64 MiB; with at most ten users, the per-IP rate limit and the account lockout,
neither CPU nor memory can be exhausted through the sign-in form. The parameters are spelled out
here instead of relying on the library default, so a library update can never change them
silently. When they are changed, :func:`verify_password` reports ``new_hash`` for a stored hash
made with the old parameters and the caller stores it (rehash on the next successful sign-in).

**Policy** (NIST SP 800-63B style: length and blocklists, no composition rules):

* at least ``max(12, auth.password_min_length)`` characters — the setting can raise the spec's
  minimum but never lower it; a setting above :data:`MIN_LENGTH_CEILING` counts as that value,
  so no setting can make every password unacceptable — and at most :data:`MAX_LENGTH`;
* not on the bundled list of common/breached passwords (:mod:`.wordlist`), also not when only
  digits/punctuation were added before or after a listed word (``Sunshine2026!!``) and not in
  leetspeak (``P@ssw0rd2026!``: ``@``/``4`` = a, ``0`` = o, ``1``/``!`` = i or l, ``3`` = e,
  ``$``/``5`` = s, ``7`` = t);
* not one of the hand-written Turkish base words (``data/common-passwords-tr.txt``: *şifre*,
  *yönetici*, club, city and common names) with only digits/punctuation around it
  (``Galatasaray1905``, ``Şifre123456!``, leetspeak included), and not only such words with
  digits/punctuation between them (``Ankara.06.Ankara``);
* not built from repeated or sequential characters (``aaaa…``, ``123456789012``, ``qwerty…``);
* not containing the user name, a word of the display name, the site name or the product name
  (leetspeak included).

Comparisons are case-insensitive and ignore Turkish diacritics (``Ş`` = ``s``, ``ı`` = ``i``).
"""

from __future__ import annotations

import contextlib
import functools
import re
import secrets
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from arcilogger_api.auth.wordlist import common_passwords, turkish_base_words

ARGON2_TIME_COST: Final = 3
ARGON2_MEMORY_KIB: Final = 64 * 1024
ARGON2_PARALLELISM: Final = 4
ARGON2_HASH_LEN: Final = 32
ARGON2_SALT_LEN: Final = 16

#: The spec's minimum (13: "min 12 chars"); ``auth.password_min_length`` can only raise it.
MIN_LENGTH_FLOOR: Final = 12
#: Highest effective minimum: a larger ``auth.password_min_length`` (a typo such as 300) would
#: otherwise refuse every password — including the generated temporary ones — and lock users out.
MIN_LENGTH_CEILING: Final = 128
#: Long passphrases are welcome; the cap only bounds the work per request.
MAX_LENGTH: Final = 256
#: Temporary passwords: candidates tried before giving up (one is practically always enough).
TEMPORARY_PASSWORD_ATTEMPTS: Final = 100
#: Fewer distinct characters than this is a repetition (``abababababab``).
MIN_DISTINCT: Final = 5
#: Words shorter than this (from names) are not checked: too many false positives.
MIN_CONTEXT_WORD: Final = 4
PRODUCT_NAME: Final = "arcilogger"

_HASHER: Final = PasswordHasher(
    time_cost=ARGON2_TIME_COST,
    memory_cost=ARGON2_MEMORY_KIB,
    parallelism=ARGON2_PARALLELISM,
    hash_len=ARGON2_HASH_LEN,
    salt_len=ARGON2_SALT_LEN,
    type=Type.ID,
)

#: The alphabet, the digits and keyboard rows (also Turkish Q, folded: ı→i, ğ→g, ş→s, ö→o, ç→c)
#: for the sequence rule; each is checked cyclically, forwards and backwards.
_SEQUENCES: Final = (
    "abcdefghijklmnopqrstuvwxyz",
    "0123456789",
    "qwertyuiopasdfghjklzxcvbnm",
    "qwertyuiopguasdfghjklsizxcvbnmoc",
    "1qaz2wsx3edc4rfv5tgb6yhn7ujm8ik9ol0p",  # keyboard columns
)
_TEMP_ALPHABET: Final = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"
_TEMP_GROUP: Final = 4
#: Leetspeak read back as letters; ``1`` (and ``!``) is tried both as ``i`` and as ``l``.
_LEET: Final = {"@": "a", "4": "a", "0": "o", "3": "e", "$": "s", "5": "s", "7": "t"}
_LEET_I: Final = str.maketrans({**_LEET, "1": "i", "!": "i"})
_LEET_L: Final = str.maketrans({**_LEET, "1": "l", "!": "l"})
_LEET_CHARACTERS: Final = frozenset({*_LEET, "1", "!"})
#: At most this many leetspeak characters next to the word are read as part of it.
_LEET_EDGE: Final = 4
#: prefix (digits, punctuation, spaces), word, suffix.
_EDGES: Final = re.compile(r"^([\W\d_]*)(.*?)([\W\d_]*)$", re.DOTALL)


def hasher() -> PasswordHasher:
    return _HASHER


def hash_password(password: str) -> str:
    """Argon2id PHC string (``$argon2id$v=19$m=65536,t=3,p=4$…``)."""
    return _HASHER.hash(password)


@functools.cache
def _dummy_hash() -> str:
    return _HASHER.hash(secrets.token_urlsafe(16))


def dummy_verify(password: str) -> None:
    """Spend the time of one real verification (unknown user, account without password).

    Without it a failed sign-in for an unknown user name would answer measurably faster and
    reveal which user names exist.
    """
    with contextlib.suppress(VerificationError):
        _HASHER.verify(_dummy_hash(), password)


@dataclass(frozen=True)
class Verified:
    ok: bool
    #: A fresh hash when the stored one used other parameters (store it after a success).
    new_hash: str | None = None


def verify_password(stored_hash: str, password: str) -> Verified:
    """Check ``password`` against a stored hash; never raises for a bad or missing hash."""
    try:
        _HASHER.verify(stored_hash, password)
    except VerifyMismatchError:
        return Verified(ok=False)
    except (InvalidHashError, VerificationError):
        dummy_verify(password)  # e.g. "!" = no password set: same timing as a real check
        return Verified(ok=False)
    if _HASHER.check_needs_rehash(stored_hash):
        return Verified(ok=True, new_hash=_HASHER.hash(password))
    return Verified(ok=True)


def generate_temporary_password(min_length: int = MIN_LENGTH_FLOOR) -> str:
    """A random one-time password of at least ``min_length`` characters, no look-alike letters.

    Groups of four separated by ``-`` (``xxxx-xxxx-xxxx-xxxx``, at least four groups = ~92 bits);
    more groups when the minimum length requires them.
    """
    groups = max(4, -(-(min_length + 1) // (_TEMP_GROUP + 1)))  # 5 characters per group with "-"
    return "-".join(
        "".join(secrets.choice(_TEMP_ALPHABET) for _ in range(_TEMP_GROUP)) for _ in range(groups)
    )


def temporary_password(context: PolicyContext) -> str:
    """A generated password that passes the policy for ``context`` (shown to the admin once).

    Bounded: a random candidate fails only by a rare accident (e.g. it contains a short user
    name), so :data:`TEMPORARY_PASSWORD_ATTEMPTS` failures mean the policy cannot be met at all;
    that is an error, never an endless loop in a request that holds locks.
    """
    for _ in range(TEMPORARY_PASSWORD_ATTEMPTS):
        candidate = generate_temporary_password(context.effective_min_length)
        if not password_problems(candidate, context):
            return candidate
    raise RuntimeError(
        f"no temporary password passed the policy in {TEMPORARY_PASSWORD_ATTEMPTS} attempts"
    )


# -- policy ---------------------------------------------------------------------------------------


def fold(text: str) -> str:
    """Case-insensitive, diacritics-insensitive form (``İŞĞÜÖÇı`` → ``isguoci``)."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.replace("ı", "i")


def _alnum(text: str) -> str:
    return re.sub(r"[\W_]+", "", text)


def _core(text: str) -> str:
    """The word inside leading/trailing digits, punctuation and spaces (``!!pass2026`` → pass)."""
    return re.sub(r"^[\W\d_]+|[\W\d_]+$", "", text)


def _leet_run(text: str) -> str:
    """The leetspeak characters at the start of ``text`` (at most :data:`_LEET_EDGE`)."""
    run = ""
    for character in text[:_LEET_EDGE]:
        if character not in _LEET_CHARACTERS:
            break
        run += character
    return run


def unleet(text: str) -> set[str]:
    """``text`` with leetspeak read back as letters (``p@ssw0rd`` → ``password``).

    Two readings, as ``1``/``!`` stand for both ``i`` and ``l`` (``l1verp00l``).
    """
    return {text.translate(_LEET_I), text.translate(_LEET_L)}


def _is_sequence(folded: str) -> bool:
    """A run of a sequence (``abcdefghijkl``, ``987654321098``) or a repeated chunk
    (``abc123abc123``)."""
    compact = _alnum(folded)
    if len(compact) < 4:
        return False
    if re.fullmatch(r"(.+?)\1+", compact):
        return True
    for sequence in _SEQUENCES:
        doubled = sequence * 3
        if compact in doubled or compact in doubled[::-1]:
            return True
    return False


@dataclass(frozen=True)
class PolicyContext:
    """What a password must not be based on, plus the minimum length setting."""

    min_length: int = MIN_LENGTH_FLOOR
    username: str | None = None
    display_name: str | None = None
    site_name: str | None = None

    @property
    def effective_min_length(self) -> int:
        return min(MIN_LENGTH_CEILING, max(MIN_LENGTH_FLOOR, self.min_length))

    def words(self) -> set[str]:
        found = {PRODUCT_NAME}
        if self.username and len(self.username) >= 3:
            found.add(_alnum(fold(self.username)))
        for name in (self.display_name, self.site_name):
            if not name:
                continue
            parts = [_alnum(fold(part)) for part in re.split(r"[\s._-]+", name)]
            found.update(part for part in parts if len(part) >= MIN_CONTEXT_WORD)
            compact = _alnum(fold(name))
            if len(compact) >= MIN_CONTEXT_WORD:
                found.add(compact)
        found.discard("")
        return found


@dataclass(frozen=True)
class Problem:
    rule: str
    message_tr: str
    message_en: str

    def as_dict(self) -> dict[str, str]:
        return {"rule": self.rule, "message_tr": self.message_tr, "message_en": self.message_en}


REUSED: Final = Problem(
    "reused",
    "Yeni parola mevcut paroladan farklı olmalı.",
    "The new password must differ from the current one.",
)


def password_problems(password: str, context: PolicyContext) -> list[Problem]:
    """Every rule ``password`` breaks (empty list: acceptable)."""
    problems: list[Problem] = []
    minimum = context.effective_min_length
    if len(password) < minimum:
        problems.append(
            Problem(
                "too_short",
                f"Parola en az {minimum} karakter olmalı.",
                f"The password must be at least {minimum} characters long.",
            )
        )
    if len(password) > MAX_LENGTH:
        problems.append(
            Problem(
                "too_long",
                f"Parola en fazla {MAX_LENGTH} karakter olabilir.",
                f"The password must not be longer than {MAX_LENGTH} characters.",
            )
        )
    folded = fold(password)
    if _is_common(password, folded):
        problems.append(
            Problem(
                "common",
                "Bu parola yaygın veya sızdırılmış bir parola ya da yaygın bir kelimeye rakam "
                "veya işaret eklenmiş hâli; başka bir parola seçin.",
                "This password is a common or breached password, or a common word with digits "
                "or symbols added; choose another.",
            )
        )
    if len(set(folded)) < MIN_DISTINCT or _is_sequence(folded):
        problems.append(
            Problem(
                "repetitive",
                "Parola tekrarlanan veya ardışık karakterlerden oluşuyor.",
                "The password consists of repeated or sequential characters.",
            )
        )
    if any(_contains_any(_alnum(form), context.words()) for form in {folded, *unleet(folded)}):
        problems.append(
            Problem(
                "personal",
                "Parola kullanıcı adını, adınızı, şirket adını veya ürün adını içermemeli.",
                "The password must not contain the user name, your name, the company name or "
                "the product name.",
            )
        )
    return problems


@functools.cache
def _turkish_words() -> frozenset[str]:
    return frozenset(_alnum(fold(word)) for word in turkish_base_words())


def _cores(folded: str) -> set[str]:
    """The word a password is built around, in every reading.

    The part inside leading/trailing digits and punctuation, also with the leetspeak characters
    next to it counted as letters (``4dmin2026`` → ``admin``, ``y0n3t1c1.2026`` → ``yonetici``),
    with leetspeak undone and without inner separators (``fener-bahce`` → ``fenerbahce``).
    """
    edges = _EDGES.match(folded)
    if edges is None:  # pragma: no cover - the pattern matches every string
        return set()
    prefix, core, suffix = edges.groups()
    left = _leet_run(prefix[::-1])[::-1]
    right = _leet_run(suffix)
    found = {core}
    for start in range(len(left) + 1):
        for end in range(len(right) + 1):
            for reading in unleet(left[len(left) - start :] + core + right[:end]):
                found.add(_core(reading))
    found |= {_alnum(word) for word in found}
    return {word for word in found if len(word) >= MIN_CONTEXT_WORD}


def _is_common(password: str, folded: str) -> bool:
    listed = common_passwords()
    cores = _cores(folded)
    candidates = {password.casefold(), folded, folded.strip(), *unleet(folded), *cores}
    if any(candidate in listed for candidate in candidates):
        return True
    turkish = _turkish_words()
    if any(word in turkish for word in cores):
        return True
    # Only Turkish base words with digits/punctuation between them ("Şifre.2026.Şifre").
    words = re.findall(r"[^\W\d_]+", folded)
    return bool(words) and all(word in turkish for word in words)


def _contains_any(compact: str, words: Iterable[str]) -> bool:
    return any(word in compact for word in words)
