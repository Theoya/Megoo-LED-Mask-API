"""HTTP API tests with FastAPI's TestClient and the in-memory FakeTransport."""
import base64
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from maskapi import Mask
from maskapi import protocol as P
from maskapi.fake import FakeTransport
from maskapi.server import create_app


@pytest.fixture
def api(tmp_path):
    fake = FakeTransport()
    with TestClient(create_app(Mask(fake, gap=0, ack_timeout=0.5, retry_wait=0, play_gap=0,
                                    shows=tmp_path / "shows.json"))) as c:
        c.fake = fake
        yield c


def png(color=(255, 0, 0), size=(46, 58)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def gif(n=3) -> bytes:
    frames = [Image.new("RGB", (20, 20), (60 * i, 0, 0)) for i in range(n)]
    buf = io.BytesIO()
    frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:], duration=100)
    return buf.getvalue()


def described(api):
    return [P.describe(c) for c in api.fake.commands]


def test_status_and_connect(api):
    s = api.get("/status").json()
    assert s["connected"] is False
    assert api.post("/connect").json() == {"ok": True}
    s = api.get("/status").json()
    assert s["connected"] and s["name"] == "MASK-FAKE00"
    api.post("/disconnect")
    assert api.get("/status").json()["connected"] is False


def test_diy_multipart(api):
    r = api.post("/diy", files={"file": ("a.png", png(), "image/png")}, data={"slot": "3", "fit": "cover"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["slots"] == [3] and body["bytes"] == 8004 and body["played"]
    assert api.fake.slots[3][:3] == bytes([255, 0, 0])
    assert api.fake.playing == [3]
    assert Image.open(io.BytesIO(base64.b64decode(body["preview_png_base64"][0]))).size == (46, 58)


def test_diy_json_and_dry_run(api):
    b = base64.b64encode(png((0, 255, 0), (100, 50))).decode()
    r = api.post("/diy", json={"image_base64": b, "slot": 2, "play": False})
    assert r.status_code == 200 and r.json()["played"] is False
    assert 2 in api.fake.slots and api.fake.playing == []
    r = api.post("/diy", json={"image_base64": b, "slot": 9, "dry_run": True})
    assert r.json()["uploaded"] is False and 9 not in api.fake.slots


def test_diy_bad_input(api):
    assert api.post("/diy", json={"image_base64": "!!!"}).status_code == 400
    assert api.post("/diy", json={"slot": 1}).status_code == 422
    assert api.post("/diy", json={"image_base64": base64.b64encode(b"nope").decode()}).status_code == 400
    assert api.post("/diy", json={"image_base64": "aGk=", "slot": 21}).status_code == 422
    assert api.post("/diy", files={"other": ("a", b"x")}).status_code == 422


def test_animation_gif_and_frames(api):
    r = api.post("/animation", files={"file": ("a.gif", gif(4), "image/gif")}, data={"start_slot": "5"})
    assert r.status_code == 200, r.text
    assert r.json()["slots"] == [5, 6, 7, 8] and api.fake.playing == [5, 6, 7, 8]
    files = [("file", (f"{i}.png", png((0, 0, 50 * i)), "image/png")) for i in range(2)]
    assert api.post("/animation", files=files).json()["slots"] == [1, 2]
    frames = [base64.b64encode(png()).decode()] * 3
    r = api.post("/animation", json={"frames_base64": frames, "start_slot": 10, "play": False})
    assert r.json()["slots"] == [10, 11, 12]
    r = api.post("/animation", files={"file": ("a.gif", gif(15), "image/gif")}, data={"max_frames": "4"})
    assert r.json()["slots"] == [1, 2, 3, 4]
    assert api.post("/animation", json={"frames_base64": frames, "start_slot": 19}).status_code == 400


def test_diy_play_delete_count(api):
    for s in (1, 2):
        api.post("/diy", json={"image_base64": base64.b64encode(png()).decode(), "slot": s, "play": False})
    assert api.get("/diy/count").json() == {"ok": True, "count": 2}
    assert api.post("/diy/play", json={"slots": [1, 2]}).status_code == 200
    assert api.fake.playing == [1, 2]
    assert api.post("/diy/2/play").status_code == 200 and api.fake.playing == [2]
    assert api.request("DELETE", "/diy", json={"slots": [1]}).status_code == 200
    assert api.get("/diy/count").json()["count"] == 1
    assert api.post("/diy/play", json={"slots": list(range(1, 12))}).status_code == 200  # long PLAY
    assert api.fake.playing == list(range(1, 12))
    assert api.post("/diy/play", json={"slots": list(range(1, 22))}).status_code == 422
    assert api.post("/diy/play", json={"slots": []}).status_code == 422


def test_text_and_16row_image(api):
    r = api.post("/text", json={"text": "HI", "color": "00ff00", "brightness": 40})
    assert r.status_code == 200 and r.json()["mode"] == 1 and len(r.json()["preview"]) == 16
    assert described(api)[-2:] == ["MODE 01", "LIGHT 28"]
    r = api.post("/text", json={"text": "SCROLLING TEXT IS LONG", "speed": 3})
    assert r.json()["mode"] == 3 and described(api)[-1] == "SPEED 03"
    r = api.post("/image", files={"file": ("a.png", png(size=(44, 16)), "image/png")}, data={"threshold": "10"})
    assert r.status_code == 200 and r.json()["width"] == 44
    assert api.fake.completed[-1][:2] == b"\xff\xff"  # every row lit
    b = base64.b64encode(png(size=(88, 16))).decode()
    assert api.post("/image", json={"image_base64": b, "width": 0, "dry_run": True}).json()["width"] == 88
    assert api.post("/text", json={"text": "x", "color": "red"}).status_code == 422


def test_builtins_and_settings(api):
    assert api.post("/builtin/image/0").status_code == 200
    assert api.post("/builtin/animation/3", json={"extra": [1]}).status_code == 200
    assert api.post("/builtin/loop").status_code == 200
    api.post("/brightness", json={"level": 60})
    api.post("/mode", json={"mode": 2})
    api.post("/speed", json={"speed": 4})
    api.post("/color/foreground", json={"color": "#ff0000"})
    api.post("/color/background", json={"color": "000010", "enable": False})
    api.post("/text-mode", json={"enable": True, "mode": 1})
    assert described(api) == ["IMAG 00", "ANIM 03 01", "LOOA", "LIGHT 3c", "MODE 02", "SPEED 04",
                              "FC 01 ff 00 00", "BC 00 00 00 10", "M 01 01"]
    assert api.post("/brightness", json={"level": 0}).status_code == 422
    assert api.get("/status").json()["state"]["brightness"] == 60


def test_gesture(api):
    assert api.post("/gesture", json={"enable": True, "loop": "random", "select": "diy", "ids": [1, 2]}).status_code == 200
    assert api.post("/gesture", json={"enable": False}).status_code == 200
    assert described(api) == ["FACE 01 01 02 02 01 02", "FACE 00 00 00 00"]  # off: no reply awaited
    assert api.post("/gesture", json={"select": "diy", "ids": list(range(8))}).status_code == 422


def test_rhythm(api):
    assert api.post("/rhythm", json={"mode": 1, "bars": [9, 0, 5]}).status_code == 200
    char, plain = api.fake.writes[-1]
    assert char == P.MUSIC_CHAR and plain[:4] == bytes([0x0F, 1, 0x90, 0x50])
    assert api.post("/rhythm", json={"mode": 1, "bars": [10]}).status_code == 400


def test_raw(api):
    r = api.post("/raw", json={"name": "light", "args": [10], "wait": 0})
    assert r.json()["plaintext"].startswith("064c49474854" + "0a")
    r = api.post("/raw", json={"name": "CHEC", "expect": "CHEC"})
    assert r.json()["notifications"] == ["CHEC 00"]
    r = api.post("/raw", json={"hex": "054d4f444503", "wait": 0})
    assert described(api)[-1] == "MODE 03"
    assert api.post("/raw", json={"name": "A", "hex": "00"}).status_code == 422
    assert api.post("/raw", json={"hex": "00" * 17}).status_code == 400


def test_generic_upload(api):
    payload = bytes(range(200))
    r = api.post("/upload", json={"payload_hex": payload.hex(), "kind": "diy", "slot": 7, "after": [["PLAY", 1, 7]]})
    assert r.status_code == 200 and api.fake.slots[7] == payload and api.fake.playing == [7]
    r = api.post("/upload", json={"payload_base64": base64.b64encode(payload).decode(), "bitmap_len": 80})
    assert api.fake.dats == (200, 80, 0)
    assert api.post("/upload", json={"bitmap_len": 1}).status_code == 400


def test_link_errors_map_to_http():
    fake = FakeTransport(fail_connects=99)
    with TestClient(create_app(Mask(fake, gap=0, retry_wait=0))) as c:
        r = c.post("/brightness", json={"level": 5})
        assert r.status_code == 503 and "TransportError" in r.json()["detail"]
    fake = FakeTransport(error_on="PLAY")
    with TestClient(create_app(Mask(fake, gap=0))) as c:
        assert c.post("/diy/1/play").status_code == 502


def test_diy_avoid_eyes_query_param(api):
    r = api.post("/diy?avoid_eyes=true&margin=0", files={"file": ("a.png", png((255, 255, 255)), "image/png")})
    assert r.status_code == 200, r.text
    off = (10 * 58 + 17) * 3  # inside the image-left eye hole
    assert api.fake.slots[1][off : off + 3] == b"\x00\x00\x00"
    b = base64.b64encode(png((255, 255, 255))).decode()
    r = api.post("/diy?avoid_eyes=true", json={"image_base64": b, "slot": 2, "avoid_eyes": False})
    assert api.fake.slots[2][off : off + 3] == b"\xff\xff\xff"  # body wins over query
    assert api.post("/diy?valign=middle", json={"image_base64": b}).status_code == 422


def test_shows_endpoints(api):
    s = api.get("/shows").json()
    assert s["order"] == ["logo", "spiral", "session", "virgil"] and s["shows"]["virgil"]["slots"] == [11]
    files = [("file", (f"{i}.png", png((0, 0, 50 * i)), "image/png")) for i in range(3)]
    assert api.post("/shows/eyes", files=files, data={"start_slot": "5"}).status_code == 409  # overlap
    r = api.post("/shows/eyes", files=files, data={"start_slot": "5", "dry_run": "true", "force": "true"})
    assert r.json()["slots"] == [5, 6, 7] and r.json()["uploaded"] is False and 5 not in api.fake.slots
    r = api.post("/shows/eyes?force=true&avoid_eyes=true", files=files, data={"start_slot": "5"})
    assert r.status_code == 200, r.text
    assert r.json()["dropped"] == ["spiral"] and api.fake.playing == [5, 6, 7]
    r = api.post("/shows/blink", files={"file": ("a.gif", gif(4), "image/gif")}, data={"start_slot": "11", "force": "true"})
    assert r.json()["slots"] == [11, 12, 13, 14]
    assert api.post("/shows/logo/play").json() == {"ok": True, "show": "logo", "slots": [1]}
    assert api.post("/shows/nope/play").status_code == 404
    assert api.post("/next").json()["show"] == "eyes"
    assert api.post("/next").json()["show"] == "blink"
    assert api.get("/shows").json()["current"] == "blink"
    r = api.put("/shows/order", json={"order": ["blink", "logo"]})
    assert r.json()["order"] == ["blink", "logo", "eyes"]
    assert api.post("/gesture/our-shows").json()["ids"] == [11, 1, 5]
    assert described(api)[-1] == "FACE 01 00 02 03 0b 01 05"
    assert api.post("/shows/bad name/play").status_code == 404
    assert api.post("/shows/b@d", files=files).status_code == 422


def test_panel_endpoint(api):
    j = api.get("/panel?margin=2").json()
    assert j["width"] == 46 and j["height"] == 58 and len(j["eyes"]) == 2 and j["margin"] == 2
    assert len(j["rows"]) == 58 and j["rows"][17][10] == "E"
    assert api.get("/panel?margin=9").status_code == 422


def test_openapi(api):
    spec = api.get("/openapi.json").json()
    for path in ("/diy", "/animation", "/diy/play", "/diy/count", "/gesture", "/builtin/loop", "/text", "/raw"):
        assert path in spec["paths"]
    content = spec["paths"]["/diy"]["post"]["requestBody"]["content"]
    assert {"multipart/form-data", "application/json"} <= set(content)
