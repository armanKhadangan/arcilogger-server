"""Argon2id hashing, the password policy and the bundled common-password list (spec 13)."""

from __future__ import annotations

import gzip
import re
from pathlib import Path

import pytest
from argon2 import PasswordHasher, Type

from arcilogger_api.auth import passwords, wordlist
from arcilogger_api.auth.passwords import (
    MAX_LENGTH,
    MIN_LENGTH_CEILING,
    MIN_LENGTH_FLOOR,
    TEMPORARY_PASSWORD_ATTEMPTS,
    PolicyContext,
    dummy_verify,
    fold,
    generate_temporary_password,
    hash_password,
    password_problems,
    temporary_password,
    unleet,
    verify_password,
)

CONTEXT = PolicyContext(
    min_length=12, username="tolga", display_name="Tolga Yılmaz", site_name="Örnek A.Ş."
)


def _rules(password: str, context: PolicyContext = CONTEXT) -> list[str]:
    return [problem.rule for problem in password_problems(password, context)]


# -- hashing --------------------------------------------------------------------------------------


def test_hash_uses_the_documented_argon2id_parameters() -> None:
    stored = hash_password("Kırmızı balon uçtu 7")
    assert stored.startswith("$argon2id$v=19$m=65536,t=3,p=4$")
    salt, digest = stored.split("$")[-2:]
    assert len(salt) == 22  # 16 bytes, unpadded base64
    assert len(digest) == 43  # 32 bytes
    assert hash_password("Kırmızı balon uçtu 7") != stored  # random salt


def test_verify_accepts_the_right_password_only() -> None:
    stored = hash_password("Kırmızı balon uçtu 7")
    assert verify_password(stored, "Kırmızı balon uçtu 7").ok
    assert verify_password(stored, "Kırmızı balon uçtu 7").new_hash is None
    assert not verify_password(stored, "kırmızı balon uçtu 7").ok
    assert not verify_password(stored, "").ok


@pytest.mark.parametrize("stored", ["!", "", "$argon2id$v=19$garbage", "plain-text"])
def test_verify_never_raises_for_unusable_hashes(stored: str) -> None:
    assert not verify_password(stored, "anything at all").ok


def test_old_parameters_are_rehashed_after_a_successful_check() -> None:
    weak = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1, type=Type.ID)
    stored = weak.hash("Eski parametreler 42")
    result = verify_password(stored, "Eski parametreler 42")
    assert result.ok
    assert result.new_hash is not None
    assert result.new_hash.startswith("$argon2id$v=19$m=65536,t=3,p=4$")
    assert verify_password(result.new_hash, "Eski parametreler 42") == passwords.Verified(ok=True)
    assert verify_password(stored, "wrong").new_hash is None


class _SpyHasher:
    def __init__(self, real: PasswordHasher, calls: list[str]) -> None:
        self.real = real
        self.calls = calls

    def verify(self, stored: str, password: str) -> bool:
        self.calls.append(stored)
        return self.real.verify(stored, password)

    def __getattr__(self, name: str) -> object:
        return getattr(self.real, name)


