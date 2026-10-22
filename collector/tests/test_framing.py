"""RFC 6587 TCP framing."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from arcilogger_collector.framing import FrameDecoder, FramingError

MESSAGES = [b"<189>date=2026-09-23 a=1", b"<30>2026-09-23T09:38:26+03:00 CORE-R1 dhcp,info x"]


def _octet(message: bytes) -> bytes:
    return str(len(message)).encode() + b" " + message


def test_octet_counted_frames() -> None:
    decoder = FrameDecoder(65536)
    assert decoder.feed(b"".join(_octet(m) for m in MESSAGES)) == MESSAGES


def test_lf_framed_messages_keep_carriage_returns() -> None:
    decoder = FrameDecoder(65536)
    assert decoder.feed(b"<1>a\n<2>b\r\n<3>c") == [b"<1>a", b"<2>b\r"]
    assert decoder.pending() == b"<3>c"


def test_mixed_framings_on_one_connection() -> None:
    decoder = FrameDecoder(65536)
    assert decoder.feed(_octet(b"<1>x y") + b"<2>lf\n" + _octet(b"<3>z")) == [
        b"<1>x y",
        b"<2>lf",
        b"<3>z",
    ]


@given(st.lists(st.integers(min_value=1, max_value=40), max_size=20))
def test_any_split_yields_the_same_messages(cuts: list[int]) -> None:
    stream = b"".join(_octet(m) for m in MESSAGES) + b"<5>tail one\n<6>tail two\n"
    decoder = FrameDecoder(65536)
    out: list[bytes] = []
    position = 0
    for cut in cuts:
        out += decoder.feed(stream[position : position + cut])
        position += cut
    out += decoder.feed(stream[position:])
    assert out == [*MESSAGES, b"<5>tail one", b"<6>tail two"]


def test_oversized_lf_message_is_split_not_lost() -> None:
    decoder = FrameDecoder(1024)
    data = b"<1>" + b"a" * 2500
    out = decoder.feed(data)
    assert b"".join(out) + decoder.pending() == data
    assert all(len(chunk) <= 1024 for chunk in out)


@pytest.mark.parametrize("data", [b"99999999999999 x", b"70000 " + b"a" * 10])
def test_bad_octet_counts_are_framing_errors(data: bytes) -> None:
    with pytest.raises(FramingError):
        FrameDecoder(65536).feed(data)


def test_incomplete_octet_frame_waits_for_more() -> None:
    decoder = FrameDecoder(65536)
    assert decoder.feed(b"8 <1>ab") == []
    assert decoder.feed(b"cde") == [b"<1>abcde"]
