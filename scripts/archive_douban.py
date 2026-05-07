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
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_FILE = BASE_DIR / "data" / "douban.json"

CATEGORIES = {
    "movie": "https://movie.douban.com/people/{user}/{status}",
    "book": "https://book.douban.com/people/{user}/{status}",
}
CATEGORY_BASE_URLS = {
    "movie": "https://movie.douban.com",
    "book": "https://book.douban.com",
}

STATUSES = ("wish", "do", "collect")
PERSONAL_FIELDS = ("source", "category", "status", "douban_id", "title", "url", "rating", "comment", "tags")
SENSITIVE_COOKIE_NAMES = ("dbcl2", "ck", "push_noty_num", "push_doumail_num")


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


def fetch_page(session: requests.Session, url: str, timeout: int, retries: int) -> str:
    response = None
    for attempt in range(retries + 1):
        try:
            response = session.get(url, timeout=timeout, allow_redirects=True)
            if response.status_code in {429, 500, 502, 503, 504} and attempt < retries:
                sleep_seconds = 3 + attempt * 5
                print(f"Temporary HTTP {response.status_code}; retrying in {sleep_seconds}s")
                time.sleep(sleep_seconds)
                continue
            break
        except requests.RequestException as exc:
            if attempt >= retries:
                raise DoubanArchiveError(f"Network request failed after retries: {url}") from exc
            sleep_seconds = 3 + attempt * 5
            print(f"Network request failed; retrying in {sleep_seconds}s")
            time.sleep(sleep_seconds)

    if response is None:
        raise DoubanArchiveError(f"No response received: {url}")

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
    book_items = soup.select(".subject-item")
    if book_items:
        return book_items
    return soup.select(".item")


def parse_records(html: str, category: str, status: str, fetched_at: str) -> List[Dict[str, object]]:
    soup = BeautifulSoup(html, "lxml")
    records: List[Dict[str, object]] = []

    for item in find_items(soup):
        link = item.select_one("a[href*='/subject/']")
        if not link or not link.get("href"):
            continue

        url = urljoin(CATEGORY_BASE_URLS[category], link["href"])
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
                "first_seen_at": fetched_at,
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
    try:
        with DATA_FILE.open("r", encoding="utf-8") as file_obj:
            existing = json.load(file_obj)
    except json.JSONDecodeError as exc:
        raise DoubanArchiveError(f"Existing archive is invalid JSON: {DATA_FILE}") from exc
    if not isinstance(existing, dict):
        raise DoubanArchiveError(f"Existing archive root must be an object: {DATA_FILE}")
    return existing


def record_key(record: Dict[str, object]) -> Optional[str]:
    category = record.get("category")
    douban_id = record.get("douban_id")
    if not category or not douban_id:
        return None
    return f"{category}:{douban_id}"


def personal_snapshot(record: Dict[str, object]) -> Dict[str, object]:
    return {field: record.get(field) for field in PERSONAL_FIELDS}


def merge_record(existing: Optional[Dict[str, object]], fresh: Dict[str, object], fetched_at: str) -> Tuple[Dict[str, object], bool]:
    if existing and personal_snapshot(existing) == personal_snapshot(fresh):
        return existing, False

    merged = dict(fresh)
    if existing:
        merged["first_seen_at"] = existing.get("first_seen_at") or existing.get("updated_at") or fetched_at
    return merged, True


def sort_records(records: Iterable[Dict[str, object]]) -> List[Dict[str, object]]:
    return sorted(
        records,
        key=lambda item: (
            str(item.get("category", "")),
            str(item.get("status", "")),
            str(item.get("title", "")),
            str(item.get("douban_id", "")),
        ),
    )


def merge_records(existing: Dict[str, object], fresh_records: List[Dict[str, object]], fetched_at: str) -> Tuple[Dict[str, object], bool]:
    merged: Dict[str, Dict[str, object]] = {}
    for record in existing.get("records", []):
        if not isinstance(record, dict):
            continue
        key = record_key(record)
        if key:
            merged[key] = record

    changed = False
    for record in fresh_records:
        key = record_key(record)
        if not key:
            continue
        merged_record, record_changed = merge_record(merged.get(key), record, fetched_at)
        merged[key] = merged_record
        changed = changed or record_changed

    records = sort_records(merged.values())
    existing_records = sort_records([record for record in existing.get("records", []) if isinstance(record, dict)])
    changed = changed or records != existing_records

    return {
        "version": 1,
        "generated_at": fetched_at if changed else existing.get("generated_at"),
        "records": records,
    }, changed


