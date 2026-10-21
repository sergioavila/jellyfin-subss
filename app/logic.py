"""Language groups and subtitle ranking. No I/O."""

from __future__ import annotations

# search = ISO 639-1 sent to Jellyfin. include/exclude are codes on the stream.
# tokens look at name/comment/path. generic = unlabeled spa/eng that we treat as "already have"
# so a later scan does not download forever. The other regional group still counts as missing.
GROUPS: dict[str, dict] = {
    "es-419": {
        "search": "es",
        "include": {"es-419", "es-mx", "es-ar", "es-cl", "es-co", "es-pe", "es-uy", "es-ve", "es-cr", "es-ec", "es-gt", "es-hn", "es-ni", "es-pa", "es-py", "es-sv", "es-do", "es-bo", "es-cu"},
        "exclude": {"es-es"},
        "generic": {"es", "spa", "spanish", "español", "espanol"},
        "include_tokens": ("latam", "latino", "latin america", "mexican", "mexico", "argentin", "chile", "colombia", "es-mx", "es-419"),
        "exclude_tokens": ("es-es", "españa", "espana", "european spanish", "spanish (spain)", "spain"),
    },
    "es-ES": {
        "search": "es",
        "include": {"es-es"},
        "exclude": {"es-419", "es-mx", "es-ar", "es-cl", "es-co", "es-pe", "es-uy", "es-ve"},
        "generic": {"es", "spa", "spanish", "español", "espanol"},
        "include_tokens": ("es-es", "españa", "espana", "european spanish", "spanish (spain)", "castilian"),
        "exclude_tokens": ("latam", "latino", "latin america", "mexican", "es-mx", "es-419"),
    },
    "pt-BR": {
        "search": "pt",
        "include": {"pt-br", "pt-brz"},
        "exclude": {"pt-pt"},
        "generic": {"pt", "por", "portuguese", "português", "portugues"},
        "include_tokens": ("pt-br", "brasil", "brazil", "brazilian"),
        "exclude_tokens": ("pt-pt", "portugal", "european portuguese"),
    },
    "pt-PT": {
        "search": "pt",
        "include": {"pt-pt"},
        "exclude": {"pt-br", "pt-brz"},
        "generic": {"pt", "por", "portuguese", "português", "portugues"},
        "include_tokens": ("pt-pt", "portugal", "european portuguese"),
        "exclude_tokens": ("pt-br", "brasil", "brazil", "brazilian"),
    },
    "en": {
        "search": "en",
        "include": {"en", "eng", "en-us", "en-au", "english"},
        "exclude": {"en-gb"},
        "generic": {"en", "eng", "english"},
        "include_tokens": ("en-us", "american"),
        "exclude_tokens": ("en-gb", "british", "english (uk)"),
    },
    "en-GB": {
        "search": "en",
        "include": {"en-gb"},
        "exclude": {"en-us"},
        "generic": {"en", "eng", "english"},
        "include_tokens": ("en-gb", "british", "english (uk)", "english (gb)"),
        "exclude_tokens": ("en-us", "american"),
    },
    "fr": {
        "search": "fr",
        "include": {"fr", "fre", "fra", "fr-fr", "french"},
        "exclude": {"fr-ca"},
        "generic": {"fr", "fre", "fra", "french", "français", "francais"},
        "include_tokens": ("fr-fr", "france"),
        "exclude_tokens": ("fr-ca", "quebec", "canadian french", "canadien"),
    },
    "fr-CA": {
        "search": "fr",
        "include": {"fr-ca"},
        "exclude": {"fr-fr"},
        "generic": {"fr", "fre", "fra", "french", "français", "francais"},
        "include_tokens": ("fr-ca", "quebec", "québec", "canadian french", "canadien"),
        "exclude_tokens": ("fr-fr",),
    },
    "zh-Hans": {
        "search": "zh",
        "include": {"zh", "chi", "zho", "zh-cn", "zh-hans", "zh-sg"},
        "exclude": {"zh-tw", "zh-hk", "zh-hant"},
        "generic": {"zh", "chi", "zho", "chinese"},
        "include_tokens": ("simplified", "简体", "zh-cn", "zh-hans"),
        "exclude_tokens": ("traditional", "繁體", "繁体", "zh-tw", "zh-hk", "zh-hant"),
    },
    "zh-Hant": {
        "search": "zh",
        "include": {"zh-tw", "zh-hk", "zh-hant"},
        "exclude": {"zh-cn", "zh-hans", "zh-sg"},
        "generic": {"zh", "chi", "zho", "chinese"},
        "include_tokens": ("traditional", "繁體", "繁体", "zh-tw", "zh-hk", "zh-hant"),
        "exclude_tokens": ("simplified", "简体", "zh-cn", "zh-hans"),
    },
    "it": {
        "search": "it",
        "include": {"it", "ita", "italian", "italiano"},
        "exclude": set(),
        "generic": {"it", "ita", "italian", "italiano"},
        "include_tokens": (),
        "exclude_tokens": (),
    },
    "de": {
        "search": "de",
        "include": {"de", "ger", "deu", "german", "deutsch"},
        "exclude": set(),
        "generic": {"de", "ger", "deu", "german", "deutsch"},
        "include_tokens": (),
        "exclude_tokens": (),
    },
    "ja": {
        "search": "ja",
        "include": {"ja", "jpn", "japanese"},
        "exclude": set(),
        "generic": {"ja", "jpn", "japanese"},
        "include_tokens": (),
        "exclude_tokens": (),
    },
    "ko": {
        "search": "ko",
        "include": {"ko", "kor", "korean"},
        "exclude": set(),
        "generic": {"ko", "kor", "korean"},
        "include_tokens": (),
        "exclude_tokens": (),
    },
}

