"""
api_clients.py
--------------
Wrappers for Jikan (MyAnimeList) and AniList GraphQL.

- Jikan provides search/details/genres for both anime & manga (no API key).
- AniList is queried by MAL ID to fetch the bannerImage + extra-large cover,
  which Jikan doesn't reliably expose.
"""

import time
import requests
from typing import Optional
from functools import lru_cache

JIKAN_BASE = "https://api.jikan.moe/v4"
ANILIST_URL = "https://graphql.anilist.co"

# ---- reusable session (connection pooling) ----
_session = requests.Session()
_session.headers.update({"Accept": "application/json"})

# Jikan asks for ~3 req/sec. Simple in-process throttle.
_LAST_CALL = {"t": 0.0}
def _throttle(min_interval: float = 0.34):
    delta = time.time() - _LAST_CALL["t"]
    if delta < min_interval:
        time.sleep(min_interval - delta)
    _LAST_CALL["t"] = time.time()


# ---- simple TTL cache ----
_cache: dict[str, tuple[float, object]] = {}
_CACHE_TTL = 300  # 5 minutes

def _cached_get(key: str, fetcher, ttl: float = _CACHE_TTL):
    now = time.time()
    if key in _cache:
        ts, val = _cache[key]
        if now - ts < ttl:
            return val
    val = fetcher()
    _cache[key] = (now, val)
    return val


# ----------------------- JIKAN -----------------------

def jikan_get(path: str, params: dict | None = None) -> dict:
    """GET from Jikan with throttle and automatic retry on 429 / 5xx."""
    for attempt in range(3):
        _throttle()
        try:
            r = _session.get(f"{JIKAN_BASE}{path}", params=params or {}, timeout=15)
        except requests.RequestException:
            if attempt < 2:
                time.sleep(1.5)
                continue
            raise
        if r.status_code == 429:
            # back off and retry
            time.sleep(2 + attempt * 2)
            continue
        if r.status_code >= 500 and attempt < 2:
            time.sleep(1.5)
            continue
        r.raise_for_status()
        return r.json()
    # final attempt, let it raise
    r.raise_for_status()
    return r.json()


def _title_matches(item: dict, query: str) -> bool:
    """Return True if the item is a relevant match for the query.

    1. Full query as a substring of any title → match.
    2. Single-word query → substring match (lenient).
    3. Multi-word → ALL unique query words must appear in at least one title.
    """
    q_lower = query.lower()
    q_words = list(dict.fromkeys(q_lower.split()))  # deduplicate, preserve order
    if not q_words:
        return True

    titles = [
        (item.get("title") or "").lower(),
        (item.get("title_english") or "").lower(),
        (item.get("title_japanese") or "").lower(),
    ]
    for syn in (item.get("title_synonyms") or []):
        titles.append(syn.lower())

    # Full query substring match (strongest signal)
    if any(q_lower in t for t in titles):
        return True

    # Single word → substring match (lenient)
    if len(q_words) == 1:
        return False  # already checked above

    # Multi-word → require ALL query words in at least one title
    for t in titles:
        if all(w in t for w in q_words):
            return True
    return False


def _search_with_filter(endpoint: str, query: str, *, genres: str = "",
                        min_score: str = "", status: str = "", type_: str = "",
                        order_by: str = "members", sort: str = "desc",
                        max_pages: int = 4) -> dict:
    """Fetch up to max_pages API pages and collect all title-matched results.

    Because Jikan does loose word matching, a query like "My Dress-Up Darling"
    may scatter relevant entries (Season 1, Season 2, …) across multiple pages
    filled with unrelated titles.  This helper aggregates them.
    Filter-only searches (no text) use more pages for comprehensive coverage.
    """
    all_matched: list[dict] = []
    seen_ids: set[int] = set()

    for pg in range(1, max_pages + 1):
        params = {
            "q": query, "limit": 25, "page": pg,
            "order_by": order_by, "sort": sort,
            "sfw": "true",
        }
        if genres:    params["genres"] = genres
        if min_score: params["min_score"] = min_score
        if status:    params["status"] = status
        if type_:     params["type"] = type_

        resp = jikan_get(endpoint, params)
        page_data = resp.get("data", [])
        pagination = resp.get("pagination", {})

        for item in page_data:
            mid = item.get("mal_id")
            if mid in seen_ids:
                continue
            seen_ids.add(mid)
            if not query or _title_matches(item, query):
                all_matched.append(item)

        # stop early if the API has no more pages
        if not pagination.get("has_next_page"):
            break

    return {"data": all_matched, "pagination": {}}


