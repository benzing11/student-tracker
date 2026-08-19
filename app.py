from flask import Flask, request, jsonify, session, render_template
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import date, datetime, timedelta
from pathlib import Path
import sqlite3
import re
import secrets
import os

from database import (
    get_db_connection, create_database, COURSES, TOTAL_LEVELS,
    PROGRAMS, DEPARTMENTS, YEARS
)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@elite.edu")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")

COURSE_ALIASES = {
    "aptitude":"Aptitude", "math":"Basic mathematics - Algebra", "mathematics":"Basic mathematics - Algebra",
    "basic mathematics":"Basic mathematics - Algebra", "algebra":"Basic mathematics - Algebra",
    "c":"C Programming", "c programming":"C Programming", "c learning":"C Programming Learning",
    "c programming learning":"C Programming Learning", "communication":"Communication",
    "network":"Computer Networking", "networking":"Computer Networking", "computer networking":"Computer Networking",
    "ds":"Data Structure", "data structure":"Data Structure", "data structures":"Data Structure",
    "db":"Database Programming", "database":"Database Programming", "database programming":"Database Programming",
    "electrical":"Electrical Skills", "electrical skills":"Electrical Skills", "electronics":"Electronics skill",
    "electronics skill":"Electronics skill", "gd":"Group Discussion", "group discussion":"Group Discussion",
    "html":"HTML / CSS", "css":"HTML / CSS", "html css":"HTML / CSS", "html / css":"HTML / CSS",
    "java":"Java Programming", "java programming":"Java Programming", "javascript":"Java Script",
    "java script":"Java Script", "js":"Java Script", "linux":"Linux", "mechanical":"Mechanical Manufacturing",
    "mechanical manufacturing":"Mechanical Manufacturing", "cpp":"Programming C++", "c++":"Programming C++",
    "c plus plus":"Programming C++", "programming c++":"Programming C++", "python":"Programming Python",
    "py":"Programming Python", "programming python":"Programming Python", "git":"Version control - Git, Github",
    "github":"Version control - Git, Github", "git github":"Version control - Git, Github",
    "version control":"Version control - Git, Github", "version control - git, github":"Version control - Git, Github",
}


def normalize_course(value):
    if not value: return None
    text = re.sub(r"\s+", " ", str(value).strip().lower())
    return COURSE_ALIASES.get(text)


def normalize_level(value):
    if value is None: return None
    m = re.search(r"(?:level|lev|l)?\s*[-_ ]?\s*(\d+)", str(value).strip().lower())
    return int(m.group(1)) if m else None


def normalize_status(value):
    text = str(value or "").strip().lower()
    if text in {"completed","complete","done","yes","true","1","success","✅"}: return "Completed"
    if text in {"not completed","not_completed","incomplete","pending","no","false","0","❌"}: return "Not Completed"
    return None


def row_dict(row): return dict(row) if row else None

def rows_dict(rows): return [dict(r) for r in rows]


def student_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if session.get("role") != "student" or not session.get("student_id"):
            return jsonify(message="Student login required."), 401
        db = get_db_connection(); s = db.execute("SELECT account_status FROM students WHERE id=?", (session["student_id"],)).fetchone(); db.close()
        if not s or s["account_status"] != "Active":
            session.clear(); return jsonify(message="Your account is inactive. Please contact Admin."), 403
        return fn(*args, **kwargs)
    return wrapper


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if session.get("role") != "admin": return jsonify(message="Admin login required."), 401
        return fn(*args, **kwargs)
    return wrapper


def audit(action, target_student_id=None, details=""):
    db = get_db_connection()
    db.execute("INSERT INTO audit_logs(actor_role,actor_id,action,target_student_id,details) VALUES(?,?,?,?,?)",
               (session.get("role","system"), session.get("student_id"), action, target_student_id, details[:1000]))
    db.commit(); db.close()


def valid_date(value):
    try: return datetime.strptime(str(value), "%Y-%m-%d").date()
    except Exception: return None