LEGACY = {"es": "es-419", "pt": "pt-BR", "zh": "zh-Hans"}


def normalize_langs(raw) -> list[str]:
    if isinstance(raw, str):
        raw = [raw]
    out: list[str] = []
    seen: set[str] = set()
    for code in raw or []:
        if not isinstance(code, str):
            continue
        code = LEGACY.get(code.strip(), code.strip())
        if not code or code in seen:
            continue
        seen.add(code)
        out.append(code)
    return out


def wanted_langs(settings: dict | None) -> list[str]:
    settings = settings or {}
    langs = normalize_langs(settings.get("languages"))
    if langs:
        return langs
    return normalize_langs(settings.get("language")) or ["es-419"]


def normalize_lang(value: str | None) -> str:
    if not value:
        return ""
    return value.strip().lower().replace("_", "-")


def resolve_group(wanted: str) -> dict:
    code = LEGACY.get(wanted, wanted)
    if code in GROUPS:
        return GROUPS[code]
    n = normalize_lang(code)
    return {
        "search": n.split("-")[0] or n,
        "include": {n},
        "exclude": set(),
        "generic": {n},
        "include_tokens": (),
        "exclude_tokens": (),
    }


def search_code(wanted: str) -> str:
    return resolve_group(wanted)["search"]


PROVIDER_LANGS = {
    "es-419": "es-MX,es",
    "es-ES": "es-ES,es",
    "pt-BR": "pt-BR,pt",
    "pt-PT": "pt-PT,pt",
    "en": "en",
    "en-GB": "en-GB,en",
    "fr": "fr",
    "fr-CA": "fr-CA,fr",
    "zh-Hans": "zh-CN,zh",
    "zh-Hant": "zh-TW,zh",
    "it": "it",
    "de": "de",
    "ja": "ja",
    "ko": "ko",
}


def provider_langs(wanted: str) -> str:
    code = LEGACY.get(wanted, wanted)
    return PROVIDER_LANGS.get(code, search_code(code))


def os_movie_hash(size: int, head: bytes, tail: bytes) -> str:
    """OpenSubtitles moviehash: filesize + first 64KiB + last 64KiB, as uint64 little-endian."""
    import struct

    value = size & 0xFFFFFFFFFFFFFFFF
    for chunk in (head[:65536], tail[:65536]):
        for i in range(0, len(chunk), 8):
            block = chunk[i : i + 8].ljust(8, b"\0")
            value = (value + struct.unpack("<Q", block)[0]) & 0xFFFFFFFFFFFFFFFF
    return f"{value:016x}"


def _blob(item: dict) -> str:
    parts = (
        item.get("Language"),
        item.get("language"),
        item.get("ThreeLetterISOLanguageName"),
        item.get("Name"),
        item.get("Comment"),
        item.get("Title"),
        item.get("DisplayTitle"),
        item.get("Path"),
    )
    return " ".join(str(p) for p in parts if p).lower()


def _has_token(blob: str, tokens: tuple | list) -> bool:
    return any(tok in blob for tok in tokens if tok)


