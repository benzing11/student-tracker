"""Seed a comprehensive test student for dev-skip. Idempotent — wipes and recreates only this student's data."""
from datetime import date, timedelta
from database import get_db_connection, create_database, COURSES
from werkzeug.security import generate_password_hash

TEST_EMAIL = "test.student@fxec.edu"
TEST_REG = "FXEC2099001"
TEST_NAME = "Test Student"
TEST_PW = "Student123"

create_database()
db = get_db_connection()
pw = generate_password_hash(TEST_PW)

# Upsert student
existing = db.execute("SELECT id FROM students WHERE email=?", (TEST_EMAIL,)).fetchone()
if existing:
    sid = existing["id"]
    db.execute("""UPDATE students SET register_no=?,name=?,course=?,program=?,department=?,year=?,batch=?,mobile=?,account_status='Active',password_hash=? WHERE id=?""",
               (TEST_REG, TEST_NAME, "Programming Python", "B.E.", "CSE", "III Year", "2023-2027", "9999999999", pw, sid))
else:
    cur = db.execute("""INSERT INTO students(email,password_hash,register_no,name,course,program,department,year,batch,mobile,account_status)
                        VALUES(?,?,?,?,?,?,?,?,?,?, 'Active')""",
                     (TEST_EMAIL, pw, TEST_REG, TEST_NAME, "Programming Python", "B.E.", "CSE", "III Year", "2023-2027", "9999999999"))
    sid = cur.lastrowid
db.commit()

today = date.today()
d1 = today - timedelta(days=1)
d2 = today - timedelta(days=2)

# --- Wipe old test student data from feature tables ---
for tbl, col in [("planners","student_id"),("ps_progress","student_id"),("progress_requests","student_id"),
                 ("support_requests","student_id"),("task_instances","student_id"),("task_submissions","student_id"),
                 ("activity_points","student_id"),("slot_bookings","student_id"),("slot_reports","student_id"),
                 ("planners","student_id")]:
    if col == "student_id":
        if tbl == "task_submissions":
            db.execute(f"DELETE FROM {tbl} WHERE instance_id IN (SELECT id FROM task_instances WHERE student_id=?)", (sid,))
        elif tbl == "slot_reports":
            db.execute(f"DELETE FROM {tbl} WHERE student_id=?", (sid,))
        else:
            db.execute(f"DELETE FROM {tbl} WHERE student_id=?", (sid,))
db.commit()

# --- Planners: 3 days, streak=3 ---
venues = ["in:Elite - Lab 1", "in:Elite - Lab 2", "in:Elite - Lab 3", "in:Test Other Lab", "out:Home", "in:Elite - Lab 1", "out:Coffee shop"]
tasks_today = ["Aptitude set 1", "Python L3 problems", "C revision set 2", "DS practice", "Comm reading", "Git branching", "Mock review"]
tasks_d1 = ["Aptitude set 0", "Python L2 problems", "C revision set 1", "DS basics", "Comm practice", "Git init", "Mock test"]
tasks_d2 = ["Aptitude extra", "Python L1 review", "C basics", "DS intro", "Comm intro", "Git clone", "Mock setup"]

