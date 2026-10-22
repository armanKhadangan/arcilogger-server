"""Kamu SM official console client "Zamane" (``tss-client-console-<version>.jar``, Java 8).

Documented usage (Kamu SM "Ücretsiz Zaman Damgası İstemci Yazılımı" page)::

    java -jar <client>.jar -z <file> <server> <port> <customer no> <password>
                           [proxy ip] [proxy port] [proxy user] [proxy pass] [sha-256]
    java -jar <client>.jar -c|-CC <file> <file>.zd
    java -jar <client>.jar -k <server> <port> <customer no> <password>

The stamp is written as ``<file>.zd``. The client's console messages and exit codes are **not
documented**, so this provider never relies on them for success: a stamp counts only when the
``.zd`` file exists and parses as a time-stamp token, and the sealing pipeline then verifies it
independently. Output is kept for the audit trail with the customer number and password redacted.

Residual risk (documented in ADR-0001): the client takes the password on its command line; on
Windows only administrators can read another account's process command line.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

from arcilogger_core.tsa.base import (
    StampResult,
    TsaConfigurationError,
    TsaError,
    TsaNoCreditError,
    TsaResponseError,
    TsaUnavailableError,
)
from arcilogger_core.tsa.client import CREDIT_WORDS, token_path_for
from arcilogger_core.tsa.verify import inspect_token

ZAMANE_DIGESTS = {"sha256": "sha-256", "sha512": "sha-512"}
UNREACHABLE_WORDS = (
    "connect",
    "bağlan",
    "timeout",
    "timed out",
    "zaman aşımı",
    "unknownhost",
    "refused",
    "unreachable",
)
CREDENTIAL_WORDS = (
    "password",
    "parola",
    "şifre",
    "authentication",
    "kimlik",
    "müşteri no",
    "unauthorized",
)

Runner = Callable[..., "subprocess.CompletedProcess[bytes]"]


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1254", errors="replace")  # Turkish Windows console code page


class ZamaneCliProvider:
    name = "zamane"

    def __init__(
        self,
        *,
        java: Sequence[str],
        jar: Path,
        url: str,
        port: int,
        customer: str,
        password: str,
        legal_grade: bool,
        digest: str = "sha256",
        timeout_seconds: float = 60,
        verify_flag: str = "-CC",
        credit_regex: str = r"(?i)(?:kontör|kontor|credit)\D{0,40}?(\d+)",
        runner: Runner = subprocess.run,
    ) -> None:
        if digest not in ZAMANE_DIGESTS:
            raise TsaConfigurationError(f"Zamane does not support {digest}")
        self.java = tuple(java)
        self.jar = jar
        self.url = url
        self.port = port
        self._customer = customer
        self._password = password
        self.legal_grade = legal_grade
        self.digest = digest
        self.timeout_seconds = timeout_seconds
        self.verify_flag = verify_flag
        self.credit_regex = re.compile(credit_regex)
        self._runner = runner

    def redact(self, text: str) -> str:
        for secret in (self._password, self._customer):
            if secret and len(secret) >= 3:
                text = text.replace(secret, "***")
        return text

    def _run(self, args: Sequence[str]) -> tuple[int, str]:
        if not self.jar.is_file():
            raise TsaConfigurationError(f"Zamane client not found: {self.jar}")
        command = [*self.java, "-jar", str(self.jar), *args]
        try:
            completed = self._runner(
                command, capture_output=True, timeout=self.timeout_seconds, check=False
            )
        except subprocess.TimeoutExpired as exc:
            raise TsaUnavailableError(
                f"Zamane client timed out after {self.timeout_seconds} s"
            ) from exc
        except FileNotFoundError as exc:
            raise TsaConfigurationError(f"Java not found: {self.java[0]}") from exc
        output = _decode(completed.stdout or b"") + _decode(completed.stderr or b"")
        return completed.returncode, self.redact(output.strip())

    @staticmethod
    def classify(output: str) -> TsaError:
        lowered = output.lower()
        if any(word in lowered for word in CREDIT_WORDS):
            return TsaNoCreditError(output)
        if any(word in lowered for word in CREDENTIAL_WORDS):
            return TsaConfigurationError(f"Kamu SM rejected the credentials: {output}")
        if any(word in lowered for word in UNREACHABLE_WORDS):
            return TsaUnavailableError(output)
        return TsaError(output or "Zamane client failed without output")

    def stamp(self, file_path: Path) -> StampResult:
        token_path = token_path_for(file_path)
        token_path.unlink(missing_ok=True)
        code, output = self._run(
            [
                "-z",
                str(file_path),
                self.url,
                str(self.port),
                self._customer,
                self._password,
                ZAMANE_DIGESTS[self.digest],
            ]
        )
        if not token_path.is_file():
            raise self.classify(output or f"exit code {code}, no .zd file")
        try:
            info = inspect_token(token_path.read_bytes())
        except TsaError as exc:
            raise TsaResponseError(f"Zamane wrote an unreadable token: {exc}") from exc
        return StampResult(token_path=token_path, info=info, raw_output=output)

    def check(self, file_path: Path, token_path: Path) -> tuple[bool, str]:
        """Kamu SM's own check (``-c``/``-CC``); informative, the authoritative check is ours."""
        code, output = self._run([self.verify_flag, str(file_path), str(token_path)])
        return code == 0, output

    def credits(self) -> int | None:
        code, output = self._run(["-k", self.url, str(self.port), self._customer, self._password])
        match = self.credit_regex.search(output)
        if code != 0 or match is None:
            raise (
                self.classify(output)
                if code != 0
                else TsaResponseError(
                    f"credit not found in the client output (check tsa.credit_regex): {output!r}"
                )
            )
        return int(match.group(1))