def stream_lang(stream: dict) -> str:
    return normalize_lang(stream.get("Language") or stream.get("language") or stream.get("ThreeLetterISOLanguageName"))


def _same_family(lang: str, group: dict) -> bool:
    if not lang:
        return True
    family = group["include"] | group["exclude"] | group["generic"]
    search = group["search"]
    return lang in family or lang.startswith(search + "-")


def stream_matches_lang(stream: dict, wanted: str) -> bool:
    group = resolve_group(wanted)
    lang = stream_lang(stream)
    blob = _blob(stream)
    if not _same_family(lang, group):
        return False
    if lang in group["exclude"] or _has_token(blob, group["exclude_tokens"]):
        return False
    if lang in group["include"] or _has_token(blob, group["include_tokens"]):
        return True
    return lang in group["generic"]


def is_subtitle(stream: dict) -> bool:
    return (stream.get("Type") or stream.get("type") or "").lower() == "subtitle"


def is_forced(stream: dict) -> bool:
    return bool(stream.get("IsForced") or stream.get("isForced"))


# Image-based tracks: Jellyfin cannot convert these to SRT for translation.
_IMAGE_CODECS = frozenset({
    "pgssub",
    "hdmv_pgs_subtitle",
    "dvdsub",
    "dvd_subtitle",
    "vobsub",
    "xsub",
    "dvb_subtitle",
    "dvbsub",
})


def is_text_subtitle(stream: dict) -> bool:
    """True when the track is text (SRT/ASS/…), not PGS/VobSub bitmap."""
    if "IsTextSubtitleStream" in stream:
        return bool(stream["IsTextSubtitleStream"])
    codec = (stream.get("Codec") or stream.get("codec") or "").strip().lower()
    return codec not in _IMAGE_CODECS if codec else True


def has_language(streams: list[dict] | None, wanted: str, *, count_forced: bool = False) -> bool:
    for stream in streams or []:
        if not is_subtitle(stream):
            continue
        if is_forced(stream) and not count_forced:
            continue
        if stream_matches_lang(stream, wanted):
            return True
    return False


def missing_langs(streams: list[dict] | None, wanted: list[str]) -> list[str]:
    return [lang for lang in wanted if not has_language(streams, lang)]


def present_langs(streams: list[dict] | None, wanted: list[str]) -> list[str]:
    return [lang for lang in wanted if has_language(streams, lang)]


def _tally(episodes: list[dict], wanted: list[str]) -> tuple[list[str], list[str], dict[str, int], int]:
    total = len(episodes)
    counts = {lang: sum(1 for ep in episodes if lang in ep["have"]) for lang in wanted}
    have = [lang for lang in wanted if counts[lang]]
    missing = [lang for lang in wanted if counts[lang] < total]
    return have, missing, counts, total


# ponytail: path prefix vs VirtualFolders. Breaks if Locations empty or overlap oddly. Upgrade: GET /Items?ParentId=folder.
_KIND_CAT = {"Episode": "Series", "Movie": "Películas"}


def category_of(item: dict, folders: list[dict] | None) -> str:
    path = item.get("Path") or ""
    best = ""
    best_len = -1
    for folder in folders or []:
        for loc in folder.get("locations") or []:
            loc = str(loc).rstrip("/")
            if loc and (path == loc or path.startswith(loc + "/")) and len(loc) > best_len:
                best = folder.get("name") or ""
                best_len = len(loc)
    return best or _KIND_CAT.get(item.get("Type") or "", "Otros")


def _category_order(names: list[str], folders: list[dict] | None) -> list[str]:
    seen: list[str] = []
    for folder in folders or []:
        name = folder.get("name")
        if name in names and name not in seen:
            seen.append(name)
    leftovers = sorted((n for n in names if n not in seen), key=lambda n: (n == "Otros", n.lower()))
    return seen + leftovers


