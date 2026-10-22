"""Authentication building blocks (spec 13): passwords, TOTP, rate limits, accounts, first run.

The HTTP endpoints live in :mod:`arcilogger_api.routers.auth`, ``.users`` and ``.audit``; the
modules here hold the logic they share with the ``arcilogger-api`` command line:

* :mod:`.passwords` — Argon2id hashing and the password policy (with :mod:`.wordlist`, the
  bundled list of common and breached passwords).
* :mod:`.totp` — TOTP (RFC 6238) with replay protection, the encrypted secret, QR code and the
  single-use recovery codes.
* :mod:`.ratelimit` — in-memory per-IP limits for the sign-in endpoints.
* :mod:`.accounts` — lockout, session rotation and revocation, the sign-in state machine.
* :mod:`.firstrun` — the one-time setup token for the first administrator.
* :mod:`.messages` — error codes with Turkish and English texts.

docs/SECURITY.md describes the behaviour as a whole.
"""
