from __future__ import annotations

import base64
from pathlib import Path
from typing import Iterator
from urllib.parse import urljoin

import httpx

CLIENT = 'MediaBrowser Client="Jellyfin Subss", Device="CasaOS", DeviceId="subss-casaos", Version="0.1.0"'


class JellyfinError(RuntimeError):
    pass


class Jellyfin:
    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0):
        self.base = base_url.rstrip("/") + "/"
        self.api_key = api_key
        self.client = httpx.Client(timeout=timeout, follow_redirects=True)

    def close(self) -> None:
        self.client.close()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f'{CLIENT}, Token="{self.api_key}"',
            "Accept": "application/json",
        }

    def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        url = urljoin(self.base, path.lstrip("/"))
        try:
            response = self.client.request(method, url, headers=self._headers(), **kwargs)
        except httpx.HTTPError as exc:
            raise JellyfinError(f"No se pudo hablar con Jellyfin: {exc}") from exc
        if response.status_code >= 400:
            raise JellyfinError(f"Jellyfin {response.status_code} {path}: {response.text[:240]}")
        return response

    def ping(self) -> dict:
        return self.request("GET", "/System/Info").json()

    def media_folders(self) -> list[dict]:
        try:
            data = self.request("GET", "/Library/VirtualFolders").json()
        except JellyfinError:
            return []
        rows = data if isinstance(data, list) else data.get("Items") or []
        out = []
        for folder in rows:
            name = (folder.get("Name") or "").strip()
            if not name:
                continue
            locations = [str(p).rstrip("/") for p in (folder.get("Locations") or []) if p]
            out.append({"name": name, "locations": locations})
        return out

    def iter_videos(self, page_size: int = 100) -> Iterator[dict]:
        start = 0
        while True:
            params = {
                "Recursive": "true",
                "IncludeItemTypes": "Movie,Episode",
                "Fields": "MediaStreams,Path,MediaSources,ProviderIds,ParentIndexNumber,IndexNumber,SeriesName,SeriesId,ProductionYear",
                "EnableImageTypes": "Primary",
                "StartIndex": str(start),
                "Limit": str(page_size),
            }
            data = self.request("GET", "/Items", params=params).json()
            items = data.get("Items") or []
            if not items:
                return
            yield from items
            start += len(items)
            if start >= int(data.get("TotalRecordCount") or 0):
                return

    def upload_subtitle(
        self,
        item_id: str,
        content: bytes,
        language: str,
        fmt: str = "srt",
        hearing_impaired: bool = False,
        forced: bool = False,
    ) -> None:
        self.request(
            "POST",
            f"/Videos/{item_id}/Subtitles",
            json={
                "Language": language,
                "Format": fmt,
                "IsForced": forced,
                "IsHearingImpaired": hearing_impaired,
                "Data": base64.b64encode(content).decode("ascii"),
            },
        )

    def get_item(self, item_id: str) -> dict:
        return self.request("GET", f"/Items/{item_id}", params={"Fields": "MediaStreams,Path,MediaSources"}).json()

    def refresh(self, item_id: str) -> None:
        self.request("POST", f"/Items/{item_id}/Refresh", params={"Recursive": "false", "MetadataRefreshMode": "None"})

    def delete_subtitle(self, item_id: str, index: int) -> None:
        self.request("DELETE", f"/Videos/{item_id}/Subtitles/{index}")

    def image_path(self, item_id: str) -> str:
        return f"/jf/Items/{item_id}/Images/Primary?fillHeight=120&fillWidth=80&quality=80"

    def _matching_subtitle(
        self, item: dict, language: str, *, text_only: bool = False, allow_forced: bool = False
    ) -> dict | None:
        from app.logic import is_forced, is_subtitle, is_text_subtitle, stream_matches_lang

        found = None
        for stream in item.get("MediaStreams") or []:
            if not is_subtitle(stream):
                continue
            if is_forced(stream) and not allow_forced:
                continue
            if text_only and not is_text_subtitle(stream):
                continue
            if stream_matches_lang(stream, language):
                found = stream  # last match: prefer the one just uploaded
        return found

    def subtitle_stream_index(self, item: dict, language: str) -> int | None:
        stream = self._matching_subtitle(item, language, text_only=True)
        if stream is None:
            stream = self._matching_subtitle(item, language, allow_forced=True)
        if stream is None:
            return None
        return int(stream.get("Index"))

    def external_sub_path(self, item: dict, language: str) -> str | None:
        from app.logic import is_forced, is_subtitle, is_text_subtitle, stream_matches_lang

        for stream in reversed(item.get("MediaStreams") or []):
            if not is_subtitle(stream) or is_forced(stream) or not is_text_subtitle(stream):
                continue
            if stream_matches_lang(stream, language) and stream.get("Path"):
                return stream["Path"]
        return None

    def fetch_subtitle_srt(self, item: dict, language: str, path_maps: list[dict] | None = None) -> str:
        """Lee el subtítulo en `language` como texto SRT (archivo externo o stream Jellyfin)."""
        from app.logic import is_text_subtitle, map_path

        path = self.external_sub_path(item, language)
        if path:
            local = map_path(path, path_maps or [])
            if local and Path(local).is_file():
                return Path(local).read_text(encoding="utf-8", errors="replace")
        stream = self._matching_subtitle(item, language, text_only=True)
        if stream is None:
            image = self._matching_subtitle(item, language)
            if image is not None and not is_text_subtitle(image):
                codec = image.get("Codec") or image.get("DisplayTitle") or "imagen"
                raise JellyfinError(
                    f"El subtítulo en {language} es {codec} (imagen); descarga un .srt primero"
                )
            raise JellyfinError(f"No hay subtítulo en {language}")
        idx = int(stream.get("Index"))
        source = (item.get("MediaSources") or [{}])[0]
        mid = source.get("Id") or item.get("Id")
        res = self.request("GET", f"/Videos/{item['Id']}/{mid}/Subtitles/{idx}/Stream.srt")
        text = res.text
        if not text.strip():
            raise JellyfinError(f"Subtítulo vacío en {language}")
        return text

    def stream_bytes(self, path: str, extra_headers: dict | None = None) -> httpx.Response:
        headers = self._headers()
        if extra_headers:
            headers.update(extra_headers)
        url = urljoin(self.base, path.lstrip("/"))
        return self.client.send(self.client.build_request("GET", url, headers=headers), stream=True)

    def playback_url(self, item_id: str, subtitle_index: int | None) -> str:
        params = [
            "videoCodec=h264",
            "audioCodec=aac",
            "maxWidth=1280",
            "maxHeight=720",
            "videoBitRate=4000000",
            "audioBitRate=192000",
            "subtitleMethod=Hls",
            "segmentContainer=ts",
        ]
        if subtitle_index is not None:
            params.append(f"subtitleStreamIndex={subtitle_index}")
        return f"/Videos/{item_id}/master.m3u8?" + "&".join(params)
