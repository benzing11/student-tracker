"""Seed sample demo data (idempotent, keeps existing rows)."""
from datetime import date, timedelta
from database import get_db_connection, create_database, COURSES
from werkzeug.security import generate_password_hash

create_database()
db = get_db_connection()
pw = generate_password_hash("Student123")

students = [
    ("aarav.sharma@fxec.edu", "FXEC2023001", "Aarav Sharma", "B.E.", "CSE", "III Year", "2023-2027", "9876500001"),
    ("priya.venkat@fxec.edu", "FXEC2023002", "Priya Venkat", "B.E.", "CSE", "III Year", "2023-2027", "9876500002"),
    ("karthik.raja@fxec.edu", "FXEC2023003", "Karthik Raja", "B.Tech", "IT", "II Year", "2024-2028", "9876500003"),
    ("divya.sri@fxec.edu", "FXEC2023004", "Divya Sri", "B.E.", "ECE", "III Year", "2023-2027", "9876500004"),
    ("mohamed.ali@fxec.edu", "FXEC2023005", "Mohamed Ali", "B.E.", "EEE", "II Year", "2024-2028", "9876500005"),
    ("sneha.reddy@fxec.edu", "FXEC2023006", "Sneha Reddy", "B.Tech", "AIDS", "II Year", "2024-2028", "9876500006"),
    ("arun.kumar@fxec.edu", "FXEC2023007", "Arun Kumar", "B.E.", "MECH", "IV Year", "2022-2026", "9876500007"),
    ("lakshmi.n@fxec.edu", "FXEC2023008", "Lakshmi Narayani", "B.E.", "CSBS", "II Year", "2024-2028", "9876500008"),
    ("vikram.s@fxec.edu", "FXEC2023009", "Vikram Selvam", "B.Tech", "AIML", "I Year", "2025-2029", "9876500009"),
    ("anitha.m@fxec.edu", "FXEC2023010", "Anitha Murugan", "B.E.", "CIVIL", "III Year", "2023-2027", "9876500010"),
    ("rahul.d@fxec.edu", "FXEC2023011", "Rahul Dravid", "B.E.", "CSE", "I Year", "2025-2029", "9876500011"),
    ("keertana.p@fxec.edu", "FXEC2023012", "Keertana Priya", "B.Tech", "IT", "III Year", "2023-2027", "9876500012"),
]

ids = []
for email, reg, name, prog, dept, year, batch, mobile in students:
    row = db.execute("SELECT id FROM students WHERE email=? OR register_no=?", (email, reg)).fetchone()
    if row:
        ids.append(row["id"])
        continue
    cur = db.execute(
        "INSERT INTO students(email,password_hash,register_no,name,course,program,department,year,batch,mobile,account_status) VALUES(?,?,?,?,?,?,?,?,?,?, 'Active')",
        (email, pw, reg, name, "Programming Python", prog, dept, year, batch, mobile),
    )
    ids.append(cur.lastrowid)
db.commit()
print(f"students ready: {len(ids)}")

today = date.today()
yesterday = today - timedelta(days=1)
plan_tasks = ["Aptitude practice", "Python L{0} problems", "C Programming revision", "DS concepts", "Communication reading", "Git practice", "Mock test review"]
venues_pool = ["in:Elite - Lab 1", "in:Elite - Lab 2", "in:Elite - Lab 3", "in:Elite - Lab 1", "in:Elite - Lab 2", "out:Coffee shop", "out:Home"]

for i, sid in enumerate(ids):
    for d, done_pattern in [(yesterday.isoformat(), (1, 1, 1, 1, 1, 0, 0)), (today.isoformat(), (1, 1, 0, 0, 0, 0, 0))]:
        if db.execute("SELECT id FROM planners WHERE student_id=? AND date=?", (sid, d)).fetchone():
            continue
        tasks = [f"{t} - set {i+1}" if "{0}" not in t else t.format(i + 1) for t in plan_tasks]
        vlist = [venues_pool[(i + j) % len(venues_pool)] for j in range(7)]
        # vary: last student has no plan today (to demo -7 deduction path)
        if i == len(ids) - 1 and d == today.isoformat():
            continue
        db.execute(
            "INSERT INTO planners(student_id,date,h1,h2,h3,h4,h5,h6,h7,h1d,h2d,h3d,h4d,h5d,h6d,h7d,v1,v2,v3,v4,v5,v6,v7) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, d, *tasks, *done_pattern, *vlist),
        )
        for h, done in enumerate(done_pattern, 1):
            if done:
                db.execute(
                    "INSERT INTO planners_placeholder_check VALUES(0) " if False else
                    "INSERT INTO activity_points(student_id,date,hour) VALUES(?,?,?) ON CONFLICT(student_id,date,hour) DO NOTHING",
                    (sid, d, h),
                )
db.commit()

