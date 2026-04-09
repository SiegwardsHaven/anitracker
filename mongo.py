"""
mongo.py
--------
Shared MongoDB Atlas connection for the entire application.
All collections are accessed through this module.
"""

import os
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")

client = MongoClient(MONGO_URI)
db = client["anitracker"]

# Collections
users_col = db["users"]
reset_tokens_col = db["password_reset_tokens"]
anime_list_col = db["anime_list"]
manga_list_col = db["manga_list"]
security_questions_col = db["security_questions"]
avatar_cache_col = db["avatar_cache"]


def init_indexes():
    """Create all required indexes. Safe to call multiple times."""
    users_col.create_index("username", unique=True)
    reset_tokens_col.create_index("expires_at", expireAfterSeconds=0)
    anime_list_col.create_index([("user_id", 1), ("mal_id", 1)], unique=True)
    manga_list_col.create_index([("user_id", 1), ("mal_id", 1)], unique=True)
    security_questions_col.create_index([("user_id", 1)], unique=True)
    avatar_cache_col.create_index("char_id", unique=True)
