"""
Student Attendance Web Application
-----------------------------------
A Flask web app where teachers can register/login and mark
students Present/Absent, then view attendance reports.

DATABASE:
- If a DATABASE_URL environment variable is set (e.g. a free Neon/Render
  Postgres connection string), that permanent database is used -- your
  logins and attendance data will NEVER be wiped on restart/redeploy.
- If DATABASE_URL is not set, it falls back to a local SQLite file
  (attendance.db) -- fine for running on your own computer, but NOT
  safe on Render's free tier (gets wiped on restart).

Run locally:
    pip install -r requirements.txt
    python app.py

Then open in your browser: http://127.0.0.1:5000
"""

import os
import sys
import csv
import io
from datetime import date, timedelta

from flask import Flask, render_template, request, redirect, url_for, session, flash, Response
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, OperationalError

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-key")
app.permanent_session_lifetime = timedelta(days=30)  # "Remember Me" duration

# ---------------------------------------------------------------------
# Database setup (works with SQLite locally, PostgreSQL when deployed)
# ---------------------------------------------------------------------
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///attendance.db")
# Render/Neon sometimes give "postgres://" -- SQLAlchemy needs "postgresql://"
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# --- Loud, unmissable startup log: check this in Render's "Logs" tab ---
if "DATABASE_URL" in os.environ:
    safe_url = DATABASE_URL.split("@")[-1]  # hide username/password, show only host/db
    print(f"[DB] DATABASE_URL is SET. Connecting to Postgres host: {safe_url}", flush=True)
else:
    print("[DB] WARNING: DATABASE_URL is NOT set in the environment.", flush=True)
    print("[DB] Falling back to local SQLite file (attendance.db).", flush=True)
    print("[DB] This means data will be LOST on Render restarts/redeploys!", flush=True)
    print("[DB] Fix: add DATABASE_URL in Render -> Environment tab with your Neon connection string.", flush=True)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
IS_POSTGRES = engine.dialect.name == "postgresql"


