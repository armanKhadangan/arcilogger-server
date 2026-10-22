#!/bin/sh
# ARCILOGGER evidence package verifier - wrapper for Linux and macOS.
#
#   sh verify.sh [package folder or unencrypted zip] [--kokler DIR] [--hizli] [--json]
#
# Without a package argument the folder of this script is verified. The verifier itself is
# verify.py: one file, Python 3.9+ standard library only, run isolated (-I -S) so that no
# installed package and no PYTHON* environment variable can change what it does.

here=$(cd "$(dirname "$0")" && pwd)
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 &&
        "$candidate" -I -S -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
        exec "$candidate" -I -S "$here/verify.py" "$@"
    fi
done
echo "Python 3.9 veya ustu bulunamadi [Python 3.9 or newer not found]: https://www.python.org/downloads/" >&2
exit 2