def verified_completed(student_id):
    db = get_db_connection()
    rows = db.execute("""SELECT DISTINCT course,level FROM ps_progress
                         WHERE student_id=? AND LOWER(status)='completed' AND verification_status='Verified'""", (student_id,)).fetchall()
    db.close(); done=set()
    for r in rows:
        c=normalize_course(r["course"]) or r["course"]; l=normalize_level(r["level"])
        if c in COURSES and l and 1 <= l <= COURSES[c]: done.add((c,l))
    # Approved legacy requests also count as verified levels.
    db = get_db_connection()
    reqs = db.execute("SELECT course,level FROM progress_requests WHERE student_id=? AND status='Approved'", (student_id,)).fetchall(); db.close()
    for r in reqs:
        c=normalize_course(r["course"]) or r["course"]; l=normalize_level(r["level"])
        if c in COURSES and l and 1 <= l <= COURSES[c]: done.add((c,l))
    return done


def calculate_matrix(student_id, verified_only=True):
    done = verified_completed(student_id) if verified_only else set()
    result=[]
    for course,total in COURSES.items():
        n=sum(1 for c,l in done if c==course)
        result.append({"course":course,"total_levels":total,"completed_levels":n,"progress":round(n/total*100)})
    return sorted(result,key=lambda x:(-x["progress"],-x["completed_levels"],x["course"].lower()))


def calculate_stats(student_id):
    done=verified_completed(student_id); db=get_db_connection()
    reported=db.execute("SELECT COUNT(*) n FROM ps_progress WHERE student_id=?",(student_id,)).fetchone()["n"]
    pending=db.execute("SELECT COUNT(*) n FROM ps_progress WHERE student_id=? AND verification_status='Pending'",(student_id,)).fetchone()["n"]
    planner_days=db.execute("SELECT COUNT(*) n FROM planners WHERE student_id=?",(student_id,)).fetchone()["n"]
    approved=db.execute("SELECT COUNT(*) n FROM progress_requests WHERE student_id=? AND status='Approved'",(student_id,)).fetchone()["n"]
    db.close()
    return {"completed_levels":len(done),"total_levels":TOTAL_LEVELS,"overall_progress":round(len(done)/TOTAL_LEVELS*100,1),
            "ps_submissions":reported,"pending_ps":pending,"approved_history_levels":approved,"planner_days":planner_days}


def calculate_streak(student_id):
    db=get_db_connection(); rows=db.execute("""SELECT DISTINCT date FROM ps_progress
        WHERE student_id=? AND LOWER(status)='completed' AND verification_status='Verified' ORDER BY date""",(student_id,)).fetchall(); db.close()
    ds=set()
    for r in rows:
        if valid_date(r["date"]): ds.add(valid_date(r["date"]))
    if not ds:return {"current":0,"best":0}
    current=0; d=date.today()
    while d in ds: current+=1; d-=timedelta(days=1)
    best=run=0; prev=None
    for d in sorted(ds):
        run=run+1 if prev and d==prev+timedelta(days=1) else 1; best=max(best,run); prev=d
    return {"current":current,"best":best}


def leaderboard_data():
    db=get_db_connection(); students=db.execute("SELECT id,name,department,year FROM students WHERE account_status='Active' ORDER BY name").fetchall(); db.close()
    out=[]
    for s in students:
        st=calculate_stats(s["id"]); streak=calculate_streak(s["id"])
        parts = s["name"].split()
        public_name = parts[0] if len(parts) == 1 else parts[0] + " " + parts[-1][0] + "."
        out.append({"student_id":s["id"],"name":public_name,"department":s["department"],"year":s["year"],
                    "progress":st["overall_progress"],"levels":st["completed_levels"],"streak":streak["current"]})
    out.sort(key=lambda x:(-x["progress"],-x["levels"],-x["streak"],x["name"].lower()))
    for i,r in enumerate(out,1): r["rank"]=i
    return out


@app.route("/")
def index(): return render_template("index.html")


