"""
auth_db.py
----------
MongoDB-based user authentication and management.
Uses shared Atlas connection from mongo.py.
"""

import bcrypt
from datetime import datetime, timedelta
from bson.objectid import ObjectId
import secrets

from mongo import users_col as users_collection, reset_tokens_col as reset_tokens_collection, security_questions_col


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')


def verify_password(password: str, hashed: str) -> bool:
    """Verify a password against a hash."""
    return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))


def create_user(username: str, password: str, security_question: str = None, security_answer: str = None) -> dict:
    """Create a new user account."""
    if users_collection.find_one({"username": username}):
        raise ValueError("Username already taken")
    
    user = {
        "username": username,
        "password": hash_password(password),
        "avatar_url": None,
        "created_at": datetime.utcnow(),
        "is_active": True
    }
    result = users_collection.insert_one(user)
    user["_id"] = result.inserted_id

    # Store security question in separate collection
    if security_question and security_answer:
        security_questions_col.update_one(
            {"user_id": str(result.inserted_id)},
            {"$set": {
                "user_id": str(result.inserted_id),
                "username": username,
                "question": security_question,
                "answer": hash_password(security_answer),
                "updated_at": datetime.utcnow(),
            }},
            upsert=True,
        )

    return user


def get_user_by_username(username: str) -> dict:
    """Get user by username."""
    return users_collection.find_one({"username": username})


def get_user_by_id(user_id: str) -> dict:
    """Get user by ID."""
    try:
        return users_collection.find_one({"_id": ObjectId(user_id)})
    except:
        return None


def authenticate_user(username: str, password: str) -> dict:
    """Authenticate a user by username and password."""
    user = get_user_by_username(username)
    if user and verify_password(password, user["password"]):
        return user
    return None


def verify_security_answer(username: str, answer: str) -> bool:
    """Verify a user's security question answer from the security_questions collection."""
    user = get_user_by_username(username)
    if not user:
        return False
    sq = security_questions_col.find_one({"user_id": str(user["_id"])})
    if not sq or not sq.get("answer"):
        return False
    return verify_password(answer, sq["answer"])


def get_security_question(username: str) -> str:
    """Get the security question for a user."""
    user = get_user_by_username(username)
    if not user:
        return None
    sq = security_questions_col.find_one({"user_id": str(user["_id"])})
    return sq["question"] if sq else None


def update_security_question(user_id: str, question: str, answer: str):
    """Update or set a user's security question (legacy single-question)."""
    security_questions_col.update_one(
        {"user_id": user_id},
        {"$set": {
            "user_id": user_id,
            "question": question,
            "answer": hash_password(answer),
            "updated_at": datetime.utcnow(),
        }},
        upsert=True,
    )


def get_security_questions_list(user_id: str) -> list[dict]:
    """Return all security questions for a user (max 3).
    Each item: {index, question, updated_at}
    """
    doc = security_questions_col.find_one({"user_id": user_id})
    if not doc:
        return []
    # Support both legacy single-question and new multi-question format
    questions = doc.get("questions")
    if questions:
        return [{"index": i, "question": q["question"], "updated_at": q.get("updated_at")}
                for i, q in enumerate(questions)]
    # Legacy: single question field
    if doc.get("question"):
        return [{"index": 0, "question": doc["question"], "updated_at": doc.get("updated_at")}]
    return []


def set_security_questions(user_id: str, questions: list[dict]):
    """Replace all security questions for a user.
    questions: list of {question: str, answer: str} (max 3).
    """
    if len(questions) > 3:
        raise ValueError("Maximum 3 security questions allowed")
    entries = []
    for q in questions:
        entries.append({
            "question": q["question"],
            "answer": hash_password(q["answer"]),
            "updated_at": datetime.utcnow(),
        })
    security_questions_col.update_one(
        {"user_id": user_id},
        {"$set": {
            "user_id": user_id,
            "questions": entries,
            # Keep legacy field pointing to first question for password reset compat
            "question": entries[0]["question"] if entries else "",
            "answer": entries[0]["answer"] if entries else "",
            "updated_at": datetime.utcnow(),
        }},
        upsert=True,
    )


def add_security_question(user_id: str, question: str, answer: str):
    """Add a single security question (up to 3 max)."""
    existing = get_security_questions_list(user_id)
    if len(existing) >= 3:
        raise ValueError("Maximum 3 security questions allowed")
    doc = security_questions_col.find_one({"user_id": user_id})
    questions = doc.get("questions", []) if doc else []
    # Migrate legacy single-question if needed
    if not questions and doc and doc.get("question"):
        questions = [{"question": doc["question"], "answer": doc["answer"],
                       "updated_at": doc.get("updated_at", datetime.utcnow())}]
    questions.append({
        "question": question,
        "answer": hash_password(answer),
        "updated_at": datetime.utcnow(),
    })
    security_questions_col.update_one(
        {"user_id": user_id},
        {"$set": {
            "user_id": user_id,
            "questions": questions,
            "question": questions[0]["question"],
            "answer": questions[0]["answer"],
            "updated_at": datetime.utcnow(),
        }},
        upsert=True,
    )


