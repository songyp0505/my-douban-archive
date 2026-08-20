#!/usr/bin/env python3
"""Local smoke tests for the Douban archive script."""

from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parent / "archive_douban.py"


def load_archive_module():
    spec = importlib.util.spec_from_file_location("archive_douban", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load archive_douban.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def expect_error(fn, message: str) -> None:
    try:
        fn()
    except Exception:
        return
    raise AssertionError(message)


def main() -> int:
    archive = load_archive_module()
    fetched_at = "2026-05-07T00:00:00+00:00"

    movie_html = """
    <html><body>
      <div class="grid-view">
        <div class="item">
          <li class="title"><a href="/subject/1291546/"><em>霸王别姬</em> / Farewell My Concubine / 再见，我的妾</a></li>
          <span class="rating5-t" title="力荐"></span>
          <div class="date">2024-02-18</div>
          <span class="intro">1993-01-01(中国香港) / 张国荣 / 张丰毅 / 剧情</span>
          <span class="comment">2024-02-18 看过 我的短评 修改 删除</span>
          <span class="tags">标签: 华语 经典</span>
        </div>
      </div>
    </body></html>
    """
    movie_records = archive.parse_records(movie_html, "movie", "collect", fetched_at, media_type="movie")
    assert len(movie_records) == 1
    assert movie_records[0]["url"] == "https://movie.douban.com/subject/1291546/"
    assert movie_records[0]["media_type"] == "movie"
    assert movie_records[0]["rating"] == 5
    assert movie_records[0]["comment"] == "我的短评"
    assert movie_records[0]["marked_at"] == "2024-02-18"
    assert movie_records[0]["aliases"] == ["Farewell My Concubine", "再见，我的妾"]
    assert movie_records[0]["release_year"] == 1993
    assert "first_seen_at" not in movie_records[0]
    assert "updated_at" not in movie_records[0]
    assert "last_seen_at" not in movie_records[0]

    tv_html = """
    <html><body>
      <div class="grid-view">
        <div class="item">
          <li class="title"><a href="/subject/30465634/"><em>一级方程式：疾速争胜 第一季</em> / Formula 1: Drive to Survive</a></li>
          <div class="date">2026-05-08</div>
          <span class="intro">2019-03-08(美国首播) / 刘易斯·汉密尔顿 / 纪录片</span>
        </div>
      </div>
    </body></html>
    """
    tv_records = archive.parse_records(tv_html, "movie", "wish", fetched_at, media_type="tv")
    assert len(tv_records) == 1
    assert tv_records[0]["url"] == "https://movie.douban.com/subject/30465634/"
    assert tv_records[0]["media_type"] == "tv"
    assert tv_records[0]["marked_at"] == "2026-05-08"
    assert tv_records[0]["aliases"] == ["Formula 1: Drive to Survive"]
    assert tv_records[0]["release_year"] == 2019

    year_only_html = """
    <div class="grid-view"><div class="item">
      <li class="title"><a href="/subject/1394968/"><em>举起手来！</em> / Hands Up!</a></li>
      <div class="date">2020-01-01</div>
      <span class="intro">2005(中国大陆) / 郭达 / 潘长江 / 喜剧</span>
    </div></div>
    """
    year_only = archive.parse_records(year_only_html, "movie", "collect", fetched_at, media_type="movie")
    assert year_only[0]["release_year"] == 2005

    missing_year_html = """
    <div class="grid-view"><div class="item">
      <li class="title"><a href="/subject/26755511/"><em>古道清凉</em> / 古道清凉 / The Ancient Path to Enlightenment</a></li>
      <div class="date">2020-01-01</div>
      <span class="intro">中国大陆 / 古道清凉 / 纪录片</span>
    </div></div>
    """
    missing_year = archive.parse_records(missing_year_html, "movie", "wish", fetched_at, media_type="movie")
    assert missing_year[0]["aliases"] == ["The Ancient Path to Enlightenment"]
    assert missing_year[0]["release_year"] is None

    book_html = """
    <html><body>
      <li class="subject-item">
        <h2><a href="/subject/1234567/"> 测试书 </a></h2>
        <span class="rating4-t" title="推荐"></span>
        <p class="comment">我的读书短评</p>
        <p class="intro">这是一段不该保存的简介</p>
      </li>
    </body></html>
    """
    book_records = archive.parse_records(book_html, "book", "collect", fetched_at)
    assert len(book_records) == 1
    assert book_records[0]["url"] == "https://book.douban.com/subject/1234567/"
    assert "media_type" not in book_records[0]
    assert "aliases" not in book_records[0]
    assert "release_year" not in book_records[0]
    assert book_records[0]["comment"] == "我的读书短评"
    assert book_records[0]["marked_at"] is None

    noisy_book_html = """
    <html><body>
      <li class="subject-item">
        <h2><a href="/subject/7654321/"> 没写短评的书 </a></h2>
        <span class="rating3-t" title="还行"></span>
        <span class="comment">2022-11-09 读过 修改 删除</span>
      </li>
      <li class="subject-item">
        <h2><a href="/subject/7654322/"> 有短评的书 </a></h2>
        <span class="rating5-t" title="力荐"></span>
        <span class="comment">2022-11-09 读过 真不错 修改 删除</span>
      </li>
      <li class="subject-item">
        <h2><a href="/subject/7654323/"> 只有标签的书 </a></h2>
        <span class="rating5-t" title="力荐"></span>
        <span class="comment">2018-12-10 读过 标签: 小说 文学 修改 删除</span>
        <span class="tags">标签: 小说 文学</span>
      </li>
    </body></html>
    """
    noisy_records = archive.parse_records(noisy_book_html, "book", "collect", fetched_at)
    assert noisy_records[0]["comment"] == ""
    assert noisy_records[0]["marked_at"] == "2022-11-09"
    assert noisy_records[1]["comment"] == "真不错"
    assert noisy_records[1]["marked_at"] == "2022-11-09"
    assert noisy_records[2]["comment"] == ""
    assert noisy_records[2]["tags"] == ["小说", "文学"]
    assert noisy_records[2]["marked_at"] == "2018-12-10"

    existing = {"version": 1, "generated_at": "old", "records": movie_records + book_records}
    unchanged, changed = archive.merge_records(existing, movie_records + book_records, "new")
    assert changed is False
    assert unchanged["generated_at"] == "old"

    updated_movie = dict(movie_records[0])
    updated_movie["status"] = "wish"
    changed_archive, changed = archive.merge_records(existing, [updated_movie] + book_records, "new")
    assert changed is True
    assert changed_archive["generated_at"] == "new"

    legacy_movie = dict(movie_records[0])
    legacy_movie.pop("media_type")
    legacy_movie.pop("aliases")
    legacy_movie.pop("release_year")
    media_type_archive, changed = archive.merge_records(
        {"version": 1, "generated_at": "old", "records": [legacy_movie]},
        movie_records,
        "new",
    )
    assert changed is True
    assert media_type_archive["records"][0]["media_type"] == "movie"
    assert media_type_archive["records"][0]["aliases"] == ["Farewell My Concubine", "再见，我的妾"]
    assert media_type_archive["records"][0]["release_year"] == 1993

    old_timestamp_record = {**movie_records[0], "first_seen_at": fetched_at, "updated_at": fetched_at, "last_seen_at": fetched_at}
    cleaned_archive, changed = archive.merge_records(
        {"version": 1, "generated_at": "old", "records": [old_timestamp_record]},
        [movie_records[0]],
        "new",
    )
    assert changed is True
    assert "first_seen_at" not in cleaned_archive["records"][0]
    assert "updated_at" not in cleaned_archive["records"][0]
    assert "last_seen_at" not in cleaned_archive["records"][0]

    expect_error(
        lambda: archive.validate_archive({"version": 1, "records": []}, 0, "dbcl2=secret-value-123", False),
        "empty fresh archive should fail by default",
    )
    expect_error(
        lambda: archive.validate_archive(
            {"version": 1, "records": [{**movie_records[0], "title": "secret-value-123"}]},
            1,
            "dbcl2=secret-value-123",
            False,
        ),
        "cookie value leak should fail validation",
    )
    expect_error(
        lambda: archive.validate_archive(
            {"version": 1, "records": [{**book_records[0], "comment": "2022-11-09 读过 修改 删除"}]},
            1,
            "dbcl2=secret-value-123",
            False,
        ),
        "Douban UI text should fail validation",
    )
    expect_error(
        lambda: archive.validate_archive(
            {"version": 1, "records": [{**book_records[0], "first_seen_at": fetched_at}]},
            1,
            "dbcl2=secret-value-123",
            False,
        ),
        "obsolete per-record timestamps should fail validation",
    )
    expect_error(
        lambda: archive.validate_archive(
            {"version": 1, "records": [{**movie_records[0], "media_type": "episode"}]},
            1,
            "dbcl2=secret-value-123",
            False,
        ),
        "invalid media_type should fail validation",
    )
    expect_error(
        lambda: archive.validate_archive(
            {"version": 1, "records": [{**movie_records[0], "aliases": "not-a-list"}]},
            1,
            "dbcl2=secret-value-123",
            False,
        ),
        "invalid aliases should fail validation",
    )
    expect_error(
        lambda: archive.validate_archive(
            {"version": 1, "records": [{**movie_records[0], "release_year": 99}]},
            1,
            "dbcl2=secret-value-123",
            False,
        ),
        "invalid release year should fail validation",
    )
    expect_error(
        lambda: archive.validate_archive(
            {"version": 1, "records": movie_records},
            1,
            "dbcl2=secret-value-123",
            False,
            fresh_records=[{k: v for k, v in movie_records[0].items() if k != "media_type"}],
        ),
        "fresh movie records without media_type should fail validation",
    )
    expect_error(
        lambda: archive.validate_archive(
            {"version": 1, "records": [{**book_records[0], "media_type": "tv"}]},
            1,
            "dbcl2=secret-value-123",
            False,
        ),
        "book records with media_type should fail validation",
    )
    expect_error(
        lambda: archive.validate_archive(
            {"version": 1, "records": [{**book_records[0], "aliases": ["不应存在"]}]},
            1,
            "dbcl2=secret-value-123",
            False,
        ),
        "book records with movie metadata should fail validation",
    )

    movie_url = archive.build_list_url("movie", "188332994", "wish", "movie")
    tv_url = archive.build_list_url("movie", "188332994", "wish", "tv")
    book_url = archive.build_list_url("book", "188332994", "wish")
    assert "type=movie" in movie_url
    assert "type=tv" in tv_url
    assert "sort=time" in movie_url
    assert "start=0" in movie_url
    assert book_url == "https://book.douban.com/people/188332994/wish"

    print("archive self tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