@app.post("/api/register")
def register():
    d=request.get_json(silent=True) or {}
    email=str(d.get("email","")).strip().lower(); password=str(d.get("password","")); reg=re.sub(r"\s+","",str(d.get("register_no","")).strip())
    name=str(d.get("name","")).strip().upper(); course=str(d.get("course","")).strip(); batch=str(d.get("batch","")).strip(); mobile=re.sub(r"\s+","",str(d.get("mobile","")))
    program=str(d.get("program","")).strip(); department=str(d.get("department","")).strip(); year=str(d.get("year","")).strip()
    if not all([email,password,reg,name,course,batch,mobile,program,department,year]): return jsonify(message="Please fill all details."),400
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+",email): return jsonify(message="Enter a valid email address."),400
    if len(password)<8:return jsonify(message="Password must contain at least 8 characters."),400
    if not re.fullmatch(r"\d{7,15}",reg):return jsonify(message="Enter a valid register number."),400
    if not re.fullmatch(r"\d{10}",mobile):return jsonify(message="Enter a 10-digit mobile number."),400
    if program not in PROGRAMS or department not in DEPARTMENTS or year not in YEARS:return jsonify(message="Select valid academic details."),400
    db=get_db_connection()
    try:
        db.execute("""INSERT INTO students(email,password_hash,register_no,name,course,batch,mobile,program,department,year)
                      VALUES(?,?,?,?,?,?,?,?,?,?)""",(email,generate_password_hash(password),reg,name,course,batch,mobile,program,department,year));db.commit()
    except sqlite3.IntegrityError:
        db.close();return jsonify(message="Email or register number already exists."),409
    db.close();return jsonify(message="Account created successfully. Your profile is ready.")


@app.post("/api/login")
def login():
    d=request.get_json(silent=True) or {};email=str(d.get("email","")).strip().lower();password=str(d.get("password",""))
    db=get_db_connection();s=db.execute("SELECT * FROM students WHERE LOWER(email)=?",(email,)).fetchone();db.close()
    if not s or not check_password_hash(s["password_hash"],password): return jsonify(message="Invalid email or password."),401
    if s["account_status"]!="Active": return jsonify(message="Your account is inactive. Please contact Admin."),403
    session.clear();session["role"]="student";session["student_id"]=s["id"]
    return jsonify(message="Login successful.",student={k:s[k] for k in ["id","email","register_no","name","course","batch","mobile","program","department","year"]})


@app.post("/api/admin/login")
def admin_login():
    d=request.get_json(silent=True) or {}
    if str(d.get("email","")).strip().lower()==ADMIN_EMAIL and str(d.get("password",""))==ADMIN_PASSWORD:
        session.clear();session["role"]="admin";return jsonify(message="Admin login successful.")
    return jsonify(message="Invalid admin credentials."),401


@app.post("/api/logout")
def logout(): session.clear();return jsonify(message="Logged out.")


@app.get("/api/session")
def api_session():
    if session.get("role")=="student": return jsonify(logged_in=True,role="student",student=current_student())
    if session.get("role")=="admin": return jsonify(logged_in=True,role="admin")
    return jsonify(logged_in=False)


def current_student():
    sid=session.get("student_id");
    if not sid:return None
    db=get_db_connection();r=db.execute("SELECT id,email,register_no,name,course,batch,mobile,program,department,year,account_status,created_at FROM students WHERE id=?",(sid,)).fetchone();db.close();return row_dict(r)


@app.get("/api/profile")
@student_required
def profile():
    sid=session["student_id"];db=get_db_connection()
    plans=db.execute("SELECT date,h1,h2,h3,h4,h5,h6,h7 FROM planners WHERE student_id=? ORDER BY date DESC",(sid,)).fetchall()
    ps=db.execute("SELECT id,date,course,level,status,attempt_no,score,verification_status,source FROM ps_progress WHERE student_id=? ORDER BY date DESC,id DESC",(sid,)).fetchall()
    req=db.execute("SELECT id,course,level,proof_note,proof_link,status,admin_note,created_at FROM progress_requests WHERE student_id=? ORDER BY id DESC",(sid,)).fetchall();db.close()
    return jsonify(student=current_student(),planners=rows_dict(plans),ps=rows_dict(ps),requests=rows_dict(req),stats={**calculate_stats(sid),"streak":calculate_streak(sid)})