def update_security_question_at(user_id: str, index: int, question: str, answer: str):
    """Update a specific security question by index."""
    doc = security_questions_col.find_one({"user_id": user_id})
    if not doc:
        raise ValueError("No security questions found")
    questions = doc.get("questions", [])
    if not questions and doc.get("question"):
        questions = [{"question": doc["question"], "answer": doc["answer"],
                       "updated_at": doc.get("updated_at", datetime.utcnow())}]
    if index < 0 or index >= len(questions):
        raise ValueError("Invalid question index")
    questions[index] = {
        "question": question,
        "answer": hash_password(answer),
        "updated_at": datetime.utcnow(),
    }
    security_questions_col.update_one(
        {"user_id": user_id},
        {"$set": {
            "questions": questions,
            "question": questions[0]["question"],
            "answer": questions[0]["answer"],
            "updated_at": datetime.utcnow(),
        }},
    )


def delete_security_question_at(user_id: str, index: int):
    """Delete a specific security question by index."""
    doc = security_questions_col.find_one({"user_id": user_id})
    if not doc:
        raise ValueError("No security questions found")
    questions = doc.get("questions", [])
    if not questions and doc.get("question"):
        questions = [{"question": doc["question"], "answer": doc["answer"],
                       "updated_at": doc.get("updated_at", datetime.utcnow())}]
    if index < 0 or index >= len(questions):
        raise ValueError("Invalid question index")
    questions.pop(index)
    update = {
        "questions": questions,
        "updated_at": datetime.utcnow(),
    }
    if questions:
        update["question"] = questions[0]["question"]
        update["answer"] = questions[0]["answer"]
    else:
        update["question"] = ""
        update["answer"] = ""
    security_questions_col.update_one(
        {"user_id": user_id},
        {"$set": update},
    )


def verify_user_password(user_id: str, password: str) -> bool:
    """Verify the current password for an authenticated user."""
    user = get_user_by_id(user_id)
    if not user:
        return False
    return verify_password(password, user["password"])


def change_password(user_id: str, new_password: str):
    """Change a user's password."""
    users_collection.update_one(
        {"_id": ObjectId(user_id)},
        {"$set": {"password": hash_password(new_password)}}
    )


def update_avatar(user_id: str, avatar_url: str):
    """Update a user's avatar."""
    users_collection.update_one(
        {"_id": ObjectId(user_id)},
        {"$set": {"avatar_url": avatar_url}}
    )


def get_user_profile(user_id: str) -> dict:
    """Get full user profile data."""
    user = get_user_by_id(user_id)
    if not user:
        return None
    sq = security_questions_col.find_one({"user_id": user_id})
    user["has_security_question"] = bool(sq and (sq.get("questions") or sq.get("question")))
    user["security_question"] = sq["question"] if sq and sq.get("question") else None
    user["security_answer_masked"] = "*****" if sq else None
    user["security_questions_list"] = get_security_questions_list(user_id)
    user["security_questions_count"] = len(user["security_questions_list"])
    return user


def wipe_user_data(user_id: str):
    """Delete all anime/manga list entries for a user (keep account)."""
    from mongo import anime_list_col, manga_list_col
    anime_list_col.delete_many({"user_id": user_id})
    manga_list_col.delete_many({"user_id": user_id})


def delete_user_account(user_id: str):
    """Permanently delete a user account and all associated data."""
    from mongo import anime_list_col, manga_list_col
    anime_list_col.delete_many({"user_id": user_id})
    manga_list_col.delete_many({"user_id": user_id})
    security_questions_col.delete_many({"user_id": user_id})
    reset_tokens_collection.delete_many({"user_id": ObjectId(user_id)})
    users_collection.delete_one({"_id": ObjectId(user_id)})


def create_reset_token(username: str) -> str:
    """Create a password reset token for a user."""
    user = get_user_by_username(username)
    if not user:
        raise ValueError("User not found")
    
    token = secrets.token_urlsafe(32)
    reset_tokens_collection.insert_one({
        "user_id": user["_id"],
        "token": token,
        "expires_at": datetime.utcnow() + timedelta(hours=1),
        "used": False
    })
    return token


def verify_reset_token(token: str) -> dict:
    """Verify a password reset token."""
    token_doc = reset_tokens_collection.find_one({
        "token": token,
        "used": False,
        "expires_at": {"$gt": datetime.utcnow()}
    })
    if token_doc:
        return get_user_by_id(str(token_doc["user_id"]))
    return None


def reset_password(token: str, new_password: str) -> bool:
    """Reset a user's password using a valid token."""
    user = verify_reset_token(token)
    if not user:
        return False
    
    # Update password
    users_collection.update_one(
        {"_id": user["_id"]},
        {"$set": {"password": hash_password(new_password)}}
    )
    
    # Mark token as used
    reset_tokens_collection.update_one(
        {"token": token},
        {"$set": {"used": True}}
    )
    return True


def update_user_email(user_id: str, new_email: str) -> bool:
    """Update user's email address."""
    try:
        result = users_collection.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": {"email": new_email.lower()}}
        )
        return result.modified_count > 0
    except:
        return False
