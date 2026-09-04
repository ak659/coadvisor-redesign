"""
app.py
------------------------------------------------------------------
FastAPI backend for Course Compass.

Serves:
  - The frontend HTML (course_advisor_ai_webmcp.html)
  - REST endpoints backing the six WebMCP tools, persisted to
    MongoDB (see db.py)

Run with:
    uvicorn app:app --reload --port 8000

Then open:
    http://localhost:8000/
------------------------------------------------------------------
"""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db

app = FastAPI(title="CoAdvisor.AI API")

app.mount("/static", StaticFiles(directory=Path(__file__).parent), name="static")

# Allow the frontend (served from the same origin, but kept open
# during dev in case you run the HTML via Live Server on a
# different port while pointing it at this API).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_db = None

@app.on_event("startup")
def startup():
    global _db
    _db = db.init_db()


# ------------------------------------------------------------------
# Request/response schemas (Pydantic — same pattern discussed earlier
# for structured agent I/O; here it validates the HTTP request bodies)
# ------------------------------------------------------------------

class AddPlanRequest(BaseModel):
    user_id: str
    course_id: str
    actor: str = "human"  # "human" | "agent"

class RemovePlanRequest(BaseModel):
    user_id: str
    course_id: str
    actor: str = "human"

class AdvisorNoteRequest(BaseModel):
    user_id: str
    course_id: str
    text: str


# ------------------------------------------------------------------
# Plan endpoints
# ------------------------------------------------------------------

@app.get("/api/plan/{user_id}")
def get_plan(user_id: str):
    return {"plan": db.get_plan(_db, user_id)}


@app.post("/api/plan/add")
def add_to_plan(req: AddPlanRequest):
    result = db.add_plan_item(_db, req.user_id, req.course_id, req.actor)
    if result["ok"]:
        db.log_event(_db, req.user_id, req.actor, "add_to_plan", f"Added {req.course_id}")
    return result


@app.post("/api/plan/remove")
def remove_from_plan(req: RemovePlanRequest):
    result = db.remove_plan_item(_db, req.user_id, req.course_id)
    if result["ok"]:
        db.log_event(_db, req.user_id, req.actor, "remove_from_plan", f"Removed {req.course_id}")
    return result


# ------------------------------------------------------------------
# Session log endpoint
# ------------------------------------------------------------------

@app.get("/api/log/{user_id}")
def get_log(user_id: str):
    return {"log": db.get_log(_db, user_id)}


# ------------------------------------------------------------------
# Advisor notes endpoint
# ------------------------------------------------------------------

@app.post("/api/notes/add")
def add_advisor_note(req: AdvisorNoteRequest):
    result = db.add_advisor_note(_db, req.user_id, req.course_id, req.text)
    db.log_event(_db, req.user_id, "agent", "ask_advisor_note", f"Note on {req.course_id}")
    return result


@app.get("/api/notes/{user_id}")
def get_notes(user_id: str):
    return {"notes": db.get_advisor_notes(_db, user_id)}


# ------------------------------------------------------------------
# Session log — direct write endpoint (client-side logEvent() calls this)
# ------------------------------------------------------------------

class LogEventRequest(BaseModel):
    user_id: str
    actor: str
    action: str
    detail: str = ""

@app.post("/api/log/add")
def add_log_event(req: LogEventRequest):
    db.log_event(_db, req.user_id, req.actor, req.action, req.detail)
    return {"ok": True}


# ------------------------------------------------------------------
# Synthetic students — for the upperclassman check_feasibility flow.
# Seeded via generate_students.py, read-only from the API's perspective.
# ------------------------------------------------------------------

@app.get("/api/students")
def list_students():
    return {"students": db.get_all_students(_db)}


@app.get("/api/students/{student_id}")
def get_student(student_id: str):
    student = db.get_student(_db, student_id)
    if not student:
        raise HTTPException(status_code=404, detail=f"No student with id {student_id}")
    return student


# ------------------------------------------------------------------
# Serve the frontend
# ------------------------------------------------------------------

FRONTEND_PATH = Path(__file__).parent / "course_advisor_ai_webmcp.html"

@app.get("/")
def serve_frontend():
    if FRONTEND_PATH.exists():
        return FileResponse(FRONTEND_PATH)
    return {"detail": "Frontend HTML not found next to app.py — copy course_advisor_ai_webmcp.html here."}