# PS progress: mix of verified + pending
ps_entries = [
    ("Aptitude", "L1", "Completed", "Verified", 85),
    ("Aptitude", "L2", "Completed", "Verified", 78),
    ("Aptitude", "L3", "Completed", "Pending", 72),
    ("Programming Python", "L1", "Completed", "Verified", 90),
    ("Programming Python", "L2", "Completed", "Pending", 65),
    ("C Programming", "L1", "Completed", "Verified", 88),
    ("Data Structure", "L1", "Not Completed", "Pending", 30),
]
for i, sid in enumerate(ids):
    n = 5 if i < 4 else (3 if i < 8 else 2)  # top students have more
    for j in range(n):
        course, level, status, ver, score = ps_entries[(i + j) % len(ps_entries)]
        d = (today - timedelta(days=j)).isoformat()
        exists = db.execute(
            "SELECT id FROM ps_progress WHERE student_id=? AND date=? AND course=? AND level=?",
            (sid, d, course, level),
        ).fetchone()
        if exists:
            continue
        db.execute(
            "INSERT INTO ps_progress(student_id,date,course,level,status,attempt_no,score,verification_status,source) VALUES(?,?,?,?,?,?,?,?,'Daily Report')",
            (sid, d, course, level, status, 1, score, ver),
        )
db.commit()

# Progress requests
for i, sid in enumerate(ids[:8]):
    status = "Approved" if i < 4 else "Pending"
    if db.execute("SELECT id FROM progress_requests WHERE student_id=? AND course='Aptitude' AND level='L1'", (sid,)).fetchone():
        continue
    db.execute(
        "INSERT INTO progress_requests(student_id,course,level,proof_note,proof_link,status) VALUES(?,?,?,'Completed on PS portal, screenshot attached','',?)",
        (sid, "Aptitude", f"L{(i % 3) + 1}", status),
    )
db.commit()

# Support tickets
tickets = [(ids[0], "Login", "Unable to reset my planner for today.", "Open"),
           (ids[2], "PS Portal", "Level 2 score not syncing.", "Open"),
           (ids[4], "General", "How to book tomorrow slot?", "Open")]
for sid, cat, msg, st in tickets:
    if db.execute("SELECT id FROM support_requests WHERE student_id=? AND message=?", (sid, msg)).fetchone():
        continue
    db.execute("INSERT INTO support_requests(student_id,category,message,status) VALUES(?,?,?,?)", (sid, cat, msg, st))
db.commit()

# Tasks
task_defs = [
    ("Daily Git commit streak", "Push at least 1 commit today with meaningful message.", 10, "daily"),
    ("Solve 5 Aptitude sets", "Complete 5 aptitude problem sets and note scores.", 20, "weekly"),
    ("Build portfolio page", "Create a personal portfolio with HTML/CSS and deploy link.", 50, "monthly"),
]
task_ids = []
for title, desc, pts, freq in task_defs:
    r = db.execute("SELECT id FROM task_assignments WHERE title=?", (title,)).fetchone()
    if r:
        task_ids.append(r["id"])
        continue
    cur = db.execute(
        "INSERT INTO task_assignments(title,description,points,frequency,deadline,target_type) VALUES(?,?,?, ?,?, 'all')",
        (title, desc, pts, freq, (today + timedelta(days=7)).isoformat()),
    )
    task_ids.append(cur.lastrowid)
db.commit()
for tid in task_ids:
    for sid in ids:
        db.execute("INSERT INTO task_instances(task_id,student_id) VALUES(?,?) ON CONFLICT(task_id,student_id) DO NOTHING", (tid, sid))
db.commit()
# a few submissions
inst_rows = db.execute("SELECT id, student_id FROM task_instances LIMIT 6").fetchall()
for k, r in enumerate(inst_rows):
    if db.execute("SELECT id FROM task_submissions WHERE instance_id=?", (r["id"],)).fetchone():
        continue
    st = "approved" if k < 2 else ("pending" if k < 5 else "rejected")
    db.execute("INSERT INTO task_submissions(instance_id,student_id,note,status) VALUES(?,?,?,?)",
               (r["id"], r["student_id"], f"Demo proof note {k+1}: completed task with screenshots.", st))
    if st != "pending":
        db.execute("UPDATE task_instances SET status=? WHERE id=?", (st, r["id"]))
db.commit()

# Slot bookings for yesterday/today
for i, sid in enumerate(ids[:6]):
    d = yesterday.isoformat()
    if not db.execute("SELECT id FROM slot_bookings WHERE student_id=? AND slot_date=?", (sid, d)).fetchone():
        cur = db.execute("INSERT INTO slot_bookings(student_id,slot_date,course,level) VALUES(?,?,?,?)",
                         (sid, d, "Aptitude", f"L{(i % 3) + 1}"))
        bid = cur.lastrowid
        db.execute("INSERT INTO slot_reports(booking_id,student_id,slot_date,status,score) VALUES(?,?,?, 'completed', ?)",
                   (bid, sid, d, 70 + i * 3))
db.commit()

for t in ["students", "planners", "ps_progress", "progress_requests", "support_requests", "task_assignments", "task_instances", "task_submissions", "activity_points", "slot_bookings", "slot_reports"]:
    print(t, db.execute(f"SELECT COUNT(*) n FROM {t}").fetchone()["n"])
db.close()
print("Seed complete.")
