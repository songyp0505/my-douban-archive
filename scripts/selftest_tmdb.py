#!/usr/bin/env python3
"""Self-contained regression tests for incremental TMDb enrichment."""

from __future__ import annotations

import json
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import enrich_tmdb as module  # noqa: E402


class FakeTmdb:
    def __init__(self):
        self.search_results = {}
        self.detail_results = {}
        self.temporary_search = False

    def search(self, media_type, query):
        if self.temporary_search:
            raise module.TemporaryItemError("tmdb_temporarily_unavailable")
        return self.search_results.get((media_type, query), [])

    def details(self, media_type, tmdb_id):
        value = self.detail_results.get((media_type, str(tmdb_id)))
        if isinstance(value, Exception):
            raise value
        return value or {}


def record(douban_id="1", title="测试作品", media_type="movie", aliases=None, release_year=2020):
    return {
        "source": "douban",
        "category": "movie",
        "status": "wish",
        "douban_id": douban_id,
        "title": title,
        "url": f"https://movie.douban.com/subject/{douban_id}/",
        "media_type": media_type,
        "aliases": list(aliases or []),
        "release_year": release_year,
    }


def resolved_detail(media_type, tmdb_id, title, year, poster="/poster.jpg"):
    if media_type == "movie":
        return {
            "id": int(tmdb_id),
            "title": title,
            "original_title": title,
            "release_date": f"{year}-01-01",
            "poster_path": poster,
        }
    return {
        "id": int(tmdb_id),
        "name": title,
        "original_name": title,
        "first_air_date": f"{year}-01-01",
        "poster_path": poster,
    }


def add_search_result(tmdb, media_type, query, tmdb_id, title, year):
    tmdb.search_results[(media_type, query)] = [resolved_detail(media_type, tmdb_id, title, year)]
    tmdb.detail_results[(media_type, str(tmdb_id))] = resolved_detail(media_type, tmdb_id, title, year)


def expect_error(fn, message):
    try:
        fn()
    except Exception:
        return
    raise AssertionError(message)