@app.post("/api/planner")
@student_required
def save_planner():
    d=request.get_json(silent=True) or {};vals=[str(d.get(f"h{i}",""))[:120].strip() for i in range(1,8)];dt=date.today().isoformat();db=get_db_connection()
    db.execute("""INSERT INTO planners(student_id,date,h1,h2,h3,h4,h5,h6,h7) VALUES(?,?,?,?,?,?,?,?,?)
                  ON CONFLICT(student_id,date) DO UPDATE SET h1=excluded.h1,h2=excluded.h2,h3=excluded.h3,h4=excluded.h4,h5=excluded.h5,h6=excluded.h6,h7=excluded.h7,updated_at=CURRENT_TIMESTAMP""",(session["student_id"],dt,*vals));db.commit();db.close();audit("Updated daily planner",session["student_id"],dt);return jsonify(message="Today's plan saved ✓")


@app.post("/api/ps")
@student_required
def save_ps():
    d=request.get_json(silent=True) or {};course=normalize_course(d.get("course"));level=normalize_level(d.get("level"));status=normalize_status(d.get("status"))
    try: attempt=int(d.get("attempt_no",0));score=int(d.get("score",-1))
    except: return jsonify(message="Attempt and score must be numbers."),400
    if not course:return jsonify(message="Select a valid FXEC course."),400
    if not level or level<1 or level>COURSES[course]:return jsonify(message=f"{course} has only levels 1 to {COURSES[course]}."),400
    if not status:return jsonify(message="Select a valid status."),400
    if attempt<1 or attempt>999:return jsonify(message="Attempt number must be 1 or more."),400
    if score<0 or score>100:return jsonify(message="Score must be between 0 and 100."),400
    dt=date.today().isoformat();db=get_db_connection()
    existing=db.execute("SELECT id,verification_status FROM ps_progress WHERE student_id=? AND date=? AND course=? AND level=?",(session["student_id"],dt,course,f"L{level}")).fetchone()
    if existing:
        # A student can update today's report, but cannot self-verify it.
        db.execute("UPDATE ps_progress SET status=?,attempt_no=?,score=?,verification_status='Pending',source='Daily Report',updated_at=CURRENT_TIMESTAMP WHERE id=?",(status,attempt,score,existing["id"])); action="updated"
    else:
        db.execute("INSERT INTO ps_progress(student_id,date,course,level,status,attempt_no,score,verification_status,source) VALUES(?,?,?,?,?,?,?,'Pending','Daily Report')",(session["student_id"],dt,course,f"L{level}",status,attempt,score));action="saved"
    db.commit();db.close();audit(f"Daily PS report {action}",session["student_id"],f"{course} L{level}");return jsonify(message=f"{course} L{level} {action}. Awaiting verification.")


@app.post("/api/progress-request")
@student_required
def progress_request():
    d=request.get_json(silent=True) or {};course=normalize_course(d.get("course"));level=normalize_level(d.get("level"));note=str(d.get("proof_note","")).strip()[:1000];link=str(d.get("proof_link","")).strip()[:500]
    if not course or not level or level<1 or level>COURSES[course]:return jsonify(message="Select a valid course and level."),400
    if len(note)<5:return jsonify(message="Describe what proves this level was completed."),400
    db=get_db_connection();exists=db.execute("SELECT id FROM progress_requests WHERE student_id=? AND course=? AND level=? AND status='Pending'",(session["student_id"],course,f"L{level}")).fetchone()
    if exists:db.close();return jsonify(message="That level already has a pending verification request."),409
    db.execute("INSERT INTO progress_requests(student_id,course,level,proof_note,proof_link) VALUES(?,?,?,?,?)",(session["student_id"],course,f"L{level}",note,link));db.commit();db.close();audit("Submitted previous PS progress for verification",session["student_id"],f"{course} L{level}");return jsonify(message="Verification request sent to Admin.")


