"""Subtitle providers. Same Bazarr EN/ES set, no Jellyfin plugins."""

from __future__ import annotations

import html as htmlmod
import io
import logging
import re
import zipfile
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree as ET

import httpx

from app.catalog import BY_ID, kind_ok, serves
from app.logic import os_movie_hash, provider_langs, search_code, stream_matches_lang

log = logging.getLogger("subss")
UA = "Subss v0.1.0"
OS_BASE = "https://api.opensubtitles.com/api/v1"
POD_SEARCH = "https://www.podnapisi.net/ppodnapisi/search"
POD_LANG = {"es": "4", "en": "2", "pt": "26", "fr": "8", "it": "9", "de": "6", "ja": "11", "ko": "35", "zh": "13"}
GESTD = "https://api.gestdown.info"
SUBTIS = "https://api.subt.is/v1"
SUBDL = "https://api.subdl.com/api/v1/subtitles"
SSRC = "https://api.subsource.net/api/v1"
YIFY_HOSTS = ("https://yifysubtitles.ch", "https://www.yifysubtitles.live", "https://yifysubtitles.xyz")
SUBEXTS = (".srt", ".ass", ".ssa", ".vtt", ".sub")
LANG_NAME = {
    "english": "en",
    "spanish": "es",
    "español": "es",
    "espanol": "es",
    "latin": "es-MX",
    "latino": "es-MX",
    "spain": "es-ES",
    "castellano": "es-ES",
}


class ProviderError(RuntimeError):
    pass


def file_movie_hash(path: str) -> str | None:
    p = Path(path)
    if not p.is_file():
        return None
    size = p.stat().st_size
    with p.open("rb") as fh:
        head = fh.read(65536)
        if size > 65536:
            fh.seek(size - 65536)
            tail = fh.read(65536)
        else:
            tail = b""
    return os_movie_hash(size, head, tail)


def query_from_item(item: dict) -> dict:
    ids = item.get("ProviderIds") or {}
    imdb = str(ids.get("Imdb") or ids.get("IMDB") or "").removeprefix("tt")
    tmdb = str(ids.get("Tmdb") or ids.get("TmdbId") or "")
    tvdb = str(ids.get("Tvdb") or ids.get("TvdbId") or "")
    episode = (item.get("Type") or "") == "Episode"
    path = item.get("Path") or ""
    return {
        "title": item.get("SeriesName") if episode else item.get("Name"),
        "query": item.get("Name") or "",
        "year": item.get("ProductionYear"),
        "season": item.get("ParentIndexNumber") if episode else None,
        "episode": item.get("IndexNumber") if episode else None,
        "imdb": imdb,
        "tmdb": tmdb,
        "tvdb": tvdb,
        "filename": Path(path).name,
        "kind": "episode" if episode else "movie",
    }


def unzip_sub(data: bytes, fallback: str) -> tuple[bytes, str]:
    if data[:2] != b"PK":
        return data, fallback
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = [i for i in zf.infolist() if not i.is_dir()]
        for info in names:
            if info.filename.lower().endswith(SUBEXTS):
                return zf.read(info), Path(info.filename).name
        for info in names:
            if info.filename.lower().endswith(".zip"):
                return unzip_sub(zf.read(info), fallback)
    raise ProviderError("El ZIP no trae un subtítulo")


def _cand(pid: str, name: str, language: str, **extra) -> dict:
    spec = BY_ID.get(pid) or {}
    return {
        "Id": extra.pop("Id", None) or f"{pid}-{extra.get('dl_id') or name}",
        "provider": pid,
        "ProviderName": spec.get("name") or pid,
        "Name": name,
        "Language": language,
        "DownloadCount": int(extra.pop("downloads", 0) or 0),
        "CommunityRating": float(extra.pop("rating", 0) or 0),
        "IsHashMatch": bool(extra.pop("hash_match", False)),
        "IsHearingImpaired": bool(extra.pop("hi", False)),
        "IsForced": bool(extra.pop("forced", False)),
        "Format": extra.pop("fmt", "srt"),
        **extra,
    }


def _keep(rows: list[dict], wanted: str) -> list[dict]:
    out = []
    for row in rows:
        probe = {"Type": "Subtitle", "Language": row.get("Language") or "", "Name": row.get("Name") or ""}
        if stream_matches_lang(probe, wanted):
            out.append(row)
    return out


def _guess_lang(blob: str, fallback: str = "") -> str:
    low = (blob or "").lower()
    for token, code in LANG_NAME.items():
        if token in low:
            return code
    return fallback


