"""Import a shader directly from shadertoy.com (paste a link or a bare ID).

Two layers, kept independent of Qt so they're unit-testable without a
running application:

- `parse_shader_id_or_url` / `fetch_shader`: turn whatever the user pasted
  into a shader ID, then call the official shadertoy.com JSON API.
- `build_project_data`: translate the API's response shape into exactly
  the dict shape `MainWindow._apply_project_dict` already knows how to
  load (the same shape our own `.json` project files use — see
  `MainWindow._on_save_project`), so importing a Shadertoy shader reuses
  the *existing* project-loading code path start to finish rather than
  duplicating it.

Everything a Shadertoy shader can reference that this engine doesn't
support (Sound passes — no audio input at all; `video`/`webcam`/`music`/
`musicstream`/`mic`/`volume` iChannel inputs — see the ROADMAP's own notes
on what's still missing) is skipped rather than raising: the caller gets
back a best-effort project plus a list of human-readable warnings to show,
so a shader using one unsupported feature still imports everything else
instead of failing outright.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API_BASE = "https://www.shadertoy.com/api/v1/shaders/"
MEDIA_BASE = "https://www.shadertoy.com"
_REQUEST_TIMEOUT_S = 15.0

# A Shadertoy shader ID is always exactly 6 alphanumeric characters
# (case-sensitive), e.g. "XsBXWt". Accepted as-is, or extracted from a
# full view/embed URL with or without a scheme/query string.
_BARE_ID_RE = re.compile(r"^[A-Za-z0-9]{6}$")
_URL_ID_RE = re.compile(r"shadertoy\.com/(?:view|embed)(?:\.js)?/([A-Za-z0-9]{6})")

# Trailing standalone "A".."D" in a buffer renderpass' display name (the
# usual "Buf A"/"Buffer A" Shadertoy shows), used to pick which of our own
# 4 buffer slots a given renderpass maps to. See `_classify_buffer_passes`
# for the fallback used when a name doesn't match this.
_BUFFER_LETTER_RE = re.compile(r"\b([ABCD])\b\s*$")


class ShadertoyImportError(Exception):
    """Anything that stops an import before there's a shader to show:
    an unrecognised link/ID, a network failure, or an API-level error
    (bad key, shader not found, shader private/unlisted)."""


def parse_shader_id_or_url(text: str) -> str | None:
    """Extracts a 6-character shader ID from a pasted link or bare ID.
    Returns None if nothing recognisable is found (the caller is
    responsible for telling the user)."""
    text = text.strip()
    if not text:
        return None
    match = _URL_ID_RE.search(text)
    if match:
        return match.group(1)
    if _BARE_ID_RE.match(text):
        return text
    return None


def fetch_shader(shader_id: str, api_key: str) -> dict:
    """Calls the shadertoy.com JSON API and returns the `"Shader"` object
    (renderpasses, info, ...). Raises `ShadertoyImportError` with a
    French, user-facing message on any failure — network, HTTP, or an
    `{"Error": "..."}` payload the API itself returns for a bad key or an
    unknown/private shader ID."""
    url = f"{API_BASE}{urllib.parse.quote(shader_id)}?key={urllib.parse.quote(api_key)}"
    try:
        with urllib.request.urlopen(url, timeout=_REQUEST_TIMEOUT_S) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise ShadertoyImportError(
            f"Le serveur shadertoy.com a répondu une erreur ({exc.code})."
        ) from exc
    except urllib.error.URLError as exc:
        raise ShadertoyImportError(
            f"Impossible de contacter shadertoy.com : {exc.reason}"
        ) from exc
    except TimeoutError as exc:
        raise ShadertoyImportError("Délai d'attente dépassé en contactant shadertoy.com.") from exc

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ShadertoyImportError("Réponse de shadertoy.com illisible (pas du JSON valide).") from exc

    if isinstance(payload, dict) and payload.get("Error"):
        # The API's own way of reporting a bad key, an unknown ID, or a
        # private/unlisted shader — all as HTTP 200 with this field set,
        # so it has to be checked explicitly rather than relying on an
        # HTTPError above.
        raise ShadertoyImportError(f"shadertoy.com : {payload['Error']}")
    if not isinstance(payload, dict) or not isinstance(payload.get("Shader"), dict):
        raise ShadertoyImportError("Réponse de shadertoy.com inattendue (pas de shader dedans).")
    return payload["Shader"]


def _classify_buffer_passes(renderpasses: list[dict]) -> dict[int, int]:
    """Maps each `type == "buffer"` renderpass' index in `renderpasses` to
    a 0-3 buffer-letter index (A=0..D=3, matching `engine_bridge.
    BUFFER_PASSES`'s order and the `"ABCD"[value]` convention already used
    by `ichannel_panel.py`). Prefers a trailing standalone letter in the
    pass' `name` field (Shadertoy's usual "Buf A"/"Buffer A" naming);
    falls back to assigning unclaimed letters in declaration order for any
    pass whose name doesn't parse that way, since the API is understood to
    always return renderpasses in the order they render — a safe fallback
    even if the exact display string ever turns out to differ from what's
    matched here."""
    result: dict[int, int] = {}
    claimed: set[int] = set()
    for i, rp in enumerate(renderpasses):
        if rp.get("type") != "buffer":
            continue
        match = _BUFFER_LETTER_RE.search((rp.get("name") or "").strip())
        if match:
            idx = "ABCD".index(match.group(1))
            if idx not in claimed:
                result[i] = idx
                claimed.add(idx)
    for i, rp in enumerate(renderpasses):
        if rp.get("type") != "buffer" or i in result:
            continue
        for idx in range(4):
            if idx not in claimed:
                result[i] = idx
                claimed.add(idx)
                break
    return result


def _cubemap_face_urls(src: str) -> list[str]:
    """Best-effort: shadertoy.com names a cubemap's 6 face files by
    appending "_1".."_5" before the extension of the face-0 file given as
    `src` (face 0 itself carries no suffix). This is the documented
    behaviour of shadertoy.com's asset naming, but it could not be
    exercised against a live API response in this environment (see the
    ROADMAP entry — no network egress to shadertoy.com from this sandbox)
    — if face order/naming ever turns out wrong for some shader, this is
    the first place to check."""
    if not src:
        return [""] * 6
    stem, sep, ext = src.rpartition(".")
    if not sep:
        stem, ext = src, ""
    faces = [src]
    for i in range(1, 6):
        faces.append(f"{stem}_{i}.{ext}" if ext else f"{stem}_{i}")
    return faces


def _download_media(src: str, cache_dir: Path) -> str:
    """Downloads a shadertoy.com media path (e.g. `/media/a/xxxx.jpg`)
    into `cache_dir`, returning the local file path as a string (the same
    shape `_ChannelSlot`/`Engine.set_ichannel_texture` already expect from
    the "browse for a file" flow). Re-download is skipped if a same-named
    file is already cached — shadertoy.com media paths are content-addressed
    and effectively immutable, so this is safe and avoids re-fetching the
    same texture on every import of shaders that share it."""
    if not src:
        raise ShadertoyImportError("chemin de média manquant.")
    url = urllib.parse.urljoin(MEDIA_BASE, src)
    # Flatten the path into a single safe filename rather than mirroring
    # shadertoy.com's directory structure under cache_dir.
    filename = re.sub(r"[^A-Za-z0-9_.-]", "_", src.lstrip("/"))
    cache_dir.mkdir(parents=True, exist_ok=True)
    dest = cache_dir / filename
    if dest.exists() and dest.stat().st_size > 0:
        return str(dest)
    try:
        with urllib.request.urlopen(url, timeout=_REQUEST_TIMEOUT_S) as response:
            data = response.read()
    except urllib.error.HTTPError as exc:
        raise ShadertoyImportError(f"téléchargement échoué ({exc.code}) pour {src}") from exc
    except urllib.error.URLError as exc:
        raise ShadertoyImportError(f"téléchargement impossible pour {src} : {exc.reason}") from exc
    dest.write_bytes(data)
    return str(dest)


def build_project_data(
    shader: dict,
    cache_dir: Path,
    *,
    image_pass: object | None = None,
    buffer_passes: tuple | None = None,
) -> tuple[dict, list[str]]:
    """Translates a shadertoy.com `"Shader"` API object into the same
    dict shape `MainWindow._apply_project_dict` loads from a `.json`
    project file (`{"format", "common", "passes", "ichannels",
    "sliders"}`), downloading any texture/cubemap media it references
    into `cache_dir` along the way.

    `image_pass`/`buffer_passes` default to `engine_bridge.PASS_IMAGE`/
    `engine_bridge.BUFFER_PASSES` — the real pass-key constants, imported
    lazily here rather than at module load time. Both can be overridden
    (e.g. with plain placeholder values) so this function, and everything
    else in this module, is testable without the compiled `shadertoy_engine`
    native extension: only the pyo3-exposed pass-key *values* come from
    it, nothing about *how* a Shadertoy response gets translated does.

    Returns `(project_data, warnings)` — `warnings` lists, in French,
    every pass or iChannel input that couldn't be carried over (a Sound
    pass, a `video`/`webcam`/`music`/`mic`/`volume` input, a media
    download that failed, ...), so an otherwise-good import isn't lost
    to one unsupported piece. Never raises for content the shader itself
    contains — only `fetch_shader` raises, for failures *before* there's
    a shader to translate.
    """
    if image_pass is None or buffer_passes is None:
        import engine_bridge  # native module; only needed for the real pass-key constants
        if image_pass is None:
            image_pass = engine_bridge.PASS_IMAGE
        if buffer_passes is None:
            buffer_passes = engine_bridge.BUFFER_PASSES

    warnings: list[str] = []
    renderpasses = shader.get("renderpass", []) or []

    buffer_letter_by_rp_index = _classify_buffer_passes(renderpasses)
    output_id_to_buffer_index: dict[str, int] = {}
    for i, idx in buffer_letter_by_rp_index.items():
        for out in renderpasses[i].get("outputs", []) or []:
            out_id = out.get("id")
            if out_id is not None:
                output_id_to_buffer_index[str(out_id)] = idx

    common_source = ""
    pass_sources: dict = {image_pass: "", **{p: "" for p in buffer_passes}}
    ichannels: dict[str, list[dict]] = {}

    for i, rp in enumerate(renderpasses):
        rp_type = rp.get("type")
        rp_name = (rp.get("name") or rp_type or "?").strip()
        code = rp.get("code") or ""

        if rp_type == "common":
            common_source = code
            continue
        if rp_type == "sound":
            warnings.append(
                "La passe Son a été ignorée (ce moteur n'a pas d'entrée audio, "
                "seulement un iSampleRate fixe)."
            )
            continue
        if rp_type == "image":
            pass_key = image_pass
        elif rp_type == "buffer":
            idx = buffer_letter_by_rp_index.get(i)
            if idx is None:
                warnings.append(f"Passe « {rp_name} » n'a pas pu être associée à un Buffer A-D, ignorée.")
                continue
            pass_key = buffer_passes[idx]
        else:
            warnings.append(f"Passe « {rp_name} » de type non supporté ({rp_type!r}), ignorée.")
            continue

        pass_sources[pass_key] = code

        channels: list[dict] = [{"kind": "empty", "value": None} for _ in range(4)]
        for inp in rp.get("inputs", []) or []:
            channel_idx = inp.get("channel")
            if not isinstance(channel_idx, int) or not (0 <= channel_idx < 4):
                continue
            ctype = inp.get("ctype")
            src = inp.get("src") or ""
            if ctype == "texture":
                try:
                    path = _download_media(src, cache_dir)
                except ShadertoyImportError as exc:
                    warnings.append(f"iChannel{channel_idx} de « {rp_name} » : {exc}")
                    continue
                channels[channel_idx] = {"kind": "image", "value": path}
            elif ctype == "cubemap":
                try:
                    faces = [_download_media(url, cache_dir) for url in _cubemap_face_urls(src)]
                except ShadertoyImportError as exc:
                    warnings.append(f"iChannel{channel_idx} de « {rp_name} » (cubemap) : {exc}")
                    continue
                channels[channel_idx] = {"kind": "cubemap", "value": faces}
            elif ctype == "buffer":
                target_idx = output_id_to_buffer_index.get(str(inp.get("id")))
                if target_idx is None:
                    warnings.append(
                        f"iChannel{channel_idx} de « {rp_name} » référence un buffer introuvable, ignoré."
                    )
                    continue
                channels[channel_idx] = {"kind": "buffer", "value": target_idx}
            elif ctype == "keyboard":
                channels[channel_idx] = {"kind": "keyboard", "value": None}
            else:
                # video / webcam / music / musicstream / mic / volume / any
                # future ctype this engine has no equivalent for yet.
                warnings.append(
                    f"iChannel{channel_idx} de « {rp_name} » : entrée « {ctype} » non supportée, laissée vide."
                )
        ichannels[str(pass_key)] = channels

    data = {
        "format": 3,
        "common": common_source,
        "passes": {str(k): v for k, v in pass_sources.items()},
        "ichannels": ichannels,
        "sliders": {},
    }
    return data, warnings