def build_library(items: list[dict], wanted: list[str], folders: list[dict] | None = None) -> list[dict]:
    series: dict[str, dict] = {}
    movies: list[dict] = []
    for item in items:
        iid = item.get("Id") or ""
        streams = item.get("MediaStreams")
        have = present_langs(streams, wanted)
        miss = [lang for lang in wanted if lang not in have]
        cat = category_of(item, folders)
        if (item.get("Type") or "") == "Episode":
            sid = item.get("SeriesId") or item.get("SeriesName") or iid
            season_n = item.get("ParentIndexNumber")
            if season_n is None:
                season_n = 0
            row = series.setdefault(
                sid,
                {
                    "id": sid,
                    "title": item.get("SeriesName") or "Serie",
                    "seasons": {},
                    "category": cat,
                },
            )
            if item.get("SeriesName"):
                row["title"] = item["SeriesName"]
            season = row["seasons"].setdefault(
                int(season_n),
                {
                    "index": int(season_n),
                    "title": item.get("SeasonName")
                    or (f"Temporada {season_n}" if season_n else "Especiales"),
                    "episodes": [],
                },
            )
            season["episodes"].append(
                {
                    "id": iid,
                    "title": item.get("Name") or iid,
                    "index": int(item.get("IndexNumber") or 0),
                    "have": have,
                    "missing": miss,
                }
            )
            continue
        movies.append(
            {
                "kind": "movie",
                "id": iid,
                "title": item.get("Name") or iid,
                "year": item.get("ProductionYear"),
                "image_id": iid,
                "have": have,
                "missing": miss,
                "category": cat,
            }
        )
    groups: list[dict] = []
    for row in series.values():
        seasons = []
        all_eps: list[dict] = []
        for key in sorted(row["seasons"]):
            season = row["seasons"][key]
            season["episodes"].sort(key=lambda e: (e["index"], e["title"]))
            have, missing, counts, total = _tally(season["episodes"], wanted)
            seasons.append(
                {
                    "index": season["index"],
                    "title": season["title"],
                    "episodes": season["episodes"],
                    "have": have,
                    "missing": missing,
                    "counts": counts,
                    "total": total,
                }
            )
            all_eps.extend(season["episodes"])
        have, missing, counts, total = _tally(all_eps, wanted)
        groups.append(
            {
                "kind": "series",
                "id": row["id"],
                "title": row["title"],
                "image_id": row["id"],
                "seasons": seasons,
                "have": have,
                "missing": missing,
                "counts": counts,
                "total": total,
                "category": row["category"],
            }
        )
    groups.extend(movies)
    groups.sort(key=lambda g: (g["title"] or "").lower())
    buckets: dict[str, list[dict]] = {}
    names: list[str] = []
    for group in groups:
        cat = group.pop("category") or "Otros"
        if cat not in buckets:
            buckets[cat] = []
            names.append(cat)
        buckets[cat].append(group)
    return [{"name": name, "groups": buckets[name]} for name in _category_order(names, folders)]


def variant_adjust(sub: dict, wanted: str) -> float:
    group = resolve_group(wanted)
    lang = stream_lang(sub)
    blob = _blob(sub)
    if lang in group["exclude"] or _has_token(blob, group["exclude_tokens"]):
        return -120.0
    if lang in group["include"] or _has_token(blob, group["include_tokens"]):
        return 80.0
    return 0.0


def subtitle_score(sub: dict, *, include_hi: bool = False, wanted: str = "") -> float:
    """Higher is better. Forced tracks are last-resort (negative unless nothing else)."""
    if sub.get("IsForced") or sub.get("isForced"):
        return -100.0
    hi = bool(sub.get("IsHearingImpaired") or sub.get("isHearingImpaired"))
    hi_penalty = 40.0 if hi and not include_hi else 0.0
    hash_bonus = 1000.0 if (sub.get("IsHashMatch") or sub.get("isHashMatch")) else 0.0
    rating = float(sub.get("CommunityRating") or sub.get("communityRating") or 0)
    downloads = int(sub.get("DownloadCount") or sub.get("downloadCount") or 0)
    variant = variant_adjust(sub, wanted) if wanted else 0.0
    return hash_bonus + rating * 10.0 + min(downloads, 8000) / 80.0 - hi_penalty + variant


def pick_best(subs: list[dict], *, include_hi: bool = False, wanted: str = "") -> dict | None:
    ranked = sorted(subs, key=lambda s: subtitle_score(s, include_hi=include_hi, wanted=wanted), reverse=True)
    return ranked[0] if ranked else None


def map_path(jellyfin_path: str | None, maps: list[dict]) -> str | None:
    if not jellyfin_path:
        return None
    for item in maps or []:
        src = (item.get("from") or "").rstrip("/")
        dst = (item.get("to") or "").rstrip("/")
        if src and jellyfin_path.startswith(src):
            return dst + jellyfin_path[len(src) :]
    return jellyfin_path