@app.get("/api/dashboard")
@student_required
def dashboard():
    sid=session["student_id"];dt=date.today().isoformat();db=get_db_connection();planner=db.execute("SELECT * FROM planners WHERE student_id=? AND date=?",(sid,dt)).fetchone();ps=db.execute("SELECT id,date,course,level,status,attempt_no,score,verification_status,source FROM ps_progress WHERE student_id=? AND date=? ORDER BY id DESC",(sid,dt)).fetchall();db.close();streak=calculate_streak(sid)
    return jsonify(today={"planner":row_dict(planner),"ps_progress":rows_dict(ps)},stats={**calculate_stats(sid),"current_streak":streak["current"],"best_streak":streak["best"]})


@app.get("/api/course-matrix")
@student_required
def course_matrix(): return jsonify(courses=calculate_matrix(session["student_id"]))


@app.get("/api/leaderboard")
@student_required
def leaderboard(): return jsonify(leaderboard=leaderboard_data())


@app.post("/api/support")
@student_required
def create_support():
    d=request.get_json(silent=True) or {};allowed={"Forgot password","Change name","Profile correction","Progress issue","Planner issue","PS Portal issue","Other"};cat=str(d.get("category","")).strip();msg=str(d.get("message","")).strip()[:1000]
    if cat not in allowed or len(msg)<5:return jsonify(message="Select an issue type and describe the issue."),400
    db=get_db_connection();db.execute("INSERT INTO support_requests(student_id,category,message) VALUES(?,?,?)",(session["student_id"],cat,msg));db.commit();db.close();audit("Submitted support request",session["student_id"],cat);return jsonify(message="Support request sent to Admin.")


@app.get("/api/support")
@student_required
def my_support():
    db=get_db_connection();rows=db.execute("SELECT id,category,message,status,admin_note,created_at,updated_at FROM support_requests WHERE student_id=? ORDER BY id DESC",(session["student_id"],)).fetchall();db.close();return jsonify(requests=rows_dict(rows))


@app.get("/api/admin/dashboard")
@admin_required
def admin_dashboard():
    db=get_db_connection();total=db.execute("SELECT COUNT(*) n FROM students").fetchone()["n"];active=db.execute("SELECT COUNT(*) n FROM students WHERE account_status='Active'").fetchone()["n"];plans=db.execute("SELECT COUNT(*) n FROM planners WHERE date=?",(date.today().isoformat(),)).fetchone()["n"];ps=db.execute("SELECT COUNT(*) n FROM ps_progress WHERE date=?",(date.today().isoformat(),)).fetchone()["n"];pending=db.execute("SELECT COUNT(*) n FROM progress_requests WHERE status='Pending'").fetchone()["n"];issues=db.execute("SELECT COUNT(*) n FROM support_requests WHERE status!='Resolved'").fetchone()["n"];verified=db.execute("SELECT COUNT(*) n FROM progress_requests WHERE status='Approved'").fetchone()["n"];recent=db.execute("SELECT p.date,p.course,p.level,p.status,p.verification_status,p.score,s.name,s.register_no FROM ps_progress p JOIN students s ON s.id=p.student_id ORDER BY p.id DESC LIMIT 30").fetchall();db.close()
    return jsonify(stats={"total_students":total,"active_students":active,"plans_today":plans,"ps_today":ps,"pending_verifications":pending,"open_issues":issues,"approved_history":verified},recent_activity=rows_dict(recent),total_courses=len(COURSES),total_levels=TOTAL_LEVELS)


