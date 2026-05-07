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
          <li class="title"><a href="/subject/1291546/"><em>霸王别姬</em></a></li>
          <span class="rating5-t" title="力荐"></span>
          <span class="comment">2024-02-18 看过 我的短评 修改 删除</span>
          <span class="tags">标签: 华语 经典</span>
        </div>
      </div>
    </body></html>
    """
    movie_records = archive.parse_records(movie_html, "movie", "collect", fetched_at)
    assert len(movie_records) == 1
    assert movie_records[0]["url"] == "https://movie.douban.com/subject/1291546/"
    assert movie_records[0]["rating"] == 5
    assert movie_records[0]["comment"] == "我的短评"
    assert movie_records[0]["marked_at"] == "2024-02-18"
    assert "first_seen_at" not in movie_records[0]
    assert "updated_at" not in movie_records[0]
    assert "last_seen_at" not in movie_records[0]

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

    print("archive self tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
