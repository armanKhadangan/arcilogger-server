"""Test helper: a real SMTP server on 127.0.0.1 for the alert e-mail tests (stdlib only).

:class:`SmtpTestServer` runs :mod:`socketserver` with one thread per connection and speaks
enough SMTP for :mod:`smtplib`: EHLO, AUTH PLAIN (no STARTTLS — the tests use
``[smtp] security = "none"``), MAIL, RCPT, DATA, RSET, NOOP, QUIT. It records what it accepted
and can refuse recipients (5xx or 4xx), demand credentials or wait before its greeting
(timeouts). Never used by the services.
"""

from __future__ import annotations

import base64
import email
import email.policy
import socketserver
import threading
import time
from dataclasses import dataclass, field
from email.message import EmailMessage
from types import TracebackType


@dataclass
class Received:
    sender: str
    recipients: list[str]
    data: bytes
    #: ``(user, password)`` of ``AUTH PLAIN`` on that connection, if any.
    login: tuple[str, str] | None

    @property
    def message(self) -> EmailMessage:
        return email.message_from_bytes(self.data, policy=email.policy.default)

    def part(self, subtype: str) -> str:
        body = self.message.get_body(preferencelist=(subtype,))
        if not isinstance(body, EmailMessage):
            raise LookupError(f"no text/{subtype} part")
        return str(body.get_content())


@dataclass
class FakeSmtp:
    host: str = "127.0.0.1"
    port: int = 0
    received: list[Received] = field(default_factory=list)
    #: ``RCPT TO`` of these addresses: ``550`` (permanent) / ``451`` (temporary).
    refuse: set[str] = field(default_factory=set)
    defer: set[str] = field(default_factory=set)
    #: Required ``AUTH PLAIN`` credentials (``None``: no authentication needed).
    credentials: tuple[str, str] | None = None
    #: Seconds to wait before the greeting.
    delay: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def messages_to(self, recipient: str) -> list[Received]:
        with self.lock:
            return [r for r in self.received if recipient in r.recipients]


class _Handler(socketserver.StreamRequestHandler):
    server: _Server

    def reply(self, text: str) -> None:
        self.wfile.write(text.encode("ascii") + b"\r\n")

    def handle(self) -> None:
        fake = self.server.fake
        if fake.delay:
            time.sleep(fake.delay)
        self.reply("220 fake.test ESMTP")
        sender, login = "", None
        recipients: list[str] = []
        while True:
            raw = self.rfile.readline()
            if not raw:
                return
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            verb, _, rest = line.partition(" ")
            verb = verb.upper()
            if verb == "EHLO":
                self.wfile.write(b"250-fake.test\r\n250-AUTH PLAIN\r\n250 8BITMIME\r\n")
            elif verb == "HELO":
                self.reply("250 fake.test")
            elif verb == "AUTH":
                login = self._auth(rest)
                if login is None:
                    self.reply("535 5.7.8 authentication failed")
                else:
                    self.reply("235 2.7.0 accepted")
            elif verb == "MAIL":
                if fake.credentials is not None and login is None:
                    self.reply("530 5.7.0 authentication required")
                    continue
                sender, recipients = rest.partition(":")[2].split()[0].strip("<>"), []
                self.reply("250 2.1.0 ok")
            elif verb == "RCPT":
                address = rest.partition(":")[2].strip().strip("<>")
                if address in fake.refuse:
                    self.reply("550 5.1.1 mailbox unavailable")
                elif address in fake.defer:
                    self.reply("451 4.3.0 try again later")
                else:
                    recipients.append(address)
                    self.reply("250 2.1.5 ok")
            elif verb == "DATA":
                if not recipients:
                    self.reply("554 5.5.1 no valid recipients")
                    continue
                self.reply("354 end with <CRLF>.<CRLF>")
                data = self._data()
                with fake.lock:
                    fake.received.append(Received(sender, list(recipients), data, login))
                self.reply("250 2.0.0 queued")
            elif verb == "RSET":
                sender, recipients = "", []
                self.reply("250 2.0.0 ok")
            elif verb == "NOOP":
                self.reply("250 2.0.0 ok")
            elif verb == "QUIT":
                self.reply("221 2.0.0 bye")
                return
            else:
                self.reply("502 5.5.2 not implemented")

    def _auth(self, rest: str) -> tuple[str, str] | None:
        mechanism, _, initial = rest.partition(" ")
        if mechanism.upper() != "PLAIN":
            return None
        if not initial:
            self.reply("334 ")
            initial = self.rfile.readline().decode("ascii").strip()
        try:
            _, user, password = base64.b64decode(initial).decode("utf-8").split("\0")
        except ValueError:
            return None
        expected = self.server.fake.credentials
        return (user, password) if expected is None or expected == (user, password) else None

    def _data(self) -> bytes:
        lines = []
        while True:
            raw = self.rfile.readline()
            if raw in (b".\r\n", b".\n", b""):
                break
            lines.append(raw[1:] if raw.startswith(b"..") else raw)
        return b"".join(lines)


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, fake: FakeSmtp) -> None:
        super().__init__((fake.host, 0), _Handler)
        self.fake = fake


class SmtpTestServer:
    """``with SmtpTestServer() as fake:`` — listening on ``fake.host``:``fake.port``."""

    def __init__(self) -> None:
        self.fake = FakeSmtp()
        self._server = _Server(self.fake)
        self.fake.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> FakeSmtp:
        self._thread.start()
        return self.fake

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        self._server.shutdown()
        self._server.server_close()
