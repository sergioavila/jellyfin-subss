from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app import catalog, db, worker
from app.jellyfin import Jellyfin, JellyfinError
from app.logic import build_library, has_language, normalize_langs, wanted_langs
from app.providers import Hub, ProviderError

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
log = logging.getLogger("subss")

DATA_DIR = os.environ.get("DATA_DIR", str(Path(__file__).resolve().parent.parent / "data"))
STATIC = Path(__file__).resolve().parent / "static"
scheduler = BackgroundScheduler()
_server: dict | None = None
_server_at = 0.0
_pinging = threading.Lock()
_library: list | None = None
_library_at = 0.0
_library_lock = threading.Lock()


def _hours() -> int:
    try:
        return max(1, int(db.get_settings().get("scan_hours") or 6))
    except Exception:
        return 6


def _reschedule() -> None:
    scheduler.remove_all_jobs()
    scheduler.add_job(worker.start_background, "interval", hours=_hours(), id="scan", replace_existing=True)


def _remember_server(info: dict | None) -> dict | None:
    global _server, _server_at
    _server = info
    _server_at = time.time()
    return _server


def _ping_jellyfin(timeout: float = 3.0) -> dict | None:
    settings = db.get_settings()
    if not settings["jellyfin_url"] or not settings["api_key"]:
        return _remember_server(None)
    jf = Jellyfin(settings["jellyfin_url"], settings["api_key"], timeout=timeout)
    try:
        info = jf.ping()
        return _remember_server({"ServerName": info.get("ServerName"), "Version": info.get("Version")})
    except JellyfinError as exc:
        return _remember_server({"error": str(exc)})
    finally:
        jf.close()


def _server_view() -> dict | None:
    stale = time.time() - _server_at > 60
    if stale and _pinging.acquire(blocking=False):
        def _bg():
            try:
                _ping_jellyfin()
            finally:
                _pinging.release()

        threading.Thread(target=_bg, daemon=True).start()
    return _server


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.connect(DATA_DIR)
    scheduler.start()
    _reschedule()
    threading.Thread(target=_ping_jellyfin, daemon=True).start()
    yield
    if scheduler.running:
        scheduler.shutdown(wait=False)