class OpenSubtitles:
    def __init__(self, api_key: str, username: str, password: str):
        if not api_key or not username or not password:
            raise ProviderError("OpenSubtitles.com pide usuario, contraseña y API key")
        self.api_key = api_key
        self.username = username
        self.password = password
        self.token = ""
        self.client = httpx.Client(timeout=30.0, follow_redirects=True)

    def close(self) -> None:
        self.client.close()

    def _headers(self, auth: bool = False) -> dict[str, str]:
        headers = {
            "Api-Key": self.api_key,
            "User-Agent": UA,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if auth and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def login(self) -> dict:
        try:
            res = self.client.post(
                f"{OS_BASE}/login",
                headers=self._headers(),
                json={"username": self.username, "password": self.password},
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"OpenSubtitles.com no responde: {exc}") from exc
        if res.status_code >= 400:
            raise ProviderError(f"OpenSubtitles.com {res.status_code}: {res.text[:220]}")
        data = res.json()
        self.token = data.get("token") or ""
        if not self.token:
            raise ProviderError("OpenSubtitles.com no devolvió token. Revisa usuario (no el email) y contraseña.")
        user = data.get("user") or {}
        return {
            "user": user.get("username") or self.username,
            "allowed_downloads": user.get("allowed_downloads"),
            "vip": user.get("vip"),
        }

    def search(self, item: dict, wanted: str, moviehash: str | None, prefer_hash: bool) -> list[dict]:
        if not self.token:
            self.login()
        q = query_from_item(item)
        langs = provider_langs(wanted)
        params: dict[str, str] = {"languages": langs, "ai_translated": "exclude"}
        if q["kind"] == "episode":
            params["type"] = "episode"
            if q["season"] is not None:
                params["season_number"] = str(q["season"])
            if q["episode"] is not None:
                params["episode_number"] = str(q["episode"])
            if q["title"]:
                params["query"] = q["title"]
        else:
            params["type"] = "movie"
            if q["title"]:
                params["query"] = q["title"]
            if q["year"]:
                params["year"] = str(q["year"])
        if q["imdb"]:
            params["imdb_id"] = q["imdb"]
        if q["tmdb"]:
            params["tmdb_id"] = q["tmdb"]
        if prefer_hash and moviehash:
            hashed = dict(params)
            hashed["moviehash"] = moviehash
            found = self._get_subs(hashed)
            if found:
                return found
        return self._get_subs(params)

    def _get_subs(self, params: dict) -> list[dict]:
        res = self.client.get(f"{OS_BASE}/subtitles", headers=self._headers(auth=True), params=params)
        if res.status_code >= 400:
            raise ProviderError(f"OpenSubtitles.com búsqueda {res.status_code}: {res.text[:220]}")
        out = []
        for row in res.json().get("data") or []:
            attr = row.get("attributes") or {}
            for f in attr.get("files") or []:
                out.append(
                    _cand(
                        "opensubtitlescom",
                        f.get("file_name") or attr.get("release") or "",
                        attr.get("language") or "",
                        file_id=f.get("file_id"),
                        Id=f"os-{f.get('file_id')}",
                        downloads=attr.get("download_count") or 0,
                        rating=attr.get("ratings") or 0,
                        hash_match=bool(attr.get("moviehash_match")),
                        hi=bool(attr.get("hearing_impaired")),
                        forced=bool(attr.get("foreign_parts_only")),
                    )
                )
        return out

    def download(self, candidate: dict) -> tuple[bytes, str]:
        if not self.token:
            self.login()
        res = self.client.post(
            f"{OS_BASE}/download",
            headers=self._headers(auth=True),
            json={"file_id": candidate["file_id"]},
        )
        if res.status_code >= 400:
            raise ProviderError(f"OpenSubtitles.com descarga {res.status_code}: {res.text[:220]}")
        payload = res.json()
        link = payload.get("link")
        name = payload.get("file_name") or "subtitle.srt"
        if not link:
            raise ProviderError("OpenSubtitles.com no dio enlace de descarga (límite diario?)")
        file_res = self.client.get(link, headers={"User-Agent": UA})
        if file_res.status_code >= 400:
            raise ProviderError(f"No se pudo bajar el SRT ({file_res.status_code})")
        return file_res.content, name


class Podnapisi:
    def __init__(self, client: httpx.Client | None = None):
        self.client = client or httpx.Client(timeout=30.0, follow_redirects=True, headers={"User-Agent": UA})
        self._own = client is None

    def close(self) -> None:
        if self._own:
            self.client.close()

    def search(self, item: dict, wanted: str, **_k) -> list[dict]:
        q = query_from_item(item)
        params = {"sXML": "1", "sK": q["title"] or q["query"]}
        lang = POD_LANG.get(search_code(wanted))
        if lang:
            params["sJ"] = lang
        if q["year"]:
            params["sY"] = str(q["year"])
        if q["kind"] == "episode" and q["season"] is not None:
            params["sTS"] = str(q["season"])
            if q["episode"] is not None:
                params["sTE"] = str(q["episode"])
        try:
            res = self.client.get(POD_SEARCH, params=params)
        except httpx.HTTPError as exc:
            raise ProviderError(f"Podnapisi no responde: {exc}") from exc
        if res.status_code >= 400:
            raise ProviderError(f"Podnapisi {res.status_code}")
        try:
            root = ET.fromstring(res.content)
        except ET.ParseError as exc:
            raise ProviderError("Podnapisi devolvió XML inválido") from exc
        out = []
        for sub in root.findall(".//subtitle"):
            pid = (sub.findtext("id") or "").strip()
            if not pid:
                continue
            title = (sub.findtext("release") or sub.findtext("title") or pid).strip()
            lang = (sub.findtext("language") or "").strip()
            out.append(
                _cand(
                    "podnapisi",
                    title,
                    lang,
                    pid=pid,
                    Id=f"pod-{pid}",
                    downloads=sub.findtext("downloads") or 0,
                    rating=sub.findtext("rating") or 0,
                    hi="hearing" in title.lower(),
                )
            )
        return out

    def download(self, candidate: dict) -> tuple[bytes, str]:
        url = f"https://www.podnapisi.net/subtitles/{candidate['pid']}/download"
        res = self.client.get(url)
        if res.status_code >= 400:
            raise ProviderError(f"Podnapisi descarga {res.status_code}")
        return unzip_sub(res.content, f"{candidate['pid']}.srt")


class Hub:
    def __init__(self, settings: dict):
        self.settings = settings
        self.http = httpx.Client(timeout=25.0, follow_redirects=True, headers={"User-Agent": UA})
        self._os: OpenSubtitles | None = None
        self._pod: Podnapisi | None = None
        self._titlovi_token = ""
        self.last_errors: list[str] = []

    def close(self) -> None:
        if self._os:
            self._os.close()
        self.http.close()

    def creds(self, pid: str) -> dict:
        row = (self.settings.get("provider_creds") or {}).get(pid) or {}
        if pid == "opensubtitlescom" and not row.get("api_key"):
            return {
                "username": row.get("username") or self.settings.get("os_username") or "",
                "password": row.get("password") or self.settings.get("os_password") or "",
                "api_key": row.get("api_key") or self.settings.get("os_api_key") or "",
                "use_hash": row.get("use_hash", self.settings.get("os_use_hash", True)),
            }
        return row

    def enabled_ids(self) -> list[str]:
        ids = self.settings.get("enabled_providers")
        if ids is None:
            ids = []
            if self.settings.get("os_enabled"):
                ids.append("opensubtitlescom")
            if self.settings.get("podnapisi_enabled"):
                ids.append("podnapisi")
        return [i for i in ids if i in BY_ID]

    def os(self) -> OpenSubtitles:
        if self._os is None:
            c = self.creds("opensubtitlescom")
            self._os = OpenSubtitles(c.get("api_key") or "", c.get("username") or "", c.get("password") or "")
        return self._os

    def pod(self) -> Podnapisi:
        if self._pod is None:
            self._pod = Podnapisi(self.http)
        return self._pod

    def search(self, item: dict, wanted: str, moviehash: str | None) -> list[dict]:
        family = search_code(wanted)
        q = query_from_item(item)
        out: list[dict] = []
        self.last_errors = []
        for pid in self.enabled_ids():
            if not serves(pid, family) or not kind_ok(pid, q["kind"]):
                continue
            fn = SEARCH.get(pid)
            if not fn:
                continue
            try:
                found = fn(self, item, wanted, moviehash)
                out.extend(_keep(found, wanted) if pid not in ("opensubtitlescom",) else found)
            except Exception as exc:  # noqa: BLE001 — one dead site must not kill the scan
                name = (BY_ID.get(pid) or {}).get("name") or pid
                self.last_errors.append(f"{name}: {exc}"[:180])
                log.warning("%s: %s", pid, exc)
        return out

    def download(self, candidate: dict) -> tuple[bytes, str]:
        pid = candidate.get("provider")
        fn = DOWNLOAD.get(pid)
        if not fn:
            raise ProviderError(f"No hay descarga para {pid}")
        return fn(self, candidate)

    def test(self, pid: str) -> dict:
        if pid not in BY_ID:
            raise ProviderError("Proveedor desconocido")
        fn = TEST.get(pid)
        if not fn:
            raise ProviderError("Ese sitio no tiene prueba")
        return fn(self)

    def get(self, url: str, **kw) -> httpx.Response:
        return self.http.get(url, **kw)

    def json(self, url: str, **kw) -> dict | list:
        res = self.http.get(url, **kw)
        if res.status_code >= 400:
            raise ProviderError(f"{url} → {res.status_code}")
        return res.json()


def _search_os(hub: Hub, item, wanted, moviehash):
    c = hub.creds("opensubtitlescom")
    use_hash = bool(c.get("use_hash", True)) and bool(hub.settings.get("prefer_hash", True))
    return hub.os().search(item, wanted, moviehash, use_hash)


def _dl_os(hub: Hub, cand):
    return hub.os().download(cand)


def _test_os(hub: Hub):
    return {"ok": True, **hub.os().login()}


def _search_pod(hub: Hub, item, wanted, _h):
    return hub.pod().search(item, wanted)


def _dl_pod(hub: Hub, cand):
    return hub.pod().download(cand)


def _ok_get(hub: Hub, url: str, name: str) -> dict:
    try:
        res = hub.get(url)
    except httpx.HTTPError as exc:
        raise ProviderError(f"{name} no responde: {exc}") from exc
    if res.status_code >= 400:
        raise ProviderError(f"{name} {res.status_code}")
    return {"ok": True, "status": res.status_code}


def _search_gestdown(hub: Hub, item, wanted, _h):
    q = query_from_item(item)
    if q["kind"] != "episode" or q["season"] is None or q["episode"] is None:
        return []
    show_id = ""
    if q["tvdb"]:
        try:
            data = hub.json(f"{GESTD}/shows/external/tvdb/{q['tvdb']}")
            show_id = (data.get("show") or data).get("id") if isinstance(data, dict) else ""
        except ProviderError:
            show_id = ""
    if not show_id:
        data = hub.json(f"{GESTD}/shows/search/{quote(q['title'] or q['query'])}")
        shows = data.get("shows") or []
        want = (q["title"] or "").lower()
        pick = next((s for s in shows if (s.get("name") or "").lower() == want), shows[0] if shows else None)
        if not pick:
            return []
        show_id = pick["id"]
    lang = search_code(wanted)
    payload = hub.json(f"{GESTD}/subtitles/get/{show_id}/{q['season']}/{q['episode']}/{lang}")
    out = []
    for row in payload.get("matchingSubtitles") or []:
        if not row.get("completed", True):
            continue
        sid = row.get("subtitleId")
        name = row.get("release") or row.get("version") or sid
        spoken = row.get("language") or lang
        code = LANG_NAME.get(str(spoken).lower(), lang)
        out.append(
            _cand(
                "gestdown",
                str(name),
                code,
                dl_id=sid,
                downloads=row.get("downloadCount") or 0,
                hi=bool(row.get("hearingImpaired")),
            )
        )
    return out


def _dl_url(hub: Hub, cand, fallback: str):
    url = cand.get("url")
    if not url:
        raise ProviderError("Sin enlace")
    res = hub.get(url)
    if res.status_code >= 400:
        raise ProviderError(f"Descarga {res.status_code}")
    return unzip_sub(res.content, fallback)


def _dl_gestdown(hub: Hub, cand):
    res = hub.get(f"{GESTD}/subtitles/download/{cand['dl_id']}")
    if res.status_code >= 400:
        raise ProviderError(f"Gestdown descarga {res.status_code}")
    return unzip_sub(res.content, f"{cand['dl_id']}.srt")


def _search_subtis(hub: Hub, item, wanted, moviehash):
    if search_code(wanted) != "es":
        return []
    q = query_from_item(item)
    if q["kind"] != "movie":
        return []
    paths = []
    if moviehash:
        paths.append(f"{SUBTIS}/subtitle/find/file/hash/{moviehash}")
    if q["filename"]:
        paths.append(f"{SUBTIS}/subtitle/find/file/name/{quote(q['filename'])}")
    title = q["title"] or q["query"]
    if title:
        paths.append(f"{SUBTIS}/titles/search/{quote(title)}")
    out = []
    for path in paths:
        try:
            data = hub.json(path)
        except ProviderError:
            continue
        if not isinstance(data, dict):
            continue
        sub = data.get("subtitle") or {}
        link = sub.get("subtitle_link") or data.get("subtitle_link")
        if link:
            name = sub.get("subtitle_file_name") or sub.get("title_name") or title
            out.append(_cand("subtis", name, "es", url=link, hash_match="hash" in path, dl_id=link))
            break
        for row in data.get("results") or []:
            slug = row.get("slug")
            if not slug:
                continue
            try:
                found = hub.json(f"{SUBTIS}/subtitle/search/title/{slug}")
            except ProviderError:
                continue
            sub = found.get("subtitle") if isinstance(found, dict) else {}
            link = (sub or {}).get("subtitle_link") or (found.get("subtitle_link") if isinstance(found, dict) else None)
            if link:
                name = (sub or {}).get("subtitle_file_name") or row.get("title_name") or slug
                out.append(_cand("subtis", name, "es", url=link, dl_id=link))
                break
        if out:
            break
    return out


def _search_subdl(hub: Hub, item, wanted, _h):
    key = hub.creds("subdl").get("api_key") or ""
    if not key:
        raise ProviderError("SubDL pide API key")
    q = query_from_item(item)
    family = search_code(wanted).upper()
    params = {"api_key": key, "languages": family, "subs_per_page": 10, "type": "tv" if q["kind"] == "episode" else "movie"}
    if q["imdb"]:
        params["imdb_id"] = f"tt{q['imdb']}"
    else:
        params["film_name"] = q["title"] or q["query"]
    if q["kind"] == "episode":
        if q["season"] is not None:
            params["season_number"] = q["season"]
        if q["episode"] is not None:
            params["episode_number"] = q["episode"]
    data = hub.json(SUBDL, params=params)
    if not data.get("status"):
        raise ProviderError(data.get("error") or "SubDL no encontró")
    out = []
    for row in data.get("subtitles") or []:
        rel = row.get("url") or ""
        url = rel if rel.startswith("http") else f"https://dl.subdl.com{rel}"
        lang = row.get("lang") or family.lower()
        out.append(
            _cand(
                "subdl",
                row.get("release_name") or row.get("name") or rel,
                lang,
                url=url,
                dl_id=rel,
                hi=bool(row.get("hi")),
            )
        )
    return out


def _search_subsource(hub: Hub, item, wanted, _h):
    key = hub.creds("subsource").get("api_key") or ""
    if not key:
        raise ProviderError("SubSource pide API key")
    headers = {"X-API-Key": key}
    q = query_from_item(item)
    params = {
        "searchType": "imdb" if q["imdb"] else "text",
        "q": f"tt{q['imdb']}" if q["imdb"] else (q["title"] or q["query"]),
        "type": "tvseries" if q["kind"] == "episode" else "movie",
    }
    if q["year"] and not q["imdb"]:
        params["year"] = q["year"]
    data = hub.json(f"{SSRC}/movies/search", params=params, headers=headers)
    movies = data.get("data") or data.get("results") or []
    movie_id = None
    for row in movies:
        if q["kind"] == "episode" and q["season"] is not None and row.get("season") not in (None, q["season"], str(q["season"])):
            continue
        movie_id = row.get("movieId") or row.get("id")
        if movie_id:
            break
    if not movie_id:
        return []
    lang = search_code(wanted)
    payload = hub.json(f"{SSRC}/subtitles", params={"movieId": movie_id, "language": lang}, headers=headers)
    rows = payload.get("data") or payload.get("subtitles") or payload.get("results") or []
    out = []
    for row in rows:
        if q["kind"] == "episode" and q["episode"] is not None:
            ep = row.get("episode") or row.get("episodeNumber")
            if ep not in (None, q["episode"], str(q["episode"])):
                continue
        sid = row.get("id") or row.get("subtitleId")
        if not sid:
            continue
        name = row.get("release") or row.get("name") or str(sid)
        out.append(
            _cand(
                "subsource",
                name,
                row.get("language") or lang,
                dl_id=sid,
                downloads=row.get("downloads") or 0,
                hi=bool(row.get("hearingImpaired") or row.get("hi")),
            )
        )
    return out


def _dl_subsource(hub: Hub, cand):
    key = hub.creds("subsource").get("api_key") or ""
    res = hub.get(f"{SSRC}/subtitles/{cand['dl_id']}/download", headers={"X-API-Key": key})
    if res.status_code >= 400:
        raise ProviderError(f"SubSource descarga {res.status_code}")
    return unzip_sub(res.content, f"{cand['dl_id']}.srt")


def _search_subx(hub: Hub, item, wanted, _h):
    if search_code(wanted) != "es":
        return []
    key = hub.creds("subx").get("api_key") or ""
    if not key:
        raise ProviderError("SubX pide API key")
    q = query_from_item(item)
    params = {"api_key": key, "q": q["title"] or q["query"], "lang": "es"}
    if q["imdb"]:
        params["imdb"] = f"tt{q['imdb']}"
    if q["kind"] == "episode":
        params["season"] = q["season"]
        params["episode"] = q["episode"]
    data = hub.json("https://subx-api.duckdns.org/api/search", params=params)
    rows = data if isinstance(data, list) else data.get("results") or data.get("data") or []
    out = []
    for row in rows:
        url = row.get("download") or row.get("url") or row.get("link")
        if not url:
            continue
        out.append(_cand("subx", row.get("title") or row.get("name") or url, "es", url=url, dl_id=url))
    return out


def _search_titlovi(hub: Hub, item, wanted, _h):
    if search_code(wanted) != "en":
        return []
    token = _titlovi_token(hub)
    q = query_from_item(item)
    params = {
        "token": token,
        "query": q["title"] or q["query"],
        "json": "True",
        "lang": "2",
    }
    if q["imdb"]:
        params["imdbID"] = f"tt{q['imdb']}"
    if q["kind"] == "episode":
        params["season"] = q["season"]
        params["episode"] = q["episode"]
    data = hub.json("https://kodi.titlovi.com/api/subtitles/search", params=params)
    rows = data if isinstance(data, list) else data.get("Subtitles") or data.get("subtitles") or []
    out = []
    for row in rows:
        sid = row.get("Id") or row.get("id")
        if not sid:
            continue
        out.append(
            _cand(
                "titlovi",
                row.get("Release") or row.get("Title") or str(sid),
                "en",
                dl_id=sid,
                downloads=row.get("DownloadCount") or 0,
            )
        )
    return out


def _titlovi_token(hub: Hub) -> str:
    if hub._titlovi_token:
        return hub._titlovi_token
    c = hub.creds("titlovi")
    if not c.get("username") or not c.get("password"):
        raise ProviderError("Titlovi pide usuario y contraseña")
    res = hub.http.post(
        "https://kodi.titlovi.com/api/subtitles/gettoken",
        params={"username": c["username"], "password": c["password"], "json": True},
    )
    if res.status_code >= 400:
        raise ProviderError(f"Titlovi login {res.status_code}")
    hub._titlovi_token = (res.json() or {}).get("Token") or ""
    if not hub._titlovi_token:
        raise ProviderError("Titlovi no devolvió token")
    return hub._titlovi_token


def _dl_titlovi(hub: Hub, cand):
    token = _titlovi_token(hub)
    res = hub.get(
        "https://kodi.titlovi.com/api/subtitles/download",
        params={"token": token, "subtitleID": cand["dl_id"], "json": True},
    )
    if res.status_code >= 400:
        raise ProviderError(f"Titlovi descarga {res.status_code}")
    return unzip_sub(res.content, f"{cand['dl_id']}.srt")


def _search_yify(hub: Hub, item, wanted, _h):
    q = query_from_item(item)
    if q["kind"] != "movie" or not q["imdb"]:
        return []
    family = search_code(wanted)
    html = ""
    for host in YIFY_HOSTS:
        try:
            res = hub.get(f"{host}/movie-imdb/tt{q['imdb']}")
        except httpx.HTTPError:
            continue
        if res.status_code < 400 and len(res.text) > 200:
            html = res.text
            base = host
            break
    else:
        return []
    out = []
    for href, label in re.findall(r'href="(/subtitles/[^"]+)"[^>]*>([^<]{0,80})', html, flags=re.I):
        lang = _guess_lang(href + " " + label, "")
        if not lang:
            continue
        url = href if href.startswith("http") else f"{base}{href}"
        dl = url if url.endswith(".zip") else f"{url}.zip"
        out.append(_cand("yifysubtitles", htmlmod.unescape(label.strip() or href), lang, url=dl, dl_id=href))
    if not out:
        for href in re.findall(r'href="(/subtitles/[^"]+-(english|spanish|spanish-latin)[^"]*)"', html, flags=re.I):
            path, token = href if isinstance(href, tuple) else (href, "")
            lang = _guess_lang(path, family)
            url = f"{base}{path}"
            out.append(_cand("yifysubtitles", path.rsplit("/", 1)[-1], lang, url=f"{url}.zip" if not url.endswith(".zip") else url, dl_id=path))
    return out


def _search_tvsubtitles(hub: Hub, item, wanted, _h):
    q = query_from_item(item)
    if q["kind"] != "episode" or not q["title"] or q["season"] is None or q["episode"] is None:
        return []
    family = search_code(wanted)
    res = hub.http.post("https://www.tvsubtitles.net/search1.php", data={"qs": q["title"]})
    if res.status_code >= 400:
        return []
    show = re.search(r'href="/(tvshow-\d+(?:-\d+)?\.html)"', res.text)
    if not show:
        return []
    page = hub.get(f"https://www.tvsubtitles.net/{show.group(1)}")
    ep = f"{int(q['season']):d}x{int(q['episode']):02d}"
    alt = f"s{int(q['season']):02d}e{int(q['episode']):02d}"
    episode_href = ""
    for href, label in re.findall(r'href="(episode-\d+\.html)"[^>]*>([^<]*)', page.text, flags=re.I):
        blob = (label + href).lower()
        if ep in blob or alt in blob:
            episode_href = href
            break
    if not episode_href:
        return []
    epage = hub.get(f"https://www.tvsubtitles.net/{episode_href}")
    out = []
    for href, flag in re.findall(r'href="(subtitle-\d+\.html)".{0,200}?flags/([a-z]{2})\.', epage.text, flags=re.I | re.S):
        if flag.lower() != family:
            continue
        out.append(_cand("tvsubtitles", href, family, url=f"https://www.tvsubtitles.net/{href.replace('.html', '.zip').replace('subtitle-', 'download-')}", dl_id=href))
    return out


def _search_subtitulamos(hub: Hub, item, wanted, _h):
    if search_code(wanted) != "es":
        return []
    q = query_from_item(item)
    term = q["title"] or q["query"]
    res = hub.get(f"https://www.subtitulamos.tv/search", params={"q": term})
    if res.status_code >= 400:
        return []
    out = []
    for href, label in re.findall(r'href="(/(?:shows|episodes|downloads?)/[^"]+)"[^>]*>([^<]{0,80})', res.text, flags=re.I):
        name = htmlmod.unescape(label.strip())
        if q["kind"] == "episode" and q["season"] is not None:
            blob = (href + " " + name).lower()
            if f"s{int(q['season']):02d}e{int(q['episode'] or 0):02d}" not in blob and f"{q['season']}x{int(q['episode'] or 0):02d}" not in blob:
                if "/shows/" in href and len(out) < 3:
                    show = hub.get(f"https://www.subtitulamos.tv{href}")
                    out.extend(_subtitulamos_episode(hub, show.text, q, wanted))
                continue
        lang = _guess_lang(href + " " + name, "es")
        url = href if href.startswith("http") else f"https://www.subtitulamos.tv{href}"
        out.append(_cand("subtitulamostv", name or href, lang, url=url, dl_id=href))
    return out[:8]


def _subtitulamos_episode(hub: Hub, html: str, q: dict, wanted: str) -> list[dict]:
    out = []
    needle = f"s{int(q['season']):02d}e{int(q['episode'] or 0):02d}"
    for href, label in re.findall(r'href="(/[^"]+)"[^>]*>([^<]{0,80})', html, flags=re.I):
        blob = (href + " " + label).lower()
        if needle not in blob and f"{q['season']}x{int(q['episode'] or 0):02d}" not in blob:
            continue
        if not any(x in href.lower() for x in ("download", "episode", "sub")):
            continue
        url = href if href.startswith("http") else f"https://www.subtitulamos.tv{href}"
        out.append(_cand("subtitulamostv", htmlmod.unescape(label.strip() or href), "es", url=url, dl_id=href))
    return out


def _search_subf2m(hub: Hub, item, wanted, _h):
    q = query_from_item(item)
    term = q["title"] or q["query"]
    family = search_code(wanted)
    slug_lang = "spanish" if family == "es" else "english"
    res = hub.get("https://subf2m.co/subtitles/searchbytitle", params={"query": term, "l": ""})
    if res.status_code >= 400:
        return []
    hrefs = re.findall(r'href="(/subtitles/[^"/]+)"', res.text)
    if not hrefs:
        return []
    page = hub.get(f"https://subf2m.co{hrefs[0]}/{slug_lang}")
    out = []
    for href in re.findall(r'href="(/subtitles/[^"]+/[^"]+)"', page.text):
        if "download" in href or href.endswith(slug_lang) or slug_lang in href:
            url = f"https://subf2m.co{href}"
            out.append(_cand("subf2m", href.rsplit("/", 1)[-1], family, url=url, dl_id=href))
        if len(out) >= 8:
            break
    return out


def _search_subtitlecat(hub: Hub, item, wanted, _h):
    q = query_from_item(item)
    term = q["title"] or q["query"]
    family = search_code(wanted)
    res = hub.get("https://www.subtitlecat.com/index.php", params={"search": term})
    if res.status_code >= 400:
        return []
    out = []
    for href, label in re.findall(r'href="([^"]+\.html)"[^>]*>([^<]{0,100})', res.text, flags=re.I):
        blob = (href + " " + label).lower()
        lang = _guess_lang(blob, "")
        if not lang:
            continue
        if q["kind"] == "episode" and q["season"] is not None:
            if f"s{int(q['season']):02d}e{int(q['episode'] or 0):02d}" not in blob and f"{q['season']}x" not in blob:
                continue
        url = href if href.startswith("http") else f"https://www.subtitlecat.com/{href.lstrip('/')}"
        out.append(_cand("subtitlecat", htmlmod.unescape(label.strip() or href), lang, url=url, dl_id=href))
        if len(out) >= 8:
            break
    # Subtitlecat pages wrap the real srt; download follows the page then a .srt/.zip link.
    return _keep(out, wanted) if out else out


def _dl_page_then_file(hub: Hub, cand):
    url = cand.get("url") or ""
    res = hub.get(url)
    if res.status_code >= 400:
        raise ProviderError(f"Descarga {res.status_code}")
    data = res.content
    ctype = (res.headers.get("content-type") or "").lower()
    if "html" in ctype or data[:15].lstrip().lower().startswith(b"<!doctype") or data[:6].lstrip().lower().startswith(b"<html"):
        page = res.text
        file_href = re.search(r'href="([^"]+\.(?:srt|ass|ssa|vtt|zip|sub))"', page, flags=re.I)
        if not file_href:
            raise ProviderError("No hay archivo en la página")
        href = file_href.group(1)
        file_url = href if href.startswith("http") else str(res.url).rsplit("/", 1)[0] + "/" + href.lstrip("/")
        file_res = hub.get(file_url)
        if file_res.status_code >= 400:
            raise ProviderError(f"Archivo {file_res.status_code}")
        data = file_res.content
        url = file_url
    name = Path(url).name or "subtitle.srt"
    if not Path(name).suffix:
        name += ".srt"
    return unzip_sub(data, name)


def _search_animetosho(hub: Hub, item, wanted, _h):
    if search_code(wanted) != "en":
        return []
    q = query_from_item(item)
    term = q["title"] or q["query"]
    if q["kind"] == "episode" and q["episode"] is not None:
        term = f"{term} {q['episode']:02d}"
    data = hub.json("https://feed.animetosho.org/json", params={"q": term})
    rows = data if isinstance(data, list) else data.get("items") or []
    out = []
    for row in rows:
        title = row.get("title") or row.get("name") or ""
        link = row.get("torrent_url") or row.get("link") or ""
        # ponytail: AnimeTosho JSON is release-oriented; we only keep direct subtitle attachments.
        for att in row.get("attachments") or []:
            name = str(att.get("name") or "")
            url = att.get("url") or att.get("link")
            if url and name.lower().endswith(SUBEXTS):
                out.append(_cand("animetosho", name, "en", url=url, dl_id=url))
        if link.lower().endswith(SUBEXTS):
            out.append(_cand("animetosho", title or Path(link).name, "en", url=link, dl_id=link))
        if len(out) >= 8:
            break
    return out


def _skip(*_a, **_k):
    return []


def _need_gestdown(hub: Hub):
    raise ProviderError("Addic7ed pide captcha. Activa Gestdown, que es el mismo catálogo.")


def _need_login(hub: Hub):
    c = hub.creds("pipocas")
    if not c.get("username") or not c.get("password"):
        raise ProviderError("Pipocas pide usuario y contraseña")
    return _ok_get(hub, "https://pipocas.tv", "Pipocas")


def _test_gestdown(hub: Hub):
    data = hub.json(f"{GESTD}/shows/search/Andor")
    n = len(data.get("shows") or [])
    return {"ok": True, "shows": n}


def _test_subtis(hub: Hub):
    hub.json(f"{SUBTIS}/titles/search/roma")
    return {"ok": True}


def _test_subdl(hub: Hub):
    key = hub.creds("subdl").get("api_key") or ""
    if not key:
        raise ProviderError("Falta la API key de SubDL")
    hub.json(SUBDL, params={"api_key": key, "imdb_id": "tt0111161", "languages": "EN"})
    return {"ok": True}


def _test_subsource(hub: Hub):
    key = hub.creds("subsource").get("api_key") or ""
    if not key:
        raise ProviderError("Falta la API key de SubSource")
    hub.json(f"{SSRC}/movies/search", params={"searchType": "text", "q": "Andor", "type": "tvseries"}, headers={"X-API-Key": key})
    return {"ok": True}


def _test_subx(hub: Hub):
    if not hub.creds("subx").get("api_key"):
        raise ProviderError("Falta la API key de SubX")
    return _ok_get(hub, "https://subx-api.duckdns.org", "SubX")


def _test_titlovi(hub: Hub):
    _titlovi_token(hub)
    return {"ok": True, "user": hub.creds("titlovi").get("username")}


SEARCH = {
    "opensubtitlescom": _search_os,
    "podnapisi": _search_pod,
    "gestdown": _search_gestdown,
    "addic7ed": _skip,
    "subtis": _search_subtis,
    "subtitulamostv": _search_subtitulamos,
    "subx": _search_subx,
    "tvsubtitles": _search_tvsubtitles,
    "yifysubtitles": _search_yify,
    "subdl": _search_subdl,
    "subsource": _search_subsource,
    "subf2m": _search_subf2m,
    "subtitlecat": _search_subtitlecat,
    "pipocas": _skip,
    "titlovi": _search_titlovi,
    "animetosho": _search_animetosho,
}

DOWNLOAD = {
    "opensubtitlescom": _dl_os,
    "podnapisi": _dl_pod,
    "gestdown": _dl_gestdown,
    "subtis": lambda h, c: _dl_url(h, c, "subtis.srt"),
    "subtitulamostv": _dl_page_then_file,
    "subx": lambda h, c: _dl_url(h, c, "subx.srt"),
    "tvsubtitles": lambda h, c: _dl_url(h, c, "tvsubtitles.srt"),
    "yifysubtitles": lambda h, c: _dl_url(h, c, "yify.srt"),
    "subdl": lambda h, c: _dl_url(h, c, "subdl.srt"),
    "subsource": _dl_subsource,
    "subf2m": _dl_page_then_file,
    "subtitlecat": _dl_page_then_file,
    "titlovi": _dl_titlovi,
    "animetosho": lambda h, c: _dl_url(h, c, "animetosho.srt"),
}

TEST = {
    "opensubtitlescom": _test_os,
    "podnapisi": lambda h: _ok_get(h, "https://www.podnapisi.net", "Podnapisi"),
    "gestdown": _test_gestdown,
    "addic7ed": _need_gestdown,
    "subtis": _test_subtis,
    "subtitulamostv": lambda h: _ok_get(h, "https://www.subtitulamos.tv", "Subtitulamos"),
    "subx": _test_subx,
    "tvsubtitles": lambda h: _ok_get(h, "https://www.tvsubtitles.net", "TVSubtitles"),
    "yifysubtitles": lambda h: _ok_get(h, "https://yifysubtitles.ch", "YIFY"),
    "subdl": _test_subdl,
    "subsource": _test_subsource,
    "subf2m": lambda h: _ok_get(h, "https://subf2m.co", "subf2m"),
    "subtitlecat": lambda h: _ok_get(h, "https://www.subtitlecat.com", "Subtitle Cat"),
    "pipocas": _need_login,
    "titlovi": _test_titlovi,
    "animetosho": lambda h: _ok_get(h, "https://animetosho.org", "Anime Tosho"),
}
