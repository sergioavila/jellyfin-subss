"""Extrae subtítulos de texto desde un MKV (mkvmerge/mkvextract, estilo MKV-Sub-Extractor)."""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger("subss")

# Codecs de texto que mkvmerge reporta (no PGS/VobSub).
_TEXT_MARKERS = (
    "subrip",
    "srt",
    "utf-8",
    "utf8",
    "s_text",
    "substation",
    "ass",
    "ssa",
    "webvtt",
    "vtt",
)
_IMAGE_MARKERS = ("pgs", "hdmv", "vobsub", "dvd", "sup", "bitmap")


def _run(cmd: list[str], *, timeout: float = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def _is_text_codec(codec: str) -> bool:
    c = (codec or "").lower()
    if any(x in c for x in _IMAGE_MARKERS):
        return False
    return any(x in c for x in _TEXT_MARKERS)


def _ext_for_codec(codec: str) -> str:
    c = (codec or "").lower()
    if any(x in c for x in ("ass", "ssa", "substation")):
        return ".ass"
    if "vtt" in c or "webvtt" in c:
        return ".vtt"
    return ".srt"


def list_text_subtitle_tracks(video: str) -> list[dict]:
    """Pistas de subtítulo de texto vía `mkvmerge -J`."""
    try:
        proc = _run(["mkvmerge", "-J", video], timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        log.warning("mkvmerge no disponible: %s", exc)
        return []
    if proc.returncode != 0 or not proc.stdout.strip():
        log.warning("mkvmerge -J falló: %s", (proc.stderr or "")[:200])
        return []
    try:
        ident = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return []
    out = []
    for track in ident.get("tracks") or []:
        if track.get("type") != "subtitles":
            continue
        codec = str(track.get("codec") or "")
        if not _is_text_codec(codec):
            continue
        props = track.get("properties") or {}
        out.append(
            {
                "id": int(track["id"]),
                "codec": codec,
                "language": props.get("language") or props.get("language_ietf") or "",
                "name": props.get("track_name") or "",
                "forced": bool(props.get("forced_track")),
            }
        )
    return out


def _track_matches(track: dict, wanted: str) -> bool:
    from app.logic import is_forced, stream_matches_lang

    probe = {
        "Type": "Subtitle",
        "Language": track.get("language") or "",
        "DisplayTitle": track.get("name") or "",
        "IsForced": track.get("forced"),
    }
    if is_forced(probe):
        return False
    return stream_matches_lang(probe, wanted)


def _to_srt(path: Path) -> str:
    if path.suffix.lower() == ".srt":
        text = path.read_text(encoding="utf-8", errors="replace")
        return text if text.strip() else ""
    # ASS/VTT → SRT con ffmpeg (ya en la imagen)
    srt = path.with_suffix(".srt")
    try:
        proc = _run(["ffmpeg", "-y", "-i", str(path), str(srt)], timeout=120)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        log.warning("ffmpeg convert skip: %s", exc)
        return ""
    if proc.returncode != 0 or not srt.is_file():
        log.warning("ffmpeg → srt falló: %s", (proc.stderr or "")[-200:])
        return ""
    text = srt.read_text(encoding="utf-8", errors="replace")
    srt.unlink(missing_ok=True)
    return text if text.strip() else ""


def extract_srt_for_lang(video: str, language: str) -> str | None:
    """
    Extrae la pista de texto que matchea `language` del MKV.
    Devuelve SRT o None si no hay pista de texto / tools / archivo.
    """
    path = Path(video)
    if not path.is_file():
        return None
    tracks = [t for t in list_text_subtitle_tracks(str(path)) if _track_matches(t, language)]
    if not tracks:
        return None
    track = tracks[-1]  # última = suele ser la más específica / recién añadida
    tid = track["id"]
    ext = _ext_for_codec(track["codec"])
    with tempfile.TemporaryDirectory(prefix="subss-mkv-") as tmp:
        out = Path(tmp) / f"track{tid}{ext}"
        spec = f"{tid}:{out}"
        try:
            # Misma idea que Darkfall48/MKV-Sub-Extractor: mkvextract tracks TID:path
            proc = _run(["mkvextract", "tracks", str(path), spec], timeout=300)
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            log.warning("mkvextract skip: %s", exc)
            return None
        if proc.returncode != 0 or not out.is_file():
            log.warning("mkvextract falló track %s: %s", tid, (proc.stderr or "")[:200])
            return None
        text = _to_srt(out)
        if text:
            log.info("extraído MKV track %s (%s) → %s", tid, track.get("codec"), language)
            return text
    return None
