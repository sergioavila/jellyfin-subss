from __future__ import annotations

import logging
import subprocess
import threading
import time
from pathlib import Path

from app import db
from app.jellyfin import Jellyfin, JellyfinError
from app.logic import map_path, missing_langs, pick_best, search_code, wanted_langs
from app.providers import Hub, ProviderError, file_movie_hash

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