def validate_archive(result: Dict[str, object], fresh_count: int, cookie: str, allow_empty: bool) -> None:
    records = result.get("records")
    if not isinstance(records, list):
        raise DoubanArchiveError("Archive validation failed: records must be a list.")
    if fresh_count == 0 and not allow_empty:
        raise DoubanArchiveError("Parsed zero fresh records; stop to avoid committing an empty/broken archive.")

    cookie_parts = [part.strip() for part in cookie.split(";") if "=" in part]
    cookie_values = [part.split("=", 1)[1] for part in cookie_parts]
    serialized = json.dumps(result, ensure_ascii=False)

    for value in cookie_values:
        if len(value) >= 8 and value in serialized:
            raise DoubanArchiveError("Archive validation failed: cookie-like secret leaked into output.")

    for record in records:
        if not isinstance(record, dict):
            raise DoubanArchiveError("Archive validation failed: every record must be an object.")
        missing = [field for field in ("source", "category", "status", "douban_id", "title", "url") if not record.get(field)]
        if missing:
            raise DoubanArchiveError(f"Archive validation failed: record is missing {', '.join(missing)}.")
        if record.get("source") != "douban":
            raise DoubanArchiveError("Archive validation failed: source must be douban.")
        if record.get("category") not in CATEGORIES:
            raise DoubanArchiveError("Archive validation failed: unknown category.")
        if record.get("status") not in STATUSES:
            raise DoubanArchiveError("Archive validation failed: unknown status.")
        if not re.match(r"^\d+$", str(record.get("douban_id", ""))):
            raise DoubanArchiveError("Archive validation failed: invalid douban_id.")
        if not re.match(r"^https://(movie|book)\.douban\.com/subject/\d+/?", str(record.get("url", ""))):
            raise DoubanArchiveError("Archive validation failed: URL is not a Douban subject URL.")


def write_archive(result: Dict[str, object]) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_file = DATA_FILE.with_suffix(".json.tmp")
    with temp_file.open("w", encoding="utf-8") as file_obj:
        json.dump(result, file_obj, ensure_ascii=False, indent=2)
        file_obj.write("\n")
    temp_file.replace(DATA_FILE)


def archive(args: argparse.Namespace) -> int:
    cookie = os.environ.get("DOUBAN_COOKIE", "").strip()
    user = os.environ.get("DOUBAN_USER", "").strip()
    if not cookie:
        raise DoubanArchiveError("Missing DOUBAN_COOKIE environment variable.")
    if not user:
        raise DoubanArchiveError("Missing DOUBAN_USER environment variable.")
    if args.min_sleep > args.max_sleep:
        raise DoubanArchiveError("--min-sleep cannot be greater than --max-sleep.")
    if not any(cookie_name + "=" in cookie for cookie_name in SENSITIVE_COOKIE_NAMES):
        print("WARNING: Cookie does not look like a logged-in Douban browser cookie.", file=sys.stderr)

    fetched_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    session = build_session(cookie)
    fresh_records: List[Dict[str, object]] = []
    page_totals: Dict[str, int] = {}

    for category, template in CATEGORIES.items():
        for status in STATUSES:
            page_url = template.format(user=user, status=status)
            page_count = 0
            while page_url:
                page_count += 1
                print(f"Fetching {category}/{status} page {page_count}")
                html = fetch_page(session, page_url, args.timeout, args.retries)
                page_records = parse_records(html, category, status, fetched_at)
                print(f"Parsed {len(page_records)} records from {category}/{status} page {page_count}")
                fresh_records.extend(page_records)

                if args.max_pages and page_count >= args.max_pages:
                    break
                page_url = find_next_url(html, page_url)
                if page_url:
                    time.sleep(random.uniform(args.min_sleep, args.max_sleep))
            page_totals[f"{category}/{status}"] = page_count

    existing = load_existing()
    result, changed = merge_records(existing, fresh_records, fetched_at)
    validate_archive(result, len(fresh_records), cookie, args.allow_empty)

    if changed or not DATA_FILE.exists():
        write_archive(result)
        print(f"Wrote archive: {len(fresh_records)} fresh records; total records: {len(result['records'])}")
    else:
        print(f"No archive content changes: {len(fresh_records)} fresh records; total records: {len(result['records'])}")
    print("Fetched pages: " + ", ".join(f"{key}={value}" for key, value in sorted(page_totals.items())))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Archive personal Douban movie and book records.")
    parser.add_argument("--max-pages", type=int, default=0, help="Maximum pages per category/status; 0 means all pages.")
    parser.add_argument("--timeout", type=int, default=20, help="HTTP timeout in seconds.")
    parser.add_argument("--retries", type=int, default=2, help="Retries for temporary network/server failures.")
    parser.add_argument("--min-sleep", type=float, default=2.0, help="Minimum sleep between paginated requests.")
    parser.add_argument("--max-sleep", type=float, default=5.0, help="Maximum sleep between paginated requests.")
    parser.add_argument("--allow-empty", action="store_true", help="Allow committing an archive when no fresh records were parsed.")
    return parser.parse_args()


def main() -> int:
    try:
        return archive(parse_args())
    except DoubanArchiveError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
