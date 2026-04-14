from __future__ import annotations

import asyncio
import html
import logging
import re
import time
import unicodedata
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from config import (
    API_CACHE_TTL_SECONDS,
    CATALOG_API_URL,
    CATALOG_SITE_BASE,
    HOME_SECTION_LIMIT,
    POPULAR_LIMIT,
    PUBLISHER_DISCOVERY_MAX_ID,
    SEARCH_LIMIT,
    UPDATES_LIMIT,
)
from core.http_client import get_http_client
from services.cache import TTLCache

logger = logging.getLogger(__name__)

GET_HQS_BY_NAME = """
query getHqsByName($name: String!) {
  getHqsByName(name: $name) {
    id
    name
    editoraId
    status
    publisherName
    impressionsCount
  }
}
"""

GET_HQS_BY_ID = """
query getHqsById($id: Int!) {
  getHqsById(id: $id) {
    id
    name
    synopsis
    editoraId
    status
    publisherName
    hqCover
    impressionsCount
    capitulos {
      name
      id
      number
    }
  }
}
"""

GET_CHAPTER_BY_ID = """
query getChapterById($chapterId: Int!) {
  getChapterById(chapterId: $chapterId) {
    name
    number
    oneshot
    pictures {
      pictureUrl
    }
    hq {
      id
      name
      capitulos {
        id
        number
      }
    }
  }
}
"""

GET_HQS_BY_FILTERS = """
query getHqsByFilters($orderByViews: Boolean, $limit: Int, $publisherId: Int, $loadCovers: Boolean) {
  getHqsByFilters(
    orderByViews: $orderByViews,
    limit: $limit,
    publisherId: $publisherId,
    loadCovers: $loadCovers
  ) {
    id
    name
    editoraId
    status
    publisherName
    impressionsCount
    hqCover
    synopsis
    updatedAt
  }
}
"""

GET_RECENTLY_UPDATED = """
query getRecentlyUpdatedHqs {
  getRecentlyUpdatedHqs {
    id
    name
    hqCover
    synopsis
    updatedAt
    updatedChapters
  }
}
"""

GET_CAROUSEL = """
query getCarouselOfHqs {
  getCarouselOfHqs {
    name
    hqId
    hqCover
  }
}
"""

GET_HQS_BY_PUBLISHER_ID = """
query getHqsByPublisherId($publisherId: Int!) {
  getHqsByPublisherId(publisherId: $publisherId) {
    id
    name
    editoraId
    status
    publisherName
    impressionsCount
    hqCover
    synopsis
    updatedAt
  }
}
"""

_CACHE = TTLCache(max_items=1024)
_INFLIGHT: dict[str, asyncio.Task] = {}
_REQUEST_SEMAPHORE = asyncio.Semaphore(12)

SEARCH_TTL = min(max(API_CACHE_TTL_SECONDS, 180), 1800)
DETAIL_TTL = max(API_CACHE_TTL_SECONDS, 1800)
CHAPTER_TTL = max(API_CACHE_TTL_SECONDS, 900)
HOME_TTL = max(API_CACHE_TTL_SECONDS, 600)
PUBLISHER_TTL = max(API_CACHE_TTL_SECONDS, 3600)


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _fix_mojibake(value: Any) -> str:
    text = _clean(value)
    if not text:
        return ""
    if not any(marker in text for marker in ("Ã", "Â", "â")):
        return html.unescape(text)
    try:
        repaired = text.encode("latin1").decode("utf-8")
    except Exception:
        return html.unescape(text)
    repaired = html.unescape(repaired)
    if repaired.count("Ã") + repaired.count("Â") + repaired.count("â") < text.count("Ã") + text.count("Â") + text.count("â"):
        return repaired
    return html.unescape(text)


