"""
db.py
------------------------------------------------------------------
MongoDB connection and collection setup for the app's writable
data: plan items, session log, advisor notes.

catalog.db (SQLite) stays separate and read-only -- it has real
relational structure (FK joins across degrees/tracks/courses/
prerequisites) and doesn't benefit from being document-shaped.
This file only covers the app-state side.

------------------------------------------------------------------
SETUP -- what you need to do on your end:
------------------------------------------------------------------
1. Create a free MongoDB Atlas cluster: https://www.mongodb.com/cloud/atlas/register
2. Get your connection string (Atlas UI -> Connect -> Drivers)
   It looks like:
   mongodb+srv://<username>:<password>@<cluster>.mongodb.net/?retryWrites=true&w=majority
3. Set it as an environment variable rather than hardcoding it:
   PowerShell:  $env:MONGODB_URI = "mongodb+srv://..."
   Or put it in a .env file (see load_dotenv below) -- don't commit
   that file to git.
4. pip install pymongo python-dotenv
------------------------------------------------------------------
"""

import os
from datetime import datetime, timezone

from pymongo import MongoClient, ASCENDING
from pymongo.errors import DuplicateKeyError

# Uncomment once you have python-dotenv installed and a .env file:
# from dotenv import load_dotenv
# load_dotenv()

MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
DB_NAME = os.environ.get("MONGODB_DB_NAME", "course_compass")


def get_client(uri: str = MONGODB_URI) -> MongoClient:
    return MongoClient(uri)


def get_db(client: MongoClient = None):
    client = client or get_client()
    return client[DB_NAME]


def ensure_indexes(db):
    """
    Mirrors the constraints/indexes from the SQLite schema:
      - plan_items: unique per (user_id, course_id), indexed by user_id
      - session_log: indexed by user_id
      - advisor_notes: indexed by user_id
    """
    db.plan_items.create_index(
        [("user_id", ASCENDING), ("course_id", ASCENDING)], unique=True
    )
    db.session_log.create_index([("user_id", ASCENDING)])
    db.advisor_notes.create_index([("user_id", ASCENDING)])


def init_db(client: MongoClient = None):
    db = get_db(client)
    ensure_indexes(db)
    return db


# ------------------------------------------------------------------
# Data access functions -- same operations as the SQLite version,
# same shape of inputs/outputs, so the FastAPI layer above them
# doesn't need to know which database is underneath.
# ------------------------------------------------------------------

def now_iso():
    return datetime.now(timezone.utc).isoformat()


def add_plan_item(db, user_id: str, course_id: str, added_by: str):
    try:
        db.plan_items.insert_one({
            "user_id": user_id,
            "course_id": course_id,
            "added_by": added_by,
            "added_at": now_iso(),
        })
        return {"ok": True}
    except DuplicateKeyError:
        return {"ok": False, "error": f"{course_id} is already in the plan"}


def remove_plan_item(db, user_id: str, course_id: str):
    result = db.plan_items.delete_one({"user_id": user_id, "course_id": course_id})
    if result.deleted_count == 0:
        return {"ok": False, "error": f"{course_id} was not in the plan"}
    return {"ok": True}


def get_plan(db, user_id: str):
    items = list(db.plan_items.find({"user_id": user_id}, {"_id": 0}))
    return items


def log_event(db, user_id: str, actor: str, action: str, detail: str = ""):
    db.session_log.insert_one({
        "user_id": user_id,
        "actor": actor,
        "action": action,
        "detail": detail,
        "created_at": now_iso(),
    })


def get_log(db, user_id: str, limit: int = 100):
    cursor = db.session_log.find({"user_id": user_id}, {"_id": 0}).sort("created_at", -1).limit(limit)
    return list(cursor)


def add_advisor_note(db, user_id: str, course_id: str, note_text: str):
    db.advisor_notes.insert_one({
        "user_id": user_id,
        "course_id": course_id,
        "note_text": note_text,
        "created_at": now_iso(),
    })
    return {"ok": True}


def get_advisor_notes(db, user_id: str):
    return list(db.advisor_notes.find({"user_id": user_id}, {"_id": 0}))


# ------------------------------------------------------------------
# Synthetic students (seeded by generate_students.py) -- used for
# the upperclassman check_feasibility flow.
# ------------------------------------------------------------------

def get_all_students(db):
    return list(db.students.find({}, {"_id": 0}))


def get_student(db, student_id: str):
    return db.students.find_one({"student_id": student_id}, {"_id": 0})
