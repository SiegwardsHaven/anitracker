"""
database.py
-----------
MongoDB-backed 'My List'. All anime/manga entries are scoped to a user_id
so each account has its own library that follows them when logged in.
"""

from datetime import datetime
from bson.objectid import ObjectId
from mongo import anime_list_col, manga_list_col

ANIME_STATUSES = ["Watching", "Completed", "On Hold", "Dropped", "Plan to Watch"]
MANGA_STATUSES = ["Reading",  "Completed", "On Hold", "Dropped", "Plan to Read"]

# Status -> hex color (used in CSS classes)
STATUS_COLORS = {
    # anime
    "Watching":      "#3b82f6",  # blue
    "Plan to Watch": "#a855f7",  # purple
    # manga
    "Reading":       "#10b981",  # green
    "Plan to Read":  "#f59e0b",  # amber
    # shared
    "Completed":     "#22c55e",  # green
    "On Hold":       "#eab308",  # yellow
    "Dropped":       "#ef4444",  # red
}


def init_db():
    """No-op for MongoDB — indexes are created in mongo.init_indexes()."""
    pass


# ---------- ANIME ----------

def upsert_anime(user_id, mal_id, title, cover_url, total_eps, status,
                 progress=0, score=None, title_english=None, title_japanese=None,
                 notes=None, genres=None, studios=None):
    update_fields = {
        "title": title,
        "cover_url": cover_url,
        "total_eps": total_eps or 0,
        "status": status,
        "progress": progress,
        "score": score,
        "updated_at": datetime.utcnow(),
    }
    if title_english is not None:
        update_fields["title_english"] = title_english
    if title_japanese is not None:
        update_fields["title_japanese"] = title_japanese
    if notes is not None:
        update_fields["notes"] = notes or ""
    if genres is not None:
        update_fields["genres"] = genres
    if studios is not None:
        update_fields["studios"] = studios
    anime_list_col.update_one(
        {"user_id": user_id, "mal_id": mal_id},
        {"$set": update_fields,
         "$setOnInsert": {
            "added_at": datetime.utcnow(),
        }},
        upsert=True,
    )


def remove_anime(user_id, mal_id):
    anime_list_col.delete_one({"user_id": user_id, "mal_id": mal_id})


def get_anime_entry(user_id, mal_id):
    doc = anime_list_col.find_one({"user_id": user_id, "mal_id": mal_id})
    if doc:
        doc["_id"] = str(doc["_id"])
    return doc


def list_anime(user_id, status=None):
    query = {"user_id": user_id}
    if status:
        query["status"] = status
    return list(anime_list_col.find(query).sort("updated_at", -1))


# ---------- MANGA ----------

def upsert_manga(user_id, mal_id, title, cover_url, total_chs, status,
                 progress=0, score=None, title_english=None, title_japanese=None,
                 notes=None, genres=None):
    update_fields = {
        "title": title,
        "cover_url": cover_url,
        "total_chs": total_chs or 0,
        "status": status,
        "progress": progress,
        "score": score,
        "updated_at": datetime.utcnow(),
    }
    if title_english is not None:
        update_fields["title_english"] = title_english
    if title_japanese is not None:
        update_fields["title_japanese"] = title_japanese
    if notes is not None:
        update_fields["notes"] = notes or ""
    if genres is not None:
        update_fields["genres"] = genres
    manga_list_col.update_one(
        {"user_id": user_id, "mal_id": mal_id},
        {"$set": update_fields,
         "$setOnInsert": {
            "added_at": datetime.utcnow(),
        }},
        upsert=True,
    )


def remove_manga(user_id, mal_id):
    manga_list_col.delete_one({"user_id": user_id, "mal_id": mal_id})


def get_manga_entry(user_id, mal_id):
    doc = manga_list_col.find_one({"user_id": user_id, "mal_id": mal_id})
    if doc:
        doc["_id"] = str(doc["_id"])
    return doc


def list_manga(user_id, status=None):
    query = {"user_id": user_id}
    if status:
        query["status"] = status
    return list(manga_list_col.find(query).sort("updated_at", -1))
