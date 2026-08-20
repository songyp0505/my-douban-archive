#!/usr/bin/env python3
"""Self-contained regression tests for incremental TMDb enrichment."""

from __future__ import annotations

import json
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import enrich_tmdb as module  # noqa: E402
from archive_douban import DoubanArchiveError  # noqa: E402


class FakeTmdb:
    def __init__(self):
        self.find_results = {}
        self.search_results = {}
        self.detail_results = {}
        self.temporary_find = False

    def find_by_imdb(self, imdb_id):
        if self.temporary_find:
            raise module.TemporaryItemError("tmdb_temporarily_unavailable")
        return self.find_results.get(imdb_id, [])

    def search(self, media_type, query):
        return self.search_results.get((media_type, query), [])

    def details(self, media_type, tmdb_id):
        value = self.detail_results.get((media_type, str(tmdb_id)))
        if isinstance(value, Exception):
            raise value
        return value or {}


class FakeResponse:
    def __init__(self, status_code=200, url="https://movie.douban.com/subject/1/", text="ok"):
        self.status_code = status_code
        self.url = url
        self.text = text


class FakeSession:
    def __init__(self, response):
        self.response = response

    def get(self, *_args, **_kwargs):
        return self.response


def record(douban_id="1", title="测试作品", media_type="movie"):
    return {
        "source": "douban",
        "category": "movie",
        "status": "wish",
        "douban_id": douban_id,
        "title": title,
        "url": f"https://movie.douban.com/subject/{douban_id}/",
        "media_type": media_type,
    }


def html_detail(title="万物生灵", year=2020, imdb_id="tt10590066", original="All Creatures Great and Small", episodes=6):
    imdb_line = f'<span class="pl">IMDb:</span> {imdb_id}<br>' if imdb_id else ""
    episode_line = f'<span class="pl">集数:</span> {episodes}<br>' if episodes else ""
    return f"""
    <html><head><meta property="og:title" content="{title}"></head><body>
      <h1><span property="v:itemreviewed">{title}</span><span class="year">({year})</span></h1>
      <div id="info">
        <span class="pl">原名:</span> {original}<br>
        <span class="pl">又名:</span> 万物既伟大又渺小 / 万物生灵 第一季<br>
        {episode_line}
        {imdb_line}
      </div>
    </body></html>
    """


def resolved_detail(media_type, tmdb_id, title, year, poster="/poster.jpg"):
    if media_type == "movie":
        return {"id": int(tmdb_id), "title": title, "original_title": title, "release_date": f"{year}-01-01", "poster_path": poster}
    return {"id": int(tmdb_id), "name": title, "original_name": title, "first_air_date": f"{year}-01-01", "poster_path": poster}


