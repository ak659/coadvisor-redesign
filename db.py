"""
db.py  (SQLite version)
------------------------------------------------------------------
Drop-in replacement for the MongoDB-backed db.py. Same function
names and signatures, so app.py's calls (db.get_plan(_db, ...),
db.add_plan_item(_db, ...), etc.) work unchanged.

No internet/cloud dependency -- everything lives in a local file,
app_data.db, created automatically on first run.

catalog.js (the course/degree/track/prerequisite data) is untouched
by this file -- that's static, generated, client-side data, never
part of either database.
------------------------------------------------------------------
"""

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = os.environ.get(
    "SQLITE_DB_PATH",
    str(Path(__file__).parent / "app_data.db")
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS plan_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    course_id   TEXT NOT NULL,
    added_by    TEXT NOT NULL CHECK (added_by IN ('human', 'agent')),
    added_at    TEXT NOT NULL,
    UNIQUE (user_id, course_id)
);

CREATE TABLE IF NOT EXISTS session_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    actor       TEXT NOT NULL CHECK (actor IN ('human', 'agent')),
    action      TEXT NOT NULL,
    detail      TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS advisor_notes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    course_id   TEXT NOT NULL,
    note_text   TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS students (
    student_id          TEXT PRIMARY KEY,
    name                TEXT,
    email               TEXT,
    degree_id           TEXT,
    degree_name         TEXT,
    track_id            TEXT,
    track_name          TEXT,
    year_standing       INTEGER,
    persona             TEXT,
    completed_courses   TEXT,   -- JSON-encoded list
    grades              TEXT    -- JSON-encoded dict
);

CREATE INDEX IF NOT EXISTS idx_plan_items_user ON plan_items(user_id);
CREATE INDEX IF NOT EXISTS idx_session_log_user ON session_log(user_id);
CREATE INDEX IF NOT EXISTS idx_advisor_notes_user ON advisor_notes(user_id);
"""


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def get_client():
    """Kept for interface parity with the Mongo version -- returns a
    sqlite3 connection instead of a MongoClient."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def get_db(client=None):
    """Interface parity: in the Mongo version this selected a database
    from a client. Here, the connection itself IS the "db"."""
    return client or get_client()


def init_db(client=None):
    conn = get_db(client)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


# ------------------------------------------------------------------
# Plan items
# ------------------------------------------------------------------

def add_plan_item(db, user_id: str, course_id: str, added_by: str):
    try:
        db.execute(
            "INSERT INTO plan_items (user_id, course_id, added_by, added_at) VALUES (?, ?, ?, ?)",
            (user_id, course_id, added_by, now_iso())
        )
        db.commit()
        return {"ok": True}
    except sqlite3.IntegrityError:
        return {"ok": False, "error": f"{course_id} is already in the plan"}


def remove_plan_item(db, user_id: str, course_id: str):
    cur = db.execute(
        "DELETE FROM plan_items WHERE user_id = ? AND course_id = ?",
        (user_id, course_id)
    )
    db.commit()
    if cur.rowcount == 0:
        return {"ok": False, "error": f"{course_id} was not in the plan"}
    return {"ok": True}


def get_plan(db, user_id: str):
    rows = db.execute(
        "SELECT user_id, course_id, added_by, added_at FROM plan_items WHERE user_id = ?",
        (user_id,)
    ).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------
# Session log
# ------------------------------------------------------------------

def log_event(db, user_id: str, actor: str, action: str, detail: str = ""):
    db.execute(
        "INSERT INTO session_log (user_id, actor, action, detail, created_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, actor, action, detail, now_iso())
    )
    db.commit()


def get_log(db, user_id: str, limit: int = 100):
    rows = db.execute(
        "SELECT user_id, actor, action, detail, created_at FROM session_log "
        "WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
        (user_id, limit)
    ).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------
# Advisor notes
# ------------------------------------------------------------------

def add_advisor_note(db, user_id: str, course_id: str, note_text: str):
    db.execute(
        "INSERT INTO advisor_notes (user_id, course_id, note_text, created_at) VALUES (?, ?, ?, ?)",
        (user_id, course_id, note_text, now_iso())
    )
    db.commit()
    return {"ok": True}


def get_advisor_notes(db, user_id: str):
    rows = db.execute(
        "SELECT user_id, course_id, note_text, created_at FROM advisor_notes WHERE user_id = ?",
        (user_id,)
    ).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------
# Synthetic students
# ------------------------------------------------------------------

def get_all_students(db):
    import json
    rows = db.execute("SELECT * FROM students").fetchall()
    students = []
    for r in rows:
        s = dict(r)
        s["completed_courses"] = json.loads(s["completed_courses"] or "[]")
        s["grades"] = json.loads(s["grades"] or "{}")
        students.append(s)
    return students


def get_student(db, student_id: str):
    import json
    row = db.execute("SELECT * FROM students WHERE student_id = ?", (student_id,)).fetchone()
    if not row:
        return None
    s = dict(row)
    s["completed_courses"] = json.loads(s["completed_courses"] or "[]")
    s["grades"] = json.loads(s["grades"] or "{}")
    return s
