"""Runnable check for subtitle ranking, language groups, and Bazarr catalog."""

from __future__ import annotations

import io
import unittest
import zipfile

from app.catalog import PROVIDERS, serves
from app.logic import (
    build_library,
    has_language,
    map_path,
    missing_langs,
    normalize_langs,
    os_movie_hash,
    pick_best,
    provider_langs,
    search_code,
    subtitle_score,
    wanted_langs,
)
from app.providers import DOWNLOAD, SEARCH, TEST, query_from_item, unzip_sub
from app.worker import _provider_names


class LanguageTests(unittest.TestCase):
    def test_unlabeled_spanish_counts_as_latino(self):
        streams = [{"Type": "Subtitle", "Language": "spa"}]
        self.assertTrue(has_language(streams, "es-419"))
        self.assertTrue(has_language(streams, "es"))

    def test_mexico_is_latino_not_spain(self):
        streams = [{"Type": "Subtitle", "Language": "es-MX"}]
        self.assertTrue(has_language(streams, "es-419"))
        self.assertFalse(has_language(streams, "es-ES"))

    def test_spain_does_not_fill_latino(self):
        streams = [{"Type": "Subtitle", "Language": "es-ES"}]
        self.assertFalse(has_language(streams, "es-419"))
        self.assertTrue(has_language(streams, "es-ES"))

    def test_latam_in_title(self):
        streams = [{"Type": "Subtitle", "Language": "spa", "DisplayTitle": "Spanish (LATAM)"}]
        self.assertTrue(has_language(streams, "es-419"))
        self.assertFalse(has_language(streams, "es-ES"))

    def test_forced_does_not_count(self):
        streams = [{"Type": "Subtitle", "Language": "es-MX", "IsForced": True}]
        self.assertFalse(has_language(streams, "es-419"))
        self.assertTrue(has_language(streams, "es-419", count_forced=True))

    def test_english_is_not_spanish(self):
        streams = [{"Type": "Subtitle", "Language": "eng"}, {"Type": "Audio", "Language": "eng"}]
        self.assertFalse(has_language(streams, "es-419"))

    def test_search_code_is_iso(self):
        self.assertEqual(search_code("es-419"), "es")
        self.assertEqual(search_code("es-ES"), "es")
        self.assertEqual(search_code("pt-BR"), "pt")

    def test_provider_langs_include_region(self):
        self.assertIn("es-MX", provider_langs("es-419"))
        self.assertIn("es-ES", provider_langs("es-ES"))

    def test_wanted_langs_from_list_or_legacy(self):
        self.assertEqual(wanted_langs({"languages": ["en", "de"]}), ["en", "de"])
        self.assertEqual(wanted_langs({"language": "es"}), ["es-419"])
        self.assertEqual(normalize_langs(["es-419", "es-419", "en"]), ["es-419", "en"])
        self.assertEqual(normalize_langs([]), [])

    def test_missing_langs_skips_ones_already_there(self):
        streams = [{"Type": "Subtitle", "Language": "spa"}]
        self.assertEqual(missing_langs(streams, ["es-419", "en", "de"]), ["en", "de"])

    def test_library_groups_series_by_season(self):
        items = [
            {
                "Id": "e1",
                "Type": "Episode",
                "Name": "Pilot",
                "SeriesName": "Andor",
                "SeriesId": "s1",
                "ParentIndexNumber": 1,
                "IndexNumber": 1,
                "MediaStreams": [{"Type": "Subtitle", "Language": "spa"}],
            },
            {
                "Id": "e2",
                "Type": "Episode",
                "Name": "One Year Later",
                "SeriesName": "Andor",
                "SeriesId": "s1",
                "ParentIndexNumber": 1,
                "IndexNumber": 2,
                "MediaStreams": [{"Type": "Subtitle", "Language": "eng"}],
            },
            {
                "Id": "m1",
                "Type": "Movie",
                "Name": "Dune",
                "ProductionYear": 2021,
                "MediaStreams": [{"Type": "Subtitle", "Language": "spa"}],
            },
        ]
        cats = build_library(items, ["es-419", "en"])
        groups = [g for cat in cats for g in cat["groups"]]
        show = next(g for g in groups if g["kind"] == "series")
        movie = next(g for g in groups if g["kind"] == "movie")
        self.assertEqual([c["name"] for c in cats], ["Películas", "Series"])
        self.assertEqual(show["title"], "Andor")
        self.assertEqual(show["total"], 2)
        self.assertEqual(show["counts"]["es-419"], 1)
        self.assertEqual(show["counts"]["en"], 1)
        self.assertEqual(show["seasons"][0]["episodes"][0]["have"], ["es-419"])
        self.assertEqual(movie["missing"], ["en"])
        self.assertEqual(movie["have"], ["es-419"])
        self.assertEqual(movie["title"], "Dune")

    def test_library_uses_jellyfin_folder_names(self):
        items = [
            {
                "Id": "m1",
                "Type": "Movie",
                "Name": "Dune",
                "Path": "/data/pelis/Dune.mkv",
                "MediaStreams": [],
            },
            {
                "Id": "e1",
                "Type": "Episode",
                "Name": "Pilot",
                "SeriesName": "Andor",
                "SeriesId": "s1",
                "ParentIndexNumber": 1,
                "IndexNumber": 1,
                "Path": "/data/series/Andor/S01E01.mkv",
                "MediaStreams": [],
            },
        ]
        folders = [
            {"name": "Películas", "locations": ["/data/pelis"]},
            {"name": "Series", "locations": ["/data/series"]},
        ]
        cats = build_library(items, ["en"], folders)
        self.assertEqual([c["name"] for c in cats], ["Películas", "Series"])
        self.assertEqual(cats[0]["groups"][0]["title"], "Dune")
        self.assertEqual(cats[1]["groups"][0]["title"], "Andor")


