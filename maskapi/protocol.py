"""Shining Mask BLE wire format: pure functions, no I/O.

Commands are [len][ASCII name][args] zero-padded to 16 bytes and AES-128-ECB
encrypted. Notifications decrypt the same way and carry [len][body].
Upload data goes in plaintext on the upload characteristic as [len+1][idx][part].
Layouts below follow the official app (cn.com.heaton.shiningmask 1.2.3).
"""
from __future__ import annotations

import time
from typing import Iterable, Iterator, Optional, Sequence, Union

from Crypto.Cipher import AES

KEY = bytes.fromhex("32672f7974ad43451d9c6c894a0e8764")
SERVICE = "0000fff0-0000-1000-8000-00805f9b34fb"
CMD_CHAR = "d44bc439-abfd-45a2-b575-925416129600"
NOTIFY_CHAR = "d44bc439-abfd-45a2-b575-925416129601"
UPLOAD_CHAR = "d44bc439-abfd-45a2-b575-92541612960a"
MUSIC_CHAR = "d44bc439-abfd-45a2-b575-92541612960b"
NAME_PREFIX = "MASK"
ROWS = 16
CHUNK = 98
BLOCK = 16
PANEL_W, PANEL_H = 46, 58
DIY_SLOTS = range(1, 21)
MAX_PER_PACKET = 10  # slots per short PLAY / DELE packet
MAX_PLAY = 20  # long PLAY form: 19 'PLAY' n + 10 slots, then [n-10] + the rest
PLAY_LONG_LEN = 19  # length byte of the long form's first packet (verified on the mask)
FACE_MAX_IDS = 7  # 15-byte body limit: 'FACE' + 4 header bytes + ids

# MODE values (from mask-go; 1 and 3 verified)
MODE_STEADY, MODE_BLINK, MODE_SCROLL_LEFT, MODE_SCROLL_RIGHT, MODE_STEADY2 = 1, 2, 3, 4, 5
# FACE select modes
SELECT_ALL, SELECT_BUILTIN, SELECT_DIY = 0, 1, 2

Arg = Union[int, bytes, bytearray, Iterable[int]]


def encrypt(plain: bytes) -> bytes:
    if len(plain) % BLOCK:
        raise ValueError("plaintext must be a multiple of 16 bytes")
    return AES.new(KEY, AES.MODE_ECB).encrypt(plain)


def decrypt(data: bytes) -> bytes:
    return AES.new(KEY, AES.MODE_ECB).decrypt(bytes(data))


def args_bytes(*args: Arg) -> bytes:
    """Flatten ints (0-255), bytes and int iterables into one byte string."""
    out = bytearray()
    for a in args:
        if isinstance(a, int):
            if not 0 <= a <= 255:
                raise ValueError(f"argument {a} out of byte range")
            out.append(a)
        else:
            out += bytes(a)
    return bytes(out)


def pad(plain: bytes) -> bytes:
    if len(plain) > BLOCK:
        raise ValueError("raw plaintext longer than 16 bytes")
    return bytes(plain).ljust(BLOCK, b"\x00")


def plaintext(name: str, *args: Arg) -> bytes:
    """Unencrypted, padded command block: [len][name][args] + zeros."""
    body = name.encode("ascii") + args_bytes(*args)
    if len(body) > BLOCK - 1:
        raise ValueError(f"command {name!r} too long ({len(body)} > 15 bytes)")
    return pad(bytes([len(body)]) + body)


def command(name: str, *args: Arg) -> bytes:
    """Encrypted 16-byte packet for the command characteristic."""
    return encrypt(plaintext(name, *args))


def raw(plain: bytes) -> bytes:
    """Pad arbitrary plaintext to 16 bytes and encrypt it as-is (no length byte added)."""
    return encrypt(pad(plain))


def notify_body(data: bytes) -> bytes:
    plain = decrypt(data)
    return plain[1 : 1 + min(plain[0], BLOCK - 1)]


def decode_notify(data: bytes) -> str:
    return fmt(notify_body(data))


def fmt(body: bytes) -> str:
    """Leading ASCII command name, then remaining bytes as hex: 'LIGHT 3c', 'CHEC 03'."""
    i = 0
    while i < len(body) and 0x41 <= body[i] <= 0x5A:
        i += 1
    return " ".join(filter(None, [body[:i].decode(), body[i:].hex(" ")]))


def describe(plain: bytes) -> str:
    """Human-readable form of a command plaintext block."""
    return fmt(plain[1 : 1 + min(plain[0], BLOCK - 1)]) or plain.hex()


# ---- upload --------------------------------------------------------------------------

def u16(n: int) -> bytes:
    if not 0 <= n <= 0xFFFF:
        raise ValueError(f"{n} does not fit in u16")
    return n.to_bytes(2, "big")


