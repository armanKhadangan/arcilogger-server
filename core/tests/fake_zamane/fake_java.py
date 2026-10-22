"""Stand-in for ``java -jar tss-client-console.jar`` (no Java, no network) for the tests.

Behaviour is selected with FAKE_ZAMANE_MODE; tokens come from the mock TSA kept in
FAKE_ZAMANE_STATE. The real client's console wording is unknown, so the messages below are
deliberately different from anything the provider could depend on.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    args = sys.argv[1:]
    if len(args) < 3 or args[0] != "-jar" or not Path(args[1]).is_file():
        print("Error: Unable to access jarfile")
        return 1
    command, rest = args[2], args[3:]
    mode = os.environ.get("FAKE_ZAMANE_MODE", "ok")
    state = Path(os.environ["FAKE_ZAMANE_STATE"])
    print("invoked with: " + " ".join(rest))  # lets tests check that secrets are redacted
    if mode == "hang":
        time.sleep(30)
    if command in ("-z", "-ZC"):
        if mode == "no_credit":
            print("HATA: Not enough credit")
            return 3
        if mode == "wrong_password":
            print("HATA: Müşteri no veya parola hatalı")
            return 4
        if mode == "unreachable":
            print("java.net.ConnectException: Connection refused")
            return 5
        if mode == "silent_failure":
            print("İşlem bitti")
            return 0
        from arcilogger_core.tsa.mock import MockTsa, MockTsaProvider

        target = Path(rest[0])
        MockTsaProvider(MockTsa(state, key_type="ec")).stamp(target)
        if mode == "garbage_token":
            (target.parent / (target.name + ".zd")).write_bytes(b"not a token")
        print("tamam")
        return 0
    if command in ("-c", "-CC"):
        from arcilogger_core.tsa.mock import MockTsa
        from arcilogger_core.tsa.verify import verify_token

        tsa = MockTsa(state, key_type="ec")
        result = verify_token(
            Path(rest[1]).read_bytes(), roots=[tsa.root_certificate], file_path=Path(rest[0])
        )
        print("geçerli" if result.ok else "geçersiz: " + "; ".join(result.errors))
        return 0 if result.ok else 2
    if command == "-k":
        if mode == "credit_garbled":
            print("bilgi yok")
            return 0
        print("Kalan kontör miktarı: 9634")
        return 0
    print("unknown command")
    return 1


if __name__ == "__main__":
    sys.exit(main())