@app.get("/api/admin/student/<register_no>")
@admin_required
def admin_student(register_no):
    db=get_db_connection();s=db.execute("SELECT id,email,register_no,name,course,batch,mobile,program,department,year,account_status,created_at FROM students WHERE TRIM(register_no)=?",(str(register_no).strip(),)).fetchone()
    if not s:db.close();return jsonify(message="Student not found."),404
    plans=db.execute("SELECT id,date,h1,h2,h3,h4,h5,h6,h7 FROM planners WHERE student_id=? ORDER BY date DESC",(s["id"],)).fetchall();ps=db.execute("SELECT id,date,course,level,status,attempt_no,score,verification_status,source FROM ps_progress WHERE student_id=? ORDER BY date DESC,id DESC",(s["id"],)).fetchall();req=db.execute("SELECT id,course,level,proof_note,proof_link,status,admin_note,created_at FROM progress_requests WHERE student_id=? ORDER BY id DESC",(s["id"],)).fetchall();db.close();st=calculate_stats(s["id"]);return jsonify(student=dict(s),planners=rows_dict(plans),ps_progress=rows_dict(ps),progress_requests=rows_dict(req),stats={**st,"current_streak":calculate_streak(s["id"])["current"],"best_streak":calculate_streak(s["id"])["best"]},course_matrix=calculate_matrix(s["id"]))


@app.get("/api/admin/students")
@admin_required
def admin_students():
    q=str(request.args.get("q","")).strip();db=get_db_connection();rows=db.execute("SELECT id,name,register_no,email,course,batch,mobile,program,department,year,account_status FROM students WHERE register_no LIKE ? OR name LIKE ? OR email LIKE ? OR department LIKE ? ORDER BY name LIMIT 100",(f"%{q}%",f"%{q}%",f"%{q}%",f"%{q}%")).fetchall();db.close();out=[]
    for s in rows:
        st=calculate_stats(s["id"]);sr=calculate_streak(s["id"]);x=dict(s);x.update(progress=st["overall_progress"],levels=st["completed_levels"],total_levels=st["total_levels"],streak=sr["current"]);out.append(x)
    return jsonify(students=out)


@app.get("/api/admin/course-matrix")
@admin_required
def admin_course_matrix():
    # Admin sees only courses with verified completed levels.
    db=get_db_connection();rows=db.execute("""SELECT course, COUNT(DISTINCT student_id || ':' || level) completed_levels,
        COUNT(DISTINCT student_id) students FROM ps_progress
        WHERE LOWER(status)='completed' AND verification_status='Verified' GROUP BY course ORDER BY completed_levels DESC""").fetchall();db.close();return jsonify(courses=rows_dict(rows))


@app.get("/api/admin/support")
@admin_required
def admin_support():
    db=get_db_connection();rows=db.execute("""SELECT r.id,r.category,r.message,r.status,r.admin_note,r.created_at,r.updated_at,s.name,s.register_no
        FROM support_requests r JOIN students s ON s.id=r.student_id
        ORDER BY CASE WHEN r.status='Open' THEN 0 WHEN r.status='In Progress' THEN 1 ELSE 2 END,r.id DESC""").fetchall();db.close();return jsonify(requests=rows_dict(rows))