def main() -> int:
    archived = record(
        "35270213",
        "万物生灵",
        "movie",
        ["All Creatures Great and Small", "万物既伟大又渺小"],
        2020,
    )
    assert module.archive_evidence(archived) == {
        "douban_title": "万物生灵",
        "original_title": None,
        "aliases": ["All Creatures Great and Small", "万物既伟大又渺小"],
        "douban_year": 2020,
        "imdb_id": None,
        "episode_count": None,
    }

    # The archived main title and release year resolve an ordinary movie.
    movie_tmdb = FakeTmdb()
    add_search_result(movie_tmdb, "movie", "这个杀手不太冷", 101, "这个杀手不太冷", 1994)
    movie = module.Enricher(movie_tmdb, (0, 0)).resolve(
        record("1", "这个杀手不太冷", "movie", ["Léon"], 1994)
    )
    assert movie["status"] == "resolved"
    assert movie["resolved_type"] == "movie"
    assert movie["tmdb_id"] == "101"
    assert movie["match_method"] == "title_year"

    # Archive media_type is evidence, not authority: an alias/year can correct movie to TV.
    tv_tmdb = FakeTmdb()
    add_search_result(tv_tmdb, "tv", "All Creatures Great and Small", 108255, "All Creatures Great and Small", 2020)
    corrected = module.Enricher(tv_tmdb, (0, 0)).resolve(archived)
    assert corrected["status"] == "resolved"
    assert corrected["source_media_type"] == "movie"
    assert corrected["resolved_type"] == "tv"
    assert corrected["tmdb_id"] == "108255"

    # Ambiguous cross-type exact titles are never resolved by taking the first result.
    ambiguous_tmdb = FakeTmdb()
    add_search_result(ambiguous_tmdb, "movie", "同名作品", 10, "同名作品", 2020)
    add_search_result(ambiguous_tmdb, "tv", "同名作品", 11, "同名作品", 2020)
    ambiguous = module.Enricher(ambiguous_tmdb, (0, 0)).resolve(record("3", "同名作品", "movie", [], 2020))
    assert ambiguous["status"] == "unresolved"
    assert ambiguous["reason"] == "no_confident_match"
    assert ambiguous["tmdb_id"] is None

    # A conflicting release year is rejected even with an exact title.
    wrong_year_tmdb = FakeTmdb()
    add_search_result(wrong_year_tmdb, "movie", "年份测试", 12, "年份测试", 2019)
    wrong_year = module.Enricher(wrong_year_tmdb, (0, 0)).resolve(record("4", "年份测试", "movie", [], 2020))
    assert wrong_year["status"] == "unresolved"

    temporary_tmdb = FakeTmdb()
    temporary_tmdb.temporary_search = True
    temporary = module.Enricher(temporary_tmdb, (0, 0)).resolve(record("6", "接口异常"))
    assert temporary["status"] == "pending"
    assert temporary["reason"] == "tmdb_temporarily_unavailable"

    override = module.override_item(
        record("26755511", "古道清凉", "movie", [], 2015),
        {"resolved_type": "tv", "tmdb_id": "101492", "tmdb_title": "古道清凉", "production_year": 2015},
    )
    assert override["resolved_type"] == "tv"
    assert override["tmdb_id"] == "101492"
    assert override["production_year"] == 2015
    assert module.override_matches(
        override, {"resolved_type": "tv", "tmdb_id": "101492", "production_year": 2015}
    )
    assert not module.override_matches(
        override, {"resolved_type": "tv", "tmdb_id": "999", "production_year": 2015}
    )

    expect_error(
        lambda: module.movie_records({"records": [{**record(), "aliases": "bad"}]}),
        "invalid archive aliases must fail",
    )
    expect_error(
        lambda: module.movie_records({"records": [{**record(), "release_year": "2020"}]}),
        "invalid archive release year must fail",
    )

    # Existing resolved data is stable, while missing years can be filled from TMDb.
    stable_record = record("7", "稳定作品")
    stable_item = {
        "source_title": "稳定作品",
        "source_media_type": "movie",
        "status": "resolved",
        "resolved_type": "movie",
        "tmdb_id": "77",
        "production_year": 2020,
        "match_method": "seed",
        "source": "seed",
    }
    archive = {"version": 1, "records": [stable_record]}
    cache = {"version": 1, "generated_at": "old", "items": {"7": stable_item}}
    unchanged, stats = module.enrich(archive, cache, {}, module.Enricher(FakeTmdb(), (0, 0)), 20, False)
    assert stats["changed"] == 0
    assert unchanged["generated_at"] == "old"

    missing_year_item = dict(stable_item)
    missing_year_item["production_year"] = None
    year_tmdb = FakeTmdb()
    year_tmdb.detail_results[("movie", "77")] = resolved_detail("movie", "77", "稳定作品", 2020)
    filled, stats = module.enrich(
        archive,
        {"version": 1, "generated_at": "old", "items": {"7": missing_year_item}},
        {},
        module.Enricher(year_tmdb, (0, 0)),
        20,
        False,
    )
    assert stats["changed"] == 1
    assert filled["items"]["7"]["production_year"] == 2020

    # More new records than the per-run limit are retained as retryable pending entries.
    batch_tmdb = FakeTmdb()
    add_search_result(batch_tmdb, "tv", "万物生灵", 108255, "万物生灵", 2020)
    batch_archive = {
        "version": 1,
        "records": [record("8", "万物生灵", "tv", [], 2020), record("9", "稍后处理", "movie", [], 2020)],
    }
    batched, stats = module.enrich(
        batch_archive,
        {"version": 1, "generated_at": None, "items": {}},
        {},
        module.Enricher(batch_tmdb, (0, 0)),
        1,
        False,
    )
    assert stats["resolved"] == 1
    assert batched["items"]["9"]["status"] == "pending"
    assert batched["items"]["9"]["reason"] == "batch_limit"

    # batch_limit entries run before repeatedly failing pending entries next time.
    second_tmdb = FakeTmdb()
    add_search_result(second_tmdb, "movie", "稍后处理", 109, "稍后处理", 2020)
    second, _stats = module.enrich(
        batch_archive,
        batched,
        {},
        module.Enricher(second_tmdb, (0, 0)),
        1,
        False,
    )
    assert second["items"]["9"]["status"] == "resolved"

    try:
        module.validate_tmdb_archive(batched, batch_archive, ("secret-value-123",))
    except module.EnrichmentError:
        raise AssertionError("clean payload should validate")
    leaked = json.loads(json.dumps(batched))
    leaked["items"]["9"]["source_title"] = "secret-value-123"
    expect_error(
        lambda: module.validate_tmdb_archive(leaked, batch_archive, ("secret-value-123",)),
        "secret leakage must fail validation",
    )

    overrides = json.loads((SCRIPT_DIR.parent / "config" / "tmdb_overrides.json").read_text(encoding="utf-8"))
    assert overrides["26755511"]["tmdb_id"] == "101492"
    assert overrides["35270213"]["tmdb_id"] == "108255"

    workflow = (SCRIPT_DIR.parent / ".github" / "workflows" / "enrich_tmdb.yml").read_text(encoding="utf-8")
    assert 'workflows: ["Archive Douban"]' in workflow
    assert "schedule:" not in workflow
    assert "git diff --quiet" in workflow
    assert "data/douban.json" in workflow
    assert "DOUBAN_COOKIE" not in workflow
    assert "movie.douban.com/subject" not in (SCRIPT_DIR / "enrich_tmdb.py").read_text(encoding="utf-8")

    print("TMDb enrichment self tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
