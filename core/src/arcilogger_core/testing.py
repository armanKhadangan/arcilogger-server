"""Test helpers shared by the tests of every package — never used by a service.

* :func:`refused_port` gives a port on 127.0.0.1 that refuses connections for as long as the
  test needs it (an SMTP server or a database that is down).
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from contextlib import contextmanager


@contextmanager
def refused_port() -> Iterator[int]:
    """``with refused_port() as port:`` — nobody listens on ``127.0.0.1:<port>`` inside.

    The port stays bound, without listening, until the context ends: a connection to it is
    refused (on Linux and Windows), and no other test can listen on it meanwhile. A port that
    was bound and closed again is free for the next ``bind(0)`` of another xdist worker.
    """
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        port: int = held.getsockname()[1]
        yield port