def init_db():
    id_type = "SERIAL PRIMARY KEY" if IS_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"
    try:
        with engine.begin() as conn:
            conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS teachers (
                    id {id_type},
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL
                )
            """))
            conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS students (
                    id {id_type},
                    roll_no TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    class_name TEXT
                )
            """))
            conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS attendance (
                    id {id_type},
                    student_id INTEGER NOT NULL REFERENCES students(id),
                    date TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('Present', 'Absent')),
                    marked_by TEXT,
                    UNIQUE(student_id, date)
                )
            """))
        print(f"[DB] SUCCESS: connected and tables ready ({'Postgres/Neon' if IS_POSTGRES else 'local SQLite'}).", flush=True)
    except OperationalError as e:
        print(f"[DB] FAILED TO CONNECT: {e}", flush=True)
        print("[DB] Check that DATABASE_URL in Render's Environment tab exactly matches "
              "the connection string copied from Neon (including password and ?sslmode=require).", flush=True)
        raise


def query(sql, params=None, fetch="all"):
    """Run a SQL statement. fetch: 'all' | 'one' | None (for writes)."""
    with engine.begin() as conn:
        result = conn.execute(text(sql), params or {})
        if fetch == "all":
            return result.mappings().all()
        if fetch == "one":
            return result.mappings().first()
        return None


def login_required(func):
    from functools import wraps

    @wraps(func)
    def wrapper(*args, **kwargs):
        if "teacher" not in session:
            flash("Please log in first.", "warning")
            return redirect(url_for("login"))
        return func(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------
@app.route("/")
def home():
    if "teacher" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        try:
            query(
                "INSERT INTO teachers (username, password_hash) VALUES (:u, :p)",
                {"u": username, "p": generate_password_hash(password)},
                fetch=None,
            )
            flash("Registration successful! You can now log in.", "success")
            return redirect(url_for("login"))
        except IntegrityError:
            flash("That username is already taken.", "danger")

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        remember = request.form.get("remember") == "on"

        teacher = query(
            "SELECT * FROM teachers WHERE username = :u", {"u": username}, fetch="one"
        )

        if teacher and check_password_hash(teacher["password_hash"], password):
            session.permanent = remember  # stay logged in for 30 days if checked
            session["teacher"] = username
            flash(f"Welcome back, {username}!", "success")
            return redirect(url_for("dashboard"))
        else:
            flash("Invalid username or password.", "danger")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("teacher", None)
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))


# ---------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------
@app.route("/dashboard")
@login_required
def dashboard():
    student_count = query("SELECT COUNT(*) AS c FROM students", fetch="one")["c"]
    today = str(date.today())
    today_marked = query(
        "SELECT COUNT(*) AS c FROM attendance WHERE date = :d", {"d": today}, fetch="one"
    )["c"]
    return render_template(
        "dashboard.html",
        teacher=session["teacher"],
        student_count=student_count,
        today=today,
        today_marked=today_marked,
    )


# ---------------------------------------------------------------------
# Student management
# ---------------------------------------------------------------------
@app.route("/students", methods=["GET", "POST"])
@login_required
def students():
    if request.method == "POST":
        roll_no = request.form["roll_no"].strip()
        name = request.form["name"].strip()
        class_name = request.form["class_name"].strip()
        try:
            query(
                "INSERT INTO students (roll_no, name, class_name) VALUES (:r, :n, :c)",
                {"r": roll_no, "n": name, "c": class_name},
                fetch=None,
            )
            flash(f"Student '{name}' added.", "success")
        except IntegrityError:
            flash("A student with that roll number already exists.", "danger")

    all_students = query(
        "SELECT * FROM students ORDER BY CAST(roll_no AS INTEGER), roll_no"
    )
    return render_template("students.html", students=all_students)


@app.route("/students/delete/<int:student_id>", methods=["POST"])
@login_required
def delete_student(student_id):
    student = query("SELECT * FROM students WHERE id = :id", {"id": student_id}, fetch="one")
    if student:
        query("DELETE FROM attendance WHERE student_id = :id", {"id": student_id}, fetch=None)
        query("DELETE FROM students WHERE id = :id", {"id": student_id}, fetch=None)
        flash(f"Student '{student['name']}' and their attendance records were deleted.", "info")
    else:
        flash("Student not found.", "danger")
    return redirect(url_for("students"))


@app.route("/students/edit/<int:student_id>", methods=["GET", "POST"])
@login_required
def edit_student(student_id):
    student = query("SELECT * FROM students WHERE id = :id", {"id": student_id}, fetch="one")
    if not student:
        flash("Student not found.", "danger")
        return redirect(url_for("students"))

    if request.method == "POST":
        roll_no = request.form["roll_no"].strip()
        name = request.form["name"].strip()
        class_name = request.form["class_name"].strip()
        try:
            query(
                "UPDATE students SET roll_no = :r, name = :n, class_name = :c WHERE id = :id",
                {"r": roll_no, "n": name, "c": class_name, "id": student_id},
                fetch=None,
            )
            flash(f"Student '{name}' updated.", "success")
            return redirect(url_for("students"))
        except IntegrityError:
            flash("Another student already has that roll number.", "danger")
            student = query("SELECT * FROM students WHERE id = :id", {"id": student_id}, fetch="one")

    return render_template("edit_student.html", student=student)


# ---------------------------------------------------------------------
# Attendance
# ---------------------------------------------------------------------
@app.route("/attendance/mark", methods=["GET", "POST"])
@login_required
def mark_attendance():
    all_students = query(
        "SELECT * FROM students ORDER BY CAST(roll_no AS INTEGER), roll_no"
    )
    selected_date = request.values.get("att_date") or str(date.today())

    if request.method == "POST":
        for student in all_students:
            status = request.form.get(f"status_{student['id']}")
            if status in ("Present", "Absent"):
                query("""
                    INSERT INTO attendance (student_id, date, status, marked_by)
                    VALUES (:sid, :d, :s, :m)
                    ON CONFLICT (student_id, date)
                    DO UPDATE SET status = excluded.status, marked_by = excluded.marked_by
                """, {"sid": student["id"], "d": selected_date, "s": status,
                      "m": session["teacher"]}, fetch=None)
        flash(f"Attendance for {selected_date} saved.", "success")

    existing = query(
        "SELECT student_id, status FROM attendance WHERE date = :d", {"d": selected_date}
    )
    existing_map = {row["student_id"]: row["status"] for row in existing}

    return render_template(
        "mark_attendance.html",
        students=all_students,
        selected_date=selected_date,
        existing_map=existing_map,
    )


@app.route("/attendance/by-date", methods=["GET", "POST"])
@login_required
def view_by_date():
    selected_date = request.values.get("att_date") or str(date.today())

    rows = query("""
        SELECT s.roll_no, s.name, a.status
        FROM attendance a
        JOIN students s ON s.id = a.student_id
        WHERE a.date = :d
        ORDER BY CAST(s.roll_no AS INTEGER), s.roll_no
    """, {"d": selected_date})

    present_count = sum(1 for r in rows if r["status"] == "Present")
    return render_template(
        "view_by_date.html",
        rows=rows,
        selected_date=selected_date,
        present_count=present_count,
        total=len(rows),
    )


@app.route("/attendance/by-student", methods=["GET", "POST"])
@login_required
def view_by_student():
    roll_no = request.values.get("roll_no", "").strip()
    rows = []
    student_name = None
    present_pct = None

    if roll_no:
        student = query(
            "SELECT * FROM students WHERE roll_no = :r", {"r": roll_no}, fetch="one"
        )
        if student:
            student_name = student["name"]
            rows = query(
                "SELECT date, status FROM attendance WHERE student_id = :sid ORDER BY date",
                {"sid": student["id"]},
            )
            if rows:
                present = sum(1 for r in rows if r["status"] == "Present")
                present_pct = round(present / len(rows) * 100, 1)
        else:
            flash("No student found with that roll number.", "danger")

    return render_template(
        "view_by_student.html",
        rows=rows,
        roll_no=roll_no,
        student_name=student_name,
        present_pct=present_pct,
    )


# ---------------------------------------------------------------------
# CSV Export
# ---------------------------------------------------------------------
@app.route("/export/csv")
@login_required
def export_csv():
    """Download all attendance records as a CSV file (opens in Excel/Sheets)."""
    rows = query("""
        SELECT s.roll_no, s.name, s.class_name, a.date, a.status, a.marked_by
        FROM attendance a
        JOIN students s ON s.id = a.student_id
        ORDER BY a.date, CAST(s.roll_no AS INTEGER), s.roll_no
    """)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Roll No", "Name", "Class", "Date", "Status", "Marked By"])
    for r in rows:
        writer.writerow([r["roll_no"], r["name"], r["class_name"] or "",
                          r["date"], r["status"], r["marked_by"] or ""])

    filename = f"attendance_export_{date.today()}.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/export/csv/date")
@login_required
def export_csv_by_date():
    """Download attendance for one specific date as a CSV file."""
    selected_date = request.args.get("att_date") or str(date.today())

    rows = query("""
        SELECT s.roll_no, s.name, s.class_name, a.date, a.status, a.marked_by
        FROM attendance a
        JOIN students s ON s.id = a.student_id
        WHERE a.date = :d
        ORDER BY CAST(s.roll_no AS INTEGER), s.roll_no
    """, {"d": selected_date})

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Roll No", "Name", "Class", "Date", "Status", "Marked By"])
    for r in rows:
        writer.writerow([r["roll_no"], r["name"], r["class_name"] or "",
                          r["date"], r["status"], r["marked_by"] or ""])

    filename = f"attendance_{selected_date}.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ---------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------
init_db()  # ensure tables exist whenever the app module is loaded (e.g. by gunicorn)

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0")
