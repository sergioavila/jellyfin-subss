from __future__ import annotations

import logging
import subprocess
import threading
import time
from pathlib import Path

from app import db
from app.jellyfin import Jellyfin, JellyfinError
from app.logic import has_language, map_path, missing_langs, pick_best, search_code, wanted_langs
from app.providers import Hub, ProviderError, file_movie_hash
from app.translate import context_for_item, jellyfin_lang_tag, translate_srt_text

log = logging.getLogger("subss")

_state = {
    "running": False,
    "phase": "idle",
    "current": "",
    "error": "",
    "queue": [],
}
_lock = threading.Lock()


def status() -> dict:
    with _lock:
        out = dict(_state)
        out["queue"] = [dict(row) for row in _state["queue"]]
        return out


def _set(**kwargs) -> None:
    with _lock:
        _state.update(kwargs)


def _qset(index: int, **kwargs) -> None:
    with _lock:
        if 0 <= index < len(_state["queue"]):
            row = dict(_state["queue"][index])
            row.update(kwargs)
            _state["queue"][index] = row


def ffsubsync(video: str, subtitle: str) -> bool:
    if not Path(video).exists() or not Path(subtitle).exists():
        return False
    out = str(Path(subtitle).with_suffix(".synced.srt"))
    try:
        # ponytail: ffsubsync is O(duration) CPU VAD; upgrade to alass if hard cases pile up
        result = subprocess.run(
            ["ffs", video, "-i", subtitle, "-o", out],
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        log.warning("ffsubsync skip: %s", exc)
        return False
    if result.returncode != 0 or not Path(out).exists():
        log.warning("ffsubsync failed: %s", result.stderr[-400:])
        return False
    Path(out).replace(subtitle)
    return True


def _item_title(item: dict) -> str:
    if (item.get("Type") or "") == "Episode":
        season = int(item.get("ParentIndexNumber") or 0)
        episode = int(item.get("IndexNumber") or 0)
        show = item.get("SeriesName") or ""
        name = item.get("Name") or item.get("Id")
        return f"{show} S{season:02d}E{episode:02d} · {name}".strip(" ·")
    return item.get("Name") or item["Id"]


def scan_once(item_ids: list[str] | None = None) -> None:
    if status()["running"]:
        raise RuntimeError("Ya hay un escaneo en curso")
    _set(running=True, phase="conectando", current="", error="", queue=[])
    settings = db.get_settings()
    job_id = db.start_job()
    counts = {"scanned": 0, "missing": 0, "downloaded": 0, "synced": 0, "failed": 0}
    jf: Jellyfin | None = None
    hub: Hub | None = None
    only = [i for i in (item_ids or []) if i]
    try:
        if not settings["jellyfin_url"] or not settings["api_key"]:
            raise JellyfinError("Falta la URL o la API key de Jellyfin")
        hub = Hub(settings)
        if not hub.enabled_ids():
            raise ProviderError("Activa al menos un proveedor de subtítulos")
        jf = Jellyfin(settings["jellyfin_url"], settings["api_key"])
        jf.ping()
        langs = wanted_langs(settings)
        pause = max(1, int(settings["rate_limit_s"]))
        videos = _load_videos(jf, only)
        jobs = []
        for item in videos:
            title = _item_title(item)
            pending = missing_langs(item.get("MediaStreams"), langs)
            if not pending:
                if only:
                    jobs.append(
                        {
                            "id": item["Id"],
                            "title": title,
                            "language": "",
                            "status": "ok",
                            "detail": "ya está",
                        }
                    )
                continue
            counts["missing"] += 1
            for lang in pending:
                jobs.append(
                    {
                        "id": item["Id"],
                        "title": title,
                        "language": lang,
                        "status": "pending",
                        "detail": "",
                        "provider": "",
                        "item": item,
                    }
                )
        _set(queue=[{k: v for k, v in job.items() if k != "item"} for job in jobs])
        last_id = ""
        for index, job in enumerate(jobs):
            item = job.get("item")
            title = job["title"]
            lang = job["language"]
            if job["status"] == "ok":
                counts["scanned"] += 1
                continue
            if item["Id"] != last_id:
                counts["scanned"] += 1
                last_id = item["Id"]
            _set(phase="buscando", current=f"{title} · {lang}")
            _qset(index, status="running")
            try:
                action, detail, hash_match, provider = _download_one(jf, item, settings, counts, hub, lang)
                _qset(index, status=action, detail=detail, hash_match=hash_match, provider=provider)
                if action == "error":
                    counts["failed"] += 1
            except Exception as exc:  # noqa: BLE001 — one bad title must not kill the job
                counts["failed"] += 1
                detail = f"{lang} · {exc}"[:400]
                db.add_event(item_id=item["Id"], title=title, action="error", detail=detail, language=lang)
                _qset(index, status="error", detail=detail, provider="")
                log.exception("item %s %s", title, lang)
            time.sleep(pause)
        db.finish_job(job_id, status="ok", message="", **counts)
        _set(phase="idle", current="", running=False)
    except Exception as exc:  # noqa: BLE001
        db.finish_job(job_id, status="error", message=str(exc)[:400], **counts)
        _set(phase="idle", current="", running=False, error=str(exc))
        raise
    finally:
        if jf:
            jf.close()
        if hub:
            hub.close()


def _load_videos(jf: Jellyfin, only: list[str]) -> list[dict]:
    if not only:
        return list(jf.iter_videos())
    # ponytail: GetItem 400s with extra fields on this server; walk once and filter
    wanted = set(only)
    found = {item["Id"]: item for item in jf.iter_videos() if item.get("Id") in wanted}
    return [found[iid] for iid in only if iid in found]


def _provider_names(errors: list[str]) -> str:
    names: list[str] = []
    for err in errors[:4]:
        name = err.split(":", 1)[0].strip()
        if name and name not in names:
            names.append(name)
    return " · ".join(names)


def _download_one(
    jf: Jellyfin, item: dict, settings: dict, counts: dict, hub: Hub, lang: str
) -> tuple[str, str, bool, str]:
    title = _item_title(item)
    video = map_path(item.get("Path"), settings.get("path_maps") or [])
    moviehash = file_movie_hash(video) if video else None
    candidates = hub.search(item, lang, moviehash)
    best = pick_best(candidates, include_hi=bool(settings["include_hi"]), wanted=lang)
    if not best:
        if hub.last_errors:
            provider = _provider_names(hub.last_errors)
            detail = f"{lang} · " + " · ".join(hub.last_errors[:4])
            action = "error"
        else:
            provider = ""
            detail = f"{lang} · ningún sitio devolvió este idioma"
            action = "empty"
        db.add_event(item_id=item["Id"], title=title, action=action, detail=detail[:400], language=lang)
        return action, detail[:400], False, provider
    _set(phase="descargando", current=title)
    try:
        raw, filename = hub.download(best)
    except Exception as exc:  # noqa: BLE001
        src = best.get("ProviderName") or best.get("provider") or "proveedor"
        detail = f"{lang} · {src}: {exc}"[:400]
        db.add_event(item_id=item["Id"], title=title, action="error", detail=detail, language=lang)
        return "error", detail, False, src
    fmt = Path(filename).suffix.lstrip(".").lower() or "srt"
    if fmt not in {"srt", "ass", "ssa", "vtt", "sub"}:
        fmt = "srt"
    synced = False
    hash_match = bool(best.get("IsHashMatch"))
    if settings["autosync"] and not hash_match and video and Path(video).exists():
        tmp = Path(video).with_name(Path(video).stem + ".subss-tmp.srt")
        tmp.write_bytes(raw)
        _set(phase="sincronizando", current=title)
        synced = ffsubsync(video, str(tmp))
        if synced:
            raw = tmp.read_bytes()
            counts["synced"] += 1
        tmp.unlink(missing_ok=True)
    lang_tag = best.get("Language") or search_code(lang)
    jf.upload_subtitle(
        item["Id"],
        raw,
        lang_tag,
        fmt,
        hearing_impaired=bool(best.get("IsHearingImpaired")),
        forced=bool(best.get("IsForced")),
    )
    jf.refresh(item["Id"])
    counts["downloaded"] += 1
    action = "synced" if synced else "downloaded"
    detail = f"{lang} · {best.get('ProviderName')}: {best.get('Name') or ''}".strip()
    db.add_event(
        item_id=item["Id"],
        title=title,
        action=action,
        detail=detail,
        hash_match=hash_match,
        review=not hash_match,
        language=lang,
    )
    provider = best.get("ProviderName") or best.get("provider") or ""
    return action, detail, hash_match, provider


def _upsert_q(item: dict, lang: str, **kwargs) -> int:
    iid = item["Id"]
    with _lock:
        for i, row in enumerate(_state["queue"]):
            if row.get("id") == iid and (row.get("language") or "") == (lang or ""):
                updated = dict(row)
                updated.update(kwargs)
                updated["id"] = iid
                updated["title"] = _item_title(item)
                updated["language"] = lang
                _state["queue"][i] = updated
                return i
        row = {
            "id": iid,
            "title": _item_title(item),
            "language": lang,
            "status": "pending",
            "detail": "",
            "provider": "",
        }
        row.update(kwargs)
        _state["queue"].append(row)
        return len(_state["queue"]) - 1


def retry_once(item_id: str, language: str = "") -> None:
    if status()["running"]:
        raise RuntimeError("Ya hay un escaneo en curso")
    _set(running=True, phase="conectando", current="", error="")
    settings = db.get_settings()
    job_id = db.start_job()
    counts = {"scanned": 0, "missing": 0, "downloaded": 0, "synced": 0, "failed": 0}
    jf: Jellyfin | None = None
    hub: Hub | None = None
    try:
        if not settings["jellyfin_url"] or not settings["api_key"]:
            raise JellyfinError("Falta la URL o la API key de Jellyfin")
        hub = Hub(settings)
        if not hub.enabled_ids():
            raise ProviderError("Activa al menos un proveedor de subtítulos")
        jf = Jellyfin(settings["jellyfin_url"], settings["api_key"])
        jf.ping()
        langs = wanted_langs(settings)
        pause = max(1, int(settings["rate_limit_s"]))
        videos = _load_videos(jf, [item_id])
        if not videos:
            raise JellyfinError("No está en la biblioteca")
        item = videos[0]
        pending = missing_langs(item.get("MediaStreams"), langs)
        targets = [language] if language else pending
        if language and language not in pending:
            _upsert_q(item, language, status="ok", detail="ya está", provider="")
            counts["scanned"] = 1
            db.finish_job(job_id, status="ok", message="", **counts)
            _set(phase="idle", current="", running=False)
            return
        if not targets:
            raise ProviderError("Nada que reintentar")
        counts["missing"] = len(targets)
        for lang in targets:
            counts["scanned"] += 1
            idx = _upsert_q(item, lang, status="running", detail="", provider="")
            title = _item_title(item)
            _set(phase="buscando", current=f"{title} · {lang}")
            try:
                action, detail, hash_match, provider = _download_one(jf, item, settings, counts, hub, lang)
                _qset(idx, status=action, detail=detail, hash_match=hash_match, provider=provider)
                if action == "error":
                    counts["failed"] += 1
            except Exception as exc:  # noqa: BLE001
                counts["failed"] += 1
                detail = f"{lang} · {exc}"[:400]
                db.add_event(item_id=item["Id"], title=title, action="error", detail=detail, language=lang)
                _qset(idx, status="error", detail=detail, provider="")
                log.exception("retry %s %s", title, lang)
            time.sleep(pause)
        db.finish_job(job_id, status="ok", message="", **counts)
        _set(phase="idle", current="", running=False)
    except Exception as exc:  # noqa: BLE001
        db.finish_job(job_id, status="error", message=str(exc)[:400], **counts)
        _set(phase="idle", current="", running=False, error=str(exc))
        raise
    finally:
        if jf:
            jf.close()
        if hub:
            hub.close()


def start_background(item_ids: list[str] | None = None) -> None:
    threading.Thread(target=_safe_scan, args=(item_ids,), daemon=True).start()


def start_retry(item_id: str, language: str = "") -> None:
    if status()["running"]:
        raise RuntimeError("Ya hay un escaneo en curso")
    threading.Thread(target=_safe_retry, args=(item_id, language), daemon=True).start()


def start_translate(item_ids: list[str], source: str, target: str) -> None:
    if status()["running"]:
        raise RuntimeError("Ya hay un escaneo en curso")
    if not item_ids:
        raise RuntimeError("Marca películas o capítulos para traducir")
    if not source or not target:
        raise RuntimeError("Indica idioma de origen y destino")
    if source == target:
        raise RuntimeError("Origen y destino no pueden ser el mismo")
    threading.Thread(target=_safe_translate, args=(item_ids, source, target), daemon=True).start()


def _provider_srt(hub: Hub, item: dict, settings: dict, lang: str) -> tuple[bytes, dict]:
    """Descarga un .srt de texto para `lang` (p. ej. cuando el embebido es solo imagen)."""
    if not hub.enabled_ids():
        raise ProviderError("Activa al menos un proveedor de subtítulos")
    video = map_path(item.get("Path"), settings.get("path_maps") or [])
    moviehash = file_movie_hash(video) if video else None
    candidates = hub.search(item, lang, moviehash)
    best = pick_best(candidates, include_hi=bool(settings["include_hi"]), wanted=lang)
    if not best:
        hint = " · ".join(hub.last_errors[:3]) if hub.last_errors else "sin resultados"
        raise ProviderError(f"No hay .srt de {lang}: {hint}")
    raw, _filename = hub.download(best)
    if settings.get("autosync") and video:
        tmp = Path(video).with_name(Path(video).stem + ".subss-tmp.srt")
        tmp.write_bytes(raw)
        if ffsubsync(video, str(tmp)):
            raw = tmp.read_bytes()
        tmp.unlink(missing_ok=True)
    return raw, best


def _source_srt_text(
    jf: Jellyfin,
    hub: Hub | None,
    item: dict,
    settings: dict,
    source: str,
    title: str,
) -> tuple[str, str]:
    """Texto SRT de origen: pista de texto en Jellyfin, o descarga si solo hay imagen/nada."""
    try:
        return jf.fetch_subtitle_srt(item, source, settings.get("path_maps") or []), "Jellyfin"
    except JellyfinError as exc:
        msg = str(exc)
        # Image embeds (DVDSUB/PGS) or missing text track → pull a real .srt
        if "imagen" not in msg and "No hay subtítulo" not in msg:
            raise
        log.info("origen %s sin texto (%s); buscando .srt", source, msg)

    if hub is None:
        raise ProviderError(
            f"El subtítulo en {source} es imagen embebida; activa proveedores para bajar un .srt"
        )
    _set(phase="buscando", current=f"{title} · origen {source}")
    raw, best = _provider_srt(hub, item, settings, source)
    provider = best.get("ProviderName") or best.get("provider") or "proveedor"
    jf.upload_subtitle(item["Id"], raw, jellyfin_lang_tag(source), "srt")
    jf.refresh(item["Id"])
    return raw.decode("utf-8", errors="replace"), provider


def _translate_one(
    jf: Jellyfin,
    item: dict,
    settings: dict,
    counts: dict,
    *,
    source: str,
    target: str,
    ctx_cache: dict,
    hub: Hub | None = None,
) -> tuple[str, str]:
    title = _item_title(item)
    streams = item.get("MediaStreams")
    if has_language(streams, target):
        detail = f"{source}→{target} · ya está"
        return "ok", detail

    gemini = (settings.get("gemini_api_key") or "").strip()
    if not gemini:
        raise RuntimeError("Configura la API key de Gemini en Ajustes")

    cache_key = item.get("SeriesId") or item["Id"]
    if cache_key not in ctx_cache:
        ctx_cache[cache_key] = context_for_item(
            item, (settings.get("tmdb_api_key") or "").strip(), target
        )
    ctx = ctx_cache[cache_key]

    _set(phase="leyendo", current=title)
    try:
        raw, origin = _source_srt_text(jf, hub, item, settings, source, title)
    except ProviderError as exc:
        detail = f"{source}→{target} · {exc}"[:400]
        db.add_event(item_id=item["Id"], title=title, action="empty", detail=detail, language=target)
        return "empty", detail

    _set(phase="traduciendo", current=f"{title} · {source}→{target}")

    def on_batch(i, total, _n):
        _set(phase="traduciendo", current=f"{title} · lote {i}/{total}")

    translated = translate_srt_text(
        raw,
        api_key=gemini,
        ctx=ctx,
        source_lang=source,
        target_lang=target,
        on_batch=on_batch,
    )
    jf.upload_subtitle(item["Id"], translated.encode("utf-8"), jellyfin_lang_tag(target), "srt")
    jf.refresh(item["Id"])
    counts["downloaded"] += 1
    detail = f"{source}→{target} · Gemini"
    if origin != "Jellyfin":
        detail += f" · origen {origin}"
    if ctx.title:
        detail += f" · {ctx.title}"
    db.add_event(
        item_id=item["Id"],
        title=title,
        action="translated",
        detail=detail[:400],
        review=True,
        language=target,
    )
    return "translated", detail[:400]


def translate_once(item_ids: list[str], source: str, target: str) -> None:
    if status()["running"]:
        raise RuntimeError("Ya hay un escaneo en curso")
    _set(running=True, phase="conectando", current="", error="", queue=[])
    settings = db.get_settings()
    job_id = db.start_job()
    counts = {"scanned": 0, "missing": 0, "downloaded": 0, "synced": 0, "failed": 0}
    jf: Jellyfin | None = None
    hub: Hub | None = None
    only = [i for i in item_ids if i]
    try:
        if not settings["jellyfin_url"] or not settings["api_key"]:
            raise JellyfinError("Falta la URL o la API key de Jellyfin")
        if not (settings.get("gemini_api_key") or "").strip():
            raise RuntimeError("Configura la API key de Gemini en Ajustes → Traducir")
        jf = Jellyfin(settings["jellyfin_url"], settings["api_key"])
        jf.ping()
        hub = Hub(settings)
        videos = _load_videos(jf, only)
        if not videos:
            raise JellyfinError("No está en la biblioteca")
        jobs = []
        for item in videos:
            jobs.append(
                {
                    "id": item["Id"],
                    "title": _item_title(item),
                    "language": target,
                    "status": "pending",
                    "detail": f"{source}→{target}",
                    "provider": "Gemini",
                    "item": item,
                }
            )
        counts["missing"] = len(jobs)
        _set(queue=[{k: v for k, v in job.items() if k != "item"} for job in jobs])
        ctx_cache: dict = {}
        pause = max(1, int(settings["rate_limit_s"]))
        for index, job in enumerate(jobs):
            item = job["item"]
            title = job["title"]
            counts["scanned"] += 1
            _set(phase="traduciendo", current=f"{title} · {source}→{target}")
            _qset(index, status="running")
            try:
                action, detail = _translate_one(
                    jf,
                    item,
                    settings,
                    counts,
                    source=source,
                    target=target,
                    ctx_cache=ctx_cache,
                    hub=hub,
                )
                _qset(index, status=action, detail=detail, provider="Gemini")
                if action == "error":
                    counts["failed"] += 1
            except Exception as exc:  # noqa: BLE001
                counts["failed"] += 1
                detail = f"{source}→{target} · {exc}"[:400]
                db.add_event(item_id=item["Id"], title=title, action="error", detail=detail, language=target)
                _qset(index, status="error", detail=detail, provider="Gemini")
                log.exception("translate %s", title)
            time.sleep(pause)
        db.finish_job(job_id, status="ok", message="", **counts)
        _set(phase="idle", current="", running=False)
    except Exception as exc:  # noqa: BLE001
        db.finish_job(job_id, status="error", message=str(exc)[:400], **counts)
        _set(phase="idle", current="", running=False, error=str(exc))
        raise
    finally:
        if jf:
            jf.close()
        if hub:
            hub.close()


def _safe_scan(item_ids: list[str] | None = None) -> None:
    try:
        scan_once(item_ids)
    except Exception:
        log.exception("scan failed")


def _safe_retry(item_id: str, language: str) -> None:
    try:
        retry_once(item_id, language)
    except Exception:
        log.exception("retry failed")


def _safe_translate(item_ids: list[str], source: str, target: str) -> None:
    try:
        translate_once(item_ids, source, target)
    except Exception:
        log.exception("translate failed")