@app.post("/api/admin/support/<int:rid>")
@admin_required
def update_support(rid):
    d=request.get_json(silent=True) or {};status=str(d.get("status",""));note=str(d.get("admin_note","")).strip()[:1000]
    if status not in {"Open","In Progress","Resolved"}:return jsonify(message="Invalid status."),400
    db=get_db_connection();cur=db.execute("UPDATE support_requests SET status=?,admin_note=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(status,note,rid));db.commit();db.close()
    if not cur.rowcount:return jsonify(message="Request not found."),404
    audit("Updated support request",None,f"Request {rid}: {status}");return jsonify(message="Support updated.")




@app.get("/api/admin/daily-verifications")
@admin_required
def admin_daily_verifications():
    db=get_db_connection();rows=db.execute("""SELECT p.*,s.name,s.register_no,s.email FROM ps_progress p
        JOIN students s ON s.id=p.student_id
        WHERE p.verification_status='Pending'
        ORDER BY p.id DESC LIMIT 200""").fetchall();db.close();return jsonify(records=rows_dict(rows))


@app.post("/api/admin/daily-verifications/<int:record_id>")
@admin_required
def review_daily_verification(record_id):
    d=request.get_json(silent=True) or {};decision=str(d.get("decision",""));note=str(d.get("admin_note","")).strip()[:1000]
    if decision not in {"Verified","Rejected"}:return jsonify(message="Invalid decision."),400
    db=get_db_connection();r=db.execute("SELECT * FROM ps_progress WHERE id=?",(record_id,)).fetchone()
    if not r:db.close();return jsonify(message="PS record not found."),404
    db.execute("UPDATE ps_progress SET verification_status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(decision,record_id));db.commit();db.close()
    audit(f"Daily PS verification {decision}",r["student_id"],f"{r['course']} {r['level']} · {note}");return jsonify(message=f"Daily PS report {decision.lower()}.")

@app.get("/api/admin/verifications")
@admin_required
def admin_verifications():
    db=get_db_connection();rows=db.execute("""SELECT r.*,s.name,s.register_no,s.email FROM progress_requests r JOIN students s ON s.id=r.student_id
        ORDER BY CASE WHEN r.status='Pending' THEN 0 ELSE 1 END,r.id DESC""").fetchall();db.close();return jsonify(requests=rows_dict(rows))


@app.post("/api/admin/verifications/<int:rid>")
@admin_required
def review_verification(rid):
    d=request.get_json(silent=True) or {};decision=str(d.get("decision",""));note=str(d.get("admin_note","")).strip()[:1000]
    if decision not in {"Approved","Rejected","Correction Required"}:return jsonify(message="Invalid verification decision."),400
    db=get_db_connection();r=db.execute("SELECT * FROM progress_requests WHERE id=?",(rid,)).fetchone()
    if not r:db.close();return jsonify(message="Request not found."),404
    db.execute("UPDATE progress_requests SET status=?,admin_note=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(decision,note,rid))
    if decision=="Approved":
        # Official completion is created/updated by Admin, never directly by the student.
        db.execute("""INSERT INTO ps_progress(student_id,date,course,level,status,attempt_no,score,verification_status,source)
            VALUES(?,?,?,?,? ,1,0,'Verified','Previous Progress')
            ON CONFLICT DO NOTHING""",(r["student_id"],date.today().isoformat(),r["course"],r["level"],"Completed"))
        # SQLite has no matching unique constraint; prevent duplicates explicitly.
        existing=db.execute("SELECT id FROM ps_progress WHERE student_id=? AND course=? AND level=? AND source='Previous Progress'",(r["student_id"],r["course"],r["level"])).fetchone()
        if existing: db.execute("UPDATE ps_progress SET status='Completed',verification_status='Verified',updated_at=CURRENT_TIMESTAMP WHERE id=?",(existing["id"],))
    db.commit();db.close();audit(f"Progress verification {decision}",r["student_id"],f"Request {rid} {r['course']} {r['level']}");return jsonify(message=f"Verification {decision.lower()}.")


@app.post("/api/admin/student/<int:sid>/reset-password")
@admin_required
def reset_password(sid):
    pw=str((request.get_json(silent=True) or {}).get("password",""));
    if len(pw)<8:return jsonify(message="Password must contain at least 8 characters."),400
    db=get_db_connection();cur=db.execute("UPDATE students SET password_hash=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(generate_password_hash(pw),sid));db.commit();db.close()
    if not cur.rowcount:return jsonify(message="Student not found."),404
    audit("Admin reset student password",sid,"Temporary password set by Admin");return jsonify(message="Password reset successfully.")


@app.post("/api/admin/student/<int:sid>/update-name")
@admin_required
def update_name(sid):
    name=str((request.get_json(silent=True) or {}).get("name","" )).strip().upper()
    if len(name)<2:return jsonify(message="Enter a valid name."),400
    db=get_db_connection();cur=db.execute("UPDATE students SET name=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(name,sid));db.commit();db.close()
    if not cur.rowcount:return jsonify(message="Student not found."),404
    audit("Admin corrected student name",sid,name);return jsonify(message="Student name updated.")


@app.post("/api/admin/student/<int:sid>/update-academic")
@admin_required
def update_academic(sid):
    d=request.get_json(silent=True) or {};program=str(d.get("program",""));department=str(d.get("department",""));year=str(d.get("year",""));batch=str(d.get("batch",""))
    if program not in PROGRAMS or department not in DEPARTMENTS or year not in YEARS:return jsonify(message="Invalid academic details."),400
    db=get_db_connection();cur=db.execute("UPDATE students SET program=?,department=?,year=?,batch=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(program,department,year,batch,sid));db.commit();db.close()
    if not cur.rowcount:return jsonify(message="Student not found."),404
    audit("Admin corrected academic details",sid,f"{program} {department} {year} {batch}");return jsonify(message="Academic details updated.")


@app.post("/api/admin/student/<int:sid>/status")
@admin_required
def account_status(sid):
    status=str((request.get_json(silent=True) or {}).get("status",""))
    if status not in {"Active","Inactive"}:return jsonify(message="Invalid account status."),400
    db=get_db_connection();cur=db.execute("UPDATE students SET account_status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(status,sid));db.commit();db.close()
    if not cur.rowcount:return jsonify(message="Student not found."),404
    audit(f"Admin set account {status}",sid,"");return jsonify(message=f"Account {status.lower()}.")


@app.delete("/api/admin/ps-progress/<int:record_id>")
@admin_required
def delete_ps(record_id):
    db=get_db_connection();r=db.execute("SELECT student_id,course,level FROM ps_progress WHERE id=?",(record_id,)).fetchone();cur=db.execute("DELETE FROM ps_progress WHERE id=?",(record_id,));db.commit();db.close()
    if not cur.rowcount:return jsonify(message="Record not found."),404
    audit("Admin removed PS record",r["student_id"] if r else None,f"{r['course']} {r['level']}" if r else str(record_id));return jsonify(message="PS record removed.")


@app.delete("/api/admin/planner/<int:planner_id>")
@admin_required
def delete_planner(planner_id):
    db=get_db_connection();r=db.execute("SELECT student_id FROM planners WHERE id=?",(planner_id,)).fetchone();cur=db.execute("DELETE FROM planners WHERE id=?",(planner_id,));db.commit();db.close()
    if not cur.rowcount:return jsonify(message="Planner record not found."),404
    audit("Admin removed planner record",r["student_id"] if r else None,str(planner_id));return jsonify(message="Planner record removed.")


@app.get("/api/admin/audit")
@admin_required
def admin_audit():
    db=get_db_connection();rows=db.execute("SELECT id,actor_role,action,target_student_id,details,created_at FROM audit_logs ORDER BY id DESC LIMIT 100").fetchall();db.close();return jsonify(logs=rows_dict(rows))


@app.get("/api/health")
def health(): return jsonify(status="ok",app="Elite Student Progress Tracker",database="SQLite",courses=len(COURSES),total_levels=TOTAL_LEVELS)


@app.errorhandler(404)
def not_found(e):
    if request.path.startswith("/api/"):return jsonify(message="API endpoint not found."),404
    return e

@app.errorhandler(500)
def server_error(e):
    if request.path.startswith("/api/"):return jsonify(message="Internal server error."),500
    return e

create_database()

if __name__=="__main__":
    port = int(os.environ.get("PORT", 5000))
    print("="*58);print("ELITE STUDENT PROGRESS TRACKER");print(f"URL: http://127.0.0.1:{port}");print(f"Admin: {ADMIN_EMAIL} / {ADMIN_PASSWORD}");print("="*58)
    app.run(host="0.0.0.0",port=port)
