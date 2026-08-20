"""Tests autonomes de `python_ui/shadertoy_import.py`.

N'a besoin ni de PySide6 ni du module natif `shadertoy_engine` (voir
`build_project_data`'s `image_pass`/`buffer_passes` overrides) — juste de
la stdlib, y compris pour simuler l'API shadertoy.com avec un petit
`http.server` local (le sandbox de développement n'a pas d'accès réseau
sortant vers shadertoy.com lui-même, voir la note à ce sujet dans
ROADMAP.md, mais un serveur sur 127.0.0.1 n'est pas concerné par cette
restriction : ce n'est pas du trafic sortant vers un domaine externe).
"""
import http.server
import json
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python_ui"))

import shadertoy_import as si  # noqa: E402


# ---- parse_shader_id_or_url -------------------------------------------

assert si.parse_shader_id_or_url("XsBXWt") == "XsBXWt"
assert si.parse_shader_id_or_url("  XsBXWt  ") == "XsBXWt"
assert si.parse_shader_id_or_url("https://www.shadertoy.com/view/XsBXWt") == "XsBXWt"
assert si.parse_shader_id_or_url("http://shadertoy.com/view/XsBXWt") == "XsBXWt"
assert si.parse_shader_id_or_url("shadertoy.com/view/XsBXWt") == "XsBXWt"
assert si.parse_shader_id_or_url("https://www.shadertoy.com/embed/XsBXWt?gui=true") == "XsBXWt"
assert si.parse_shader_id_or_url("") is None
assert si.parse_shader_id_or_url("not a shader link") is None
assert si.parse_shader_id_or_url("XsBXW") is None  # 5 chars, too short
assert si.parse_shader_id_or_url("XsBXWt1") is None  # 7 chars, too long
print("parse_shader_id_or_url: ok")


# ---- _cubemap_face_urls -------------------------------------------------

faces = si._cubemap_face_urls("/media/cube00/uffizi.png")
assert faces == [
    "/media/cube00/uffizi.png",
    "/media/cube00/uffizi_1.png",
    "/media/cube00/uffizi_2.png",
    "/media/cube00/uffizi_3.png",
    "/media/cube00/uffizi_4.png",
    "/media/cube00/uffizi_5.png",
], faces
assert si._cubemap_face_urls("") == [""] * 6
print("_cubemap_face_urls: ok")


# ---- _classify_buffer_passes --------------------------------------------

# Names parse cleanly -> letter taken from the name, regardless of order.
renderpasses_named = [
    {"type": "buffer", "name": "Buf B"},
    {"type": "image", "name": "Image"},
    {"type": "buffer", "name": "Buf A"},
]
assert si._classify_buffer_passes(renderpasses_named) == {0: 1, 2: 0}

# Unparseable names fall back to declaration-order assignment of
# whatever letters aren't already claimed by a parseable name.
renderpasses_mixed = [
    {"type": "buffer", "name": "Custom pass"},
    {"type": "buffer", "name": "Buf A"},
    {"type": "buffer", "name": "another one"},
]
result = si._classify_buffer_passes(renderpasses_mixed)
assert result[1] == 0, result  # "Buf A" claims letter A explicitly
assert result[0] == 1, result  # first unparseable name -> first free letter (B)
assert result[2] == 2, result  # second unparseable name -> next free letter (C)
print("_classify_buffer_passes: ok")


# ---- fetch_shader, against a local fake Shadertoy API -------------------

class _FakeApiHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence
        pass

    def do_GET(self):
        if "/shaders/badkey" in self.path:
            body = json.dumps({"Error": "invalid key"}).encode()
        elif "/shaders/missing" in self.path:
            body = json.dumps({"Error": "Shader not found"}).encode()
        elif "/shaders/goodid" in self.path:
            body = json.dumps({"Shader": {"info": {"id": "goodid"}, "renderpass": []}}).encode()
        elif self.path.startswith("/media/"):
            body = b"\x89PNGfakebytes"
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)


_server = http.server.HTTPServer(("127.0.0.1", 0), _FakeApiHandler)
_thread = threading.Thread(target=_server.serve_forever, daemon=True)
_thread.start()
_port = _server.server_address[1]

# Point the module at the fake server instead of the real shadertoy.com
# for the duration of these tests.
si.API_BASE = f"http://127.0.0.1:{_port}/api/v1/shaders/"
si.MEDIA_BASE = f"http://127.0.0.1:{_port}"

