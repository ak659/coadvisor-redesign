"""
db.py  (PostgreSQL version)
------------------------------------------------------------------
Production-oriented data layer: connection pooling (via SQLAlchemy's
engine), row-level locking for real concurrent writes, optimistic
concurrency control (a `version` column) so two simultaneous writers
(human + agent, or two agents) can't silently clobber each other,
and atomic multi-step writes wrapped in real transactions.

Same function names/signatures as the SQLite and MongoDB versions,
so app.py's calls are unchanged regardless of which db.py is active.

Environment variable expected:
    DATABASE_URL = "postgresql+psycopg2://user:pass@host:port/dbname"
------------------------------------------------------------------
"""

import os
import json
from datetime import datetime, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import QueuePool

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg2://postgres:postgres@localhost:5432/coadvisor"
)

# Connection pool sized for the ~10-15 concurrent agent scenario:
# pool_size = steady-state connections kept open; max_overflow = extra
# connections allowed to spike beyond that before requests start
# queuing. 15 + 10 comfortably covers 10-15 concurrent agents plus
# normal human traffic without exhausting Postgres's own connection
# limit (default 100).
engine = create_engine(
    DATABASE_URL,
    poolclass=QueuePool,
    pool_size=15,
    max_overflow=10,
    pool_pre_ping=True,   # detects and replaces dead connections automatically
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS plan_items (
    id          SERIAL PRIMARY KEY,
    user_id     TEXT NOT NULL,
    course_id   TEXT NOT NULL,
    added_by    TEXT NOT NULL CHECK (added_by IN ('human', 'agent')),
    added_at    TIMESTAMPTZ NOT NULL,
    version     INTEGER NOT NULL DEFAULT 1,
    UNIQUE (user_id, course_id)
);

CREATE TABLE IF NOT EXISTS session_log (
    id          SERIAL PRIMARY KEY,
    user_id     TEXT NOT NULL,
    actor       TEXT NOT NULL CHECK (actor IN ('human', 'agent')),
    action      TEXT NOT NULL,
    detail      TEXT,
    created_at  TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS advisor_notes (
    id          SERIAL PRIMARY KEY,
    user_id     TEXT NOT NULL,
    course_id   TEXT NOT NULL,
    note_text   TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL
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
    completed_courses   JSONB,
    grades              JSONB
);

CREATE INDEX IF NOT EXISTS idx_plan_items_user ON plan_items(user_id);
CREATE INDEX IF NOT EXISTS idx_session_log_user ON session_log(user_id);
CREATE INDEX IF NOT EXISTS idx_advisor_notes_user ON advisor_notes(user_id);
"""


def now_iso():
    return datetime.now(timezone.utc)


def get_client():
    """Interface parity with SQLite/Mongo versions -- returns the
    SQLAlchemy engine (already a pool, not a single connection)."""
    return engine


def get_db(client=None):
    return client or get_client()


def init_db(client=None):
    eng = get_db(client)
    with eng.begin() as conn:
        for statement in SCHEMA.strip().split(";"):
            statement = statement.strip()
            if statement:
                conn.execute(text(statement))
    return eng


# ------------------------------------------------------------------
# Plan items
# ------------------------------------------------------------------

def add_plan_item(db, user_id: str, course_id: str, added_by: str):
    """
    Wrapped in a transaction (via engine.begin()) so the insert either
    fully succeeds or fully rolls back -- no partial state on error.
    """
    try:
        with db.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO plan_items (user_id, course_id, added_by, added_at) "
                    "VALUES (:user_id, :course_id, :added_by, :added_at)"
                ),
                {"user_id": user_id, "course_id": course_id, "added_by": added_by, "added_at": now_iso()}
            )
        return {"ok": True}
    except IntegrityError:
        return {"ok": False, "error": f"{course_id} is already in the plan"}


def remove_plan_item(db, user_id: str, course_id: str):
    with db.begin() as conn:
        result = conn.execute(
            text("DELETE FROM plan_items WHERE user_id = :user_id AND course_id = :course_id"),
            {"user_id": user_id, "course_id": course_id}
        )
        deleted = result.rowcount
    if deleted == 0:
        return {"ok": False, "error": f"{course_id} was not in the plan"}
    return {"ok": True}


def get_plan(db, user_id: str):
    with db.connect() as conn:
        rows = conn.execute(
            text("SELECT user_id, course_id, added_by, added_at, version FROM plan_items WHERE user_id = :user_id"),
            {"user_id": user_id}
        ).mappings().all()
    return [dict(r) for r in rows]


def update_plan_item_optimistic(db, user_id: str, course_id: str, expected_version: int, **updates):
    """
    Optimistic concurrency example: only applies an update if the row's
    version still matches what the caller last read. If another writer
    (agent or human) changed the row in between, expected_version won't
    match anymore and this returns a conflict instead of silently
    overwriting the other writer's change.

    Not currently wired into any endpoint -- included as the pattern
    to use once plan_items gets fields worth concurrently editing
    beyond simple add/remove (e.g. a per-course status or note field).
    """
    if not updates:
        return {"ok": False, "error": "no fields to update"}

    set_clause = ", ".join(f"{k} = :{k}" for k in updates)
    with db.begin() as conn:
        result = conn.execute(
            text(
                f"UPDATE plan_items SET {set_clause}, version = version + 1 "
                "WHERE user_id = :user_id AND course_id = :course_id AND version = :expected_version"
            ),
            {**updates, "user_id": user_id, "course_id": course_id, "expected_version": expected_version}
        )
        if result.rowcount == 0:
            return {"ok": False, "error": "conflict: row was modified by another writer since last read"}
    return {"ok": True}


# ------------------------------------------------------------------
# Session log
# ------------------------------------------------------------------

def log_event(db, user_id: str, actor: str, action: str, detail: str = ""):
    with db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO session_log (user_id, actor, action, detail, created_at) "
                "VALUES (:user_id, :actor, :action, :detail, :created_at)"
            ),
            {"user_id": user_id, "actor": actor, "action": action, "detail": detail, "created_at": now_iso()}
        )


def get_log(db, user_id: str, limit: int = 100):
    with db.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT user_id, actor, action, detail, created_at FROM session_log "
                "WHERE user_id = :user_id ORDER BY created_at DESC LIMIT :limit"
            ),
            {"user_id": user_id, "limit": limit}
        ).mappings().all()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------
# Advisor notes
# ------------------------------------------------------------------

def add_advisor_note(db, user_id: str, course_id: str, note_text: str):
    with db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO advisor_notes (user_id, course_id, note_text, created_at) "
                "VALUES (:user_id, :course_id, :note_text, :created_at)"
            ),
            {"user_id": user_id, "course_id": course_id, "note_text": note_text, "created_at": now_iso()}
        )
    return {"ok": True}


def get_advisor_notes(db, user_id: str):
    with db.connect() as conn:
        rows = conn.execute(
            text("SELECT user_id, course_id, note_text, created_at FROM advisor_notes WHERE user_id = :user_id"),
            {"user_id": user_id}
        ).mappings().all()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------
# Synthetic students
# ------------------------------------------------------------------

def get_all_students(db):
    with db.connect() as conn:
        rows = conn.execute(text("SELECT * FROM students")).mappings().all()
    return [dict(r) for r in rows]  # JSONB columns already decode to Python objects automatically


def get_student(db, student_id: str):
    with db.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM students WHERE student_id = :student_id"),
            {"student_id": student_id}
        ).mappings().fetchone()
    return dict(row) if row else None


def insert_student(db, student: dict):
    with db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO students (student_id, name, email, degree_id, degree_name, "
                "track_id, track_name, year_standing, persona, completed_courses, grades) "
                "VALUES (:student_id, :name, :email, :degree_id, :degree_name, "
                ":track_id, :track_name, :year_standing, :persona, :completed_courses, :grades)"
            ),
            {
                **student,
                "completed_courses": json.dumps(student["completed_courses"]),
                "grades": json.dumps(student["grades"]),
            }
        )


def clear_students(db):
    with db.begin() as conn:
        conn.execute(text("DELETE FROM students"))