def dats_text(total_len: int, bitmap_len: int, flag: int = 0) -> bytes:
    """DATS plaintext for a 16-row bitmap upload: [u16 total][u16 bitmap_len][flag]."""
    return plaintext("DATS", u16(total_len), u16(bitmap_len), flag)


def dats_diy(total_len: int, slot: int) -> bytes:
    """DATS plaintext for a full-panel DIY image: [u16 total] 00 [slot] 01."""
    check_slot(slot)
    return plaintext("DATS", u16(total_len), 0, slot, 1)


def datcp(timestamp: Optional[int] = None) -> bytes:
    """DATCP plaintext. The DIY path appends u32 BE unix seconds; text sends none."""
    if timestamp is None:
        return plaintext("DATCP")
    return plaintext("DATCP", int(timestamp).to_bytes(4, "big"))


def datcp_diy(timestamp: Optional[int] = None) -> bytes:
    return datcp(int(time.time()) if timestamp is None else timestamp)


def chunks(payload: bytes, size: int = CHUNK) -> Iterator[bytes]:
    """Upload-characteristic packets: [len(part)+1][idx & 0xFF][part]."""
    for idx, off in enumerate(range(0, len(payload), size)):
        part = payload[off : off + size]
        yield bytes([len(part) + 1, idx & 0xFF]) + part


# ---- slots / built-ins / gesture / music ----------------------------------------------

def check_slot(slot: int) -> int:
    if slot not in DIY_SLOTS:
        raise ValueError(f"DIY slot {slot} out of range 1-20")
    return slot


def slot_list(name: str, slots: Sequence[int]) -> bytes:
    """PLAY / DELE plaintext: [n+5] name [n] [slots...], n <= 10."""
    slots = [check_slot(s) for s in slots]
    if not 1 <= len(slots) <= MAX_PER_PACKET:
        raise ValueError(f"{name} takes 1-{MAX_PER_PACKET} slots per packet, got {len(slots)}")
    return plaintext(name, len(slots), slots)


def play(slots: Sequence[int]) -> bytes:
    """Short PLAY (1-10 slots). Use play_packets for up to 20."""
    return slot_list("PLAY", slots)


def play_packets(slots: Sequence[int]) -> list:
    """PLAY plaintext blocks for 1-20 slots (verified on the mask, which answers PLAYOK).
    <=10: one block [n+5] 'PLAY' n slots. >10: [19] 'PLAY' n [first 10 slots], then a
    second block [n-10] [remaining slots], sent ~0.1 s later."""
    slots = [check_slot(s) for s in slots]
    if not 1 <= len(slots) <= MAX_PLAY:
        raise ValueError(f"PLAY takes 1-{MAX_PLAY} slots, got {len(slots)}")
    if len(slots) <= MAX_PER_PACKET:
        return [play(slots)]
    n = len(slots)
    first = bytes([PLAY_LONG_LEN]) + b"PLAY" + bytes([n]) + bytes(slots[:MAX_PER_PACKET])
    return [first, pad(bytes([n - MAX_PER_PACKET]) + bytes(slots[MAX_PER_PACKET:]))]


def dele(slots: Sequence[int]) -> bytes:
    return slot_list("DELE", slots)


def face(enable: bool, loop_random: bool = False, select: int = SELECT_ALL,
         ids: Sequence[int] = ()) -> bytes:
    """Gesture sensor config: FACE enable loop select count ids...
    Verified: 0c FACE 01 00 02 04 01 02 0c 0b -> FACEOK; off: 08 FACE 00 00 02 00 (no reply)."""
    if select not in (SELECT_ALL, SELECT_BUILTIN, SELECT_DIY):
        raise ValueError("select must be 0 (all), 1 (built-in) or 2 (DIY)")
    ids = [] if select == SELECT_ALL else list(ids)
    if len(ids) > FACE_MAX_IDS:
        raise ValueError(f"FACE takes at most {FACE_MAX_IDS} ids")
    return plaintext("FACE", int(enable), int(loop_random), select, len(ids), ids)


def rhythm(mode: int, bars: Sequence[int]) -> bytes:
    """Mic/rhythm plaintext for MUSIC_CHAR: [0x0F][mode 0-4][24 bars 0-9, 2 per byte, high nibble first]."""
    if not 0 <= mode <= 4:
        raise ValueError("rhythm mode must be 0-4")
    bars = list(bars) + [0] * (24 - len(bars))
    if len(bars) != 24 or any(not 0 <= b <= 9 for b in bars):
        raise ValueError("need up to 24 bar levels in 0-9")
    return pad(bytes([0x0F, mode]) + bytes((bars[i] << 4) | bars[i + 1] for i in range(0, 24, 2)))
