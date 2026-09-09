"""
generate_students.py
------------------------------------------------------------------
Generates synthetic upperclassman students with plausible academic
transcripts, respecting real prerequisite chains from the catalog
(no impossible transcripts -- a student never has a course without
having completed its prerequisites first).

Personas control how "far along" and how successful a student's
progression looks, so later clustering/feasibility-checking has
genuine sub-populations to find, not uniform noise:
  - fast_track:      ahead of pace, high grades, early track selection
  - at_risk:          behind pace, lower grades, incomplete progression
  - track_switcher:   partial progress in one track, then pivoted
  - average:          the bulk of the population, steady normal pace

Writes directly to MongoDB (students collection), reading the
connection string from the MONGODB_URI environment variable --
same pattern as db.py.

Run with:
    $env:MONGODB_URI = "mongodb+srv://..."   (PowerShell)
    python generate_students.py
------------------------------------------------------------------
"""

import os
import random
from pathlib import Path

import pandas as pd
from faker import Faker
from pymongo import MongoClient

fake = Faker()
random.seed(42)  # reproducible while iterating; remove/change seed for fresh runs

DATA_DIR = Path(__file__).parent / "student_data"
MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
DB_NAME = os.environ.get("MONGODB_DB_NAME", "course_compass")

N_STUDENTS = 40

PERSONA_WEIGHTS = {
    "fast_track": 0.15,
    "at_risk": 0.15,
    "track_switcher": 0.15,
    "average": 0.55,
}

PERSONA_GRADE_WEIGHTS = {
    "fast_track": [60, 30, 10, 0],
    "at_risk": [5, 20, 40, 35],
    "track_switcher": [25, 40, 25, 10],
    "average": [30, 40, 20, 10],
}

PERSONA_COMPLETION_RATE = {
    "fast_track": 0.95,
    "at_risk": 0.55,
    "track_switcher": 0.70,
    "average": 0.80,
}


def load_catalog():
    degrees = pd.read_csv(DATA_DIR / "degrees.csv")
    tracks = pd.read_csv(DATA_DIR / "tracks.csv")
    courses = pd.read_csv(DATA_DIR / "courses.csv")
    prereqs = pd.read_csv(DATA_DIR / "prerequisites.csv")
    return degrees, tracks, courses, prereqs


def build_path(courses_df, degree_id, track_id=None):
    deg_courses = courses_df[courses_df["degree_id"] == degree_id]
    core1 = deg_courses[deg_courses["level"] == "core_year1"]["course_id"].tolist()
    core2 = deg_courses[deg_courses["level"] == "core_year2"]["course_id"].tolist()
    path = core1 + core2
    if track_id:
        track_courses = deg_courses[deg_courses["track_id"] == track_id]["course_id"].tolist()
        path += track_courses
    return path


def weighted_grade(persona):
    return random.choices(["A", "B", "C", "D"], weights=PERSONA_GRADE_WEIGHTS[persona])[0]


def build_prereq_lookup(prereqs_df):
    lookup = {}
    for _, row in prereqs_df.iterrows():
        key = (row["degree_id"], row["course_id"])
        lookup.setdefault(key, []).append(row["prereq_course_id"])
    return lookup


def generate_one_student(sid, degrees_df, tracks_df, courses_df, prereq_lookup):
    persona = random.choices(
        list(PERSONA_WEIGHTS.keys()), weights=list(PERSONA_WEIGHTS.values())
    )[0]

    degree_row = degrees_df.sample(1).iloc[0]
    degree_id = degree_row["degree_id"]
    degree_name = degree_row["degree_name"]

    year_standing = random.choice([3, 4])

    degree_tracks = tracks_df[tracks_df["degree_id"] == degree_id]
    chosen_track_row = degree_tracks.sample(1).iloc[0]
    track_id = chosen_track_row["track_id"]
    track_name = chosen_track_row["track_name"]

    def prereqs_of(cid):
        return prereq_lookup.get((degree_id, cid), [])

    completed_courses = []
    grades = {}

    if persona == "track_switcher":
        other_rows = degree_tracks[degree_tracks["track_id"] != track_id]
        if len(other_rows):
            other_track_row = other_rows.sample(1).iloc[0]
            abandoned_path = build_path(courses_df, degree_id, other_track_row["track_id"])
            core_len = len(build_path(courses_df, degree_id, None))
            cutoff = core_len + random.randint(1, 2)
            for cid in abandoned_path[:cutoff]:
                reqs = prereqs_of(cid)
                if cid not in completed_courses and all(r in completed_courses for r in reqs):
                    completed_courses.append(cid)
                    grades[cid] = weighted_grade(persona)

    path = build_path(courses_df, degree_id, track_id)
    rate = PERSONA_COMPLETION_RATE[persona]

    for cid in path:
        if cid in completed_courses:
            continue
        reqs = prereqs_of(cid)
        if not all(r in completed_courses for r in reqs):
            continue
        if random.random() < rate:
            completed_courses.append(cid)
            grades[cid] = weighted_grade(persona)
        else:
            break

    return {
        "student_id": sid,
        "name": fake.name(),
        "email": fake.email(),
        "degree_id": degree_id,
        "degree_name": degree_name,
        "track_id": track_id,
        "track_name": track_name,
        "year_standing": year_standing,
        "persona": persona,
        "completed_courses": completed_courses,
        "grades": grades,
    }


def validate(students, prereq_lookup):
    errors = []
    for s in students:
        completed_set = set(s["completed_courses"])
        for cid in s["completed_courses"]:
            reqs = prereq_lookup.get((s["degree_id"], cid), [])
            missing = [r for r in reqs if r not in completed_set]
            if missing:
                errors.append(f"{s['student_id']}: {cid} completed but missing prereqs {missing}")
    if errors:
        raise ValueError("Invalid synthetic transcripts generated:\n" + "\n".join(errors))


def main():
    degrees_df, tracks_df, courses_df, prereqs_df = load_catalog()
    prereq_lookup = build_prereq_lookup(prereqs_df)

    students = [
        generate_one_student(f"student_{i+1:03d}", degrees_df, tracks_df, courses_df, prereq_lookup)
        for i in range(N_STUDENTS)
    ]

    validate(students, prereq_lookup)

    persona_counts = {}
    for s in students:
        persona_counts[s["persona"]] = persona_counts.get(s["persona"], 0) + 1
    avg_completed = sum(len(s["completed_courses"]) for s in students) / len(students)

    print(f"Generated {len(students)} students.")
    print(f"Persona distribution: {persona_counts}")
    print(f"Average courses completed per student: {avg_completed:.1f}")
    print("All transcripts validated -- no student has a course without its prerequisites.")

    client = MongoClient(MONGODB_URI)
    db = client[DB_NAME]
    db.students.delete_many({})
    db.students.insert_many(students)
    db.students.create_index("student_id", unique=True)
    print(f"Inserted {len(students)} students into {DB_NAME}.students")
    client.close()


if __name__ == "__main__":
    main()
