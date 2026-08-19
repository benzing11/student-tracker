import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATABASE = str(BASE_DIR / "database.db")

COURSES = {
    "Aptitude": 9,
    "Basic mathematics - Algebra": 1,
    "C Programming": 6,
    "C Programming Learning": 4,
    "Communication": 2,
    "Computer Networking": 4,
    "Data Structure": 11,
    "Database Programming": 4,
    "Electrical Skills": 1,
    "Electronics skill": 2,
    "Group Discussion": 1,
    "HTML / CSS": 1,
    "Java Programming": 4,
    "Java Script": 1,
    "Linux": 1,
    "Mechanical Manufacturing": 2,
    "Programming C++": 3,
    "Programming Python": 4,
    "Version control - Git, Github": 1,
}
TOTAL_LEVELS = sum(COURSES.values())

PROGRAMS = ["B.E.", "B.Tech"]
DEPARTMENTS = ["CSE", "ECE", "EEE", "MECH", "CIVIL", "IT", "AIDS", "CSBS", "AIML"]
YEARS = ["I Year", "II Year", "III Year", "IV Year"]


def get_db_connection():
    db = sqlite3.connect(DATABASE, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=30000")
    return db


def create_database():
    db = get_db_connection()
    db.executescript("""
    CREATE TABLE IF NOT EXISTS students (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        register_no TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        course TEXT NOT NULL,
        batch TEXT NOT NULL,
        mobile TEXT NOT NULL,
        program TEXT NOT NULL DEFAULT 'B.E.',
        department TEXT NOT NULL DEFAULT 'CSE',
        year TEXT NOT NULL DEFAULT 'I Year',
        account_status TEXT NOT NULL DEFAULT 'Active',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS planners (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER NOT NULL,
        date TEXT NOT NULL,
        h1 TEXT, h2 TEXT, h3 TEXT, h4 TEXT, h5 TEXT, h6 TEXT, h7 TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(student_id,date),
        FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS ps_progress (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER NOT NULL,
        date TEXT NOT NULL,
        course TEXT NOT NULL,
        level TEXT NOT NULL,
        status TEXT NOT NULL,
        attempt_no INTEGER NOT NULL,
        score INTEGER NOT NULL,
        verification_status TEXT NOT NULL DEFAULT 'Pending',
        source TEXT NOT NULL DEFAULT 'Daily Report',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS progress_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER NOT NULL,
        course TEXT NOT NULL,
        level TEXT NOT NULL,
        proof_note TEXT NOT NULL,
        proof_link TEXT DEFAULT '',
        status TEXT NOT NULL DEFAULT 'Pending',
        admin_note TEXT DEFAULT '',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS support_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER NOT NULL,
        category TEXT NOT NULL,
        message TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'Open',
        admin_note TEXT DEFAULT '',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        actor_role TEXT NOT NULL,
        actor_id INTEGER,
        action TEXT NOT NULL,
        target_student_id INTEGER,
        details TEXT DEFAULT '',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(target_student_id) REFERENCES students(id) ON DELETE SET NULL
    );

    CREATE INDEX IF NOT EXISTS idx_planner_student_date ON planners(student_id,date);
    CREATE INDEX IF NOT EXISTS idx_ps_student_date ON ps_progress(student_id,date);
    CREATE INDEX IF NOT EXISTS idx_ps_course_level ON ps_progress(student_id,course,level,status,verification_status);
    CREATE INDEX IF NOT EXISTS idx_requests_status ON progress_requests(status);
    CREATE INDEX IF NOT EXISTS idx_support_status ON support_requests(status);
    CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at);
    """)

    # Lightweight migrations for an older database created by an earlier build.
    cols = {r[1] for r in db.execute("PRAGMA table_info(students)").fetchall()}
    migrations = {
        "program": "ALTER TABLE students ADD COLUMN program TEXT NOT NULL DEFAULT 'B.E.'",
        "department": "ALTER TABLE students ADD COLUMN department TEXT NOT NULL DEFAULT 'CSE'",
        "year": "ALTER TABLE students ADD COLUMN year TEXT NOT NULL DEFAULT 'I Year'",
        "account_status": "ALTER TABLE students ADD COLUMN account_status TEXT NOT NULL DEFAULT 'Active'",
        "updated_at": "ALTER TABLE students ADD COLUMN updated_at TEXT",
    }
    for col, sql in migrations.items():
        if col not in cols:
            db.execute(sql)

    ps_cols = {r[1] for r in db.execute("PRAGMA table_info(ps_progress)").fetchall()}
    for col, sql in {
        "verification_status": "ALTER TABLE ps_progress ADD COLUMN verification_status TEXT NOT NULL DEFAULT 'Pending'",
        "source": "ALTER TABLE ps_progress ADD COLUMN source TEXT NOT NULL DEFAULT 'Daily Report'",
        "updated_at": "ALTER TABLE ps_progress ADD COLUMN updated_at TEXT",
    }.items():
        if col not in ps_cols:
            db.execute(sql)

    db.commit()
    db.close()
    print("Database ready.")