def main() -> int:
    assert module.fetch_detail_page(FakeSession(FakeResponse()), "https://movie.douban.com/subject/1/", 1, 0) == "ok"
    try:
        module.fetch_detail_page(
            FakeSession(FakeResponse(url="https://sec.douban.com/c?r=subject")),
            "https://movie.douban.com/subject/1/",
            1,
            0,
        )
    except DoubanArchiveError:
        pass
    else:
        raise AssertionError("Douban security redirects must be blocked")

    parsed = module.parse_douban_detail(html_detail())
    assert parsed == {
        "douban_title": "万物生灵",
        "original_title": "All Creatures Great and Small",
        "aliases": ["万物既伟大又渺小", "万物生灵 第一季"],
        "douban_year": 2020,
        "imdb_id": "tt10590066",
        "episode_count": 6,
    }

    # IMDb is the preferred identity bridge for both movies and TV.
    movie_tmdb = FakeTmdb()
    movie_tmdb.find_results["tt0110413"] = [("movie", {"id": 101})]
    movie_tmdb.detail_results[("movie", "101")] = resolved_detail("movie", "101", "这个杀手不太冷", 1994)
    movie = module.Enricher(
        movie_tmdb,
        lambda _record: html_detail("这个杀手不太冷", 1994, "tt0110413", "Léon", None),
        (0, 0),
    ).resolve(record(title="这个杀手不太冷 / Léon"))
    assert movie["status"] == "resolved"
    assert movie["resolved_type"] == "movie"
    assert movie["tmdb_id"] == "101"
    assert movie["match_method"] == "imdb"

    tv_tmdb = FakeTmdb()
    tv_tmdb.find_results["tt10590066"] = [("tv", {"id": 108255})]
    tv_tmdb.detail_results[("tv", "108255")] = resolved_detail("tv", "108255", "万物生灵", 2020)
    corrected = module.Enricher(tv_tmdb, lambda _record: html_detail(), (0, 0)).resolve(
        record("35270213", "万物生灵", "movie")
    )
    assert corrected["status"] == "resolved"
    assert corrected["source_media_type"] == "movie"
    assert corrected["resolved_type"] == "tv"
    assert corrected["tmdb_id"] == "108255"

    # Without IMDb, an exact title/year candidate is accepted across both endpoints.
    fallback_tmdb = FakeTmdb()
    fallback_tmdb.search_results[("tv", "All Creatures Great and Small")] = [
        {"id": 108255, "name": "万物生灵", "original_name": "All Creatures Great and Small", "first_air_date": "2020-09-01"}
    ]
    fallback_tmdb.detail_results[("tv", "108255")] = resolved_detail("tv", "108255", "万物生灵", 2020)
    fallback = module.Enricher(
        fallback_tmdb,
        lambda _record: html_detail(imdb_id=None),
        (0, 0),
    ).resolve(record("2", "万物生灵", "tv"))
    assert fallback["status"] == "resolved"
    assert fallback["tmdb_id"] == "108255"
    assert fallback["match_method"] == "title_year"

    # Ambiguous cross-type exact titles are never resolved by taking the first result.
    ambiguous_tmdb = FakeTmdb()
    ambiguous_tmdb.search_results[("movie", "同名作品")] = [
        {"id": 10, "title": "同名作品", "original_title": "同名作品", "release_date": ""}
    ]
    ambiguous_tmdb.search_results[("tv", "同名作品")] = [
        {"id": 11, "name": "同名作品", "original_name": "同名作品", "first_air_date": ""}
    ]
    ambiguous = module.Enricher(
        ambiguous_tmdb,
        lambda _record: html_detail("同名作品", 2020, None, "", None),
        (0, 0),
    ).resolve(record("3", "同名作品", "movie"))
    assert ambiguous["status"] == "unresolved"
    assert ambiguous["reason"] == "no_confident_match"
    assert ambiguous["tmdb_id"] is None

    # A conflicting release year is rejected even with an exact title.
    wrong_year_tmdb = FakeTmdb()
    wrong_year_tmdb.search_results[("movie", "年份测试")] = [
        {"id": 12, "title": "年份测试", "original_title": "年份测试", "release_date": "2019-01-01"}
    ]
    wrong_year = module.Enricher(
        wrong_year_tmdb,
        lambda _record: html_detail("年份测试", 2020, None, "", None),
        (0, 0),
    ).resolve(record("4", "年份测试", "movie"))
    assert wrong_year["status"] == "unresolved"

    blocked = module.Enricher(
        FakeTmdb(),
        lambda _record: (_ for _ in ()).throw(DoubanArchiveError("Douban requested captcha")),
        (0, 0),
    ).resolve(record("5", "访问异常"))
    assert blocked["status"] == "pending"
    assert blocked["reason"] == "douban_access_blocked"

    temporary_tmdb = FakeTmdb()
    temporary_tmdb.temporary_find = True
    temporary = module.Enricher(temporary_tmdb, lambda _record: html_detail(), (0, 0)).resolve(
        record("6", "接口异常")
    )
    assert temporary["status"] == "pending"
    assert temporary["reason"] == "tmdb_temporarily_unavailable"

    override = module.override_item(
        record("26755511", "古道清凉", "movie"),
        {"resolved_type": "tv", "tmdb_id": "101492", "tmdb_title": "古道清凉", "production_year": 2015},
    )
    assert override["resolved_type"] == "tv"
    assert override["tmdb_id"] == "101492"
    assert override["production_year"] == 2015
    assert module.override_matches(override, {
        "resolved_type": "tv", "tmdb_id": "101492", "production_year": 2015,
    })
    assert not module.override_matches(override, {
        "resolved_type": "tv", "tmdb_id": "999", "production_year": 2015,
    })

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
    unchanged, stats = module.enrich(
        archive, cache, {}, module.Enricher(FakeTmdb(), lambda _record: "", (0, 0)), 20, False
    )
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
        module.Enricher(year_tmdb, lambda _record: "", (0, 0)),
        20,
        False,
    )
    assert stats["changed"] == 1
    assert filled["items"]["7"]["production_year"] == 2020

    # More new records than the per-run limit are retained as retryable pending entries.
    batch_tmdb = FakeTmdb()
    batch_tmdb.find_results["tt10590066"] = [("tv", {"id": 108255})]
    batch_tmdb.detail_results[("tv", "108255")] = resolved_detail("tv", "108255", "万物生灵", 2020)
    batch_archive = {"version": 1, "records": [record("8", "万物生灵"), record("9", "稍后处理")]}
    batched, stats = module.enrich(
        batch_archive,
        {"version": 1, "generated_at": None, "items": {}},
        {},
        module.Enricher(batch_tmdb, lambda _record: html_detail(), (0, 0)),
        1,
        False,
    )
    assert stats["resolved"] == 1
    assert batched["items"]["9"]["status"] == "pending"
    assert batched["items"]["9"]["reason"] == "batch_limit"

    # batch_limit entries run before repeatedly failing pending entries next time.
    second_tmdb = FakeTmdb()
    second_tmdb.find_results["tt10590066"] = [("tv", {"id": 108255})]
    second_tmdb.detail_results[("tv", "108255")] = resolved_detail("tv", "108255", "万物生灵", 2020)
    second, _stats = module.enrich(
        batch_archive,
        batched,
        {},
        module.Enricher(second_tmdb, lambda _record: html_detail(), (0, 0)),
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
    try:
        module.validate_tmdb_archive(leaked, batch_archive, ("secret-value-123",))
    except module.EnrichmentError:
        pass
    else:
        raise AssertionError("secret leakage must fail validation")

    overrides = json.loads((SCRIPT_DIR.parent / "config" / "tmdb_overrides.json").read_text(encoding="utf-8"))
    assert overrides["26755511"]["tmdb_id"] == "101492"
    assert overrides["35270213"]["tmdb_id"] == "108255"

    workflow = (SCRIPT_DIR.parent / ".github" / "workflows" / "enrich_tmdb.yml").read_text(encoding="utf-8")
    assert 'workflows: ["Archive Douban"]' in workflow
    assert "schedule:" not in workflow
    assert "git diff --quiet" in workflow
    assert "data/douban.json" in workflow

    print("TMDb enrichment self tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