try:
    shader = si.fetch_shader("goodid", "anykey")
    assert shader == {"info": {"id": "goodid"}, "renderpass": []}, shader

    try:
        si.fetch_shader("badkey", "anykey")
        assert False, "expected ShadertoyImportError for an Error payload"
    except si.ShadertoyImportError as exc:
        assert "invalid key" in str(exc), exc

    try:
        si.fetch_shader("missing", "anykey")
        assert False, "expected ShadertoyImportError for an unknown shader"
    except si.ShadertoyImportError as exc:
        assert "not found" in str(exc), exc

    try:
        si.fetch_shader("nosuchroute", "anykey")
        assert False, "expected ShadertoyImportError for a non-JSON/404 response"
    except si.ShadertoyImportError:
        pass
    print("fetch_shader: ok")

    # ---- build_project_data --------------------------------------------

    import tempfile

    IMAGE, BUF_A, BUF_B, BUF_C, BUF_D = "IMAGE", "BUF_A", "BUF_B", "BUF_C", "BUF_D"

    shader = {
        "info": {"id": "abcdef", "name": "Test"},
        "renderpass": [
            {
                "type": "common",
                "name": "Common",
                "code": "float shared_helper(){return 1.;}",
                "inputs": [],
                "outputs": [],
            },
            {
                "type": "buffer",
                "name": "Buf A",
                "code": "void mainImage(out vec4 c,in vec2 p){c=vec4(1.);}",
                "inputs": [],
                "outputs": [{"id": "257"}],
            },
            {
                "type": "sound",
                "name": "Sound",
                "code": "vec2 mainSound(int s,float t){return vec2(0.);}",
                "inputs": [],
                "outputs": [],
            },
            {
                "type": "image",
                "name": "Image",
                "code": "void mainImage(out vec4 c,in vec2 p){c=texture(iChannel0,p);}",
                "inputs": [
                    {"channel": 0, "ctype": "buffer", "id": "257", "src": "/media/previz/buffer00.png"},
                    {"channel": 1, "ctype": "texture", "src": "/media/a/tex.jpg"},
                    {"channel": 2, "ctype": "keyboard", "src": "/media/a/keyboard.png"},
                    {"channel": 3, "ctype": "webcam", "src": ""},
                ],
                "outputs": [{"id": "37"}],
            },
        ],
    }

    with tempfile.TemporaryDirectory() as tmp:
        data, warnings = si.build_project_data(
            shader, Path(tmp), image_pass=IMAGE, buffer_passes=(BUF_A, BUF_B, BUF_C, BUF_D)
        )

        assert data["common"] == "float shared_helper(){return 1.;}"
        assert data["passes"][str(BUF_A)] == "void mainImage(out vec4 c,in vec2 p){c=vec4(1.);}"
        assert data["passes"][str(IMAGE)] == (
            "void mainImage(out vec4 c,in vec2 p){c=texture(iChannel0,p);}"
        )
        # Buffers B-D weren't in the shader -> present but empty, matching
        # what `_pass_sources`/`_on_new` already default unused passes to.
        assert data["passes"][str(BUF_B)] == ""
        assert data["passes"][str(BUF_C)] == ""
        assert data["passes"][str(BUF_D)] == ""

        image_channels = data["ichannels"][str(IMAGE)]
        assert image_channels[0] == {"kind": "buffer", "value": 0}, image_channels[0]  # -> Buffer A
        assert image_channels[1]["kind"] == "image"
        assert Path(image_channels[1]["value"]).exists()  # actually downloaded
        assert image_channels[2] == {"kind": "keyboard", "value": None}
        assert image_channels[3] == {"kind": "empty", "value": None}  # webcam unsupported

        assert any("Son" in w for w in warnings), warnings
        assert any("webcam" in w for w in warnings), warnings
        assert len(warnings) == 2, warnings

    print("build_project_data: ok")

    # A shader with an entirely unparseable buffer name still gets a slot
    # (fallback ordering), and a `ctype` this engine has no equivalent for
    # is dropped with a warning rather than raising.
    shader2 = {
        "renderpass": [
            {"type": "buffer", "name": "Custom", "code": "X", "inputs": [], "outputs": [{"id": "1"}]},
            {"type": "image", "name": "Image", "code": "Y", "inputs": [
                {"channel": 0, "ctype": "volume", "src": "/media/a/vol.bin"},
            ], "outputs": []},
        ]
    }
    with tempfile.TemporaryDirectory() as tmp:
        data2, warnings2 = si.build_project_data(
            shader2, Path(tmp), image_pass=IMAGE, buffer_passes=(BUF_A, BUF_B, BUF_C, BUF_D)
        )
        assert data2["passes"][str(BUF_A)] == "X"
        assert data2["ichannels"][str(IMAGE)][0] == {"kind": "empty", "value": None}
        assert any("volume" in w for w in warnings2), warnings2
    print("build_project_data (fallback naming + unknown ctype): ok")
finally:
    si.API_BASE = "https://www.shadertoy.com/api/v1/shaders/"
    si.MEDIA_BASE = "https://www.shadertoy.com"
    _server.shutdown()

print("\nAll shadertoy_import tests passed.")