for d, tasks, done_pattern in [
    (today.isoformat(), tasks_today, (1,1,0,0,0,0,0)),
    (d1.isoformat(), tasks_d1, (1,1,1,1,1,0,0)),
    (d2.isoformat(), tasks_d2, (1,1,1,1,1,1,1)),
]:
    db.execute("""INSERT OR REPLACE INTO planners(student_id,date,h1,h2,h3,h4,h5,h6,h7,h1d,h2d,h3d,h4d,h5d,h6d,h7d,v1,v2,v3,v4,v5,v6,v7)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
               (sid, d, *tasks, *done_pattern, *venues))

# Activity points for done hours
for d, done_pattern in [(today.isoformat(), (1,1,0,0,0,0,0)), (d1.isoformat(), (1,1,1,1,1,0,0)), (d2.isoformat(), (1,1,1,1,1,1,1))]:
    for h, done in enumerate(done_pattern, 1):
        if done:
            db.execute("INSERT OR IGNORE INTO activity_points(student_id,date,hour) VALUES(?,?,?)", (sid, d, h))
db.commit()

# --- PS Progress: mix of verified + pending ---
ps_data = [
    (today.isoformat(), "Aptitude", "L1", "Completed", 1, 85, "Verified"),
    (d1.isoformat(), "Aptitude", "L2", "Completed", 1, 78, "Verified"),
    (d2.isoformat(), "Aptitude", "L3", "Completed", 1, 72, "Verified"),
    (d1.isoformat(), "Programming Python", "L1", "Completed", 1, 90, "Verified"),
    (today.isoformat(), "Programming Python", "L2", "Completed", 1, 65, "Verified"),
    (d2.isoformat(), "C Programming", "L1", "Completed", 1, 88, "Verified"),
    (today.isoformat(), "Data Structure", "L1", "Completed", 1, 55, "Pending"),
    (d1.isoformat(), "Communication", "L1", "Not Completed", 1, 30, "Pending"),
]
for d, course, level, status, attempt, score, ver in ps_data:
    db.execute("""INSERT OR IGNORE INTO ps_progress(student_id,date,course,level,status,attempt_no,score,verification_status,source)
                  VALUES(?,?,?,?,?,?,?,?,?)""", (sid, d, course, level, status, attempt, score, ver, "Daily Report"))
db.commit()

# --- Progress requests: 1 approved, 1 pending ---
db.execute("""INSERT OR IGNORE INTO progress_requests(student_id,course,level,proof_note,proof_link,status)
              VALUES(?,?,'L1','Completed on PS portal','','Approved')""", (sid, "C Programming"))
db.execute("""INSERT OR IGNORE INTO progress_requests(student_id,course,level,proof_note,proof_link,status)
              VALUES(?,?,'L2','Completed Java L2','','Pending')""", (sid, "Java Programming"))
db.commit()

# --- Support: 1 open, 1 resolved ---
db.execute("""INSERT OR IGNORE INTO support_requests(student_id,category,message,status)
              VALUES(?,'Progress issue','Level 2 score not updating','Open')""", (sid,))
db.execute("""INSERT OR IGNORE INTO support_requests(student_id,category,message,status,admin_note)
              VALUES(?,'Forgot password','Need help resetting','Resolved','Password reset done')""", (sid,))
db.commit()

# --- Task instances + submissions ---
# Ensure task_instances exist for all assignments
all_tasks = db.execute("SELECT id FROM task_assignments").fetchall()
for t in all_tasks:
    db.execute("INSERT OR IGNORE INTO task_instances(task_id,student_id,status) VALUES(?,?,?)", (t["id"], sid, "assigned"))
db.commit()

# Approve one submission (gives task points)
inst = db.execute("SELECT id FROM task_instances WHERE student_id=? AND status='assigned' LIMIT 1", (sid,)).fetchone()
if inst:
    db.execute("INSERT OR IGNORE INTO task_submissions(instance_id,student_id,note,status) VALUES(?,?,?,'approved')",
               (inst["id"], sid, "Completed with screenshots"))
    db.execute("UPDATE task_instances SET status='approved' WHERE id=?", (inst["id"],))

# Leave one as assigned (for student to submit)
# Leave one as pending (for admin to verify)
db.commit()

# --- Slot bookings: 1 reported past, no open/unreported ---
yesterday = d1.isoformat()
cur = db.execute("INSERT INTO slot_bookings(student_id,slot_date,course,level) VALUES(?,?,?,?)",
                 (sid, yesterday, "Aptitude", "L1"))
bid = cur.lastrowid
db.execute("INSERT INTO slot_reports(booking_id,student_id,slot_date,status,score) VALUES(?,?,?, 'completed', 85)",
           (bid, sid, yesterday))
db.commit()

# --- Summary ---
counts = {}
for t in ["students","planners","ps_progress","progress_requests","support_requests",
          "task_instances","task_submissions","activity_points","slot_bookings","slot_reports"]:
    counts[t] = db.execute(f"SELECT COUNT(*) n FROM {t}").fetchone()["n"]

db.close()
print(f"Test student created: id={sid}, email={TEST_EMAIL}")
print(f"Login: {TEST_EMAIL} / {TEST_PW}")
for t, n in counts.items():
    print(f"  {t}: {n}")
print("Done.")