def _normalize_text(value: Any) -> str:
    raw = _fix_mojibake(value).lower()
    raw = unicodedata.normalize("NFKD", raw)
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    raw = re.sub(r"[^a-z0-9\s-]", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()


def _slugify(value: Any) -> str:
    normalized = _normalize_text(value)
    normalized = normalized.replace(" ", "-")
    normalized = re.sub(r"-{2,}", "-", normalized).strip("-")
    return normalized or "hq"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _sort_number(value: Any) -> tuple[float, str]:
    text = _clean(value)
    try:
        return (float(text), text)
    except Exception:
        return (0.0, text)


def build_hq_url(hq_id: str | int, title: str) -> str:
    return f"{CATALOG_SITE_BASE}/hq/{_safe_int(hq_id)}/{_slugify(title)}"


def build_reader_url(chapter_id: str | int, title: str, chapter_number: str | int, page_number: int) -> str:
    return (
        f"{CATALOG_SITE_BASE}/hq-reader/{_safe_int(chapter_id)}/{_slugify(title)}"
        f"/chapter/{_clean(chapter_number) or '1'}/page/{max(1, int(page_number))}"
    )


def build_publisher_url(publisher_id: str | int, publisher_name: str) -> str:
    return f"{CATALOG_SITE_BASE}/publisher/{_safe_int(publisher_id)}/{_slugify(publisher_name)}"


def _search_score(query: str, title: str) -> tuple[int, int]:
    q = _normalize_text(query)
    t = _normalize_text(title)
    if not q or not t:
        return (0, 0)
    if q == t:
        return (500, -len(t))
    if t.startswith(q):
        return (400, -len(t))
    if q in t:
        return (300, -len(t))
    q_words = set(q.split())
    t_words = set(t.split())
    overlap = len(q_words & t_words)
    return (100 + overlap, -len(t))


def _cache_key(prefix: str, *parts: Any) -> str:
    return prefix + ":" + ":".join(_clean(part).lower() for part in parts)


async def _dedup_fetch(key: str, ttl: int, factory):
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    inflight = _INFLIGHT.get(key)
    if inflight:
        return await inflight

    async def _runner():
        data = await factory()
        return _CACHE.set(key, data, ttl, stale_ttl=ttl * 6)

    task = asyncio.create_task(_runner())
    _INFLIGHT[key] = task
    try:
        return await task
    finally:
        _INFLIGHT.pop(key, None)


async def _graphql(query: str, variables: dict[str, Any] | None, *, cache_key: str, ttl: int) -> dict[str, Any]:
    stale = _CACHE.get(cache_key, allow_stale=True)
    client = await get_http_client()

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Origin": CATALOG_SITE_BASE,
        "Referer": f"{CATALOG_SITE_BASE}/",
    }
    payload = {"query": query, "variables": variables or {}}

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            async with _REQUEST_SEMAPHORE:
                response = await client.post(CATALOG_API_URL, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            if data.get("errors"):
                raise RuntimeError(str(data["errors"]))
            return _CACHE.set(cache_key, data.get("data") or {}, ttl, stale_ttl=ttl * 6)
        except Exception as error:
            last_error = error
            if attempt < 2:
                await asyncio.sleep(0.6 * (attempt + 1))

    if stale is not None:
        logger.warning("Using stale HQ Now cache for %s after error: %r", cache_key, last_error)
        return stale
    raise RuntimeError(f"Falha ao consultar HQ Now: {last_error!r}")


async def _fetch_html_metadata(url: str) -> dict[str, str]:
    try:
        client = await get_http_client()
        response = await client.get(url, headers={"Accept": "text/html,application/xhtml+xml"})
        response.raise_for_status()
    except Exception:
        return {}

    soup = BeautifulSoup(response.text, "html.parser")
    title = ""
    description = ""
    image = ""

    title_node = soup.find("meta", attrs={"property": "og:title"}) or soup.find("title")
    if title_node:
        title = _fix_mojibake(title_node.get("content") if title_node.name == "meta" else title_node.get_text(" ", strip=True))

    desc_node = soup.find("meta", attrs={"property": "og:description"}) or soup.find("meta", attrs={"name": "description"})
    if desc_node:
        description = _fix_mojibake(desc_node.get("content"))

    image_node = soup.find("meta", attrs={"property": "og:image"})
    if image_node:
        image = urljoin(f"{CATALOG_SITE_BASE}/", _clean(image_node.get("content")))

    return {
        "title": title,
        "description": description,
        "image": image,
    }


def _normalize_summary_item(raw: dict[str, Any]) -> dict[str, Any]:
    hq_id = str(_safe_int(raw.get("id") or raw.get("hqId")) or "").strip()
    title = _fix_mojibake(raw.get("name") or raw.get("title") or "HQ")
    publisher_id = str(_safe_int(raw.get("editoraId")) or "").strip()
    publisher_name = _fix_mojibake(raw.get("publisherName") or "")
    cover_url = _clean(raw.get("hqCover"))
    summary = {
        "hq_id": hq_id,
        "title": title or "HQ",
        "display_title": title or "HQ",
        "title_slug": _slugify(title or "HQ"),
        "publisher_id": publisher_id,
        "publisher_name": publisher_name,
        "status": _fix_mojibake(raw.get("status") or ""),
        "impressions_count": _safe_int(raw.get("impressionsCount")),
        "cover_url": cover_url,
        "synopsis": _fix_mojibake(raw.get("synopsis") or ""),
        "updated_at": _clean(raw.get("updatedAt")),
        "updated_chapters": _fix_mojibake(raw.get("updatedChapters") or ""),
        "site_url": build_hq_url(hq_id, title or "HQ") if hq_id else "",
    }
    return summary


async def search_hqs(query: str, limit: int = SEARCH_LIMIT) -> list[dict[str, Any]]:
    query = _clean(query)
    if not query:
        return []

    key = _cache_key("search", query, limit)

    async def _load():
        data = await _graphql(GET_HQS_BY_NAME, {"name": query}, cache_key=key, ttl=SEARCH_TTL)
        items = [_normalize_summary_item(item) for item in (data.get("getHqsByName") or []) if isinstance(item, dict)]
        items.sort(key=lambda item: _search_score(query, item.get("title") or ""), reverse=True)
        return items[: max(1, int(limit))]

    return await _dedup_fetch(key, SEARCH_TTL, _load)


def get_cached_search_results(query: str, limit: int = SEARCH_LIMIT) -> list[dict[str, Any]] | None:
    cached = _CACHE.get(_cache_key("search", query, limit), allow_stale=True)
    if cached is None:
        return None
    return list(cached)


async def get_most_viewed(limit: int = POPULAR_LIMIT) -> list[dict[str, Any]]:
    limit = max(1, int(limit))
    key = _cache_key("popular", limit)

    async def _load():
        data = await _graphql(
            GET_HQS_BY_FILTERS,
            {"orderByViews": True, "limit": limit, "loadCovers": True},
            cache_key=key,
            ttl=HOME_TTL,
        )
        return [_normalize_summary_item(item) for item in (data.get("getHqsByFilters") or []) if isinstance(item, dict)]

    return await _dedup_fetch(key, HOME_TTL, _load)


def get_cached_most_viewed(limit: int = POPULAR_LIMIT) -> list[dict[str, Any]] | None:
    cached = _CACHE.get(_cache_key("popular", limit), allow_stale=True)
    return list(cached) if cached is not None else None


async def get_recently_updated(limit: int = UPDATES_LIMIT) -> list[dict[str, Any]]:
    limit = max(1, int(limit))
    key = _cache_key("recent_updates", limit)

    async def _load():
        data = await _graphql(GET_RECENTLY_UPDATED, {}, cache_key=key, ttl=HOME_TTL)
        items = [_normalize_summary_item(item) for item in (data.get("getRecentlyUpdatedHqs") or []) if isinstance(item, dict)]
        return items[:limit]

    return await _dedup_fetch(key, HOME_TTL, _load)


async def get_featured_hqs(limit: int = HOME_SECTION_LIMIT) -> list[dict[str, Any]]:
    limit = max(1, int(limit))
    key = _cache_key("featured", limit)

    async def _load():
        data = await _graphql(GET_CAROUSEL, {}, cache_key=key, ttl=HOME_TTL)
        items = []
        for raw in data.get("getCarouselOfHqs") or []:
            item = _normalize_summary_item(raw)
            if not item.get("hq_id"):
                continue
            items.append(item)
        return items[:limit]

    return await _dedup_fetch(key, HOME_TTL, _load)


async def get_home_payload(limit: int = HOME_SECTION_LIMIT) -> dict[str, Any]:
    featured, popular, updates = await asyncio.gather(
        get_featured_hqs(limit=min(limit, 6)),
        get_most_viewed(limit=limit),
        get_recently_updated(limit=limit),
    )
    return {
        "featured": featured,
        "popular": popular,
        "updates": updates,
    }


def get_cached_home_snapshot(limit: int = HOME_SECTION_LIMIT) -> dict[str, Any]:
    return {
        "featured": list(_CACHE.get(_cache_key("featured", min(limit, 6)), allow_stale=True) or []),
        "popular": list(_CACHE.get(_cache_key("popular", limit), allow_stale=True) or []),
        "updates": list(_CACHE.get(_cache_key("recent_updates", limit), allow_stale=True) or []),
    }


def _normalize_chapters(title: str, chapters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for raw in chapters or []:
        chapter_id = str(_safe_int(raw.get("id")) or "").strip()
        if not chapter_id:
            continue
        chapter_number = _clean(raw.get("number") or "1")
        chapter_name = _fix_mojibake(raw.get("name") or "")
        normalized.append(
            {
                "chapter_id": chapter_id,
                "chapter_number": chapter_number,
                "chapter_name": chapter_name or f"Capitulo {chapter_number}",
                "reader_url": build_reader_url(chapter_id, title, chapter_number, 1),
            }
        )
    normalized.sort(key=lambda item: _sort_number(item.get("chapter_number")), reverse=True)
    return normalized


async def get_hq_details(hq_id: str | int) -> dict[str, Any]:
    normalized_id = _safe_int(hq_id)
    if not normalized_id:
        raise ValueError("hq_id invalido.")

    key = _cache_key("hq", normalized_id)

    async def _load():
        data = await _graphql(GET_HQS_BY_ID, {"id": normalized_id}, cache_key=key, ttl=DETAIL_TTL)
        raw_result = data.get("getHqsById") or {}
        raw = raw_result[0] if isinstance(raw_result, list) else raw_result
        item = _normalize_summary_item(raw)
        chapters = _normalize_chapters(item["title"], raw.get("capitulos") or [])
        item["chapters"] = chapters
        item["chapter_count"] = len(chapters)
        item["latest_chapter"] = chapters[0] if chapters else None
        item["first_chapter"] = chapters[-1] if chapters else None

        if not item.get("cover_url") or not item.get("synopsis"):
            fallback = await _fetch_html_metadata(item["site_url"])
            if fallback.get("image") and not item.get("cover_url"):
                item["cover_url"] = fallback["image"]
            if fallback.get("description") and not item.get("synopsis"):
                item["synopsis"] = fallback["description"]
        return item

    return await _dedup_fetch(key, DETAIL_TTL, _load)


def get_cached_hq_details(hq_id: str | int) -> dict[str, Any] | None:
    cached = _CACHE.get(_cache_key("hq", _safe_int(hq_id)), allow_stale=True)
    return dict(cached) if cached is not None else None


async def get_chapter_reader_payload(chapter_id: str | int, page_number: int = 1) -> dict[str, Any]:
    normalized_id = _safe_int(chapter_id)
    if not normalized_id:
        raise ValueError("chapter_id invalido.")

    key = _cache_key("chapter", normalized_id)

    async def _load():
        data = await _graphql(GET_CHAPTER_BY_ID, {"chapterId": normalized_id}, cache_key=key, ttl=CHAPTER_TTL)
        raw = data.get("getChapterById") or {}
        hq = raw.get("hq") or {}
        title = _fix_mojibake(hq.get("name") or "HQ")
        title_id = str(_safe_int(hq.get("id")) or "").strip()
        chapter_number = _clean(raw.get("number") or "1")
        pictures = [
            _clean(item.get("pictureUrl"))
            for item in (raw.get("pictures") or [])
            if isinstance(item, dict) and _clean(item.get("pictureUrl"))
        ]

        chapters_asc = _normalize_chapters(title, hq.get("capitulos") or [])
        chapters_asc = sorted(chapters_asc, key=lambda item: _sort_number(item.get("chapter_number")))
        current_index = next(
            (index for index, item in enumerate(chapters_asc) if item["chapter_id"] == str(normalized_id)),
            -1,
        )
        previous_chapter = chapters_asc[current_index - 1] if current_index > 0 else None
        next_chapter = chapters_asc[current_index + 1] if current_index >= 0 and current_index + 1 < len(chapters_asc) else None

        page_total = max(1, len(pictures))
        current_page = max(1, min(int(page_number), page_total))
        current_image = pictures[current_page - 1] if pictures else ""

        return {
            "chapter_id": str(normalized_id),
            "chapter_name": _fix_mojibake(raw.get("name") or ""),
            "chapter_number": chapter_number,
            "oneshot": bool(raw.get("oneshot")),
            "pictures": pictures,
            "images": pictures,
            "page_count": page_total,
            "current_page": current_page,
            "current_image": current_image,
            "title_id": title_id,
            "title": title,
            "site_url": build_hq_url(title_id, title) if title_id else "",
            "reader_url": build_reader_url(normalized_id, title, chapter_number, current_page),
            "previous_chapter": previous_chapter,
            "next_chapter": next_chapter,
        }

    payload = await _dedup_fetch(key, CHAPTER_TTL, _load)
    cloned = dict(payload)
    page_total = max(1, int(cloned.get("page_count") or 1))
    current_page = max(1, min(int(page_number), page_total))
    pictures = list(cloned.get("pictures") or [])
    cloned["page_count"] = page_total
    cloned["current_page"] = current_page
    cloned["current_image"] = pictures[current_page - 1] if pictures else ""
    cloned["reader_url"] = build_reader_url(
        cloned["chapter_id"],
        cloned["title"],
        cloned["chapter_number"],
        current_page,
    )
    return cloned


def get_cached_chapter_reader_payload(chapter_id: str | int, page_number: int = 1) -> dict[str, Any] | None:
    cached = _CACHE.get(_cache_key("chapter", _safe_int(chapter_id)), allow_stale=True)
    if cached is None:
        return None
    payload = dict(cached)
    pictures = list(payload.get("pictures") or [])
    page_total = max(1, len(pictures))
    current_page = max(1, min(int(page_number), page_total))
    payload["page_count"] = page_total
    payload["current_page"] = current_page
    payload["current_image"] = pictures[current_page - 1] if pictures else ""
    payload["reader_url"] = build_reader_url(
        payload["chapter_id"],
        payload["title"],
        payload["chapter_number"],
        current_page,
    )
    return payload


async def get_publisher_hqs(publisher_id: str | int) -> list[dict[str, Any]]:
    normalized_id = _safe_int(publisher_id)
    if not normalized_id:
        return []

    key = _cache_key("publisher", normalized_id)

    async def _load():
        data = await _graphql(GET_HQS_BY_PUBLISHER_ID, {"publisherId": normalized_id}, cache_key=key, ttl=PUBLISHER_TTL)
        items = [_normalize_summary_item(item) for item in (data.get("getHqsByPublisherId") or []) if isinstance(item, dict)]
        items.sort(key=lambda item: (_normalize_text(item.get("title")), item.get("impressions_count", 0)))
        return items

    return await _dedup_fetch(key, PUBLISHER_TTL, _load)


async def list_publishers() -> list[dict[str, Any]]:
    key = _cache_key("publishers", PUBLISHER_DISCOVERY_MAX_ID)

    async def _load():
        discovered: dict[str, dict[str, Any]] = {}

        def _remember(item: dict[str, Any], sample_count: int = 0) -> None:
            publisher_id = str(item.get("publisher_id") or "").strip()
            publisher_name = _fix_mojibake(item.get("publisher_name") or "")
            if not publisher_id or not publisher_name:
                return
            discovered[publisher_id] = {
                "publisher_id": publisher_id,
                "publisher_name": publisher_name,
                "slug": _slugify(publisher_name),
                "sample_count": max(sample_count, int(discovered.get(publisher_id, {}).get("sample_count") or 0)),
                "site_url": build_publisher_url(publisher_id, publisher_name),
            }

        popular, recent = await asyncio.gather(
            get_most_viewed(limit=max(20, min(POPULAR_LIMIT, 120))),
            get_recently_updated(limit=max(20, min(UPDATES_LIMIT, 80))),
        )
        for item in [*popular, *recent]:
            _remember(item)

        if len(discovered) < 6:
            async def _probe(pid: int) -> tuple[int, list[dict[str, Any]]]:
                try:
                    items = await get_publisher_hqs(pid)
                except Exception:
                    items = []
                return pid, items

            results = await asyncio.gather(
                *[_probe(publisher_id) for publisher_id in range(1, max(1, PUBLISHER_DISCOVERY_MAX_ID) + 1)],
                return_exceptions=False,
            )
            for publisher_id, items in results:
                if not items:
                    continue
                sample = items[0]
                _remember(
                    {
                        "publisher_id": str(publisher_id),
                        "publisher_name": sample.get("publisher_name") or f"Editora {publisher_id}",
                    },
                    sample_count=len(items),
                )

        items = sorted(
            discovered.values(),
            key=lambda item: (_normalize_text(item.get("publisher_name")), item.get("publisher_id")),
        )
        return items

    return await _dedup_fetch(key, PUBLISHER_TTL, _load)


async def get_recent_updates_with_chapters(limit: int = 8) -> list[dict[str, Any]]:
    recent = await get_recently_updated(limit=max(limit, 12))

    async def _enrich(item: dict[str, Any]) -> dict[str, Any]:
        try:
            details = await get_hq_details(item["hq_id"])
        except Exception:
            return item
        latest = details.get("latest_chapter") or {}
        merged = dict(details)
        merged["updated_chapters"] = item.get("updated_chapters") or ""
        merged["updated_at"] = item.get("updated_at") or details.get("updated_at") or ""
        merged["latest_chapter"] = latest or None
        return merged

    enriched = await asyncio.gather(*[_enrich(item) for item in recent[:limit]], return_exceptions=True)
    results: list[dict[str, Any]] = []
    for item in enriched:
        if isinstance(item, dict):
            results.append(item)
    return results[:limit]


async def get_series_catalog() -> list[dict[str, Any]]:
    key = _cache_key("series_catalog")

    async def _load():
        catalog: dict[str, dict[str, Any]] = {}

        def _remember(item: dict[str, Any]) -> None:
            hq_id = str(item.get("hq_id") or "").strip()
            if not hq_id:
                return
            catalog[hq_id] = dict(item)

        publishers = await list_publishers()
        publisher_results = await asyncio.gather(
            *[get_publisher_hqs(item["publisher_id"]) for item in publishers if item.get("publisher_id")],
            return_exceptions=True,
        )

        for batch in publisher_results:
            if isinstance(batch, list):
                for item in batch:
                    _remember(item)

        featured, popular, updates = await asyncio.gather(
            get_featured_hqs(limit=max(HOME_SECTION_LIMIT, 12)),
            get_most_viewed(limit=max(POPULAR_LIMIT, 60)),
            get_recently_updated(limit=max(UPDATES_LIMIT, 40)),
        )
        for item in [*featured, *popular, *updates]:
            _remember(item)

        return sorted(
            catalog.values(),
            key=lambda item: (_normalize_text(item.get("title") or ""), item.get("hq_id") or ""),
        )

    return await _dedup_fetch(key, PUBLISHER_TTL, _load)


async def warm_catalog_cache() -> None:
    try:
        await asyncio.gather(
            get_home_payload(limit=min(HOME_SECTION_LIMIT, 8)),
            get_most_viewed(limit=min(POPULAR_LIMIT, 30)),
            get_recently_updated(limit=min(UPDATES_LIMIT, 20)),
        )
    except Exception as error:
        logger.warning("Warm cache failed: %r", error)
