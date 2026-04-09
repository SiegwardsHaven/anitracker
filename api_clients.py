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
    _throttle()
    r = _session.get(f"{JIKAN_BASE}{path}", params=params or {}, timeout=15)
    r.raise_for_status()
    return r.json()


def search_anime(query: str, *, genres: str = "", min_score: str = "",
                 status: str = "", type_: str = "", order_by: str = "members",
                 sort: str = "desc", limit: int = 24, page: int = 1) -> list[dict]:
    """
    Search anime by title (searches across title, title_english, title_japanese, and title_synonyms).
    Results ordered by 'members' (popularity) by default for better relevance.
    """
    params = {
        "q": query, "limit": limit, "page": page,
        "order_by": order_by, "sort": sort,
        "sfw": "true",
    }
    if genres:    params["genres"] = genres
    if min_score: params["min_score"] = min_score
    if status:    params["status"] = status
    if type_:     params["type"] = type_
    return jikan_get("/anime", params).get("data", [])


def search_manga(query: str, *, genres: str = "", min_score: str = "",
                 status: str = "", type_: str = "", order_by: str = "members",
                 sort: str = "desc", limit: int = 24, page: int = 1) -> list[dict]:
    """
    Search manga by title (searches across title, title_english, title_japanese, and title_synonyms).
    Results ordered by 'members' (popularity) by default for better relevance.
    """
    params = {
        "q": query, "limit": limit, "page": page,
        "order_by": order_by, "sort": sort,
        "sfw": "true",
    }
    if genres:    params["genres"] = genres
    if min_score: params["min_score"] = min_score
    if status:    params["status"] = status
    if type_:     params["type"] = type_
    return jikan_get("/manga", params).get("data", [])


def get_anime(mal_id: int) -> dict:
    key = f"anime:{mal_id}"
    return _cached_get(key, lambda: jikan_get(f"/anime/{mal_id}/full").get("data", {}))


def get_manga(mal_id: int) -> dict:
    key = f"manga:{mal_id}"
    return _cached_get(key, lambda: jikan_get(f"/manga/{mal_id}/full").get("data", {}))


def get_anime_episodes(mal_id: int, page: int = 1) -> list[dict]:
    return jikan_get(f"/anime/{mal_id}/episodes", {"page": page}).get("data", [])


def get_genres(kind: str = "anime") -> list[dict]:
    """kind = 'anime' or 'manga'"""
    key = f"genres:{kind}"
    return _cached_get(key, lambda: jikan_get(f"/genres/{kind}").get("data", []), ttl=3600)


def get_top_rated_carousel(kind: str = "anime", limit: int = 20) -> list[dict]:
    """Fetch well-known, trending anime/manga with high scores for carousel.
    Orders by popularity (members) to get trending titles, filtered by high MAL scores (7+).
    Cached for 8 hours, shuffled once at cache time.
    """
    import random
    key = f"carousel:{kind}"
    def _fetch():
        params = {
            "min_score": "7",
            "order_by": "members",
            "sort": "desc",
            "limit": 25,
            "sfw": "true",
        }
        data = jikan_get(f"/{kind}", params).get("data", [])
        random.shuffle(data)
        return data
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