class HashTests(unittest.TestCase):
    def test_stable_hex(self):
        head = b"\x01" * 65536
        tail = b"\x02" * 65536
        digest = os_movie_hash(200000, head, tail)
        self.assertEqual(len(digest), 16)
        self.assertEqual(digest, os_movie_hash(200000, head, tail))
        self.assertNotEqual(digest, os_movie_hash(200001, head, tail))


class RankTests(unittest.TestCase):
    def test_hash_match_beats_popular(self):
        popular = {"DownloadCount": 9000, "CommunityRating": 9.5, "IsHashMatch": False}
        exact = {"DownloadCount": 12, "CommunityRating": 6.0, "IsHashMatch": True}
        self.assertGreater(subtitle_score(exact), subtitle_score(popular))
        self.assertEqual(pick_best([popular, exact]), exact)

    def test_prefers_latino_when_asked(self):
        spain = {"Name": "Spanish (Spain)", "Language": "es-ES", "CommunityRating": 9, "DownloadCount": 4000}
        latam = {"Name": "Spanish (LATAM)", "Language": "es-MX", "CommunityRating": 6, "DownloadCount": 100}
        self.assertEqual(pick_best([spain, latam], wanted="es-419"), latam)
        self.assertEqual(pick_best([spain, latam], wanted="es-ES"), spain)

    def test_forced_is_last(self):
        forced = {"IsForced": True, "IsHashMatch": True, "CommunityRating": 10}
        normal = {"IsHashMatch": False, "CommunityRating": 1, "DownloadCount": 1}
        self.assertEqual(pick_best([forced, normal]), normal)

    def test_hi_penalized_unless_wanted(self):
        hi = {"IsHearingImpaired": True, "CommunityRating": 8, "DownloadCount": 100}
        clean = {"CommunityRating": 7, "DownloadCount": 100}
        self.assertEqual(pick_best([hi, clean], include_hi=False), clean)
        self.assertEqual(pick_best([hi, clean], include_hi=True), hi)


class PathTests(unittest.TestCase):
    def test_rewrites_prefix(self):
        mapped = map_path("/data/media/pelis/foo.mkv", [{"from": "/data/media", "to": "/media"}])
        self.assertEqual(mapped, "/media/pelis/foo.mkv")

    def test_passthrough_when_no_map(self):
        self.assertEqual(map_path("/media/foo.mkv", []), "/media/foo.mkv")


class CatalogTests(unittest.TestCase):
    def test_ids_unique_and_wired(self):
        ids = [p["id"] for p in PROVIDERS]
        self.assertEqual(len(ids), len(set(ids)))
        for p in PROVIDERS:
            self.assertTrue({"en", "es"} & set(p["langs"]), p["id"])
            self.assertIn(p["id"], SEARCH)
            self.assertIn(p["id"], TEST)
            if p["id"] not in {"addic7ed", "pipocas"}:
                self.assertIn(p["id"], DOWNLOAD)

    def test_spanish_only_skipped_for_english(self):
        self.assertTrue(serves("subtis", "es"))
        self.assertFalse(serves("subtis", "en"))
        self.assertTrue(serves("titlovi", "en"))
        self.assertFalse(serves("titlovi", "es"))
        self.assertTrue(serves("podnapisi", "de"))
        self.assertFalse(serves("subtis", "de"))


class QueryTests(unittest.TestCase):
    def test_tvdb_and_filename(self):
        q = query_from_item(
            {
                "Type": "Episode",
                "Name": "Reckoning",
                "SeriesName": "Andor",
                "ParentIndexNumber": 1,
                "IndexNumber": 3,
                "Path": "/media/Andor.S01E03.mkv",
                "ProviderIds": {"Imdb": "tt1111111", "Tvdb": "393189"},
            }
        )
        self.assertEqual(q["kind"], "episode")
        self.assertEqual(q["tvdb"], "393189")
        self.assertEqual(q["filename"], "Andor.S01E03.mkv")
        self.assertEqual(q["imdb"], "1111111")


class ZipTests(unittest.TestCase):
    def test_plain_passthrough(self):
        data, name = unzip_sub(b"1\n00:00:01,000 --> 00:00:02,000\nhi\n", "a.srt")
        self.assertTrue(data.startswith(b"1"))
        self.assertEqual(name, "a.srt")

    def test_unzip_picks_srt(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("readme.txt", "no")
            zf.writestr("movie.srt", "1\n")
        data, name = unzip_sub(buf.getvalue(), "x.srt")
        self.assertEqual(name, "movie.srt")
        self.assertEqual(data, b"1\n")


class RetryDetailTests(unittest.TestCase):
    def test_provider_names_from_search_errors(self):
        self.assertEqual(
            _provider_names(["subf2m.co: No hay archivo", "Podnapisi: timeout"]),
            "subf2m.co · Podnapisi",
        )


if __name__ == "__main__":
    unittest.main()