app = FastAPI(title="Subss", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _jf() -> Jellyfin:
    settings = db.get_settings()
    if not settings["jellyfin_url"] or not settings["api_key"]:
        raise HTTPException(400, "Configura Jellyfin en Ajustes")
    return Jellyfin(settings["jellyfin_url"], settings["api_key"])


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/state")
def state():
    settings = db.get_settings()
    masked = db.mask_settings(settings)
    job = db.latest_job()
    return {
        "settings": masked,
        "server": _server_view(),
        "job": job,
        "worker": worker.status(),
        "events": db.recent_events(),
        "missing": [],
        "review": db.review_queue(),
        "catalog": catalog.PROVIDERS,
    }


def _forget_library() -> None:
    global _library, _library_at
    with _library_lock:
        _library = None
        _library_at = 0.0


@app.get("/api/library")
def library(fresh: int = 0):
    global _library, _library_at
    settings = db.get_settings()
    langs = wanted_langs(settings)
    with _library_lock:
        if not fresh and _library is not None and time.time() - _library_at < 60:
            return _library
    jf = Jellyfin(settings["jellyfin_url"], settings["api_key"], timeout=60.0) if settings["jellyfin_url"] and settings["api_key"] else None
    if jf is None:
        raise HTTPException(400, "Configura Jellyfin en Ajustes")
    try:
        cats = build_library(list(jf.iter_videos()), langs, jf.media_folders())
        for cat in cats:
            for group in cat["groups"]:
                gid = group.pop("image_id", None) or group.get("id")
                group["image"] = jf.image_path(gid) if gid else ""
        with _library_lock:
            _library = cats
            _library_at = time.time()
        return cats
    except JellyfinError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        jf.close()


@app.post("/api/settings")
def save_settings(payload: dict):
    allowed = {
        "jellyfin_url",
        "api_key",
        "language",
        "languages",
        "scan_hours",
        "autosync",
        "prefer_hash",
        "include_hi",
        "rate_limit_s",
        "path_maps",
        "os_enabled",
        "os_username",
        "os_password",
        "os_api_key",
        "os_use_hash",
        "podnapisi_enabled",
        "enabled_providers",
        "provider_creds",
        "gemini_api_key",
        "tmdb_api_key",
    }
    patch = {k: payload[k] for k in allowed if k in payload}
    if "languages" in patch and not normalize_langs(patch.get("languages")):
        raise HTTPException(400, "Marca al menos un idioma")
    saved = db.mask_settings(db.save_settings(patch))
    _forget_library()
    if scheduler.running:
        _reschedule()
    return saved


@app.post("/api/test")
@app.post("/api/test/jellyfin")
def test_jellyfin():
    info = _ping_jellyfin(timeout=8.0)
    if not info:
        raise HTTPException(400, "Configura Jellyfin en Ajustes")
    if info.get("error"):
        raise HTTPException(400, info["error"])
    return {"ok": True, **info}


@app.get("/api/providers")
def providers():
    return catalog.PROVIDERS


@app.post("/api/test/opensubtitles")
def test_opensubtitles():
    return test_provider("opensubtitlescom")


@app.post("/api/test/provider/{pid}")
def test_provider(pid: str):
    if pid == "opensubtitles":
        pid = "opensubtitlescom"
    settings = db.get_settings()
    hub = Hub(settings)
    try:
        return hub.test(pid)
    except ProviderError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        hub.close()


@app.post("/api/scan")
async def scan(request: Request):
    payload = {}
    if "application/json" in (request.headers.get("content-type") or ""):
        try:
            payload = await request.json()
        except Exception:
            payload = {}
    if isinstance(payload, dict) and "ids" in payload:
        ids = [str(x) for x in (payload.get("ids") or []) if x]
        if not ids:
            raise HTTPException(400, "Marca películas o capítulos para escanear")
        worker.start_background(ids)
    else:
        worker.start_background()
    return {"ok": True}


@app.post("/api/retry")
async def retry(request: Request):
    payload = {}
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    item_id = str((payload or {}).get("id") or "")
    language = str((payload or {}).get("language") or "")
    if not item_id:
        raise HTTPException(400, "Falta el título")
    try:
        worker.start_retry(item_id, language)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


@app.post("/api/translate")
async def translate(request: Request):
    payload = {}
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    ids = [str(x) for x in (payload.get("ids") or []) if x]
    source = str(payload.get("source") or "").strip()
    target = str(payload.get("target") or "").strip()
    if not ids:
        raise HTTPException(400, "Marca películas o capítulos para traducir")
    if not source or not target:
        raise HTTPException(400, "Elige idioma de origen y destino")
    settings = db.get_settings()
    if not (settings.get("gemini_api_key") or "").strip():
        raise HTTPException(400, "Configura la API key de Gemini en Ajustes → Traducir")
    try:
        worker.start_translate(ids, source, target)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


def _play_lang(item: dict, requested: str, settings: dict) -> str:
    if requested:
        return requested
    for lang in reversed(wanted_langs(settings)):
        if has_language(item.get("MediaStreams"), lang):
            return lang
    return wanted_langs(settings)[0]


@app.get("/api/play/{item_id}")
def play(item_id: str, lang: str = ""):
    settings = db.get_settings()
    jf = _jf()
    try:
        item = jf.get_item(item_id)
        lang = _play_lang(item, lang, settings)
        idx = jf.subtitle_stream_index(item, lang)
        hls = "/jf" + jf.playback_url(item_id, idx)
        vtt = ""
        if idx is not None:
            source = (item.get("MediaSources") or [{}])[0]
            mid = source.get("Id") or item_id
            vtt = f"/jf/Videos/{item_id}/{mid}/Subtitles/{idx}/Stream.vtt"
        runtime = int((item.get("RunTimeTicks") or 0) / 10_000_000)
        start = int(runtime * 0.12) if runtime else 0
        return {"title": item.get("Name"), "hls": hls, "vtt": vtt, "start_s": start, "lang": lang}
    except JellyfinError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        jf.close()


@app.post("/api/review/{item_id}/keep")
def keep(item_id: str, lang: str = ""):
    db.clear_review(item_id, lang or None)
    return {"ok": True}


@app.post("/api/review/{item_id}/drop")
def drop(item_id: str, lang: str = ""):
    settings = db.get_settings()
    jf = _jf()
    try:
        item = jf.get_item(item_id)
        lang = _play_lang(item, lang, settings)
        idx = jf.subtitle_stream_index(item, lang)
        if idx is None:
            raise HTTPException(404, "No hay subtítulo para borrar")
        jf.delete_subtitle(item_id, idx)
        db.clear_review(item_id, lang or None)
        db.add_event(
            item_id=item_id,
            title=item.get("Name") or item_id,
            action="dropped",
            detail="Rechazado en revisión",
            language=lang,
        )
        return {"ok": True}
    except JellyfinError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        jf.close()


@app.api_route("/jf/{path:path}", methods=["GET", "HEAD"])
def jellyfin_proxy(path: str, request: Request):
    settings = db.get_settings()
    jf = Jellyfin(settings["jellyfin_url"], settings["api_key"], timeout=60.0)
    extra = {}
    if rng := request.headers.get("range"):
        extra["Range"] = rng
    suffix = f"?{request.query_params}" if request.query_params else ""
    try:
        upstream = jf.stream_bytes(path + suffix, extra)
    except JellyfinError as exc:
        jf.close()
        raise HTTPException(502, str(exc)) from exc

    headers = {
        k: v
        for k, v in upstream.headers.items()
        if k.lower() in {"content-type", "content-length", "accept-ranges", "content-range"}
    }

    def body():
        try:
            yield from upstream.iter_bytes()
        finally:
            upstream.close()
            jf.close()

    return StreamingResponse(body(), status_code=upstream.status_code, headers=headers)


@app.exception_handler(HTTPException)
def http_exc(_req, exc: HTTPException):
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
