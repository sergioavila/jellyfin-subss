"""Bazarr providers that actually serve English or Spanish.

Bazarr's list.tsx has ~80 entries. Most are a single other language
(Greek, Polish, Bulgarian, Turkish, Hebrew, Chinese, Romanian…).
This catalog is that list filtered to EN and/or ES, plus Podnapisi
(classic Bazarr, still public XML, dropped from the current UI).
"""

from __future__ import annotations

PROVIDERS: list[dict] = [
    {
        "id": "opensubtitlescom",
        "name": "OpenSubtitles.com",
        "langs": ["en", "es"],
        "kind": "both",
        "default": False,
        "hint": "Usuario (no el email) + API key de consumers.",
        "site": "https://www.opensubtitles.com",
        "fields": [
            {"key": "username", "type": "text", "name": "Usuario"},
            {"key": "password", "type": "password", "name": "Contraseña"},
            {"key": "api_key", "type": "password", "name": "API key"},
            {"key": "use_hash", "type": "switch", "name": "Buscar por hash", "default": True},
        ],
    },
    {
        "id": "podnapisi",
        "name": "Podnapisi",
        "langs": ["en", "es"],
        "kind": "both",
        "default": True,
        "hint": "Sin cuenta. XML público.",
        "site": "https://www.podnapisi.net",
        "fields": [],
    },
    {
        "id": "gestdown",
        "name": "Gestdown (Addic7ed)",
        "langs": ["en", "es"],
        "kind": "episode",
        "default": True,
        "hint": "Proxy de Addic7ed. Series, sin login.",
        "site": "https://gestdown.info",
        "fields": [],
    },
    {
        "id": "addic7ed",
        "name": "Addic7ed",
        "langs": ["en", "es"],
        "kind": "episode",
        "default": False,
        "hint": "Pide captcha. Activa Gestdown, que es el mismo catálogo sin login.",
        "site": "https://www.addic7ed.com",
        "fields": [
            {"key": "username", "type": "text", "name": "Usuario"},
            {"key": "password", "type": "password", "name": "Contraseña"},
            {"key": "cookies", "type": "text", "name": "Cookies"},
        ],
    },
    {
        "id": "subtis",
        "name": "Subtis",
        "langs": ["es"],
        "kind": "movie",
        "default": True,
        "hint": "Español, solo películas. Hash del archivo si está mapeado.",
        "site": "https://subtis.io",
        "fields": [],
    },
    {
        "id": "subtitulamostv",
        "name": "Subtitulamos.tv",
        "langs": ["es"],
        "kind": "both",
        "default": True,
        "hint": "Español. Sin cuenta.",
        "site": "https://www.subtitulamos.tv",
        "fields": [],
    },
    {
        "id": "subx",
        "name": "SubX (Subdivx)",
        "langs": ["es"],
        "kind": "both",
        "default": False,
        "hint": "API de Subdivx (LATAM). Key en subx-api.duckdns.org.",
        "site": "https://subx-api.duckdns.org",
        "fields": [{"key": "api_key", "type": "password", "name": "API key"}],
    },
    {
        "id": "tvsubtitles",
        "name": "TVSubtitles",
        "langs": ["en", "es"],
        "kind": "episode",
        "default": True,
        "hint": "Series. Sin cuenta.",
        "site": "https://www.tvsubtitles.net",
        "fields": [],
    },
    {
        "id": "yifysubtitles",
        "name": "YIFY Subtitles",
        "langs": ["en", "es"],
        "kind": "movie",
        "default": True,
        "hint": "Películas por IMDb. Sin cuenta.",
        "site": "https://yifysubtitles.ch",
        "fields": [],
    },
    {
        "id": "subdl",
        "name": "SubDL",
        "langs": ["en", "es"],
        "kind": "both",
        "default": False,
        "hint": "API key gratis en subdl.com/api.",
        "site": "https://subdl.com",
        "fields": [{"key": "api_key", "type": "password", "name": "API key"}],
    },
    {
        "id": "subsource",
        "name": "SubSource",
        "langs": ["en", "es"],
        "kind": "both",
        "default": False,
        "hint": "API key en tu perfil de subsource.net.",
        "site": "https://subsource.net",
        "fields": [{"key": "api_key", "type": "password", "name": "API key"}],
    },
    {
        "id": "subf2m",
        "name": "subf2m.co",
        "langs": ["en", "es"],
        "kind": "both",
        "default": True,
        "hint": "Índice público. Sin cuenta.",
        "site": "https://subf2m.co",
        "fields": [],
    },
    {
        "id": "subtitlecat",
        "name": "Subtitle Cat",
        "langs": ["en", "es"],
        "kind": "both",
        "default": True,
        "hint": "Multidioma. Sin cuenta.",
        "site": "https://www.subtitlecat.com",
        "fields": [],
    },
    {
        "id": "pipocas",
        "name": "Pipocas.tv",
        "langs": ["en", "es", "pt"],
        "kind": "both",
        "default": False,
        "hint": "PT / BR / EN / ES. Pide usuario.",
        "site": "https://pipocas.tv",
        "fields": [
            {"key": "username", "type": "text", "name": "Usuario"},
            {"key": "password", "type": "password", "name": "Contraseña"},
        ],
    },
    {
        "id": "titlovi",
        "name": "Titlovi",
        "langs": ["en"],
        "kind": "both",
        "default": False,
        "hint": "Inglés (y Balcanes). Pide cuenta en titlovi.com.",
        "site": "https://titlovi.com",
        "fields": [
            {"key": "username", "type": "text", "name": "Usuario"},
            {"key": "password", "type": "password", "name": "Contraseña"},
        ],
    },
    {
        "id": "animetosho",
        "name": "Anime Tosho",
        "langs": ["en"],
        "kind": "episode",
        "default": False,
        "hint": "Anime en inglés. Mejor con el título de AniDB.",
        "site": "https://animetosho.org",
        "fields": [],
    },
]

BY_ID = {p["id"]: p for p in PROVIDERS}
DEFAULT_ENABLED = [p["id"] for p in PROVIDERS if p.get("default")]
SECRET_FIELDS = ("password", "api_key", "cookies")
# Catalog lists EN/ES because that's why the site is here; these indexes still take other ISO families.
WIDE = {
    "opensubtitlescom",
    "podnapisi",
    "gestdown",
    "addic7ed",
    "tvsubtitles",
    "yifysubtitles",
    "subdl",
    "subsource",
    "subf2m",
    "subtitlecat",
}


def serves(pid: str, wanted_family: str) -> bool:
    spec = BY_ID.get(pid)
    if not spec:
        return False
    if wanted_family in spec["langs"]:
        return True
    return pid in WIDE


def kind_ok(pid: str, kind: str) -> bool:
    spec = BY_ID.get(pid) or {}
    k = spec.get("kind") or "both"
    return k == "both" or k == kind
