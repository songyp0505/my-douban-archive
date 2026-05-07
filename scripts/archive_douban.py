#!/usr/bin/env python3
"""Archive personal Douban movie and book records to JSON."""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_FILE = BASE_DIR / "data" / "douban.json"

CATEGORIES = {
    "movie": "https://movie.douban.com/people/{user}/{status}",
    "book": "https://book.douban.com/people/{user}/{status}",
}

STATUSES = ("wish", "do", "collect")


class DoubanArchiveError(RuntimeError):
    """Raised when Douban cannot be archived safely."""


def build_session(cookie: str) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Cookie": cookie,
        }
    )
    return session


def fetch_page(session: requests.Session, url: str, timeout: int) -> str:
    response = session.get(url, timeout=timeout, allow_redirects=True)
    final_url = response.url.lower()
    text = response.text

    if response.status_code == 403:
        raise DoubanArchiveError("Douban returned 403; stop and refresh DOUBAN_COOKIE.")
    if response.status_code >= 400:
        raise DoubanArchiveError(f"Douban returned HTTP {response.status_code}: {url}")
    if "captcha" in final_url or "captcha" in text.lower() or "检测到有异常请求" in text:
        raise DoubanArchiveError("Douban requested captcha; stop without bypassing it.")
    if "accounts.douban.com" in final_url or "登录豆瓣" in text:
        raise DoubanArchiveError("Douban login is required; refresh DOUBAN_COOKIE.")

    return text


def text_or_empty(node) -> str:
    return node.get_text(" ", strip=True) if node else ""


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def extract_subject_id(url: str) -> Optional[str]:
    match = re.search(r"/subject/(\d+)/", url)
    return match.group(1) if match else None


def extract_rating(item) -> Optional[int]:
    rating = item.select_one("[class*='rating']")
    if not rating:
        return None
    classes = rating.get("class", [])
    for class_name in classes:
        match = re.search(r"rating(\d)-t", class_name)
        if match:
            return int(match.group(1))
    title = rating.get("title")
    rating_map = {
        "很差": 1,
        "较差": 2,
        "还行": 3,
        "推荐": 4,
        "力荐": 5,
    }
    return rating_map.get(title or "")


def extract_tags(item) -> List[str]:
    tag_text = text_or_empty(item.select_one(".tags"))
    if not tag_text:
        return []
    tag_text = re.sub(r"^标签[:：]\s*", "", tag_text)
    return [tag for tag in re.split(r"\s+", tag_text) if tag]


def extract_comment(item) -> str:
    candidates = [
        ".comment",
        ".short-note",
        ".intro",
        ".pl",
    ]
    for selector in candidates:
        node = item.select_one(selector)
        text = normalize_space(text_or_empty(node))
        if text and not text.startswith("标签"):
            return text
    return ""


def extract_title(item) -> str:
    selectors = [
        "li.title a em",
        "li.title a",
        ".title a",
        "h2 a",
        "a[href*='/subject/']",
    ]
    for selector in selectors:
        node = item.select_one(selector)
        text = normalize_space(text_or_empty(node))
        if text:
            return text
    return ""


def find_items(soup: BeautifulSoup) -> Iterable:
    grid = soup.select_one(".grid-view")
    if grid:
        items = grid.select(".item")
        if items:
            return items
    return soup.select(".item")


def parse_records(html: str, category: str, status: str, fetched_at: str) -> List[Dict[str, object]]:
    soup = BeautifulSoup(html, "lxml")
    records: List[Dict[str, object]] = []

    for item in find_items(soup):
        link = item.select_one("a[href*='/subject/']")
        if not link or not link.get("href"):
            continue

        url = urljoin("https://www.douban.com", link["href"])
        douban_id = extract_subject_id(url)
        title = extract_title(item)
        if not douban_id or not title:
            continue

        records.append(
            {
                "source": "douban",
                "category": category,
                "status": status,
                "douban_id": douban_id,
                "title": title,
                "url": url,
                "rating": extract_rating(item),
                "comment": extract_comment(item),
                "tags": extract_tags(item),
                "updated_at": fetched_at,
                "last_seen_at": fetched_at,
            }
        )

    return records


def find_next_url(html: str, current_url: str) -> Optional[str]:
    soup = BeautifulSoup(html, "lxml")
    next_link = soup.select_one("span.next a")
    if not next_link or not next_link.get("href"):
        return None
    return urljoin(current_url, next_link["href"])


def load_existing() -> Dict[str, object]:
    if not DATA_FILE.exists():
        return {"version": 1, "records": []}
    with DATA_FILE.open("r", encoding="utf-8") as file_obj:
        return json.load(file_obj)


def merge_records(existing: Dict[str, object], fresh_records: List[Dict[str, object]], fetched_at: str) -> Dict[str, object]:
    merged: Dict[str, Dict[str, object]] = {}
    for record in existing.get("records", []):
        if not isinstance(record, dict):
            continue
        category = record.get("category")
        douban_id = record.get("douban_id")
        if category and douban_id:
            merged[f"{category}:{douban_id}"] = record

    for record in fresh_records:
        merged[f"{record['category']}:{record['douban_id']}"] = record

    records = sorted(
        merged.values(),
        key=lambda item: (
            str(item.get("category", "")),
            str(item.get("status", "")),
            str(item.get("title", "")),
            str(item.get("douban_id", "")),
        ),
    )
    return {
        "version": 1,
        "generated_at": fetched_at,
        "records": records,
    }


def archive(args: argparse.Namespace) -> int:
    cookie = os.environ.get("DOUBAN_COOKIE", "").strip()
    user = os.environ.get("DOUBAN_USER", "").strip()
    if not cookie:
        raise DoubanArchiveError("Missing DOUBAN_COOKIE environment variable.")
    if not user:
        raise DoubanArchiveError("Missing DOUBAN_USER environment variable.")

    fetched_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    session = build_session(cookie)
    fresh_records: List[Dict[str, object]] = []

    for category, template in CATEGORIES.items():
        for status in STATUSES:
            page_url = template.format(user=user, status=status)
            page_count = 0
            while page_url:
                page_count += 1
                print(f"Fetching {category}/{status} page {page_count}")
                html = fetch_page(session, page_url, args.timeout)
                fresh_records.extend(parse_records(html, category, status, fetched_at))

                if args.max_pages and page_count >= args.max_pages:
                    break
                page_url = find_next_url(html, page_url)
                if page_url:
                    time.sleep(random.uniform(args.min_sleep, args.max_sleep))

    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    result = merge_records(load_existing(), fresh_records, fetched_at)
    with DATA_FILE.open("w", encoding="utf-8") as file_obj:
        json.dump(result, file_obj, ensure_ascii=False, indent=2)
        file_obj.write("\n")

    print(f"Archived {len(fresh_records)} fresh records; total records: {len(result['records'])}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Archive personal Douban movie and book records.")
    parser.add_argument("--max-pages", type=int, default=0, help="Maximum pages per category/status; 0 means all pages.")
    parser.add_argument("--timeout", type=int, default=20, help="HTTP timeout in seconds.")
    parser.add_argument("--min-sleep", type=float, default=2.0, help="Minimum sleep between paginated requests.")
    parser.add_argument("--max-sleep", type=float, default=5.0, help="Maximum sleep between paginated requests.")
    return parser.parse_args()


def main() -> int:
    try:
        return archive(parse_args())
    except DoubanArchiveError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
