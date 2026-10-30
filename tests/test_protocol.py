"""Pure wire-format tests: command blocks, upload framing, slot/gesture/rhythm layouts."""
import pytest

from maskapi import protocol as P


def test_plaintext_layout():
    assert P.plaintext("LIGHT", 60) == b"\x06LIGHT\x3c" + bytes(9)
    assert P.plaintext("MODE", 1)[:6] == b"\x05MODE\x01"
    assert P.plaintext("LOOA")[:5] == b"\x04LOOA"
    assert P.plaintext("IMAG", 0)[:6] == b"\x05IMAG\x00"
    assert P.plaintext("FC", 1, 255, 0, 16)[:7] == b"\x06FC\x01\xff\x00\x10"
    assert P.plaintext("M", 1, 3)[:4] == b"\x03M\x01\x03"


def test_encrypt_roundtrip_and_known_vector():
    pkt = P.command("MODE", 1)
    assert len(pkt) == 16 and pkt != P.plaintext("MODE", 1)
    assert P.decrypt(pkt) == P.plaintext("MODE", 1)
    # same bytes the original mask_upload.cmd() produced
    from Crypto.Cipher import AES
    assert pkt == AES.new(bytes.fromhex("32672f7974ad43451d9c6c894a0e8764"), AES.MODE_ECB).encrypt(
        b"\x05MODE\x01".ljust(16, b"\x00"))


def test_notify_decode():
    from maskapi.fake import notify_packet
    assert P.decode_notify(notify_packet("DATSOK")) == "DATSOK"
    assert P.notify_body(notify_packet(b"CHEC\x03")) == b"CHEC\x03"
    assert P.decode_notify(notify_packet(b"CHEC\x03")) == "CHEC 03"


def test_limits():
    with pytest.raises(ValueError):
        P.plaintext("X" * 16)
    with pytest.raises(ValueError):
        P.plaintext("LIGHT", 256)
    with pytest.raises(ValueError):
        P.pad(bytes(17))


def test_dats_text_and_diy():
    assert P.dats_text(220, 88, 0)[:10] == b"\x09DATS\x00\xdc\x00\x58\x00"
    assert P.dats_diy(8004, 3)[:10] == b"\x09DATS\x1f\x44\x00\x03\x01"
    with pytest.raises(ValueError):
        P.dats_diy(8004, 21)
    with pytest.raises(ValueError):
        P.dats_text(70000, 0)


def test_datcp():
    assert P.datcp()[:6] == b"\x05DATCP"
    assert P.datcp_diy(0x01020304)[:10] == b"\x09DATCP\x01\x02\x03\x04"
    assert P.datcp_diy()[0] == 9


def test_chunks():
    payload = bytes(range(250))
    pkts = list(P.chunks(payload))
    assert [len(p) for p in pkts] == [100, 100, 56]
    assert pkts[0][:2] == bytes([99, 0]) and pkts[2][:2] == bytes([55, 2])
    assert b"".join(p[2:] for p in pkts) == payload
    big = list(P.chunks(bytes(98 * 300)))
    assert big[256][1] == 0  # index wraps at 0xFF


def test_play_dele():
    assert P.play([1])[:7] == b"\x06PLAY\x01\x01"
    assert P.play([1, 2, 3])[:9] == b"\x08PLAY\x03\x01\x02\x03"
    assert P.dele(list(range(1, 11)))[0] == 15
    with pytest.raises(ValueError):
        P.play(list(range(1, 12)))
    with pytest.raises(ValueError):
        P.play([])
    with pytest.raises(ValueError):
        P.dele([0])


def test_face():
    assert P.face(False, False, 0)[:9] == b"\x08FACE\x00\x00\x00\x00"
    assert P.face(True, True, P.SELECT_ALL, [1, 2])[:9] == b"\x08FACE\x01\x01\x00\x00"
    assert P.face(True, False, P.SELECT_DIY, [1, 2, 3])[:12] == b"\x0bFACE\x01\x00\x02\x03\x01\x02\x03"
    assert P.face(True, False, P.SELECT_BUILTIN, range(7))[0] == 15
    with pytest.raises(ValueError):
        P.face(True, False, P.SELECT_DIY, range(8))


def test_rhythm():
    f = P.rhythm(2, [9, 1] + [0] * 22)
    assert len(f) == 16 and f[:3] == bytes([0x0F, 2, 0x91])
    assert P.rhythm(0, [5])[2] == 0x50
    with pytest.raises(ValueError):
        P.rhythm(5, [])
    with pytest.raises(ValueError):
        P.rhythm(0, [10])


def test_describe():
    assert P.describe(P.plaintext("LIGHT", 60)) == "LIGHT 3c"
    assert P.describe(P.plaintext("DATCP")) == "DATCP"