def search_anime(query: str, *, genres: str = "", min_score: str = "",
                 status: str = "", type_: str = "", order_by: str = "members",
                 sort: str = "desc", **_kw) -> dict:
    # Filter-only (no text query) fetches more pages for full coverage
    pages = 3 if query else 6
    return _search_with_filter("/anime", query, genres=genres,
                               min_score=min_score, status=status,
                               type_=type_, order_by=order_by, sort=sort,
                               max_pages=pages)


def search_manga(query: str, *, genres: str = "", min_score: str = "",
                 status: str = "", type_: str = "", order_by: str = "members",
                 sort: str = "desc", **_kw) -> dict:
    pages = 3 if query else 6
    return _search_with_filter("/manga", query, genres=genres,
                               min_score=min_score, status=status,
                               type_=type_, order_by=order_by, sort=sort,
                               max_pages=pages)


def get_anime(mal_id: int) -> dict:
    key = f"anime:{mal_id}"
    return _cached_get(key, lambda: jikan_get(f"/anime/{mal_id}/full").get("data", {}))


def get_manga(mal_id: int) -> dict:
    key = f"manga:{mal_id}"
    return _cached_get(key, lambda: jikan_get(f"/manga/{mal_id}/full").get("data", {}))


def get_entry_brief(mal_id: int, kind: str) -> dict:
    """Fetch minimal info (cover image + English title) for a relation entry.

    Uses the full-detail cache when available, otherwise fetches the base
    endpoint (lighter than /full) and caches for the standard TTL.
    """
    key = f"{kind}:{mal_id}"
    # check if we already have it cached from a full fetch
    if key in _cache:
        ts, val = _cache[key]
        if time.time() - ts < _CACHE_TTL and val:
            return val

    def _fetch():
        _throttle()
        r = _session.get(f"{JIKAN_BASE}/{kind}/{mal_id}", timeout=15)
        r.raise_for_status()
        return r.json().get("data", {})

    return _cached_get(f"brief:{key}", _fetch)


def get_anime_episodes(mal_id: int, page: int = 1) -> list[dict]:
    return jikan_get(f"/anime/{mal_id}/episodes", {"page": page}).get("data", [])


def get_genres(kind: str = "anime") -> list[dict]:
    """kind = 'anime' or 'manga'"""
    key = f"genres:{kind}"
    return _cached_get(key, lambda: jikan_get(f"/genres/{kind}").get("data", []), ttl=3600)


def get_top_rated_carousel(kind: str = "anime", limit: int = 25) -> list[dict]:
    """Fetch top anime/manga by member count for the hero carousel.
    Fetches 1 Jikan page (up to 25 items), sorted by members descending.
    Cached for 8 hours.
    """
    key = f"carousel_v4:{kind}"
    def _fetch():
        all_items: list[dict] = []
        seen: set[int] = set()
        params = {
            "min_score": "7",
            "order_by": "members",
            "sort": "desc",
            "limit": 25,
            "page": 1,
            "sfw": "true",
        }
        data = jikan_get(f"/{kind}", params).get("data", [])
        for item in data:
            mid = item.get("mal_id")
            if mid and mid not in seen:
                seen.add(mid)
                all_items.append(item)
        return all_items
    results = _cached_get(key, _fetch, ttl=28800)  # 8 hours
    return results[:limit]


# ----------------------- ANILIST (banners) -----------------------

_ANILIST_QUERY = """
query ($idMal: Int, $type: MediaType) {
  Media(idMal: $idMal, type: $type) {
    id
    bannerImage
    coverImage { extraLarge large color }
    description(asHtml: false)
  }
}
"""

def anilist_extras(mal_id: int, kind: str = "ANIME") -> dict:
    """Fetch banner + hi-res cover + color from AniList using a MAL id. Cached 5 min."""
    key = f"anilist:{kind}:{mal_id}"
    def _fetch():
        try:
            r = _session.post(
                ANILIST_URL,
                json={"query": _ANILIST_QUERY,
                      "variables": {"idMal": int(mal_id), "type": kind.upper()}},
                timeout=15,
            )
            if r.status_code != 200:
                return {}
            data = r.json().get("data", {}).get("Media") or {}
            return {
                "banner": data.get("bannerImage"),
                "cover_xl": (data.get("coverImage") or {}).get("extraLarge"),
                "color": (data.get("coverImage") or {}).get("color"),
            }
        except Exception:
            return {}
    return _cached_get(key, _fetch)
