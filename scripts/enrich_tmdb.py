#!/usr/bin/env python3
"""Incrementally enrich archived Douban subjects with conservative TMDb identities."""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import requests


BASE_DIR = Path(__file__).resolve().parents[1]
ARCHIVE_FILE = BASE_DIR / "data" / "douban.json"
TMDB_FILE = BASE_DIR / "data" / "douban_tmdb.json"
OVERRIDES_FILE = BASE_DIR / "config" / "tmdb_overrides.json"
TMDB_BASE_URL = "https://api.themoviedb.org/3"
VALID_STATUSES = {"resolved", "unresolved", "pending"}


class EnrichmentError(RuntimeError):
    """Fatal configuration or data error."""


class TemporaryItemError(RuntimeError):
    """Temporary upstream failure that should remain retryable."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists() and default is not None:
        return default
    try:
        with path.open("r", encoding="utf-8") as file_obj:
            return json.load(file_obj)
    except (OSError, json.JSONDecodeError) as exc:
        raise EnrichmentError(f"Cannot read valid JSON: {path}") from exc


def clean_year(value: Any) -> Optional[int]:
    match = re.search(r"\b(18\d{2}|19\d{2}|20\d{2}|21\d{2}|2200)\b", str(value or ""))
    return int(match.group(1)) if match else None


def normalize_title(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(character for character in text if character.isalnum())


def split_titles(value: Any) -> List[str]:
    result: List[str] = []
    for part in re.split(r"\s+/\s+", str(value or "").strip()):
        part = re.sub(r"\s+", " ", part).strip()
        if part and part not in result:
            result.append(part)
    return result


def archive_evidence(record: Dict[str, Any]) -> Dict[str, Any]:
    aliases = record.get("aliases") if isinstance(record.get("aliases"), list) else []
    return {
        "douban_title": str(record.get("title") or "").strip(),
        "original_title": None,
        "aliases": [str(alias).strip() for alias in aliases if str(alias).strip()],
        "douban_year": clean_year(record.get("release_year")),
        "imdb_id": None,
        "episode_count": None,
    }


def movie_records(archive: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    records = archive.get("records")
    if not isinstance(records, list):
        raise EnrichmentError("Archive records must be a list")
    result: Dict[str, Dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict) or record.get("category") != "movie":
            continue
        douban_id = str(record.get("douban_id") or "").strip()
        if not douban_id.isdigit():
            raise EnrichmentError("Archive contains an invalid movie Douban ID")
        if record.get("media_type") not in {"movie", "tv"}:
            raise EnrichmentError(f"Archive item {douban_id} has no valid media_type")
        if not isinstance(record.get("aliases"), list):
            raise EnrichmentError(f"Archive item {douban_id} has invalid aliases")
        release_year = record.get("release_year")
        if release_year is not None and type(release_year) is not int:
            raise EnrichmentError(f"Archive item {douban_id} has invalid release_year")
        if douban_id in result:
            raise EnrichmentError(f"Archive contains duplicate Douban ID: {douban_id}")
        result[douban_id] = record
    return result


class TmdbClient:
    def __init__(self, api_key: str, session: Optional[requests.Session] = None, timeout: int = 20):
        if not api_key:
            raise EnrichmentError("Missing TMDB_API_KEY")
        self.api_key = api_key
        self.session = session or requests.Session()
        self.timeout = timeout

    def get_json(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        safe_params = dict(params or {})
        safe_params["api_key"] = self.api_key
        try:
            response = self.session.get(f"{TMDB_BASE_URL}{path}", params=safe_params, timeout=self.timeout)
        except requests.RequestException as exc:
            raise TemporaryItemError("tmdb_temporarily_unavailable") from exc
        if response.status_code in {401, 403}:
            raise EnrichmentError("TMDb authentication failed; check TMDB_API_KEY")
        if response.status_code == 429 or response.status_code >= 500:
            raise TemporaryItemError("tmdb_temporarily_unavailable")
        if response.status_code >= 400:
            raise EnrichmentError(f"TMDb returned HTTP {response.status_code}")
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise TemporaryItemError("tmdb_invalid_response") from exc
        if not isinstance(payload, dict):
            raise TemporaryItemError("tmdb_invalid_response")
        return payload

    def search(self, media_type: str, query: str) -> List[Dict[str, Any]]:
        payload = self.get_json(
            f"/search/{media_type}",
            {"query": query, "language": "zh-CN", "include_adult": "false", "page": 1},
        )
        results = payload.get("results") or []
        return [row for row in results if isinstance(row, dict)]

    def details(self, media_type: str, tmdb_id: str) -> Dict[str, Any]:
        return self.get_json(f"/{media_type}/{tmdb_id}", {"language": "zh-CN"})


def tmdb_year(media_type: str, row: Dict[str, Any]) -> Optional[int]:
    field = "release_date" if media_type == "movie" else "first_air_date"
    return clean_year(row.get(field))


def tmdb_names(media_type: str, row: Dict[str, Any]) -> List[str]:
    fields = ("title", "original_title") if media_type == "movie" else ("name", "original_name")
    return [str(row.get(field) or "").strip() for field in fields if str(row.get(field) or "").strip()]


def evidence_titles(record: Dict[str, Any], detail: Dict[str, Any]) -> List[str]:
    values: List[str] = []
    for value in [detail.get("original_title"), detail.get("douban_title")]:
        values.extend(split_titles(value))
    for value in detail.get("aliases") or []:
        values.extend(split_titles(value))
    for value in record.get("aliases") or []:
        values.extend(split_titles(value))
    values.extend(split_titles(record.get("title")))
    unique: List[str] = []
    seen = set()
    for value in values:
        normalized = normalize_title(value)
        if normalized and normalized not in seen:
            seen.add(normalized)
            unique.append(value)
    return unique[:6]


def choose_title_candidate(
    record: Dict[str, Any], detail: Dict[str, Any], candidates: Iterable[Tuple[str, Dict[str, Any]]]
) -> Optional[Tuple[str, Dict[str, Any]]]:
    evidence = {normalize_title(value) for value in evidence_titles(record, detail)}
    douban_year = detail.get("douban_year")
    ranked: Dict[Tuple[str, str], Tuple[int, str, Dict[str, Any]]] = {}
    for media_type, row in candidates:
        tmdb_id = str(row.get("id") or "").strip()
        if media_type not in {"movie", "tv"} or not tmdb_id.isdigit():
            continue
        if detail.get("episode_count") and media_type == "movie":
            continue
        names = {normalize_title(value) for value in tmdb_names(media_type, row)}
        if not evidence.intersection(names):
            continue
        candidate_year = tmdb_year(media_type, row)
        if douban_year and candidate_year and douban_year != candidate_year:
            continue
        score = 100
        if douban_year and candidate_year == douban_year:
            score += 20
        if media_type == record.get("media_type"):
            score += 5
        if detail.get("episode_count") and media_type == "tv":
            score += 10
        key = (media_type, tmdb_id)
        current = ranked.get(key)
        if current is None or score > current[0]:
            ranked[key] = (score, media_type, row)

    ordered = sorted(ranked.values(), key=lambda value: (-value[0], value[1], str(value[2].get("id"))))
    if not ordered:
        return None
    if len(ordered) > 1 and ordered[0][0] - ordered[1][0] < 10:
        return None
    return ordered[0][1], ordered[0][2]


def pending_item(record: Dict[str, Any], reason: str) -> Dict[str, Any]:
    return {
        "source_title": str(record.get("title") or "").strip(),
        "source_media_type": record.get("media_type"),
        "status": "pending",
        "resolved_type": None,
        "tmdb_id": None,
        "production_year": None,
        "imdb_id": None,
        "reason": reason,
        "source": "github:tmdb",
    }


def unresolved_item(record: Dict[str, Any], detail: Dict[str, Any], reason: str) -> Dict[str, Any]:
    return {
        "source_title": str(record.get("title") or "").strip(),
        "source_media_type": record.get("media_type"),
        "status": "unresolved",
        "resolved_type": None,
        "tmdb_id": None,
        "production_year": None,
        "imdb_id": detail.get("imdb_id"),
        "douban_year": detail.get("douban_year"),
        "original_title": detail.get("original_title") or None,
        "aliases": detail.get("aliases") or [],
        "episode_count": detail.get("episode_count"),
        "reason": reason,
        "source": "github:tmdb",
    }


def resolved_item(
    record: Dict[str, Any], detail: Dict[str, Any], media_type: str, row: Dict[str, Any], method: str
) -> Dict[str, Any]:
    tmdb_id = str(row.get("id") or "").strip()
    names = tmdb_names(media_type, row)
    year = tmdb_year(media_type, row)
    year_source = "tmdb" if year else "douban" if detail.get("douban_year") else None
    return {
        "source_title": str(record.get("title") or "").strip(),
        "source_media_type": record.get("media_type"),
        "status": "resolved",
        "resolved_type": media_type,
        "tmdb_id": tmdb_id,
        "tmdb_title": names[0] if names else str(record.get("title") or "").strip(),
        "production_year": year or detail.get("douban_year"),
        "poster_path": row.get("poster_path") or None,
        "imdb_id": detail.get("imdb_id"),
        "douban_year": detail.get("douban_year"),
        "original_title": detail.get("original_title") or None,
        "aliases": detail.get("aliases") or [],
        "episode_count": detail.get("episode_count"),
        "match_method": method,
        "year_source": year_source,
        "source": "github:tmdb",
    }


def override_item(record: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    media_type = str(override.get("resolved_type") or "").strip()
    tmdb_id = str(override.get("tmdb_id") or "").strip()
    if media_type not in {"movie", "tv"} or not tmdb_id.isdigit():
        raise EnrichmentError(f"Invalid override for Douban {record.get('douban_id')}")
    return {
        "source_title": str(record.get("title") or "").strip(),
        "source_media_type": record.get("media_type"),
        "status": "resolved",
        "resolved_type": media_type,
        "tmdb_id": tmdb_id,
        "tmdb_title": str(override.get("tmdb_title") or record.get("title") or "").strip(),
        "production_year": clean_year(override.get("production_year")),
        "poster_path": override.get("poster_path") or None,
        "imdb_id": override.get("imdb_id") or None,
        "match_method": "override",
        "year_source": "override" if clean_year(override.get("production_year")) else None,
        "source": "github:override",
    }


def override_matches(item: Optional[Dict[str, Any]], override: Dict[str, Any]) -> bool:
    if not item or item.get("status") != "resolved":
        return False
    return (
        item.get("resolved_type") == override.get("resolved_type")
        and str(item.get("tmdb_id") or "") == str(override.get("tmdb_id") or "")
        and item.get("production_year") == clean_year(override.get("production_year"))
    )


class Enricher:
    def __init__(
        self,
        tmdb: Any,
        sleep_range: Tuple[float, float] = (3.0, 8.0),
    ):
        self.tmdb = tmdb
        self.sleep_range = sleep_range

    def resolve(self, record: Dict[str, Any], override: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if override:
            return override_item(record, override)
        try:
            detail = archive_evidence(record)
            candidates: List[Tuple[str, Dict[str, Any]]] = []
            for query in evidence_titles(record, detail):
                for media_type in ("movie", "tv"):
                    candidates.extend((media_type, row) for row in self.tmdb.search(media_type, query))
            chosen = choose_title_candidate(record, detail, candidates)
            if chosen is None:
                return unresolved_item(record, detail, "no_confident_match")
            media_type, found = chosen
            full = self.tmdb.details(media_type, str(found.get("id") or ""))
            return resolved_item(record, detail, media_type, full, "title_year")
        except TemporaryItemError as exc:
            return pending_item(record, exc.reason)


def validate_tmdb_archive(
    payload: Dict[str, Any], archive: Dict[str, Any], secret_values: Sequence[str] = ()
) -> None:
    if payload.get("version") != 1 or not isinstance(payload.get("items"), dict):
        raise EnrichmentError("TMDb archive must be a version 1 object with items")
    records = movie_records(archive)
    items = payload["items"]
    missing = sorted(set(records).difference(items))
    if missing:
        raise EnrichmentError(f"TMDb archive is missing {len(missing)} current Douban items")
    for douban_id, item in items.items():
        if not str(douban_id).isdigit() or not isinstance(item, dict):
            raise EnrichmentError("TMDb archive contains an invalid item")
        status = item.get("status")
        if status not in VALID_STATUSES:
            raise EnrichmentError(f"TMDb item {douban_id} has invalid status")
        if status == "resolved":
            if item.get("resolved_type") not in {"movie", "tv"}:
                raise EnrichmentError(f"Resolved item {douban_id} has invalid type")
            if not str(item.get("tmdb_id") or "").isdigit():
                raise EnrichmentError(f"Resolved item {douban_id} has invalid TMDb ID")
        else:
            if item.get("tmdb_id") is not None or item.get("resolved_type") is not None:
                raise EnrichmentError(f"Non-resolved item {douban_id} contains an asserted identity")
            if not item.get("reason"):
                raise EnrichmentError(f"Non-resolved item {douban_id} has no reason")
    serialized = json.dumps(payload, ensure_ascii=False)
    if any(value and len(value) >= 8 and value in serialized for value in secret_values):
        raise EnrichmentError("Secret leaked into TMDb archive")
    forbidden = ("/Users/", "EMBY_API_KEY", "TMDB_API_KEY", "GITHUB_PROGRESS_TOKEN")
    if any(value in serialized for value in forbidden):
        raise EnrichmentError("Machine-local or secret data leaked into TMDb archive")


def refresh_missing_year(tmdb: Any, item: Dict[str, Any]) -> Dict[str, Any]:
    if item.get("status") != "resolved" or item.get("production_year") is not None:
        return item
    try:
        details = tmdb.details(item["resolved_type"], str(item["tmdb_id"]))
    except TemporaryItemError:
        return item
    year = tmdb_year(item["resolved_type"], details)
    if year is None:
        return item
    updated = dict(item)
    updated["production_year"] = year
    updated["year_source"] = "tmdb"
    if details.get("poster_path") and not updated.get("poster_path"):
        updated["poster_path"] = details["poster_path"]
    names = tmdb_names(item["resolved_type"], details)
    if names:
        updated["tmdb_title"] = names[0]
    return updated


def enrich(
    archive: Dict[str, Any],
    cache: Dict[str, Any],
    overrides: Dict[str, Any],
    enricher: Enricher,
    max_items: int,
    retry_unresolved: bool,
    secret_values: Sequence[str] = (),
) -> Tuple[Dict[str, Any], Dict[str, int]]:
    records = movie_records(archive)
    original_items = cache.get("items") if isinstance(cache.get("items"), dict) else {}
    items = {key: dict(value) for key, value in original_items.items() if isinstance(value, dict)}

    for douban_id, item in list(items.items()):
        items[douban_id] = refresh_missing_year(enricher.tmdb, item)

    candidates: List[Tuple[int, str]] = []
    for douban_id in sorted(records, key=int):
        old = items.get(douban_id)
        override = overrides.get(douban_id) if isinstance(overrides.get(douban_id), dict) else None
        if override and not override_matches(old, override):
            candidates.append((0, douban_id))
        elif old is None:
            candidates.append((2, douban_id))
        elif old.get("status") == "pending":
            priority = 1 if old.get("reason") == "batch_limit" else 3
            candidates.append((priority, douban_id))
        elif old.get("status") == "unresolved" and retry_unresolved:
            candidates.append((4, douban_id))

    fetched = 0
    for _priority, douban_id in sorted(candidates, key=lambda value: (value[0], int(value[1]))):
        record = records[douban_id]
        override = overrides.get(douban_id) if isinstance(overrides.get(douban_id), dict) else None
        if not override and fetched >= max_items:
            if douban_id not in items:
                items[douban_id] = pending_item(record, "batch_limit")
            continue
        items[douban_id] = enricher.resolve(record, override)
        if not override:
            fetched += 1
            if fetched < max_items and enricher.sleep_range[1] > 0:
                time.sleep(random.uniform(*enricher.sleep_range))

    changed = items != original_items
    payload = {
        "version": 1,
        "generated_at": (
            datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            if changed
            else cache.get("generated_at")
        ),
        "items": items,
    }
    validate_tmdb_archive(payload, archive, secret_values)
    stats = {
        "total": len(items),
        "resolved": sum(item.get("status") == "resolved" for item in items.values()),
        "unresolved": sum(item.get("status") == "unresolved" for item in items.values()),
        "pending": sum(item.get("status") == "pending" for item in items.values()),
        "changed": int(changed),
    }
    return payload, stats


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as file_obj:
        json.dump(payload, file_obj, ensure_ascii=False, indent=2, sort_keys=True)
        file_obj.write("\n")
    temp_path.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Incrementally enrich Douban subjects with TMDb identities")
    parser.add_argument("--retry-unresolved", action="store_true")
    parser.add_argument("--max-items", type=int, default=20)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--min-sleep", type=float, default=3.0)
    parser.add_argument("--max-sleep", type=float, default=8.0)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.max_items < 1:
        raise EnrichmentError("--max-items must be at least 1")
    if args.min_sleep < 0 or args.min_sleep > args.max_sleep:
        raise EnrichmentError("Invalid sleep range")
    api_key = os.environ.get("TMDB_API_KEY", "").strip()

    archive = load_json(ARCHIVE_FILE)
    cache = load_json(TMDB_FILE, {"version": 1, "generated_at": None, "items": {}})
    overrides = load_json(OVERRIDES_FILE, {})
    if not isinstance(overrides, dict):
        raise EnrichmentError("TMDb overrides must be an object")

    tmdb = TmdbClient(api_key, timeout=args.timeout)
    payload, stats = enrich(
        archive,
        cache,
        overrides,
        Enricher(tmdb, (args.min_sleep, args.max_sleep)),
        args.max_items,
        args.retry_unresolved,
        (api_key,),
    )
    print(json.dumps(stats, ensure_ascii=False, sort_keys=True))
    if stats["changed"] and not args.dry_run:
        write_json(TMDB_FILE, payload)
        print(f"Wrote {TMDB_FILE.relative_to(BASE_DIR)}")
    elif stats["changed"]:
        print("Dry run: TMDb archive would change")
    else:
        print("No TMDb archive changes")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except EnrichmentError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
