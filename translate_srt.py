#!/usr/bin/env python3
"""CLI: traduce un .srt con Gemini. La lógica vive en app.translate."""

from __future__ import annotations

import argparse
import json
import os
import sys
from getpass import getpass
from pathlib import Path

from app.translate import (
    BATCH_SIZE,
    CONTEXT_EXAMPLE,
    FilmContext,
    chunk_cues,
    fetch_tmdb_context,
    parse_srt,
    render_srt,
    strip_model_noise,
    translate_srt_text,
)

# Compat: ejemplos editables en CLI (alias del dict en app.translate)
CONTEXT = CONTEXT_EXAMPLE


def resolve_api_key(cli_key: str | None, *, prompt: bool) -> str:
    key = (cli_key or os.environ.get("GEMINI_API_KEY") or "").strip()
    if key:
        return key
    if prompt and sys.stdin.isatty():
        key = getpass("GEMINI_API_KEY: ").strip()
        if key:
            return key
    raise SystemExit("Falta GEMINI_API_KEY (--api-key, env o prompt).")


def resolve_tmdb_key(cli_key: str | None, *, prompt: bool) -> str:
    key = (cli_key or os.environ.get("TMDB_API_KEY") or "").strip()
    if key:
        return key
    if prompt and sys.stdin.isatty():
        return getpass("TMDB_API_KEY (Enter para omitir): ").strip()
    return ""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Traduce un .srt con Gemini 2.5 Flash.")
    p.add_argument("input", nargs="?", type=Path, help="Archivo .srt de entrada")
    p.add_argument("-o", "--output", type=Path, default=None)
    p.add_argument("--api-key", default=None)
    p.add_argument("--no-prompt-key", action="store_true")
    p.add_argument("--source", default="en")
    p.add_argument("--target", default="es")
    p.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    p.add_argument("--sleep", type=float, default=0.5)
    p.add_argument("--title", default=None)
    p.add_argument("--year", type=int, default=None)
    p.add_argument("--kind", choices=("auto", "movie", "tv"), default="auto")
    p.add_argument("--tmdb-key", default=None)
    p.add_argument("--context-json", type=Path, default=None)
    p.add_argument("--dump-context", type=Path, default=None)
    p.add_argument("--self-check", action="store_true")
    return p


def self_check() -> None:
    sample = (
        "1\n00:00:01,000 --> 00:00:02,500\nHello, Deckard.\n\n"
        "2\n00:00:03,000 --> 00:00:04,000\nLine A\nLine B\n"
    )
    cues = parse_srt(sample)
    assert len(cues) == 2 and cues[1].text == "Line A\nLine B"
    assert [c.text for c in parse_srt(render_srt(cues))] == [c.text for c in cues]
    assert len(chunk_cues(cues, 1)) == 2
    assert strip_model_noise("```srt\n1\n00:00:01,000 --> 00:00:02,000\nHola\n```").startswith("1\n")
    print("self-check ok")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.self_check:
        self_check()
        return

    ctx = FilmContext.from_dict(CONTEXT)
    if args.context_json:
        file_ctx = FilmContext.from_dict(json.loads(args.context_json.read_text(encoding="utf-8")))
        ctx.title = file_ctx.title or ctx.title
        ctx.synopsis = file_ctx.synopsis or ctx.synopsis
        if file_ctx.tone:
            ctx.tone = file_ctx.tone
        ctx.merge_names(file_ctx.do_not_translate)
        ctx.glossary.update(file_ctx.glossary)

    if args.title:
        tmdb_key = resolve_tmdb_key(args.tmdb_key, prompt=not args.no_prompt_key)
        if not tmdb_key:
            raise SystemExit("Para --title hace falta TMDB_API_KEY.")
        tmdb_ctx = fetch_tmdb_context(args.title, api_key=tmdb_key, year=args.year, kind=args.kind)
        ctx.title = tmdb_ctx.title
        if tmdb_ctx.synopsis:
            ctx.synopsis = tmdb_ctx.synopsis
        ctx.merge_names(tmdb_ctx.do_not_translate)
        print(f"Ficha TMDB: {ctx.title} — {len(ctx.do_not_translate)} nombres", flush=True)

    if args.dump_context:
        payload = {
            "title": ctx.title,
            "synopsis": ctx.synopsis,
            "tone": ctx.tone,
            "do_not_translate": ctx.do_not_translate,
            "glossary": ctx.glossary,
        }
        args.dump_context.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Contexto escrito en {args.dump_context}")
        return

    if args.input is None:
        raise SystemExit("Indica el archivo .srt (o --self-check / --dump-context).")
    if not args.input.is_file():
        raise SystemExit(f"No existe: {args.input}")

    out = args.output or args.input.with_name(args.input.stem + f".{args.target}.srt")
    api_key = resolve_api_key(args.api_key, prompt=not args.no_prompt_key)
    text = translate_srt_text(
        args.input.read_text(encoding="utf-8"),
        api_key=api_key,
        ctx=ctx,
        source_lang=args.source,
        target_lang=args.target,
        batch_size=args.batch_size,
        sleep_s=args.sleep,
        on_batch=lambda i, total, n: print(f"Lote {i}/{total} ({n} bloques)…", flush=True),
    )
    out.write_text(text, encoding="utf-8")
    print(f"Guardado: {out}", flush=True)


if __name__ == "__main__":
    main()
