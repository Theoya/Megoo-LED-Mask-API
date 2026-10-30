"""In-memory fake mask for tests and `serve --fake`. No Bluetooth involved.

Decrypts command writes and acks like the real mask (DATSOK / REOK / DATCPOK,
PLAYOK incl. the long 2-packet PLAY, DELEOK, FACEOK, CHEC+count), keeping DIY slots in a dict.
fail_connects / fail_writes inject TransportErrors; error_on makes a command reply ERROR.
"""
from __future__ import annotations

import asyncio
from typing import Union

from . import protocol as P
from .transport import Transport, TransportError

ACKS = {"PLAY": "PLAYOK", "DELE": "DELEOK", "FACE": "FACEOK"}


def notify_packet(body: Union[str, bytes]) -> bytes:
    body = body.encode() if isinstance(body, str) else bytes(body)
    return P.encrypt(P.pad(bytes([len(body)]) + body))


class FakeTransport(Transport):
    def __init__(self, fail_connects: int = 0, fail_writes: int = 0, ack: bool = True,
                 replies: dict = None, error_on: str = None):
        self.name, self.address, self.rssi = "MASK-FAKE00", "00:00:00:00:00:00", -60
        self.fail_connects = fail_connects
        self.fail_writes = fail_writes
        self.ack = ack
        self.replies = replies or {}  # command name -> notify text
        self.error_on = error_on
        self.commands = []  # decrypted plaintext blocks
        self.uploads = []  # raw upload-char packets
        self.writes = []  # (char, decrypted-or-raw bytes) on any other characteristic
        self.connects = 0
        self.dats = None
        self.received = bytearray()
        self.completed = []  # payloads finished with DATCP
        self.slots = {}  # DIY slot -> payload
        self.playing = []
        self._play_pending = None  # (n, first 10 slots) after a long-form PLAY
        self._connected = False
        self._notify = None

    @property
    def is_connected(self):
        return self._connected

    def names(self):
        return [P.describe(c).split()[0] for c in self.commands]

    async def connect(self, on_notify, on_disconnect):
        self.connects += 1
        if self.fail_connects > 0:
            self.fail_connects -= 1
            raise TransportError("fake connect failure")
        self._notify = on_notify
        self._connected = True

    async def disconnect(self):
        self._connected = False

    def _reply(self, body):
        if self.ack and body:
            asyncio.get_running_loop().call_soon(self._notify, notify_packet(body))

    async def write(self, char, data):
        if not self._connected:
            raise TransportError("not connected")
        if self.fail_writes > 0:
            self.fail_writes -= 1
            self._connected = False
            raise TransportError("fake GATT error")
        if char == P.CMD_CHAR:
            self._command(P.decrypt(data))
        elif char == P.UPLOAD_CHAR:
            self.uploads.append(bytes(data))
            self.received += data[2 : 1 + data[0]]
            self._reply("REOK")
        else:
            self.writes.append((char, P.decrypt(data) if len(data) == 16 else bytes(data)))

    def _command(self, plain):
        self.commands.append(plain)
        if self._play_pending:  # second packet of a long PLAY: [n-10] slots...
            n, first = self._play_pending
            self._play_pending = None
            rest = list(plain[1 : 1 + plain[0]])
            if len(first) + len(rest) == n:
                self.playing = first + rest
                return self._reply("PLAYOK")
            return self._reply("ERROR")
        body = plain[1 : 1 + plain[0]]
        name = P.describe(plain).split()[0]
        if self.error_on and body.startswith(self.error_on.encode()):
            return self._reply("ERROR")
        if body.startswith(b"DATCP"):
            payload = bytes(self.received)
            self.completed.append(payload)
            if self.dats and self.dats[2] == 1:
                self.slots[self.dats[1]] = payload
            self._reply("DATCPOK")
        elif body.startswith(b"DATS"):
            # text: total, bitmap_len, flag; DIY: total, (00 << 8 | slot), 01
            self.dats = (int.from_bytes(body[4:6], "big"), int.from_bytes(body[6:8], "big"), body[8])
            self.received = bytearray()
            self._reply("DATSOK")
        elif body.startswith(b"CHEC"):
            self._reply(b"CHEC" + bytes([len(self.slots)]))
        elif body.startswith(b"PLAY"):
            if plain[0] == P.PLAY_LONG_LEN and body[4] > P.MAX_PER_PACKET:
                self._play_pending = (body[4], list(plain[6:16]))
                return
            self.playing = list(body[5 : 5 + body[4]])
            self._reply("PLAYOK")
        elif body.startswith(b"FACE") and not body[4]:
            pass  # the real mask sent nothing for FACE off
        elif body.startswith(b"DELE"):
            for s in body[5 : 5 + body[4]]:
                self.slots.pop(s, None)
            self._reply("DELEOK")
        else:
            self._reply(self.replies.get(name, ACKS.get(name)))
