"""The public keys that sign ARCILOGGER licence files: the only trusted key set.

Rules (licensing contract 2.1):

* Only keys the owner committed. A private key never enters the repository: the owner makes the
  key pair offline with the issuer tool (``tools/license-issuer``) and sends only the public key
  line printed by ``keygen``.
* Nothing at run time adds a key: no environment variable, configuration file, database row or
  API. The verifier looks this attribute up at call time; tests swap it
  (``arcilogger_core.license.testing.trusted_test_keys``).
* Rotation: add a new key id beside the old one and issue new licences with it. Removing a key id
  makes every licence signed with it invalid (``license_unknown_key``): for a compromised key
  only.
* Key ids starting with ``test-`` never appear here.
* These keys sign licences only; another signed object (the phase-2 file manifest) gets its own
  key set.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

#: key id -> raw Ed25519 public key (32 bytes) as base64url without padding (43 characters).
#: Production: only keys the owner committed. Empty until the owner sends the first public key.
TRUSTED_KEYS: Final[Mapping[str, str]] = MappingProxyType({})
