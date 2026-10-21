"""Traducción SRT con Gemini + glosario TMDB. Sin I/O de Jellyfin."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from app.logic import LEGACY, PROVIDER_LANGS, search_code

MODEL = "gemini-2.5-flash"
BATCH_SIZE = 35
TEMPERATURE = 0.2
TMDB = "https://api.themoviedb.org/3"
UA = "Subss/0.1.0"

TIMESTAMP_RE = re.compile(
    r"^\d{2}:\d{2}:\d{2}[,.]\d{3}\s-->\s\d{2}:\d{2}:\d{2}[,.]\d{3}"
)
BLOCK_RE = re.compile(
    r"(?m)^\s*(\d+)\s*\n"
    r"(\d{2}:\d{2}:\d{2}[,.]\d{3}\s-->\s\d{2}:\d{2}:\d{2}[,.]\d{3}[^\n]*)\n"
    r"([\s\S]*?)(?=\n\s*\d+\s*\n\d{2}:\d{2}:\d{2}|\Z)"
)

TONE_BY_LANG = {
    "es-419": "neutro, natural, español latinoamericano (es-419); evita español de España",
    "es-ES": "neutro, natural, español de España (castellano)",
    "pt-BR": "neutro, natural, português do Brasil",
    "pt-PT": "neutro, natural, português de Portugal",
    "en": "neutral, natural American English",
    "en-GB": "neutral, natural British English",
    "fr": "neutre, naturel, français de France",
    "fr-CA": "neutre, naturel, français canadien",
    "de": "neutral, natürliches Deutsch",
    "it": "neutro, italiano naturale",
    "ja": "自然な日本語",
    "ko": "자연스러운 한국어",
    "zh-Hans": "自然的简体中文",
    "zh-Hant": "自然的繁體中文",
}

CONTEXT_EXAMPLE: dict = {
    "title": "Ejemplo: Blade Runner 2049",
    "synopsis": (
        "En un futuro distópico, el blade runner K descubre un secreto que podría "
        "desestabilizar lo que queda de la sociedad."
    ),
    "tone": TONE_BY_LANG["es-419"],
    "do_not_translate": ["K", "Deckard", "Joi", "Wallace", "Sapper Morton"],
    "glossary": {
        "blade runner": "blade runner",
        "replicant": "replicante",
        "baseline": "línea base",
    },
}


@dataclass
class Cue:
    index: int
    timestamp: str
    text: str

    def render(self) -> str:
        ts = self.timestamp.replace(".", ",")
        body = self.text.strip("\n")
        return f"{self.index}\n{ts}\n{body}"


@dataclass
class FilmContext:
    title: str = ""
    synopsis: str = ""
    tone: str = TONE_BY_LANG["es-419"]
    do_not_translate: list[str] = field(default_factory=list)
    glossary: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> FilmContext:
        return cls(
            title=str(data.get("title") or ""),
            synopsis=str(data.get("synopsis") or ""),
            tone=str(data.get("tone") or cls().tone),
            do_not_translate=[str(x) for x in (data.get("do_not_translate") or [])],
            glossary={str(k): str(v) for k, v in (data.get("glossary") or {}).items()},
        )

    def merge_names(self, names: list[str]) -> None:
        seen = {n.casefold() for n in self.do_not_translate}
        for name in names:
            name = name.strip()
            if not name or name.casefold() in seen:
                continue
            self.do_not_translate.append(name)
            seen.add(name.casefold())


def parse_srt(raw: str) -> list[Cue]:
    text = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    if text.startswith("\ufeff"):
        text = text[1:]
    cues: list[Cue] = []
    for m in BLOCK_RE.finditer(text + "\n"):
        idx = int(m.group(1))
        ts = m.group(2).strip().replace(".", ",")
        body = m.group(3).strip("\n")
        if not TIMESTAMP_RE.match(ts.split("\n", 1)[0]):
            continue
        cues.append(Cue(index=idx, timestamp=ts, text=body))
    if not cues:
        raise ValueError("No se encontraron bloques SRT válidos")
    return cues


def render_srt(cues: list[Cue]) -> str:
    return "\n\n".join(c.render() for c in cues) + "\n"


def chunk_cues(cues: list[Cue], size: int) -> list[list[Cue]]:
    if size < 1:
        raise ValueError("batch size debe ser >= 1")
    return [cues[i : i + size] for i in range(0, len(cues), size)]


def strip_model_noise(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:srt|SRT)?\s*\n?", "", t)
        t = re.sub(r"\n?```\s*$", "", t)
    return t.strip()


def tone_for(target_lang: str) -> str:
    code = LEGACY.get(target_lang, target_lang)
    return TONE_BY_LANG.get(code, f"neutro, natural ({target_lang})")


def jellyfin_lang_tag(wanted: str) -> str:
    code = LEGACY.get(wanted, wanted)
    first = (PROVIDER_LANGS.get(code) or search_code(code)).split(",")[0]
    return first.strip()


def build_system_instruction(ctx: FilmContext, source_lang: str, target_lang: str) -> str:
    names = ", ".join(ctx.do_not_translate) if ctx.do_not_translate else "(ninguno)"
    gloss_lines = (
        "\n".join(f"  - {k} → {v}" for k, v in ctx.glossary.items())
        if ctx.glossary
        else "  (vacío)"
    )
    return f"""Eres un traductor profesional de subtítulos cinematográficos.

