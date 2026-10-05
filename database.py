import os
import re
import sqlite3
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

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

import socket
from urllib.parse import urlparse

# Try to enable Postgres if env looks valid and connection succeeds quickly.
_use_pg = False
_pg_pool = None

def _try_init_pg():
    global _use_pg, _pg_pool
    if os.environ.get("USE_SQLITE", "").strip() == "1":
        return False
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url or "postgres" not in url:
        return False
    try:
        parsed = urlparse(url)
        host = parsed.hostname
        port = parsed.port or 5432
        if host:
            # Fast socket probe (0.5s timeout) to prevent 5s hangs if DB host is offline or invalid
            s = socket.create_connection((host, port), timeout=0.5)
            s.close()
    except Exception:
        # Socket check failed; quickly fall back without triggering pool noise
        return False

    try:
        import psycopg
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool
        pool = ConnectionPool(
            url,
            min_size=1,
            max_size=8,
            timeout=3,
            kwargs={"row_factory": dict_row, "autocommit": True},
            open=False,
        )
        pool.open(wait=True, timeout=3)
        _pg_pool = pool
        _use_pg = True
        print("Database backend: PostgreSQL (Supabase)")
        return True
    except Exception as e:
        print(f"Postgres unavailable ({e}), falling back to SQLite")
        _use_pg = False
        _pg_pool = None
        return False

_try_init_pg()
if not _use_pg:
    print("Database backend: SQLite ->", DATABASE)

# SQLite helpers
_pg_to_sqlite_replacements = [
    (re.compile(r"to_char\(now\(\) AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS'\)", re.IGNORECASE), "strftime('%Y-%m-%d %H:%M:%S','now')"),
    (re.compile(r"\bILIKE\b", re.IGNORECASE), "LIKE"),
]

def _translate_pg_to_sqlite(sql: str) -> str:
    if not sql:
        return sql
    for pat, repl in _pg_to_sqlite_replacements:
        sql = pat.sub(repl, sql)
    # BIGSERIAL -> INTEGER PRIMARY KEY AUTOINCREMENT handling is done in create, not runtime
    # FILTER translation for simple COUNT(*) FILTER (WHERE cond)
    # Replace: COUNT(*) FILTER (WHERE X) -> SUM(CASE WHEN X THEN 1 ELSE 0 END)
    # and COUNT(col FILTER ...) etc. Patcher for app.py handles main cases, but also translate here.
    # Use regex for COUNT(...) FILTER
    def _filter_repl(m):
        agg = m.group(1)  # e.g. COUNT(*), COUNT(ts.id), SUM(a.points), COUNT(*)
        cond = m.group(2)
        # COUNT(*) FILTER => SUM(CASE WHEN cond THEN 1 ELSE 0 END)
        if agg.strip().upper().startswith("COUNT"):
            return f"SUM(CASE WHEN {cond} THEN 1 ELSE 0 END)"
        elif agg.strip().upper().startswith("SUM"):
            # SUM(a.points) FILTER (WHERE cond) -> SUM(CASE WHEN cond THEN a.points ELSE 0 END)
            # extract inside SUM(...)
            inner = re.match(r"SUM\s*\(\s*(.+)\s*\)", agg, re.IGNORECASE)
            if inner:
                col = inner.group(1)
                return f"SUM(CASE WHEN {cond} THEN {col} ELSE 0 END)"
        return m.group(0)
    sql = re.sub(r"(COUNT\s*\(\s*(?:\*|[^)]+)\s*|SUM\s*\(\s*[^)]+\s*\))\s*FILTER\s*\(\s*WHERE\s+([^)]+)\s*\)", _filter_repl, sql, flags=re.IGNORECASE)
    # COALESCE with remaining FILTER handled above, but also: COALESCE(SUM(...) FILTER...,0) already handled
    return sql