def test_dummy_verification_does_real_argon2_work(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(passwords, "_HASHER", _SpyHasher(passwords._HASHER, calls))
    dummy_verify("whatever")
    assert len(calls) == 1
    assert calls[0].startswith("$argon2id$v=19$m=65536,t=3,p=4$")
    verify_password("!", "x")  # an account without a password costs the same
    assert len(calls) == 3  # the failed parse attempt plus one dummy verification


# -- policy ---------------------------------------------------------------------------------------


def test_fold_ignores_case_and_turkish_diacritics() -> None:
    assert fold("İSTANBUL ışık ŞĞÜÖÇ") == "istanbul isik sguoc"
    assert fold("Örnek A.Ş.") == "ornek a.s."


@pytest.mark.parametrize(
    "password",
    [
        "Mavi kuşlar sabah uçar",
        "tren-ayva-91-pusula",
        "Q8#vL2!pZ7@rT4",
        "çiğköfte yerken üşüdük",
        "Ankara'da kar yağıyor 2026",  # a listed city among other words
        "galatasaray ve fener 1905",  # "ve" is no base word
        "M3rd1v3n-k0pru-t4s",  # leetspeak of no listed word
    ],
)
def test_good_passwords_pass(password: str) -> None:
    assert password_problems(password, CONTEXT) == []


@pytest.mark.parametrize(
    ("password", "rules"),
    [
        ("kısa parola", ["too_short"]),  # 11 characters
        ("qwerty123456", ["common"]),
        ("QWERTY123456", ["common"]),  # case does not help
        ("1qaz2wsx3edc", ["common", "repetitive"]),  # listed and a keyboard column run
        ("iloveyouforever", ["common"]),  # long entry of the xato-net list
        ("Sunshine2026!!", ["common"]),  # listed word + digits/punctuation
        ("!!Football1907", ["common"]),
        ("aaaaaaaaaaaaaaa", ["common", "repetitive"]),
        ("şşşşşşşşşşşşş", ["repetitive"]),
        ("ğüğüğüğüğüğü", ["repetitive"]),
        ("abababababababab", ["repetitive"]),
        ("ab12ab12ab12ab12", ["repetitive"]),
        ("987654321098", ["repetitive"]),
        ("mnopqrstuvwxyz", ["repetitive"]),
        ("tolga-ve-kedisi-77", ["personal"]),  # user name
        ("YILMAZ ailesi bahçede", ["personal"]),  # display name word, any case
        ("ÖrnekAŞ!Mart.2026", ["personal"]),  # site name
        ("güzel örnek suyu içtik", ["personal"]),  # word of the site name
        ("ARCILOGGER yönetim 26", ["personal"]),  # product name
    ],
)
def test_policy_rules(password: str, rules: list[str]) -> None:
    assert _rules(password) == rules


@pytest.mark.parametrize(
    "password",
    [
        # Leetspeak of listed passwords, also with digits/punctuation around them.
        "P@ssw0rd2026!",
        "p4$$w0rd123456",
        "4dmin2026!!!!",  # leading leet digit belongs to the word
        "L1verp00l1905",  # 1 read as l and as i
        "Sunsh1ne.2026!",
        # Hand-written Turkish base words (data/common-passwords-tr.txt).
        "Sifre123456!",
        "Şifre123456!",
        "Yonetici2026!",
        "Y0n3t1c1.2026",  # trailing leet digit belongs to the word
        "Galatasaray1905",
        "Fenerbahce1907",
        "Fener-Bahçe-1907",  # inner separators
        "Türkiye1923!",
        "TÜRKİYE1923!!",
        "Ankara061234",
        "Mehmet123456",
        "Şifre.2026.Şifre",  # only base words with digits between them
        "Galatasaray 1905 Cimbom",
    ],
)
def test_common_variants_and_turkish_words_are_refused(password: str) -> None:
    context = PolicyContext(username="ayse.kaya", display_name="Ayşe Kaya", site_name="Örnek A.Ş.")
    assert _rules(password, context) == ["common"]


def test_leetspeak_does_not_hide_personal_words() -> None:
    assert _rules("0rn3k-y4z-m4rt-26", CONTEXT) == ["personal"]  # site name
    assert _rules("t0lg4 ve kedisi!!", CONTEXT) == ["personal"]  # user name
    assert _rules("4rc1l0gg3r yönetim", CONTEXT) == ["personal"]  # product name


def test_unleet_reads_one_both_ways() -> None:
    assert unleet("p@$$w0rd") == {"password"}
    assert unleet("l1verp00l") == {"liverpool", "llverpool"}
    assert unleet("ad!n") == {"adin", "adln"}
    assert unleet("abc") == {"abc"}


def test_turkish_word_list_is_well_formed() -> None:
    path = Path(wordlist.__file__).parent / "data" / wordlist.TURKISH_RESOURCE
    text = path.read_text(encoding="utf-8")
    assert "VERIFY ON SITE" in text  # the content is a judgement, not a study
    words = wordlist.turkish_base_words()
    assert len(words) == 239
    folded = [re.sub(r"[\W_]+", "", fold(word)) for word in words]
    assert len(set(folded)) == len(folded)  # no duplicates after folding
    assert all(word.isascii() and word.isalpha() for word in folded)
    assert {"sifre", "parola", "yonetici", "galatasaray", "fenerbahce", "istanbul"} <= set(folded)
    assert wordlist.parse_base_words("# comment\n\n  şifre \nparola\n") == ("şifre", "parola")


def test_minimum_length_setting_can_raise_but_not_lower_the_floor() -> None:
    lower = PolicyContext(min_length=8)
    assert lower.effective_min_length == MIN_LENGTH_FLOOR == 12
    assert _rules("Zp7#kQ2!vL", lower) == ["too_short"]  # 10 characters
    higher = PolicyContext(min_length=20)
    assert _rules("Mavi kuşlar sabah uçar", higher) == []  # 22 characters
    assert _rules("Mavi kuşlar sabah", higher) == ["too_short"]
    problems = password_problems("Mavi kuşlar sabah", higher)
    assert problems[0].message_tr == "Parola en az 20 karakter olmalı."
    assert problems[0].as_dict()["message_en"] == (
        "The password must be at least 20 characters long."
    )


def test_minimum_length_setting_is_capped() -> None:
    """A mistyped huge minimum (300) must not refuse every password."""
    assert PolicyContext(min_length=300).effective_min_length == MIN_LENGTH_CEILING == 128
    assert PolicyContext(min_length=128).effective_min_length == 128
    assert MIN_LENGTH_CEILING < MAX_LENGTH
    passphrase = (
        "Mavi kuşlar sabah uçar, kırmızı tren akşam gelir, sarı limon ağacı bahçede büyür, "
        "yaşlı balıkçı denize bakar ve şarkı söyler, gün biter."
    )
    assert 128 <= len(passphrase) < 300
    assert _rules(passphrase, PolicyContext(min_length=300)) == []
    assert _rules(passphrase[:127], PolicyContext(min_length=300)) == ["too_short"]


def test_maximum_length() -> None:
    assert "too_long" in _rules("Zp7#kQ2!vL" * 30)
    assert len("Zp7#kQ2!vL" * 25) <= MAX_LENGTH


def test_an_empty_organisation_name_adds_no_word() -> None:
    """``site_name`` may still be empty (not configured): no word, no refusal."""
    unnamed = PolicyContext(username="tolga", display_name="Tolga Yılmaz", site_name="")
    assert unnamed.words() == {"arcilogger", "tolga", "yilmaz", "tolgayilmaz"}
    assert _rules("Örnek kuşlar sabah uçar", unnamed) == []
    assert _rules("Örnek kuşlar sabah uçar", CONTEXT) == ["personal"]
    assert PolicyContext(site_name=None).words() == {"arcilogger"}


def test_short_context_words_are_ignored() -> None:
    context = PolicyContext(username="al", display_name="Al", site_name="Ek")
    assert password_problems("Mavi kuşlar sabah uçar al ek", context) == []
    # A short name still counts as a whole when its compact form is long enough.
    named = PolicyContext(display_name="Al Bo")
    assert _rules("Mavi kuşlar sabah uçar albo", named) == ["personal"]


def test_temporary_passwords_are_random_and_acceptable() -> None:
    seen = {generate_temporary_password() for _ in range(50)}
    assert len(seen) == 50
    for value in seen:
        assert re.fullmatch(r"[a-zA-Z2-9]{4}(-[a-zA-Z2-9]{4}){3}", value)
        assert not set(value) & set("0O1lI")
    assert all(password_problems(value, CONTEXT) == [] for value in list(seen)[:10])


@pytest.mark.parametrize(
    ("minimum", "length"),
    [(0, 19), (12, 19), (19, 19), (20, 24), (24, 24), (25, 29), (64, 64), (128, 129)],
)
def test_temporary_passwords_grow_with_the_minimum_length(minimum: int, length: int) -> None:
    value = generate_temporary_password(minimum)
    assert len(value) == length
    assert re.fullmatch(r"[a-zA-Z2-9]{4}(-[a-zA-Z2-9]{4})+", value)


@pytest.mark.parametrize("minimum", [12, 20, 64, 128, 300])
def test_temporary_password_passes_the_policy_for_any_minimum(minimum: int) -> None:
    context = PolicyContext(min_length=minimum, username="tolga", site_name="Örnek A.Ş.")
    value = temporary_password(context)
    assert context.effective_min_length <= len(value) <= MAX_LENGTH
    assert password_problems(value, context) == []


def test_temporary_password_gives_up_after_bounded_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def refuse(candidate: str, context: PolicyContext) -> list[passwords.Problem]:
        calls.append(candidate)
        return [passwords.REUSED]

    monkeypatch.setattr(passwords, "password_problems", refuse)
    with pytest.raises(RuntimeError, match="no temporary password passed the policy"):
        temporary_password(CONTEXT)
    assert len(calls) == TEMPORARY_PASSWORD_ATTEMPTS == 100


# -- bundled word list ----------------------------------------------------------------------------


def test_bundled_list_names_its_source_and_licence() -> None:
    header, entries = wordlist.bundled()
    text = "\n".join(header)
    assert "https://github.com/danielmiessler/SecLists" in text
    assert "MIT License" in text
    assert "Copyright (c) 2018 Daniel Miessler" in text
    assert (
        "source: 10k-most-common.txt min_length=0 "
        "sha256=68782d6a4a19a4768d5f15dd66bd534e7a33055cc755411e33f16d18c50fdcce" in text
    )
    assert (
        "source: xato-net-10-million-passwords-1000000.txt min_length=12 "
        "sha256=424a3e03a17df0a2bc2b3ca749d81b04e79d59cb7aeec8876a5a3f308d0caf51" in text
    )
    assert f"entries: {len(entries)}" in text
    assert len(entries) == 56138
    assert {"password", "123456", "qwerty", "iloveyouforever", "1qaz2wsx3edc"} <= entries
    assert all(entry == entry.casefold() for entry in entries)


def test_notice_file_matches_the_bundled_header() -> None:
    notice = Path(wordlist.__file__).parent / "data" / "common-passwords.NOTICE.txt"
    header = wordlist.bundled()[0]
    lines = [line.rstrip() for line in notice.read_text(encoding="utf-8").strip().splitlines()]
    assert list(header[: len(lines)]) == lines


def test_build_is_deterministic_and_round_trips(tmp_path: Path) -> None:
    first = tmp_path / "a.txt"
    first.write_bytes(b"Password\n123456\n\nqwerty\n")
    second = tmp_path / "b.txt"
    second.write_bytes(b"short\nlongenoughpassword\nPASSWORD\n")
    sources = [wordlist.Source(first), wordlist.Source(second, 10)]
    data = wordlist.build_wordlist(sources, "Test notice\nLicence: MIT")
    assert data == wordlist.build_wordlist(sources, "Test notice\nLicence: MIT")
    assert data[4:8] == b"\x00\x00\x00\x00"  # gzip mtime 0: reproducible bytes
    header, entries = wordlist.parse_wordlist(data)
    assert entries == {"password", "123456", "qwerty", "longenoughpassword"}
    assert header[:2] == ["Test notice", "Licence: MIT"]
    assert header[-1] == "entries: 4"
    assert header[2].startswith("source: a.txt min_length=0 sha256=")
    assert header[3].endswith("new_entries=1")  # "password" was already there
    with pytest.raises(ValueError, match="separator"):
        wordlist.parse_wordlist(gzip.compress(b"no header"))


def test_build_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "list.txt"
    source.write_bytes(b"alpha\nbetabetabeta\n")
    notice = tmp_path / "NOTICE.txt"
    notice.write_text("Kaynak: test", encoding="utf-8")
    out = tmp_path / "out.txt.gz"
    assert (
        wordlist.main(
            ["build", "--out", str(out), "--notice", str(notice), "--source", f"{source}:6"]
        )
        == 0
    )
    assert wordlist.parse_wordlist(out.read_bytes())[1] == {"betabetabeta"}
    assert wordlist._source(r"C:\lists\a.txt") == wordlist.Source(Path(r"C:\lists\a.txt"))
    assert wordlist._source(r"C:\lists\a.txt:12") == wordlist.Source(Path(r"C:\lists\a.txt"), 12)
    assert wordlist.main(["info"]) == 0
    printed = capsys.readouterr().out
    assert "loaded entries: 56138" in printed
    assert "Turkish base words (common-passwords-tr.txt): 239" in printed