OBRA: {ctx.title or "(sin título)"}
SINOPSIS: {ctx.synopsis or "(sin sinopsis)"}
TONO: {ctx.tone or tone_for(target_lang)}
IDIOMA ORIGEN: {source_lang}
IDIOMA DESTINO: {target_lang}

NOMBRES / TÉRMINOS QUE NO DEBES TRADUCIR (conserva exactamente):
{names}

GLOSARIO (usa estas equivalencias fijas):
{gloss_lines}

REGLAS DE FORMATO (obligatorias, sin excepciones):
1. Devuelve ÚNICAMENTE el subtítulo en formato SRT válido.
2. Conserva al 100% el número de cada bloque y la línea de timestamps
   exactamente como te la doy (formato 00:00:00,000 --> 00:00:00,000).
3. Traduce solo el texto hablado; no inventes ni borres bloques.
4. Mantén saltos de línea dentro de cada bloque cuando sea razonable.
5. No agregues saludos, explicaciones, notas, ni envolturas markdown
   (prohibido ```srt o cualquier fence).
6. No renumeres bloques. Si el lote empieza en 41, el primero sigue siendo 41.
7. Si un nombre del glosario/lista aparece en el diálogo, déjalo sin traducir.
"""


def _http_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=25) as res:
            return json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:200]
        raise RuntimeError(f"HTTP {exc.code} en TMDB: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"No se pudo contactar TMDB: {exc}") from exc


def _cast_names(credits: dict) -> list[str]:
    skip = {"himself", "herself", "self", "narrator", "host", "guest"}
    names: list[str] = []
    for row in (credits.get("cast") or [])[:40]:
        character = (row.get("character") or "").strip()
        if not character:
            continue
        for part in re.split(r"[/|]", character):
            part = re.sub(r"\s*\(.*?\)\s*", " ", part).strip()
            if not part or part.casefold() in skip or len(part) > 48:
                continue
            names.append(part)
            first = part.split(",", 1)[0].split()[0]
            if first != part and first[:1].isupper() and len(first) > 1:
                names.append(first)
    uniq: list[str] = []
    seen: set[str] = set()
    for n in names:
        k = n.casefold()
        if k in seen:
            continue
        seen.add(k)
        uniq.append(n)
    return uniq


def fetch_tmdb_by_id(
    tmdb_id: str | int,
    *,
    api_key: str,
    kind: str = "movie",
    language: str = "es-MX",
) -> FilmContext:
    key = api_key.strip()
    media = "tv" if kind in ("tv", "episode", "series") else "movie"
    q = urllib.parse.urlencode({"api_key": key, "language": language})
    detail = _http_json(f"{TMDB}/{media}/{tmdb_id}?{q}")
    credits = _http_json(f"{TMDB}/{media}/{tmdb_id}/credits?{q}")
    display = detail.get("title") or detail.get("name") or str(tmdb_id)
    return FilmContext(
        title=str(display),
        synopsis=(detail.get("overview") or "").strip(),
        do_not_translate=_cast_names(credits),
    )


def fetch_tmdb_context(
    title: str,
    *,
    api_key: str,
    year: int | None = None,
    kind: str = "auto",
    language: str = "es-MX",
) -> FilmContext:
    key = api_key.strip()
    if not key:
        raise ValueError("Falta TMDB API key")

    def search(media: str) -> dict | None:
        params = {"api_key": key, "query": title, "language": language}
        if year and media == "movie":
            params["year"] = str(year)
        if year and media == "tv":
            params["first_air_date_year"] = str(year)
        data = _http_json(f"{TMDB}/search/{media}?{urllib.parse.urlencode(params)}")
        results = data.get("results") or []
        return results[0] if results else None

    if kind == "auto":
        candidates = []
        movie = search("movie")
        tv = search("tv")
        if movie:
            candidates.append(("movie", movie))
        if tv:
            candidates.append(("tv", tv))
        if not candidates:
            raise RuntimeError(f"TMDB no encontró «{title}»")
        # ponytail: popularity pick; upgrade: interactive chooser
        media, hit = max(candidates, key=lambda c: float(c[1].get("popularity") or 0))
    else:
        media = "tv" if kind in ("tv", "episode", "series") else "movie"
        hit = search(media)
        if not hit:
            raise RuntimeError(f"TMDB no encontró {media} «{title}»")

    return fetch_tmdb_by_id(hit["id"], api_key=key, kind=media, language=language)


def context_for_item(item: dict, tmdb_key: str, target_lang: str) -> FilmContext:
    episode = (item.get("Type") or "") == "Episode"
    title = (item.get("SeriesName") if episode else item.get("Name")) or ""
    year = item.get("ProductionYear")
    kind = "tv" if episode else "movie"
    ids = item.get("ProviderIds") or {}
    tmdb = str(ids.get("Tmdb") or ids.get("TmdbId") or "").strip()
    ctx = FilmContext(title=title, tone=tone_for(target_lang))
    if not tmdb_key:
        return ctx
    try:
        if tmdb:
            fetched = fetch_tmdb_by_id(tmdb, api_key=tmdb_key, kind=kind)
        elif title:
            fetched = fetch_tmdb_context(title, api_key=tmdb_key, year=year, kind=kind)
        else:
            return ctx
    except Exception:  # noqa: BLE001 — sin ficha seguimos con el título
        return ctx
    ctx.title = fetched.title or ctx.title
    if fetched.synopsis:
        ctx.synopsis = fetched.synopsis
    ctx.merge_names(fetched.do_not_translate)
    return ctx


def translate_batch(client, batch: list[Cue], *, system_instruction: str) -> list[Cue]:
    from google.genai import types

    prompt = (
        "Traduce el siguiente fragmento SRT según las reglas del sistema. "
        "Respuesta = solo SRT.\n\n"
        f"{render_srt(batch)}"
    )
    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=TEMPERATURE,
        ),
    )
    translated = parse_srt(strip_model_noise(response.text or ""))
    if len(translated) != len(batch):
        raise RuntimeError(
            f"El modelo devolvió {len(translated)} bloques; se esperaban {len(batch)}"
        )
    return [
        Cue(index=src.index, timestamp=src.timestamp, text=dst.text)
        for src, dst in zip(batch, translated)
    ]


def translate_srt_text(
    raw: str,
    *,
    api_key: str,
    ctx: FilmContext,
    source_lang: str,
    target_lang: str,
    batch_size: int = BATCH_SIZE,
    sleep_s: float = 0.4,
    on_batch=None,
) -> str:
    from google import genai

    cues = parse_srt(raw)
    batches = chunk_cues(cues, batch_size)
    if not ctx.tone:
        ctx.tone = tone_for(target_lang)
    system_instruction = build_system_instruction(ctx, source_lang, target_lang)
    client = genai.Client(api_key=api_key)
    done: list[Cue] = []
    total = len(batches)
    for i, batch in enumerate(batches, start=1):
        if on_batch:
            on_batch(i, total, len(batch))
        last_err: Exception | None = None
        for attempt in range(1, 4):
            try:
                done.extend(translate_batch(client, batch, system_instruction=system_instruction))
                last_err = None
                break
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                time.sleep(2**attempt)
        if last_err is not None:
            raise last_err
        if sleep_s > 0 and i < total:
            time.sleep(sleep_s)
    return render_srt(done)