class SQLiteConnection:
    def __init__(self, raw):
        self._raw = raw
        self._closed = False

    def execute(self, query, params=None, *args, **kwargs):
        if query:
            # translate PG fragments if present (allows shared app.py with PG syntax)
            query = _translate_pg_to_sqlite(query)
            # Handle RETURNING id -> strip and use lastrowid via separate logic in app.py fallback,
            # but for generic case, strip RETURNING
            if "RETURNING" in query.upper():
                # Remove RETURNING clause for sqlite execution
                query = re.sub(r"\s+RETURNING\s+\S+", "", query, flags=re.IGNORECASE)
            # ANY translation fallback: if query still contains ANY(%s::bigint[]) style, expand
            if "ANY(" in query:
                # This case should be patched in app.py, but provide fallback:
                # Replace student_id=ANY(%s::bigint[]) with student_id IN (SELECT value FROM json_each(?))
                # but easier: if params is ([ids],) we can expand
                if params and len(params) == 1 and isinstance(params[0], (list, tuple)):
                    ids = params[0]
                    if not ids:
                        # empty list => force no rows: replace with IN (NULL) or = -1
                        query = query.replace("student_id=ANY(%s::bigint[])", "1=0")
                        query = query.replace("student_id=ANY(%s::bigint[])", "1=0")
                        query = re.sub(r"student_id\s*=\s*ANY\s*\(\s*%s::bigint\[\]\s*\)", "1=0", query, flags=re.IGNORECASE)
                        query = re.sub(r"student_id\s*=\s*ANY\s*\(\s*\?\s*::bigint\[\]\s*\)", "1=0", query, flags=re.IGNORECASE)
                        params = ()
                    else:
                        placeholders = ",".join(["?"] * len(ids))
                        query = re.sub(r"student_id\s*=\s*ANY\s*\(\s*(?:%s::bigint\[\]|%s|\?::bigint\[\]|\?)\s*\)", f"student_id IN ({placeholders})", query, flags=re.IGNORECASE)
                        params = tuple(ids)
                else:
                    # generic fallback: replace ANY with IN
                    query = re.sub(r"=\s*ANY\s*\(", "IN (", query, flags=re.IGNORECASE)
                    query = query.replace("::bigint[]", "")
        if params is None:
            params = ()
        # sqlite expects tuple/list
        return self._raw.execute(query, params, *args, **kwargs)

    def executemany(self, query, seq_of_params):
        query = _translate_pg_to_sqlite(query)
        return self._raw.executemany(query, seq_of_params)

    def executescript(self, script):
        script = _translate_pg_to_sqlite(script)
        return self._raw.executescript(script)

    def commit(self):
        return self._raw.commit()

    def close(self):
        if not self._closed:
            self._closed = True
            self._raw.close()

    def __getattr__(self, name):
        return getattr(self._raw, name)


class PGConnection:
    """psycopg connection wrapper that accepts SQLite-style '?' placeholders and converts them to '%s'."""
    def __init__(self, raw):
        self._raw = raw
        self._closed = False

    def execute(self, query, params=None, *args, **kwargs):
        if query and "?" in query:
            query = query.replace("?", "%s")
        return self._raw.execute(query, params, *args, **kwargs)

    def commit(self):
        self._raw.commit()

    def close(self):
        if not self._closed:
            self._closed = True
            try:
                _pg_pool.putconn(self._raw)
            except Exception:
                try:
                    self._raw.close()
                except Exception:
                    pass

    def __getattr__(self, name):
        return getattr(self._raw, name)


