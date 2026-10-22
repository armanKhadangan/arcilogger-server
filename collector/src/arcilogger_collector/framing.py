"""Syslog over TCP framing (RFC 6587).

Two framings exist and both are accepted on the same port, decided per frame:

* **octet counting** — ``<length> SP <message>`` (FortiGate "reliable" mode);
* **non-transparent** — the message ends with LF (MikroTik and most others).

A syslog message always starts with ``<PRI>``, so a frame starting with a digit is octet-counted.
The LF delimiter is framing and is removed; everything else, including a CR before the LF, is part
of the message and is kept.
"""

from __future__ import annotations


class FramingError(ValueError):
    """The peer sent something that cannot be framed (the connection is closed)."""


class FrameDecoder:
    def __init__(self, max_message_bytes: int) -> None:
        self._max = max_message_bytes
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        """Add received bytes and return the complete messages found so far."""
        self._buffer.extend(data)
        messages: list[bytes] = []
        while self._buffer:
            first = self._buffer[0]
            if 0x31 <= first <= 0x39:  # '1'..'9': octet counting
                space = self._buffer.find(b" ", 0, 12)
                if space == -1:
                    if len(self._buffer) >= 12:
                        raise FramingError("octet count too long")
                    break
                count_text = bytes(self._buffer[:space])
                if not count_text.isdigit():
                    raise FramingError("invalid octet count")
                length = int(count_text)
                if length > self._max:
                    raise FramingError(f"frame of {length} bytes exceeds the limit")
                end = space + 1 + length
                if len(self._buffer) < end:
                    break
                messages.append(bytes(self._buffer[space + 1 : end]))
                del self._buffer[:end]
            else:
                newline = self._buffer.find(b"\n")
                if newline == -1:
                    if len(self._buffer) > self._max:
                        # Never lose data: write the oversized fragment as its own message.
                        messages.append(bytes(self._buffer[: self._max]))
                        del self._buffer[: self._max]
                        continue
                    break
                messages.append(bytes(self._buffer[:newline]))
                del self._buffer[: newline + 1]
        return messages

    def pending(self) -> bytes:
        """Bytes received after the last complete message (flushed when the peer disconnects)."""
        return bytes(self._buffer)
