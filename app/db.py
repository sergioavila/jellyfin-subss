from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from app.catalog import DEFAULT_ENABLED, SECRET_FIELDS
from app.logic import LEGACY, wanted_langs

DEFAULTS = {
    "jellyfin_url": "",
    "api_key": "",
    "language": "es-419",
    "languages": ["es-419"],
    "scan_hours": 6,
    "autosync": True,
    "prefer_hash": True,
    "include_hi": False,
    "rate_limit_s": 3,
    "path_maps": [{"from": "/media", "to": "/media"}],
    "enabled_providers": list(DEFAULT_ENABLED),
    "provider_creds": {},
    "os_enabled": False,
    "os_username": "",
    "os_password": "",
    "os_api_key": "",
    "os_use_hash": True,
    "podnapisi_enabled": True,
    "gemini_api_key": "",
    "tmdb_api_key": "",
}

SECRETS = ("api_key", "os_password", "os_api_key", "gemini_api_key", "tmdb_api_key")

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


def connect(data_dir: str) -> sqlite3.Connection:
    global _conn
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(Path(data_dir) / "subss.db"), check_same_thread=False, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at REAL NOT NULL,
            finished_at REAL,
            status TEXT NOT NULL,
            scanned INTEGER DEFAULT 0,
            missing INTEGER DEFAULT 0,
            downloaded INTEGER DEFAULT 0,
            synced INTEGER DEFAULT 0,
            failed INTEGER DEFAULT 0,
            message TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            item_id TEXT NOT NULL,
            title TEXT NOT NULL,
            action TEXT NOT NULL,
            detail TEXT DEFAULT '',
            hash_match INTEGER DEFAULT 0,
            review INTEGER DEFAULT 0,
            language TEXT DEFAULT ''
        );
        """
    )
    cols = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
    if "language" not in cols:
        conn.execute("ALTER TABLE events ADD COLUMN language TEXT DEFAULT ''")
    conn.commit()
    _conn = conn
    return conn


def db() -> sqlite3.Connection:
    if _conn is None:
        raise RuntimeError("db not connected")
    return _conn


def get_settings() -> dict:
    with _lock:
        rows = {r["key"]: r["value"] for r in db().execute("SELECT key, value FROM settings")}
    out = dict(DEFAULTS)
    for key, raw in rows.items():
        if key not in DEFAULTS:
            continue
        default = DEFAULTS[key]
        if isinstance(default, bool):
            out[key] = raw in ("1", "true", "True")
        elif isinstance(default, int):
            out[key] = int(raw)
        elif isinstance(default, (list, dict)):
            out[key] = json.loads(raw)
        else:
            out[key] = raw
    out["language"] = LEGACY.get(out["language"], out["language"])
    if "languages" not in rows:
        out["languages"] = [out["language"]]
    out["languages"] = wanted_langs(out)
    out["language"] = out["languages"][0]
    if "enabled_providers" not in rows:
        enabled = list(out.get("enabled_providers") or DEFAULT_ENABLED)
        if out.get("os_enabled") and (out.get("os_username") or out.get("os_api_key")):
            if "opensubtitlescom" not in enabled:
                enabled.append("opensubtitlescom")
        if out.get("podnapisi_enabled") is False:
            enabled = [e for e in enabled if e != "podnapisi"]
        out["enabled_providers"] = enabled
    creds = dict(out.get("provider_creds") or {})
    if "opensubtitlescom" not in creds and (out.get("os_username") or out.get("os_api_key")):
        creds["opensubtitlescom"] = {
            "username": out.get("os_username") or "",
            "password": out.get("os_password") or "",
            "api_key": out.get("os_api_key") or "",
            "use_hash": out.get("os_use_hash", True),
        }
        out["provider_creds"] = creds
    return out


def mask_settings(settings: dict) -> dict:
    out = dict(settings)
    for key in SECRETS:
        val = str(out.get(key) or "")
        if val:
            out[key] = "••••" + val[-4:]
    masked_creds = {}
    for pid, row in (out.get("provider_creds") or {}).items():
        hidden = {}
        for k, v in (row or {}).items():
            if k in SECRET_FIELDS and v:
                hidden[k] = "••••" + str(v)[-4:]
            else:
                hidden[k] = v
        masked_creds[pid] = hidden
    out["provider_creds"] = masked_creds
    return out


def save_settings(patch: dict) -> dict:
    current = get_settings()
    for key in SECRETS:
        if str(patch.get(key) or "").startswith("••••"):
            patch.pop(key, None)
    if "provider_creds" in patch:
        merged = dict(current.get("provider_creds") or {})
        for pid, row in (patch.get("provider_creds") or {}).items():
            prev = dict(merged.get(pid) or {})
            for k, v in (row or {}).items():
                if k in SECRET_FIELDS and str(v or "").startswith("••••"):
                    continue
                prev[k] = v
            merged[pid] = prev
        patch["provider_creds"] = merged
    if "languages" in patch:
        langs = wanted_langs({"languages": patch.get("languages")})
        patch["languages"] = langs
        patch["language"] = langs[0]
    elif "language" in patch:
        langs = wanted_langs({"language": patch.get("language")})
        patch["languages"] = langs
        patch["language"] = langs[0]
    current.update(patch)
    with _lock:
        for key, value in current.items():
            if key not in DEFAULTS:
                continue
            stored = json.dumps(value) if isinstance(DEFAULTS[key], (list, dict)) else str(value)
            db().execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)", (key, stored))
        db().commit()
    return current


def start_job() -> int:
    with _lock:
        cur = db().execute(
            "INSERT INTO jobs(started_at, status) VALUES (?, ?)", (time.time(), "running")
        )
        db().commit()
        return int(cur.lastrowid)


def finish_job(job_id: int, **fields) -> None:
    fields.setdefault("finished_at", time.time())
    cols = ", ".join(f"{k}=?" for k in fields)
    with _lock:
        db().execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))
        db().commit()


def latest_job() -> dict | None:
    with _lock:
        row = db().execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None


def add_event(**fields) -> None:
    with _lock:
        db().execute(
            """INSERT INTO events(ts, item_id, title, action, detail, hash_match, review, language)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                time.time(),
                fields["item_id"],
                fields["title"],
                fields["action"],
                fields.get("detail", ""),
                1 if fields.get("hash_match") else 0,
                1 if fields.get("review") else 0,
                fields.get("language") or "",
            ),
        )
        db().commit()


def recent_events(limit: int = 40) -> list[dict]:
    with _lock:
        rows = db().execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


def review_queue() -> list[dict]:
    with _lock:
        rows = db().execute(
            "SELECT * FROM events WHERE review=1 AND action IN ('downloaded','synced','translated') ORDER BY id DESC LIMIT 50"
        ).fetchall()
        return [dict(r) for r in rows]


def clear_review(item_id: str, language: str | None = None) -> None:
    with _lock:
        if language:
            db().execute("UPDATE events SET review=0 WHERE item_id=? AND language=?", (item_id, language))
        else:
            db().execute("UPDATE events SET review=0 WHERE item_id=?", (item_id,))
        db().commit()