def get_db_connection():
    if _use_pg and _pg_pool is not None:
        try:
            return PGConnection(_pg_pool.getconn())
        except Exception as e:
            print(f"PG pool getconn failed ({e}), using SQLite")
    db = sqlite3.connect(DATABASE, timeout=30, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=30000")
    return SQLiteConnection(db)


def _close_pool():
    global _pg_pool
    if _pg_pool is not None:
        try:
            _pg_pool.close()
        except Exception:
            pass

import atexit
atexit.register(_close_pool)

TS = "strftime('%Y-%m-%d %H:%M:%S','now')"
TS_PG = "to_char(now() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS')"
TS_SQLITE = "CURRENT_TIMESTAMP"

def create_database():
    db = get_db_connection()
    # Use SQLite-compatible types; if PG, BIGSERIAL works, but for SQLite we use INTEGER PRIMARY KEY AUTOINCREMENT
    # Detect backend by checking if db is SQLiteConnection
    is_sqlite = isinstance(db, SQLiteConnection)
    id_type = "INTEGER PRIMARY KEY AUTOINCREMENT" if is_sqlite else "BIGSERIAL PRIMARY KEY"
    big_int = "INTEGER" if is_sqlite else "BIGINT"
    ts_default = TS_SQLITE if is_sqlite else TS_PG

    statements = [
        f"""CREATE TABLE IF NOT EXISTS students (
            id {id_type},
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            register_no TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            course TEXT NOT NULL,
            batch TEXT NOT NULL,
            mobile TEXT NOT NULL,
            program TEXT NOT NULL DEFAULT 'B.E.',
            department TEXT NOT NULL DEFAULT 'CSE',
            year TEXT NOT NULL DEFAULT 'I Year',
            account_status TEXT NOT NULL DEFAULT 'Active',
            created_at TEXT DEFAULT {ts_default},
            updated_at TEXT
        )""",
        f"""CREATE TABLE IF NOT EXISTS planners (
            id {id_type},
            student_id {big_int} NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            date TEXT NOT NULL,
            h1 TEXT, h2 TEXT, h3 TEXT, h4 TEXT, h5 TEXT, h6 TEXT, h7 TEXT,
            h1d INTEGER NOT NULL DEFAULT 0, h2d INTEGER NOT NULL DEFAULT 0,
            h3d INTEGER NOT NULL DEFAULT 0, h4d INTEGER NOT NULL DEFAULT 0,
            h5d INTEGER NOT NULL DEFAULT 0, h6d INTEGER NOT NULL DEFAULT 0,
            h7d INTEGER NOT NULL DEFAULT 0,
            v1 TEXT, v2 TEXT, v3 TEXT, v4 TEXT, v5 TEXT, v6 TEXT, v7 TEXT,
            created_at TEXT DEFAULT {ts_default},
            updated_at TEXT,
            UNIQUE(student_id, date)
        )""",
        f"""CREATE TABLE IF NOT EXISTS ps_progress (
            id {id_type},
            student_id {big_int} NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            date TEXT NOT NULL,
            course TEXT NOT NULL,
            level TEXT NOT NULL,
            status TEXT NOT NULL,
            attempt_no INTEGER NOT NULL,
            score INTEGER NOT NULL,
            verification_status TEXT NOT NULL DEFAULT 'Pending',
            source TEXT NOT NULL DEFAULT 'Daily Report',
            created_at TEXT DEFAULT {ts_default},
            updated_at TEXT
        )""",
        f"""CREATE TABLE IF NOT EXISTS progress_requests (
            id {id_type},
            student_id {big_int} NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            course TEXT NOT NULL,
            level TEXT NOT NULL,
            proof_note TEXT NOT NULL,
            proof_link TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'Pending',
            admin_note TEXT DEFAULT '',
            created_at TEXT DEFAULT {ts_default},
            updated_at TEXT
        )""",
        f"""CREATE TABLE IF NOT EXISTS support_requests (
            id {id_type},
            student_id {big_int} NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            category TEXT NOT NULL,
            message TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Open',
            admin_note TEXT DEFAULT '',
            created_at TEXT DEFAULT {ts_default},
            updated_at TEXT
        )""",
        f"""CREATE TABLE IF NOT EXISTS audit_logs (
            id {id_type},
            actor_role TEXT NOT NULL,
            actor_id {big_int},
            action TEXT NOT NULL,
            target_student_id {big_int} REFERENCES students(id) ON DELETE SET NULL,
            details TEXT DEFAULT '',
            created_at TEXT DEFAULT {ts_default}
        )""",
        f"""CREATE TABLE IF NOT EXISTS task_assignments (
            id {id_type},
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            points INTEGER NOT NULL DEFAULT 0,
            frequency TEXT NOT NULL DEFAULT 'daily',
            deadline TEXT NOT NULL,
            target_type TEXT NOT NULL DEFAULT 'all',
            target_student_id {big_int} REFERENCES students(id) ON DELETE SET NULL,
            created_by {big_int},
            created_at TEXT DEFAULT {ts_default}
        )""",
        f"""CREATE TABLE IF NOT EXISTS task_instances (
            id {id_type},
            task_id {big_int} NOT NULL REFERENCES task_assignments(id) ON DELETE CASCADE,
            student_id {big_int} NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            status TEXT NOT NULL DEFAULT 'assigned',
            created_at TEXT DEFAULT {ts_default},
            UNIQUE(task_id, student_id)
        )""",
        f"""CREATE TABLE IF NOT EXISTS task_submissions (
            id {id_type},
            instance_id {big_int} NOT NULL REFERENCES task_instances(id) ON DELETE CASCADE,
            student_id {big_int} NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            note TEXT DEFAULT '',
            file_path TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            admin_note TEXT DEFAULT '',
            submitted_at TEXT DEFAULT {ts_default},
            reviewed_at TEXT
        )""",
        f"""CREATE TABLE IF NOT EXISTS activity_points (
            id {id_type},
            student_id {big_int} NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            date TEXT NOT NULL,
            hour INTEGER NOT NULL,
            created_at TEXT DEFAULT {ts_default},
            UNIQUE(student_id, date, hour)
        )""",
        f"""CREATE TABLE IF NOT EXISTS slot_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            open_time TEXT NOT NULL DEFAULT '18:00'
        )""",
        f"""CREATE TABLE IF NOT EXISTS slot_days (
            date TEXT PRIMARY KEY,
            open_time TEXT NOT NULL DEFAULT '18:00',
            open INTEGER NOT NULL DEFAULT 1
        )""",
        f"""CREATE TABLE IF NOT EXISTS slot_bookings (
            id {id_type},
            student_id {big_int} NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            slot_date TEXT NOT NULL,
            course TEXT NOT NULL,
            level TEXT NOT NULL,
            created_at TEXT DEFAULT {ts_default},
            UNIQUE(student_id, slot_date)
        )""",
        f"""CREATE TABLE IF NOT EXISTS slot_reports (
            id {id_type},
            booking_id {big_int} NOT NULL UNIQUE REFERENCES slot_bookings(id) ON DELETE CASCADE,
            student_id {big_int} NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            slot_date TEXT NOT NULL,
            status TEXT NOT NULL,
            score INTEGER NOT NULL DEFAULT 0,
            submitted_at TEXT DEFAULT {ts_default}
        )""",
        f"""CREATE TABLE IF NOT EXISTS daily_activity (
            id {id_type},
            student_id {big_int} NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            date TEXT NOT NULL,
            planned_count INTEGER NOT NULL DEFAULT 0,
            completed_count INTEGER NOT NULL DEFAULT 0,
            deduction INTEGER NOT NULL DEFAULT 0,
            reason TEXT NOT NULL DEFAULT 'incomplete',
            created_at TEXT DEFAULT {ts_default},
            UNIQUE(student_id, date)
        )""",
        f"""CREATE TABLE IF NOT EXISTS leaderboard_snapshots (
            id {id_type},
            student_id {big_int} NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            levels INTEGER NOT NULL DEFAULT 0,
            progress REAL NOT NULL DEFAULT 0,
            streak INTEGER NOT NULL DEFAULT 0,
            week TEXT NOT NULL,
            created_at TEXT DEFAULT {ts_default},
            UNIQUE(student_id, week)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_daily_activity_date ON daily_activity(student_id,date)",
        "CREATE INDEX IF NOT EXISTS idx_slot_booking_date ON slot_bookings(slot_date)",
        "CREATE INDEX IF NOT EXISTS idx_slot_report_student ON slot_reports(student_id)",
        "CREATE INDEX IF NOT EXISTS idx_planner_student_date ON planners(student_id,date)",
        "CREATE INDEX IF NOT EXISTS idx_ps_student_date ON ps_progress(student_id,date)",
        "CREATE INDEX IF NOT EXISTS idx_ps_course_level ON ps_progress(student_id,course,level,status,verification_status)",
        "CREATE INDEX IF NOT EXISTS idx_requests_status ON progress_requests(status)",
        "CREATE INDEX IF NOT EXISTS idx_support_status ON support_requests(status)",
        "CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_task_inst_student ON task_instances(student_id,status)",
        "CREATE INDEX IF NOT EXISTS idx_task_inst_task ON task_instances(task_id)",
        "CREATE INDEX IF NOT EXISTS idx_task_sub_instance ON task_submissions(instance_id,status)",
        "CREATE INDEX IF NOT EXISTS idx_act_points_student ON activity_points(student_id)",
    ]
    for sql in statements:
        db.execute(sql)

    # Lightweight migrations for an older database created by an earlier build.
    if is_sqlite:
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
                try:
                    db.execute(sql)
                except Exception:
                    pass
        ps_cols = {r[1] for r in db.execute("PRAGMA table_info(ps_progress)").fetchall()}
        for col, sql in {
            "verification_status": "ALTER TABLE ps_progress ADD COLUMN verification_status TEXT NOT NULL DEFAULT 'Pending'",
            "source": "ALTER TABLE ps_progress ADD COLUMN source TEXT NOT NULL DEFAULT 'Daily Report'",
            "updated_at": "ALTER TABLE ps_progress ADD COLUMN updated_at TEXT",
        }.items():
            if col not in ps_cols:
                try:
                    db.execute(sql)
                except Exception:
                    pass
        for hour in range(1, 8):
            cols_p = {r[1] for r in db.execute("PRAGMA table_info(planners)").fetchall()}
            if f"h{hour}d" not in cols_p:
                try:
                    db.execute(f"ALTER TABLE planners ADD COLUMN h{hour}d INTEGER NOT NULL DEFAULT 0")
                except Exception:
                    pass
            if f"v{hour}" not in cols_p:
                try:
                    db.execute(f"ALTER TABLE planners ADD COLUMN v{hour} TEXT")
                except Exception:
                    pass
        lb_cols={r[1] for r in db.execute("PRAGMA table_info(leaderboard_snapshots)").fetchall()}
        if "rank" not in lb_cols:
            try:
                db.execute("ALTER TABLE leaderboard_snapshots ADD COLUMN rank INTEGER NOT NULL DEFAULT 0")
            except Exception:
                pass
    else:
        def has_column(table, col):
            return db.execute(
                "SELECT 1 FROM information_schema.columns WHERE table_name=%s AND column_name=%s",
                (table, col),
            ).fetchone() is not None
        student_cols = {
            "program": "ALTER TABLE students ADD COLUMN IF NOT EXISTS program TEXT NOT NULL DEFAULT 'B.E.'",
            "department": "ALTER TABLE students ADD COLUMN IF NOT EXISTS department TEXT NOT NULL DEFAULT 'CSE'",
            "year": "ALTER TABLE students ADD COLUMN IF NOT EXISTS year TEXT NOT NULL DEFAULT 'I Year'",
            "account_status": "ALTER TABLE students ADD COLUMN IF NOT EXISTS account_status TEXT NOT NULL DEFAULT 'Active'",
            "updated_at": "ALTER TABLE students ADD COLUMN IF NOT EXISTS updated_at TEXT",
        }
        for col, sql in student_cols.items():
            if not has_column("students", col):
                db.execute(sql)
        ps_cols = {
            "verification_status": "ALTER TABLE ps_progress ADD COLUMN IF NOT EXISTS verification_status TEXT NOT NULL DEFAULT 'Pending'",
            "source": "ALTER TABLE ps_progress ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'Daily Report'",
            "updated_at": "ALTER TABLE ps_progress ADD COLUMN IF NOT EXISTS updated_at TEXT",
        }
        for col, sql in ps_cols.items():
            if not has_column("ps_progress", col):
                db.execute(sql)
        for hour in range(1, 8):
            if not has_column("planners", f"h{hour}d"):
                db.execute(f"ALTER TABLE planners ADD COLUMN IF NOT EXISTS h{hour}d INTEGER NOT NULL DEFAULT 0")
            if not has_column("planners", f"v{hour}"):
                db.execute(f"ALTER TABLE planners ADD COLUMN IF NOT EXISTS v{hour} TEXT")
        if not has_column("leaderboard_snapshots", "rank"):
            db.execute("ALTER TABLE leaderboard_snapshots ADD COLUMN IF NOT EXISTS rank INTEGER NOT NULL DEFAULT 0")

    db.execute("INSERT INTO slot_settings(id, open_time) VALUES (1, '18:00') ON CONFLICT (id) DO NOTHING" if is_sqlite else "INSERT INTO slot_settings(id, open_time) VALUES (1, '18:00') ON CONFLICT (id) DO NOTHING")

    db.commit()
    db.close()
    print("Database ready.")
