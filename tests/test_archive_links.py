import importlib.util
from pathlib import Path

import pandas as pd


def load_extractor():
    path = Path(__file__).resolve().parents[1] / "scripts" / "scorecard_vote_extractor.py"
    spec = importlib.util.spec_from_file_location("scorecard_vote_extractor", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_generator():
    path = Path(__file__).resolve().parents[1] / "scripts" / "kvrosc_hall_of_fame_generator.py"
    spec = importlib.util.spec_from_file_location("kvrosc_hall_of_fame_generator", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_archive_enrich_result_rows_assigns_stream_links_in_order(monkeypatch):
    mod = load_extractor()
    monkeypatch.setattr(mod, "archive_verify_download_url", lambda url, timeout=15: True)
    result_rows = [
        {"rank": "1", "artist": "Artist One", "track": "Track One", "entry": "Artist One - Track One"},
        {"rank": "2", "artist": "Artist Two", "track": "Track Two", "entry": "Artist Two - Track Two"},
        {"rank": "3", "artist": "Artist Three", "track": "Track Three", "entry": "Artist Three - Track Three"},
    ]
    archive_sum = {"archive_identifier": "One-Synth-Challenge-999-Fake"}
    archive_detail = [
        {"archive_file": "", "archive_title": "", "match_kind": "MISSING"},
        {"archive_file": "", "archive_title": "", "match_kind": "MISSING"},
        {"archive_file": "", "archive_title": "", "match_kind": "MISSING"},
    ]
    archive_tracks = [
        {"file_name": "01 Artist One - Track One.mp3", "track": "Track One"},
        {"file_name": "02 Artist Two - Track Two.mp3", "track": "Track Two"},
        {"file_name": "03 Artist Three - Track Three.mp3", "track": "Track Three"},
    ]

    enriched = mod.archive_enrich_result_rows(result_rows, archive_sum, archive_detail, archive_tracks)

    assert len(enriched) == 3
    assert all(row["archive_url"] for row in enriched)
    assert all(row["url"] == "https://archive.org/details/One-Synth-Challenge-999-Fake" for row in enriched)
    assert [row["archive_match_kind"] for row in enriched] == ["EXACT", "EXACT", "EXACT"]
    assert enriched[0]["archive_url"].endswith("01%20Artist%20One%20-%20Track%20One.mp3")
    assert enriched[1]["archive_url"].endswith("02%20Artist%20Two%20-%20Track%20Two.mp3")
    assert enriched[2]["archive_url"].endswith("03%20Artist%20Three%20-%20Track%20Three.mp3")


def test_archive_enrich_result_rows_keeps_exact_matches(monkeypatch):
    mod = load_extractor()
    monkeypatch.setattr(mod, "archive_verify_download_url", lambda url, timeout=15: True)
    result_rows = [
        {"rank": "1", "artist": "Artist One", "track": "Track One", "entry": "Artist One - Track One"},
    ]
    archive_sum = {"archive_identifier": "One-Synth-Challenge-999-Fake"}
    archive_detail = [
        {
            "archive_identifier": "One-Synth-Challenge-999-Fake",
            "archive_title": "One Synth Challenge #999 Fake",
            "archive_file": "01 Artist One - Track One.mp3",
            "archive_track": "Track One",
            "match_kind": "EXACT",
        }
    ]
    archive_tracks = [
        {"file_name": "01 Artist One - Track One.mp3", "track": "Track One"},
    ]

    enriched = mod.archive_enrich_result_rows(result_rows, archive_sum, archive_detail, archive_tracks)

    assert enriched[0]["archive_match_kind"] == "EXACT"
    assert enriched[0]["archive_title"] == "Track One"
    assert enriched[0]["archive_url"].endswith("01%20Artist%20One%20-%20Track%20One.mp3")


def test_archive_enrich_result_rows_matches_tracks_by_artist_and_title_not_order(monkeypatch):
    mod = load_extractor()
    monkeypatch.setattr(mod, "archive_verify_download_url", lambda url, timeout=15: True)
    result_rows = [
        {"rank": "1", "artist": "Artist One", "track": "Track One", "entry": "Artist One - Track One"},
        {"rank": "2", "artist": "Artist Two", "track": "Track Two", "entry": "Artist Two - Track Two"},
    ]
    archive_sum = {"archive_identifier": "One-Synth-Challenge-999-Fake"}
    archive_detail = [
        {"archive_file": "", "archive_title": "", "match_kind": "MISSING"},
        {"archive_file": "", "archive_title": "", "match_kind": "MISSING"},
    ]
    archive_tracks = [
        {"file_name": "02 Artist Two - Track Two.mp3", "artist": "Artist Two", "track": "Track Two"},
        {"file_name": "01 Artist One - Track One.mp3", "artist": "Artist One", "track": "Track One"},
    ]

    enriched = mod.archive_enrich_result_rows(result_rows, archive_sum, archive_detail, archive_tracks)

    assert enriched[0]["archive_file"] == "01 Artist One - Track One.mp3"
    assert enriched[0]["archive_title"] == "Track One"
    assert enriched[1]["archive_file"] == "02 Artist Two - Track Two.mp3"
    assert enriched[1]["archive_title"] == "Track Two"
    assert [row["archive_match_kind"] for row in enriched] == ["EXACT", "EXACT"]


def test_archive_enrich_result_rows_matches_artist_alias_variants(monkeypatch):
    mod = load_extractor()
    monkeypatch.setattr(mod, "archive_verify_download_url", lambda url, timeout=15: True)
    result_rows = [
        {"rank": "5", "artist": "SIL3NC3_SWX", "track": "Mind Caves", "entry": "SIL3NC3_SWX - Mind Caves"},
    ]
    archive_sum = {"archive_identifier": "One-Synth-Challenge-192-Six-Sines"}
    archive_detail = [
        {"archive_file": "", "archive_title": "", "match_kind": "MISSING"},
    ]
    archive_tracks = [
        {
            "file_name": "05 SiL3NC3 SWX SoulWerX - Mind Caves (OSC192).mp3",
            "artist": "SiL3NC3 SWX SoulWerX",
            "track": "Mind Caves",
        }
    ]

    enriched = mod.archive_enrich_result_rows(result_rows, archive_sum, archive_detail, archive_tracks)

    assert enriched[0]["archive_file"] == "05 SiL3NC3 SWX SoulWerX - Mind Caves (OSC192).mp3"
    assert enriched[0]["archive_match_kind"] == "FUZZY"


def test_find_best_archive_track_match_strips_archive_osc_suffixes():
    mod = load_extractor()
    result_row = {"rank": "5", "artist": "Puma17", "track": "Brassed On", "entry": "Puma17 - Brassed On"}
    archive_tracks = [
        {
            "file_name": "04 Puma17 - Brassed On OSC180 (Waved).mp3",
            "artist": "Puma17",
            "track": "Brassed On OSC180 (Waved)",
        }
    ]

    match, kind = mod.find_best_archive_track_match(result_row, archive_tracks)

    assert match is not None
    assert match["file_name"] == "04 Puma17 - Brassed On OSC180 (Waved).mp3"
    assert kind in {"ENTRY", "RELAXED"}


def test_repair_result_rows_with_archive_fixes_track_first_legacy_entries():
    mod = load_extractor()
    result_rows = [
        {
            "rank": "1",
            "artist": "TIRING_YEARS",
            "artist_key": "tiring_years",
            "artist_canonical": "TIRING_YEARS",
            "track": "Mac of BIOnighT",
            "entry": "TIRING_YEARS - Mac of BIOnighT",
            "points": 52.0,
        },
        {
            "rank": "2",
            "artist": "Mike777",
            "artist_key": "mike777",
            "artist_canonical": "Mike777",
            "track": "Fluorescent Sky",
            "entry": "Mike777 - Fluorescent Sky",
            "points": 42.0,
        },
    ]
    archive_tracks = [
        {"artist": "Mac_of_BIOnighT", "track": "Tiring Years", "file_name": "01Mac_of_BIOnighT-TIRING_YEARS.mp3"},
        {"artist": "Mike777", "track": "Fluorescent Sky", "file_name": "01Mike777-FluorescentSky.mp3"},
    ]

    repaired = mod.repair_result_rows_with_archive(result_rows, archive_tracks)

    assert repaired[0]["artist"] == "Mac of BIOnighT"
    assert repaired[0]["track"] == "TIRING_YEARS"
    assert repaired[1]["artist"] == "Mike777"
    assert repaired[1]["track"] == "Fluorescent Sky"


def test_v02_total_scores_without_headers_use_rank_entry_points_columns():
    mod = load_extractor()
    book = mod.XlsxBook(str(Path(__file__).resolve().parents[1] / "scorecards" / "OSC062_PolyIblit_1.xlsx"))

    rows = mod.extract_results(book, mod.classify(book))

    assert [row["rank"] for row in rows[:5]] == ["1", "2", "3", "3", "4"]
    assert rows[0]["artist"] == "Z.prime"
    assert rows[0]["track"] == "We Can Overcome"
    assert rows[0]["points"] == 134.0
    assert rows[1]["artist"] == "Pulse Width Modulation"
    assert rows[1]["track"] == "The Spanish Captain"
    assert any(row["artist"] == "Yeager" and row["track"] == "Close...But No Sitar" for row in rows[:6])


def test_v02_total_scores_entry_score_layout_without_rank_column():
    mod = load_extractor()
    book = mod.XlsxBook(str(Path(__file__).resolve().parents[1] / "scorecards" / "OSC022_NR2010_1.xlsx"))

    rows = mod.extract_results(book, mod.classify(book))

    assert len(rows) == 13
    assert rows[0]["artist"] == "TIRING_YEARS"
    assert rows[0]["track"] == "Mac of BIOnighT"
    assert rows[0]["points"] == 52.0


def test_v04_total_scores_title_row_entry_score_layout():
    mod = load_extractor()
    book = mod.XlsxBook(str(Path(__file__).resolve().parents[1] / "scorecards" / "OSC060_Any-One-Synth_1.xlsx"))

    rows = mod.extract_results(book, mod.classify(book))

    assert len(rows) == 68
    assert rows[0]["artist"] == "bzur"
    assert rows[0]["track"] == "Escher's Metamorphosis II [Zebra]"
    assert rows[0]["points"] == 3611.0


def test_archive_rows_mark_verified_links_when_archive_file_exists(monkeypatch):
    mod = load_extractor()
    monkeypatch.setattr(mod, "archive_verify_download_url", lambda url, timeout=15: True)
    result_rows = [
        {"rank": "1", "artist": "Artist One", "track": "Track One", "entry": "Artist One - Track One"},
    ]
    archive_sum = {"archive_identifier": "One-Synth-Challenge-999-Fake"}
    archive_detail = [{"archive_file": "01 Artist One - Track One.mp3", "archive_track": "Track One", "match_kind": "EXACT"}]
    archive_tracks = [{"file_name": "01 Artist One - Track One.mp3", "track": "Track One"}]

    enriched = mod.archive_enrich_result_rows(result_rows, archive_sum, archive_detail, archive_tracks)

    assert enriched[0]["archive_link_state"] == "verified"


def test_archive_verify_download_url_caches_results(monkeypatch):
    mod = load_extractor()
    calls = []

    class DummyResponse:
        status = 200

        def getcode(self):
            return 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(req, timeout=30):
        calls.append(req.get_method())
        return DummyResponse()

    monkeypatch.setattr(mod, "urlopen", fake_urlopen)
    mod._ARCHIVE_URL_CHECK_CACHE.clear()

    url = "https://archive.org/download/One-Synth-Challenge-999-Fake/01%20Artist%20One%20-%20Track%20One.mp3"
    assert mod.archive_verify_download_url(url) is True
    assert mod.archive_verify_download_url(url) is True
    assert calls == ["HEAD"]


def test_archive_cache_round_trip_preserves_link_checks(tmp_path):
    mod = load_extractor()
    cache_file = tmp_path / "archive-cache.json"
    mod._ARCHIVE_INDEX_CACHE = {"rows": [{"osc": 1}], "index": {1: {"osc": 1}}}
    mod._ARCHIVE_TRACKS_CACHE = {"One-Synth-Challenge-999-Fake": [{"file_name": "01 Artist One - Track One.mp3"}]}
    mod._ARCHIVE_URL_CHECK_CACHE = {
        "https://archive.org/download/One-Synth-Challenge-999-Fake/01%20Artist%20One%20-%20Track%20One.mp3": True
    }

    assert mod.archive_cache_save(cache_file) is None

    mod._ARCHIVE_INDEX_CACHE = None
    mod._ARCHIVE_TRACKS_CACHE = {}
    mod._ARCHIVE_URL_CHECK_CACHE = {}

    assert mod.archive_cache_load(cache_file) is True
    assert mod._ARCHIVE_URL_CHECK_CACHE[
        "https://archive.org/download/One-Synth-Challenge-999-Fake/01%20Artist%20One%20-%20Track%20One.mp3"
    ] is True


def test_player_title_uses_osc_artist_and_track_not_archive_title():
    mod = load_generator()
    df = pd.DataFrame([
        {
            "osc": 1,
            "synth": "Synth1",
            "rank": "1",
            "artist": "Artist One",
            "artist_key": "artist_one",
            "track": "Track One",
            "points": 10,
            "template_version": "V01",
            "source_file": "OSC001_Test_1.xlsx",
            "year": "2000",
            "url": "",
            "archive_url": "https://archive.org/download/id/01%20Artist%20One%20-%20Track%20One.mp3",
            "archive_title": "Totally Different Archive Title",
            "archive_file": "01 Artist One - Track One.mp3",
            "archive_identifier": "id",
            "archive_match_kind": "TRACK_MATCH",
            "archive_link_state": "verified",
        }
    ])

    html = mod.build_html(df, scorecard_count=1, inventory_osc_count=1)

    assert "const oscTitle = ((playable.dataset.artistName || '') + ' - ' + (playable.dataset.track || '')" in html
    assert "setArchivePlayer(" in html
