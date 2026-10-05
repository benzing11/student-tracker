from dotenv import load_dotenv
load_dotenv()

import os
import re
import secrets
import io
import uuid
from functools import wraps
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

try:
    import psycopg
except ImportError:
    psycopg = None
from flask import Flask, request, jsonify, session, render_template, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from supabase import create_client

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

from database import (
    get_db_connection, create_database, COURSES, TOTAL_LEVELS,
    PROGRAMS, DEPARTMENTS, YEARS
)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", PERMANENT_SESSION_LIFETIME=timedelta(days=30))

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


COURSE_SHORT = {
    "Aptitude": "Apt", "Basic mathematics - Algebra": "Math", "C Programming": "C",
    "C Programming Learning": "C-L", "Communication": "Comm", "Computer Networking": "Net",
    "Data Structure": "DS", "Database Programming": "DB", "Electrical Skills": "Elec",
    "Electronics skill": "Elex", "Group Discussion": "GD", "HTML / CSS": "HTML",
    "Java Programming": "Java", "Java Script": "JS", "Linux": "Linux",
    "Mechanical Manufacturing": "Mech", "Programming C++": "C++",
    "Programming Python": "Python", "Version control - Git, Github": "Git",
}

REPORT_DEPT_ORDER = ["CSE", "ECE", "AIDS", "EEE", "CIVIL", "IT", "CSBS", "AIML", "MECH"]

ALLOWED_EXT = {"pdf","doc","docx","xls","xlsx","ppt","pptx","png","jpg","jpeg","gif","webp","mp4","mov","webm","avi","mkv","txt","zip"}
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024


def save_proof_file(file_storage):
    fn = file_storage.filename or ""
    ext = fn.rsplit(".", 1)[-1].lower() if "." in fn else ""
    if ext not in ALLOWED_EXT:
        return None, "Unsupported file type."
    data = file_storage.read()
    if len(data) > 32 * 1024 * 1024:
        return None, "File too large (max 32 MB)."
    path = f"proofs/{uuid.uuid4().hex}.{ext}"
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    sb.storage.from_(os.environ["SUPABASE_BUCKET"]).upload(
        path, data, {"content-type": file_storage.mimetype or "application/octet-stream"}
    )
    url = f"{os.environ['SUPABASE_URL']}/storage/v1/object/public/{os.environ['SUPABASE_BUCKET']}/{path}"
    return url, None


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
        if session.get("account_status") != "Active":
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
                         WHERE student_id=? AND status='Completed' AND verification_status='Verified'""", (student_id,)).fetchall()
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
        WHERE student_id=? AND status='Completed' AND verification_status='Verified' ORDER BY date""",(student_id,)).fetchall(); db.close()
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
    from datetime import date as _date
    week=_date.today().strftime("%Y-W%V")
    db=get_db_connection(); students=db.execute("SELECT id,name,department,year FROM students WHERE account_status='Active' ORDER BY name").fetchall()
    ids=[s["id"] for s in students]
    done={}; dates={}; streak_data={}
    if ids:
        ph=",".join(["?"]*len(ids))
        for r in db.execute(f"""SELECT DISTINCT student_id, course, level FROM ps_progress
            WHERE status='Completed' AND verification_status='Verified' AND student_id IN ({ph})""",tuple(ids)).fetchall():
            c=normalize_course(r["course"]) or r["course"]; l=normalize_level(r["level"])
            if c in COURSES and l and 1<=l<=COURSES[c]: done.setdefault(r["student_id"],set()).add((c,l))
        for r in db.execute(f"""SELECT student_id, course, level FROM progress_requests
            WHERE status='Approved' AND student_id IN ({ph})""",tuple(ids)).fetchall():
            c=normalize_course(r["course"]) or r["course"]; l=normalize_level(r["level"])
            if c in COURSES and l and 1<=l<=COURSES[c]: done.setdefault(r["student_id"],set()).add((c,l))
        for r in db.execute(f"""SELECT DISTINCT student_id, date FROM ps_progress
            WHERE status='Completed' AND verification_status='Verified' AND student_id IN ({ph})""",tuple(ids)).fetchall():
            d=valid_date(r["date"])
            if d: dates.setdefault(r["student_id"],set()).add(d)
    prev_rank={}
    for r in db.execute("SELECT student_id,rank FROM leaderboard_snapshots WHERE week!=?",(week,)).fetchall():
        if r["rank"]: prev_rank[r["student_id"]]=r["rank"]
    db.close()
    out=[]
    for s in students:
        sid=s["id"]
        cur=0; d=date.today()
        while d in dates.get(sid,set()): cur+=1; d-=timedelta(days=1)
        lvl=len(done.get(sid,set()))
        parts=s["name"].split(); public_name=parts[0] if len(parts)==1 else parts[0]+" "+parts[-1][0]+"."
        out.append({"student_id":sid,"name":public_name,"department":s["department"],"year":s["year"],
                    "progress":round(lvl/TOTAL_LEVELS*100,1),"levels":lvl,"streak":cur})
    out.sort(key=lambda x:(-x["levels"],-x["progress"],-x["streak"],x["name"].lower()))
    for i,r in enumerate(out,1): r["rank"]=i
    for r in out:
        sid=r["student_id"]
        if sid in prev_rank:
            delta=prev_rank[sid]-r["rank"]
            r["movement"]="up" if delta>0 else ("down" if delta<0 else "same")
            r["delta"]=abs(delta)
        else:
            r["movement"]="new";r["delta"]=0
    db=get_db_connection()
    db.executemany("INSERT INTO leaderboard_snapshots(student_id,levels,progress,streak,rank,week) VALUES(?,?,?,?,?,?) ON CONFLICT(student_id,week) DO UPDATE SET levels=excluded.levels,progress=excluded.progress,streak=excluded.streak,rank=excluded.rank",
                   [(r["student_id"],r["levels"],r["progress"],r["streak"],r["rank"],week) for r in out])
    db.commit();db.close()
    return out


def admin_performance_report(sort="performance", dept=None, limit=None):
    db=get_db_connection()
    rows=db.execute("""SELECT id,name,register_no,program,department,year,batch
        FROM students WHERE account_status='Active' ORDER BY name""").fetchall()
    ids=[r["id"] for r in rows]
    done={}
    if ids:
        ph=",".join(["?"]*len(ids))
        for r in db.execute(f"""SELECT DISTINCT student_id, course, level FROM ps_progress
            WHERE status='Completed' AND verification_status='Verified' AND student_id IN ({ph})""",tuple(ids)).fetchall():
            c=normalize_course(r["course"]) or r["course"]; l=normalize_level(r["level"])
            if c in COURSES and l and 1<=l<=COURSES[c]: done.setdefault(r["student_id"],set()).add((c,l))
        for r in db.execute(f"""SELECT student_id, course, level FROM progress_requests
            WHERE status='Approved' AND student_id IN ({ph})""",tuple(ids)).fetchall():
            c=normalize_course(r["course"]) or r["course"]; l=normalize_level(r["level"])
            if c in COURSES and l and 1<=l<=COURSES[c]: done.setdefault(r["student_id"],set()).add((c,l))
        dates={}
        for r in db.execute(f"""SELECT DISTINCT student_id, date FROM ps_progress
            WHERE status='Completed' AND verification_status='Verified' AND student_id IN ({ph})""",tuple(ids)).fetchall():
            d=valid_date(r["date"])
            if d: dates.setdefault(r["student_id"],set()).add(d)
        reported={}; pending={}; planner_days={}; approved_count={}
        for r in db.execute(f"SELECT student_id, COUNT(*) n FROM ps_progress WHERE student_id IN ({ph}) GROUP BY student_id",tuple(ids)).fetchall(): reported[r["student_id"]]=r["n"]
        for r in db.execute(f"SELECT student_id, COUNT(*) n FROM ps_progress WHERE verification_status='Pending' AND student_id IN ({ph}) GROUP BY student_id",tuple(ids)).fetchall(): pending[r["student_id"]]=r["n"]
        for r in db.execute(f"SELECT student_id, COUNT(*) n FROM planners WHERE student_id IN ({ph}) GROUP BY student_id",tuple(ids)).fetchall(): planner_days[r["student_id"]]=r["n"]
        for r in db.execute(f"SELECT student_id, COUNT(*) n FROM progress_requests WHERE status='Approved' AND student_id IN ({ph}) GROUP BY student_id",tuple(ids)).fetchall(): approved_count[r["student_id"]]=r["n"]
    else:
        dates={}; reported={}; pending={}; planner_days={}; approved_count={}
    db.close()
    out=[]
    for s in rows:
        sid=s["id"]; ds=done.get(sid,set())
        cur=0; d=date.today()
        while d in dates.get(sid,set()): cur+=1; d-=timedelta(days=1)
        best=run=0; prev=None
        for dd in sorted(dates.get(sid,set())):
            run=run+1 if prev and dd==prev+timedelta(days=1) else 1; best=max(best,run); prev=dd
        out.append({"student_id":sid,"register_no":s["register_no"],"name":s["name"],
                    "program":s["program"],"department":s["department"],"year":s["year"],"batch":s["batch"],
                    "progress":round(len(ds)/TOTAL_LEVELS*100,1),"levels":len(ds),"streak":cur,"best_streak":best,
                    "ps_submissions":reported.get(sid,0),"pending_ps":pending.get(sid,0),
                    "planner_days":planner_days.get(sid,0),"approved_history_levels":approved_count.get(sid,0),
                    "done":ds})
    if dept:
        out=[x for x in out if x["department"]==dept]
    if sort=="department":
        out.sort(key=lambda x:(REPORT_DEPT_ORDER.index(x["department"]) if x["department"] in REPORT_DEPT_ORDER else 99,-x["progress"],-x["levels"],-x["streak"],x["name"].lower()))
    else:
        out.sort(key=lambda x:(-x["levels"],-x["progress"],-x["streak"],x["name"].lower()))
    if limit:
        out=out[:limit]
    for i,x in enumerate(out,1): x["rank"]=i
    return out


def build_report_xlsx(students):
    wb=Workbook(); ws=wb.active; ws.title="Overall Report"
    green=PatternFill(start_color="C6EFCE",end_color="C6EFCE",fill_type="solid")
    red=PatternFill(start_color="FFC7CE",end_color="FFC7CE",fill_type="solid")
    header_fill=PatternFill(start_color="F2F2F2",end_color="F2F2F2",fill_type="solid")
    header_font=Font(bold=True)
    center=Alignment(horizontal="center",vertical="center")
    headers=["Register No","Name","Dept","Year","Total Levels Completed"]
    level_cols=[]
    for course,total in COURSES.items():
        for l in range(1,total+1):
            level_cols.append((course,l,f"{COURSE_SHORT[course]}-{l}"))
    for col,h in enumerate(headers,1):
        c=ws.cell(row=1,column=col,value=h); c.fill=header_fill; c.font=header_font; c.alignment=center
    for idx,(course,l,label) in enumerate(level_cols, len(headers)+1):
        c=ws.cell(row=1,column=idx,value=label); c.fill=header_fill; c.font=header_font; c.alignment=center
    for r,stu in enumerate(students,2):
        ws.cell(row=r,column=1,value=stu["register_no"])
        ws.cell(row=r,column=2,value=stu["name"])
        ws.cell(row=r,column=3,value=stu["department"])
        ws.cell(row=r,column=4,value=stu["year"])
        ws.cell(row=r,column=5,value=stu["levels"]).alignment=center
        for idx,(course,l,_) in enumerate(level_cols, len(headers)+1):
            done=(course,l) in stu["done"]
            cell=ws.cell(row=r,column=idx,value="✓" if done else "—")
            cell.fill=green if done else red
            cell.alignment=center
    ws.freeze_panes="A2"
    ws.column_dimensions["A"].width=16; ws.column_dimensions["B"].width=30
    ws.column_dimensions["C"].width=8; ws.column_dimensions["D"].width=8
    ws.column_dimensions["E"].width=10
    for idx in range(len(headers)+1,len(headers)+len(level_cols)+1):
        ws.column_dimensions[get_column_letter(idx)].width=7
    bio=io.BytesIO(); wb.save(bio); bio.seek(0)
    return bio


def activity_points_total(student_id):
    db=get_db_connection(); n=db.execute("SELECT COUNT(*) n FROM activity_points WHERE student_id=?",(student_id,)).fetchone()["n"]
    d=db.execute("SELECT COALESCE(SUM(deduction),0) d FROM daily_activity WHERE student_id=?",(student_id,)).fetchone()["d"]
    db.close(); return n-d


def finalize_activity():
    """Idempotently finalize the most recent completed day (yesterday) for
    Daily Activity deductions. Runs on admin dashboard / daily-activity load.
    -1 per planned-but-unfinished activity, -7 if the student made no plan."""
    db=get_db_connection()
    day=(local_today()-timedelta(days=1)).isoformat()
    done=db.execute("SELECT student_id FROM daily_activity WHERE date=? LIMIT 1",(day,)).fetchone()
    if done:
        db.close(); return
    students=db.execute("SELECT id FROM students WHERE account_status='Active'").fetchall()
    planned=db.execute("SELECT student_id,h1,h2,h3,h4,h5,h6,h7,h1d,h2d,h3d,h4d,h5d,h6d,h7d FROM planners WHERE date=?",(day,)).fetchall()
    by_sid={r["student_id"]:r for r in planned}
    for st in students:
        sid=st["id"]; p=by_sid.get(sid)
        if p is None:
            db.execute("INSERT INTO daily_activity(student_id,date,planned_count,completed_count,deduction,reason) VALUES(?,?,0,0,7,'no_plan') ON CONFLICT(student_id,date) DO NOTHING",(sid,day))
            continue
        hrs=[(p[f"h{i}"] or "").strip() for i in range(1,8)]
        planned_n=sum(1 for h in hrs if h)
        done_n=sum(1 for i in range(7) if hrs[i] and p[f"h{i+1}d"])
        unfinished=planned_n-done_n
        if unfinished>0:
            db.execute("INSERT INTO daily_activity(student_id,date,planned_count,completed_count,deduction,reason) VALUES(?,?,?,?,?,'incomplete') ON CONFLICT(student_id,date) DO NOTHING",(sid,day,planned_n,done_n,unfinished))
    db.commit(); db.close()


def task_points_for(student_id):
    db=get_db_connection()
    r=db.execute("""SELECT COALESCE(SUM(a.points),0) total FROM task_submissions ts
        JOIN task_instances ti ON ti.id=ts.instance_id
        JOIN task_assignments a ON a.id=ti.task_id
        WHERE ts.student_id=? AND ts.status='approved'""",(student_id,)).fetchone(); db.close()
    return r["total"]


def task_points_leaderboard():
    db=get_db_connection()
    rows=db.execute("""SELECT s.id, s.name, s.register_no, s.department, s.year,
        COALESCE(SUM(CASE WHEN ts.status='approved' THEN a.points ELSE 0 END),0) points
        FROM students s
        LEFT JOIN task_instances ti ON ti.student_id=s.id
        LEFT JOIN task_assignments a ON a.id=ti.task_id
        LEFT JOIN task_submissions ts ON ts.instance_id=ti.id
        WHERE s.account_status='Active'
        GROUP BY s.id ORDER BY points DESC, s.name""").fetchall(); db.close()
    out=[]
    for i,r in enumerate(rows,1):
        parts=r["name"].split(); pub=parts[0] if len(parts)==1 else parts[0]+" "+parts[-1][0]+"."
        out.append({"id":r["id"],"rank":i,"name":pub,"department":r["department"],"year":r["year"],"register_no":r["register_no"],"points":r["points"]})
    return out


def my_tasks(student_id):
    db=get_db_connection()
    rows=db.execute("""SELECT ti.id, a.title, a.description, a.points, a.frequency, a.deadline, ti.status,
        ts.id sub_id, ts.note, ts.file_path, ts.status sub_status, ts.admin_note
        FROM task_instances ti JOIN task_assignments a ON a.id=ti.task_id
        LEFT JOIN task_submissions ts ON ts.instance_id=ti.id
        WHERE ti.student_id=? AND ti.status<>'approved'
        ORDER BY a.deadline, a.id""",(student_id,)).fetchall(); db.close()
    return rows_dict(rows)


@app.post("/api/planner/complete")
@student_required
def planner_complete():
    d=request.get_json(silent=True) or {}
    try: hour=int(d.get("hour",0))
    except: return jsonify(message="Invalid hour."),400
    if hour<1 or hour>7: return jsonify(message="Invalid hour."),400
    sid=session["student_id"]; dt=date.today().isoformat()
    db=get_db_connection()
    plan=db.execute("SELECT id FROM planners WHERE student_id=? AND date=?",(sid,dt)).fetchone()
    if not plan:
        db.close(); return jsonify(message="You haven't planned for today, so there's nothing to mark done."),400
    db.execute(f"UPDATE planners SET h{hour}d=1 WHERE student_id=? AND date=?",(sid,dt))
    db.execute("""INSERT INTO activity_points(student_id,date,hour) VALUES(?,?,?)
        ON CONFLICT(student_id,date,hour) DO NOTHING""",(sid,dt,hour))
    db.commit(); db.close()
    return jsonify(message="Marked complete ✓",activity_points=activity_points_total(sid))


@app.get("/api/tasks")
@student_required
def tasks():
    sid=session["student_id"]
    return jsonify(task_points=task_points_for(sid),activity_points=activity_points_total(sid),
                   leaderboard=task_points_leaderboard()[:10],tasks=my_tasks(sid))


@app.post("/api/tasks/<int:instance_id>/submit")
@student_required
def submit_task(instance_id):
    sid=session["student_id"]
    note=str(request.form.get("note","")).strip()[:2000]
    if len(note)<3: return jsonify(message="Describe your completion briefly."),400
    file_url=""
    if "file" in request.files and request.files["file"].filename:
        file_url,err=save_proof_file(request.files["file"])
        if err: return jsonify(message=err),400
    db=get_db_connection()
    inst=db.execute("SELECT * FROM task_instances WHERE id=? AND student_id=?",(instance_id,sid)).fetchone()
    if not inst: db.close(); return jsonify(message="Task not found."),404
    if inst["status"]!="assigned": db.close(); return jsonify(message="Task already submitted or reviewed."),400
    db.execute("""INSERT INTO task_submissions(instance_id,student_id,note,file_path,status)
        VALUES(?,?,?,?,'pending')""",(instance_id,sid,note,file_url))
    db.execute("UPDATE task_instances SET status='submitted' WHERE id=?",(instance_id,))
    db.commit(); db.close()
    audit("Submitted task proof",sid,f"Task instance {instance_id}")
    return jsonify(message="Proof submitted. Awaiting verification.")


@app.post("/api/admin/tasks")
@admin_required
def admin_create_task():
    d=request.get_json(silent=True) or {}
    title=str(d.get("title","")).strip(); description=str(d.get("description","")).strip()
    try: points=int(d.get("points",0))
    except: return jsonify(message="Points must be a number."),400
    frequency=str(d.get("frequency","daily")).lower()
    deadline=str(d.get("deadline","")).strip(); target_type=str(d.get("target_type","all")).strip()
    target_student_id=d.get("target_student_id")
    if not title or len(description)<3: return jsonify(message="Enter a title and description."),400
    if points<0: return jsonify(message="Points cannot be negative."),400
    if frequency not in {"daily","weekly","monthly"}: return jsonify(message="Select daily, weekly or monthly."),400
    if not valid_date(deadline): return jsonify(message="Set a valid deadline."),400
    if target_type not in {"all","student"}: return jsonify(message="Invalid target."),400
    db=get_db_connection()
    if target_type=="student":
        st=db.execute("SELECT id FROM students WHERE id=? AND account_status='Active'",(target_student_id,)).fetchone()
        if not st: db.close(); return jsonify(message="Student not found or inactive."),404
    cur=db.execute("""INSERT INTO task_assignments(title,description,points,frequency,deadline,target_type,target_student_id)
        VALUES(?,?,?,?,?,?,?)""",
        (title,description,points,frequency,deadline,target_type,target_student_id if target_type=="student" else None))
    try:
        # Postgres path with RETURNING
        task_id=cur.fetchone()["id"] if cur.description else cur.lastrowid
    except Exception:
        task_id=getattr(cur, "lastrowid", None)
    if not task_id:
        # SQLite fallback
        task_id=cur.lastrowid
        if not task_id:
            # last resort: fetch max id
            task_id=db.execute("SELECT MAX(id) id FROM task_assignments").fetchone()["id"]
    if target_type=="all":
        stus=db.execute("SELECT id FROM students WHERE account_status='Active'").fetchall()
        for s in stus:
            db.execute("""INSERT INTO task_instances(task_id,student_id) VALUES(?,?)
                ON CONFLICT(task_id,student_id) DO NOTHING""",(task_id,s["id"]))
    else:
        db.execute("""INSERT INTO task_instances(task_id,student_id) VALUES(?,?)
            ON CONFLICT(task_id,student_id) DO NOTHING""",(task_id,target_student_id))
    db.commit(); db.close()
    audit("Assigned task",None,f"{title} · {points} pts · {frequency} · due {deadline}")
    return jsonify(message="Task assigned to all active students." if target_type=="all" else "Task assigned to the student.")


@app.get("/api/admin/tasks")
@admin_required
def admin_tasks():
    db=get_db_connection()
    rows=db.execute("""SELECT a.id,a.title,a.description,a.points,a.frequency,a.deadline,a.target_type,a.created_at,
        COUNT(ti.id) assigned,
        SUM(CASE WHEN ti.status='submitted' THEN 1 ELSE 0 END) submitted,
        SUM(CASE WHEN ti.status='approved' THEN 1 ELSE 0 END) approved
        FROM task_assignments a LEFT JOIN task_instances ti ON ti.task_id=a.id
        GROUP BY a.id ORDER BY a.id DESC""").fetchall(); db.close()
    return jsonify(tasks=rows_dict(rows))


@app.get("/api/admin/tasks/submissions")
@admin_required
def admin_task_submissions():
    db=get_db_connection()
    rows=db.execute("""SELECT ts.id, ts.note, ts.file_path, ts.status, ts.admin_note, ts.submitted_at,
        s.name, s.register_no, a.title, a.points, a.deadline
        FROM task_submissions ts
        JOIN task_instances ti ON ti.id=ts.instance_id
        JOIN task_assignments a ON a.id=ti.task_id
        JOIN students s ON s.id=ts.student_id
        ORDER BY CASE WHEN ts.status='pending' THEN 0 ELSE 1 END, ts.id ASC""").fetchall(); db.close()
    return jsonify(submissions=rows_dict(rows))


@app.post("/api/admin/tasks/submissions/<int:sub_id>")
@admin_required
def admin_review_task_submission(sub_id):
    d=request.get_json(silent=True) or {}; decision=str(d.get("decision","")); note=str(d.get("admin_note","")).strip()[:1000]
    if decision not in {"approved","rejected"}: return jsonify(message="Invalid decision."),400
    db=get_db_connection()
    r=db.execute("""SELECT ts.*, ti.student_id FROM task_submissions ts JOIN task_instances ti ON ti.id=ts.instance_id WHERE ts.id=?""",(sub_id,)).fetchone()
    if not r: db.close(); return jsonify(message="Submission not found."),404
    db.execute("""UPDATE task_submissions SET status=?,admin_note=?,reviewed_at=to_char(now() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS') WHERE id=?""",(decision,note,sub_id))
    db.execute("UPDATE task_instances SET status=? WHERE id=?",(decision,r["instance_id"]))
    db.commit(); db.close()
    audit(f"Task submission {decision}",r["student_id"],f"Submission {sub_id}")
    return jsonify(message="Submission approved. Task points awarded." if decision=="approved" else "Submission rejected.")


@app.get("/api/admin/activity-points")
@admin_required
def admin_activity_points():
    db=get_db_connection()
    rows=db.execute("""SELECT s.id,s.name,s.register_no,s.department,s.year,COUNT(ap.id) earned,
        COALESCE(SUM(da.deduction),0) ded
        FROM students s
        LEFT JOIN activity_points ap ON ap.student_id=s.id
        LEFT JOIN daily_activity da ON da.student_id=s.id
        WHERE s.account_status='Active'
        GROUP BY s.id ORDER BY (COUNT(ap.id)-COALESCE(SUM(da.deduction),0)) DESC, s.name""").fetchall(); db.close()
    out=[]
    for i,r in enumerate(rows,1):
        parts=r["name"].split(); pub=parts[0] if len(parts)==1 else parts[0]+" "+parts[-1][0]+"."
        points=r["earned"]-r["ded"]
        out.append({"rank":i,"name":pub,"department":r["department"],"year":r["year"],"register_no":r["register_no"],"points":points,"earned":r["earned"],"deduction":r["ded"]})
    return jsonify(students=out)


@app.get("/api/admin/tasks/leaderboard")
@admin_required
def admin_task_leaderboard():
    return jsonify(leaderboard=task_points_leaderboard())


def daily_activity_rows(sort="points", dept=None):
    finalize_activity()
    today=local_today().isoformat()
    db=get_db_connection()
    students=db.execute("SELECT id,name,register_no,department,year FROM students WHERE account_status='Active' ORDER BY name").fetchall()
    plans={r["student_id"]:r for r in db.execute("SELECT student_id,h1,h2,h3,h4,h5,h6,h7,h1d,h2d,h3d,h4d,h5d,h6d,h7d FROM planners WHERE date=?",(today,)).fetchall()}
    ded={}
    for r in db.execute("""SELECT student_id, MAX(date) mdate FROM daily_activity GROUP BY student_id""").fetchall():
        d=db.execute("SELECT deduction FROM daily_activity WHERE student_id=? AND date=?",(r["student_id"],r["mdate"])).fetchone()
        if d: ded[r["student_id"]]=d["deduction"]
    earned=db.execute("SELECT student_id, COUNT(*) n FROM activity_points GROUP BY student_id").fetchall()
    earned={r["student_id"]:r["n"] for r in earned}
    db.close()
    rows=[]; planned=0; completed_all=0
    for s in students:
        if dept and s["department"]!=dept: continue
        p=plans.get(s["id"])
        hrs=[(p[f"h{i}"] or "").strip() for i in range(1,8)] if p else []
        planned_n=sum(1 for h in hrs if h)
        done_n=sum(1 for i in range(7) if hrs and hrs[i] and p[f"h{i+1}d"]) if p else 0
        if planned_n>0: planned+=1
        if planned_n>0 and done_n==planned_n: completed_all+=1
        status="completed" if (planned_n>0 and done_n==planned_n) else ("partial" if (planned_n>0 and done_n>0) else ("planned" if planned_n>0 else "no_plan"))
        net=(earned.get(s["id"],0))-(ded.get(s["id"],0))
        rows.append({"id":s["id"],"name":s["name"],"register_no":s["register_no"],"department":s["department"],"year":s["year"],
                     "planned":planned_n,"completed":done_n,"status":status,"deduction":ded.get(s["id"],0),"points":net})
    if sort=="name": rows.sort(key=lambda x:x["name"].lower())
    elif sort=="dept": rows.sort(key=lambda x:(x["department"],x["name"].lower()))
    else: rows.sort(key=lambda x:(-x["points"],x["name"].lower()))
    return rows, planned, completed_all


@app.get("/api/admin/daily-activity")
@admin_required
def admin_daily_activity():
    sort=request.args.get("sort","points"); dept=request.args.get("dept") or None
    rows,planned,completed_all=daily_activity_rows(sort,dept)
    no_plan=[r["name"] for r in rows if r["status"]=="no_plan"]
    today=local_today().isoformat()
    msg=(f"📋 DAILY ACTIVITY – {today}\nThe following students did not plan for today's activities "
         f"and get -7 activity points:\n\n" + "\n".join(f"• {n}" for n in no_plan))
    from urllib.parse import quote
    return jsonify(date=today,planned_today=planned,
                   didnt_plan=len(no_plan),
                   completed_all=completed_all,rows=rows,
                   no_plan_names=no_plan,message=msg,wa_link="https://wa.me/?text="+quote(msg))


@app.get("/api/admin/daily-activity/export")
@admin_required
def admin_daily_activity_export():
    sort=request.args.get("sort","points"); dept=request.args.get("dept") or None
    rows,_,_=daily_activity_rows(sort,dept)
    wb=Workbook(); ws=wb.active; ws.title="Daily Activity"
    headers=["Name","Register No","Department","Year","Planned","Completed","Status","Deduction","Activity Points"]
    for c,h in enumerate(headers,1): ws.cell(row=1,column=c,value=h).font=Font(bold=True)
    for i,r in enumerate(rows,2):
        vals=[r["name"],r["register_no"],r["department"],r["year"],r["planned"],r["completed"],r["status"],r["deduction"],r["points"]]
        for c,v in enumerate(vals,1): ws.cell(row=i,column=c,value=v)
    bio=io.BytesIO(); wb.save(bio); bio.seek(0)
    return send_file(bio,as_attachment=True,download_name=f"daily_activity_{local_today().isoformat()}.xlsx",mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.get("/api/admin/report-taskpoints")
@admin_required
def admin_report_taskpoints():
    sort=request.args.get("sort","rank"); dept=request.args.get("dept") or None
    db=get_db_connection()
    rows=db.execute("""SELECT s.id,s.name,s.register_no,s.department,s.year,
        SUM(CASE WHEN ts.status='approved' THEN 1 ELSE 0 END) tasks_completed,
        COALESCE(SUM(CASE WHEN ts.status='approved' THEN a.points ELSE 0 END),0) points
        FROM students s
        LEFT JOIN task_submissions ts ON ts.student_id=s.id
        LEFT JOIN task_instances ti ON ti.id=ts.instance_id
        LEFT JOIN task_assignments a ON a.id=ti.task_id
        WHERE s.account_status='Active'
        GROUP BY s.id""").fetchall(); db.close()
    out=[]
    for r in rows:
        if dept and r["department"]!=dept: continue
        out.append({"id":r["id"],"name":r["name"],"register_no":r["register_no"],"department":r["department"],"year":r["year"],
                    "tasks_completed":r["tasks_completed"],"points":r["points"]})
    if sort=="alpha": out.sort(key=lambda x:x["name"].lower())
    elif sort=="dept": out.sort(key=lambda x:(x["department"],x["name"].lower()))
    else: out.sort(key=lambda x:(-x["points"],x["name"].lower()))
    for i,r in enumerate(out,1): r["rank"]=i
    return jsonify(rows=out)


@app.get("/api/admin/report-taskpoints/export")
@admin_required
def admin_report_taskpoints_export():
    sort=request.args.get("sort","rank"); dept=request.args.get("dept") or None
    db=get_db_connection()
    rows=db.execute("""SELECT s.id,s.name,s.register_no,s.department,s.year,
        SUM(CASE WHEN ts.status='approved' THEN 1 ELSE 0 END) tasks_completed,
        COALESCE(SUM(CASE WHEN ts.status='approved' THEN a.points ELSE 0 END),0) points
        FROM students s
        LEFT JOIN task_submissions ts ON ts.student_id=s.id
        LEFT JOIN task_instances ti ON ti.id=ts.instance_id
        LEFT JOIN task_assignments a ON a.id=ti.task_id
        WHERE s.account_status='Active'
        GROUP BY s.id""").fetchall(); db.close()
    out=[]
    for r in rows:
        if dept and r["department"]!=dept: continue
        out.append({"name":r["name"],"register_no":r["register_no"],"department":r["department"],"year":r["year"],"tasks_completed":r["tasks_completed"],"points":r["points"]})
    if sort=="alpha": out.sort(key=lambda x:x["name"].lower())
    elif sort=="dept": out.sort(key=lambda x:(x["department"],x["name"].lower()))
    else: out.sort(key=lambda x:(-x["points"],x["name"].lower()))
    wb=Workbook(); ws=wb.active; ws.title="Task Points"
    headers=["Rank","Name","Register No","Department","Year","Tasks Completed","Task Points"]
    for c,h in enumerate(headers,1): ws.cell(row=1,column=c,value=h).font=Font(bold=True)
    for i,r in enumerate(out,2):
        for c,v in enumerate([i-1,r["name"],r["register_no"],r["department"],r["year"],r["tasks_completed"],r["points"]],1):
            ws.cell(row=i,column=c,value=v)
    bio=io.BytesIO(); wb.save(bio); bio.seek(0)
    return send_file(bio,as_attachment=True,download_name="task_points.xlsx",mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.get("/api/admin/report-psprogress/export")
@admin_required
def admin_report_psprogress_export():
    sort=request.args.get("sort","rank"); dept=request.args.get("dept") or None
    db=get_db_connection()
    students=db.execute("SELECT id,name,register_no,department,year FROM students WHERE account_status='Active' ORDER BY name").fetchall()
    done=set()
    for r in db.execute("""SELECT DISTINCT student_id, level FROM ps_progress
        WHERE status='Completed' AND verification_status='Verified'""").fetchall():
        done.add((r["student_id"], normalize_level(r["level"])))
    for r in db.execute("""SELECT student_id, level FROM progress_requests WHERE status='Approved'""").fetchall():
        done.add((r["student_id"], normalize_level(r["level"])))
    db.close()
    out=[]
    for s in students:
        if dept and s["department"]!=dept: continue
        tiers=[l for l in range(1,5) if (s["id"],l) in done]
        out.append({"name":s["name"],"register_no":s["register_no"],"department":s["department"],"year":s["year"],"levels_completed":len(tiers),"tiers":[(s["id"],l) in done for l in range(1,5)]})
    if sort=="alpha": out.sort(key=lambda x:x["name"].lower())
    elif sort=="dept": out.sort(key=lambda x:(x["department"],x["name"].lower()))
    else: out.sort(key=lambda x:(-x["levels_completed"],x["name"].lower()))
    green=PatternFill("solid",fgColor="C6EFCE"); red=PatternFill("solid",fgColor="FFC7CE")
    wb=Workbook(); ws=wb.active; ws.title="PS Progress"
    headers=["Rank","Name","Register No","Department","Year","Levels Completed","L1","L2","L3","L4"]
    for c,h in enumerate(headers,1): ws.cell(row=1,column=c,value=h).font=Font(bold=True)
    for i,r in enumerate(out,2):
        vals=[i-1,r["name"],r["register_no"],r["department"],r["year"],r["levels_completed"]]
        for c,v in enumerate(vals,1): ws.cell(row=i,column=c,value=v)
        for j,ok in enumerate(r["tiers"]):
            cell=ws.cell(row=i,column=7+j,value="✓" if ok else "✗"); cell.fill=green if ok else red
    bio=io.BytesIO(); wb.save(bio); bio.seek(0)
    return send_file(bio,as_attachment=True,download_name="ps_progress.xlsx",mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.get("/api/admin/report-psprogress")
@admin_required
def admin_report_psprogress():
    sort=request.args.get("sort","rank"); dept=request.args.get("dept") or None
    db=get_db_connection()
    students=db.execute("SELECT id,name,register_no,department,year FROM students WHERE account_status='Active' ORDER BY name").fetchall()
    done=set()
    for r in db.execute("""SELECT DISTINCT student_id, level FROM ps_progress
        WHERE status='Completed' AND verification_status='Verified'""").fetchall():
        done.add((r["student_id"], normalize_level(r["level"])))
    for r in db.execute("""SELECT student_id, level FROM progress_requests WHERE status='Approved'""").fetchall():
        done.add((r["student_id"], normalize_level(r["level"])))
    db.close()
    out=[]
    for s in students:
        if dept and s["department"]!=dept: continue
        tiers=[l for l in range(1,5) if (s["id"],l) in done]
        out.append({"id":s["id"],"name":s["name"],"register_no":s["register_no"],"department":s["department"],"year":s["year"],
                    "levels_completed":len(tiers),"tiers":[{"level":l,"done":(s["id"],l) in done} for l in range(1,5)]})
    if sort=="alpha": out.sort(key=lambda x:x["name"].lower())
    elif sort=="dept": out.sort(key=lambda x:(x["department"],x["name"].lower()))
    else: out.sort(key=lambda x:(-x["levels_completed"],x["name"].lower()))
    for i,r in enumerate(out,1): r["rank"]=i
    return jsonify(rows=out)


# ---------------- PS Slot booking + daily report register ----------------
LOCAL_TZ = ZoneInfo("Asia/Kolkata")
SLOT_CAPACITY = 25


def local_now():
    return datetime.now(LOCAL_TZ)


def local_today():
    return local_now().date()


def slot_settings_row():
    db = get_db_connection()
    r = db.execute("SELECT * FROM slot_settings WHERE id=1").fetchone()
    db.close()
    return r or {"id": 1, "open_time": "18:00"}


def slot_target_date():
    return local_today() + timedelta(days=1)


def slot_ensure_target():
    open_time = slot_settings_row()["open_time"]
    today = local_today().isoformat(); target = slot_target_date().isoformat()
    db = get_db_connection()
    db.execute("INSERT INTO slot_days(date,open_time,open) VALUES(?,?,1) ON CONFLICT (date) DO NOTHING", (target, open_time))
    db.execute("INSERT INTO slot_days(date,open_time,open) VALUES(?,?,1) ON CONFLICT (date) DO NOTHING", (today, open_time))
    db.commit(); db.close()


def slot_day_open(target_iso):
    db = get_db_connection()
    r = db.execute("SELECT open FROM slot_days WHERE date=?", (target_iso,)).fetchone()
    db.close()
    return bool(r and r["open"])


def slot_booked_count(target_iso):
    db = get_db_connection()
    r = db.execute("SELECT COUNT(*) n FROM slot_bookings WHERE slot_date=?", (target_iso,)).fetchone()
    db.close()
    return r["n"] if r else 0


def slot_valid_days():
    db = get_db_connection()
    rows = db.execute("SELECT date,open FROM slot_days WHERE date<=? ORDER BY date DESC", (local_today().isoformat(),)).fetchall()
    db.close()
    return rows_dict(rows)


@app.get("/api/ps-leaderboard")
@student_required
def ps_leaderboard():
    data = leaderboard_data()
    if request.args.get("all") != "1":
        data = data[:10]
    return jsonify(leaderboard=data)


@app.get("/api/slot/status")
@student_required
def slot_status():
    sid = session["student_id"]; st = slot_settings_row(); target = slot_target_date().isoformat()
    slot_ensure_target()
    open_time = st["open_time"]; now = local_now()
    window_open = now.strftime("%H:%M") >= open_time and slot_day_open(target)
    count = slot_booked_count(target)
    today = local_today().isoformat()
    db = get_db_connection()
    bookings = db.execute("""SELECT b.id,b.slot_date,b.course,b.level,r.status report_status,r.score report_score
        FROM slot_bookings b LEFT JOIN slot_reports r ON r.booking_id=b.id
        WHERE b.student_id=? ORDER BY b.slot_date DESC""", (sid,)).fetchall(); db.close()
    blocked = any(b["slot_date"] <= today and not b["report_status"] for b in bookings)
    return jsonify(open_time=open_time, target_date=target, now=now.strftime("%Y-%m-%d %H:%M"),
                   window_open=bool(window_open), day_open=slot_day_open(target),
                   booked=count, capacity=SLOT_CAPACITY, remaining=SLOT_CAPACITY - count,
                   blocked=blocked, bookings=rows_dict(bookings),
                   my_booking=row_dict(next((b for b in bookings if b["slot_date"] == target), None)),
                   today_booking=row_dict(next((b for b in bookings if b["slot_date"] == today), None)))


@app.post("/api/slot/book")
@student_required
def slot_book():
    d = request.get_json(silent=True) or {}
    course = normalize_course(str(d.get("course", ""))); level = normalize_level(d.get("level"))
    if course not in COURSES: return jsonify(message="Select a valid course."), 400
    if not level or not (1 <= level <= COURSES[course]): return jsonify(message="Select a valid level."), 400
    sid = session["student_id"]; target = slot_target_date().isoformat(); st = slot_settings_row()
    slot_ensure_target()
    if local_now().strftime("%H:%M") < st["open_time"]:
        return jsonify(message=f"Slot booking opens at {st['open_time']} for tomorrow's slot."), 403
    if not slot_day_open(target): return jsonify(message="Booking for this slot was cancelled by Admin."), 403
    if slot_booked_count(target) >= SLOT_CAPACITY: return jsonify(message="This slot is full (25/25)."), 409
    db = get_db_connection()
    blocked = db.execute("""SELECT b.id FROM slot_bookings b LEFT JOIN slot_reports r ON r.booking_id=b.id
        WHERE b.student_id=? AND b.slot_date<=? AND r.id IS NULL LIMIT 1""", (sid, local_today().isoformat())).fetchone()
    if blocked: db.close(); return jsonify(message="Report your previous booked slot before booking a new one."), 409
    dup = db.execute("SELECT id FROM slot_bookings WHERE student_id=? AND slot_date=?", (sid, target)).fetchone()
    if dup: db.close(); return jsonify(message="You already booked this slot."), 409
    db.execute("INSERT INTO slot_bookings(student_id,slot_date,course,level) VALUES(?,?,?,?)", (sid, target, course, f"L{level}"))
    db.commit(); db.close()
    audit("Booked PS slot", sid, f"{course} L{level} · {target}")
    return jsonify(message=f"Slot booked for {target}. Report after attending.")


@app.post("/api/slot/report")
@student_required
def slot_report():
    d = request.get_json(silent=True) or {}
    booking_id = d.get("booking_id"); status = str(d.get("status", "")).strip().lower()
    if status not in {"completed", "not_completed", "not_attended"}: return jsonify(message="Select a valid status."), 400
    try: score = int(d.get("score", 0))
    except Exception: score = 0
    if score < 0 or score > 100: return jsonify(message="Score must be between 0 and 100."), 400
    sid = session["student_id"]; db = get_db_connection()
    b = db.execute("SELECT * FROM slot_bookings WHERE id=? AND student_id=?", (booking_id, sid)).fetchone()
    if not b: db.close(); return jsonify(message="Booking not found."), 404
    if db.execute("SELECT id FROM slot_reports WHERE booking_id=?", (booking_id,)).fetchone():
        db.close(); return jsonify(message="You already reported this slot."), 409
    if b["slot_date"] > local_today().isoformat(): db.close(); return jsonify(message="You can only report after the slot date."), 409
    db.execute("INSERT INTO slot_reports(booking_id,student_id,slot_date,status,score) VALUES(?,?,?,?,?)",
               (booking_id, sid, b["slot_date"], status, score))
    ps_status = "Completed" if status == "completed" else ("Not Completed" if status == "not_completed" else "Did Not Attend")
    ver = "Verified" if status == "completed" else "Pending"
    ts = "to_char(now() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS')"
    exists = db.execute("SELECT id FROM ps_progress WHERE student_id=? AND date=? AND course=? AND level=?",
                        (sid, b["slot_date"], b["course"], b["level"])).fetchone()
    if exists:
        db.execute("UPDATE ps_progress SET status=?,score=?,verification_status=?,source='Slot Report',updated_at=" + ts + " WHERE id=?",
                   (ps_status, score, ver, exists["id"]))
    else:
        db.execute("INSERT INTO ps_progress(student_id,date,course,level,status,attempt_no,score,verification_status,source) VALUES(?,?,?,?,?,1,?,?,?)",
                   (sid, b["slot_date"], b["course"], b["level"], ps_status, score, ver, "Slot Report"))
    db.commit(); db.close()
    audit("Reported PS slot", sid, f"{b['course']} L{b['level']} · {b['slot_date']} · {status}")
    return jsonify(message="Report saved. You can book the next slot.")


@app.get("/api/ps-monthly")
@student_required
def ps_monthly():
    sid = session["student_id"]
    m = request.args.get("month", date.today().strftime("%Y-%m"))
    try:
        parts = m.split("-")
        yr, mo = int(parts[0]), int(parts[1])
    except Exception:
        yr, mo = date.today().year, date.today().month
        m = date.today().strftime("%Y-%m")
    import calendar
    _, days_in_mo = calendar.monthrange(yr, mo)
    start_date = f"{m}-01"
    end_date = f"{m}-{days_in_mo:02d}"
    db = get_db_connection()
    rows = db.execute("""SELECT id, date, course, level, status, score, verification_status
        FROM ps_progress WHERE student_id=? AND date BETWEEN ? AND ?
        ORDER BY date ASC, id ASC""", (sid, start_date, end_date)).fetchall()
    db.close()
    daily = {}
    for r in rows:
        d_str = (r["date"] or "")[:10]
        if d_str not in daily: daily[d_str] = []
        daily[d_str].append(dict(r))
    return jsonify(month=m, days_in_month=days_in_mo, daily=daily, total_records=len(rows))


@app.get("/api/clearance-history")
@student_required
def clearance_history():
    sid = session["student_id"]; db = get_db_connection()
    cleared = db.execute("""SELECT course,level,MIN(date) date FROM ps_progress
        WHERE student_id=? AND status='Completed' AND verification_status='Verified' GROUP BY course,level""", (sid,)).fetchall()
    reqs = db.execute("SELECT course,level,created_at date FROM progress_requests WHERE student_id=? AND status='Approved'", (sid,)).fetchall()
    pending = db.execute("""SELECT DISTINCT course,level,MIN(date) date FROM ps_progress
        WHERE student_id=? AND verification_status='Pending' GROUP BY course,level""", (sid,)).fetchall()
    preq = db.execute("SELECT course,level,created_at date FROM progress_requests WHERE student_id=? AND status='Pending'", (sid,)).fetchall()
    db.close()
    cleared_map = {}
    for r in list(cleared) + list(reqs):
        c = normalize_course(r["course"]) or r["course"]; l = normalize_level(r["level"])
        d = (r["date"] or "")[:10]
        if c in COURSES and l and 1 <= l <= COURSES[c]:
            key = (c, l)
            if key not in cleared_map or d < cleared_map[key]: cleared_map[key] = d
    cleared_out = [{"course": c, "level": f"L{l}", "date": d} for (c, l), d in sorted(cleared_map.items(), key=lambda x: (x[1], x[0][0]))]
    pending_out = []
    for r in list(pending) + list(preq):
        c = normalize_course(r["course"]) or r["course"]; l = normalize_level(r["level"])
        d = (r["date"] or "")[:10]
        if c in COURSES and l and 1 <= l <= COURSES[c] and (c, l) not in cleared_map:
            pending_out.append({"course": c, "level": f"L{l}", "date": d})
    seen = set()
    uniq_pending = []
    for p in pending_out:
        k = (p["course"], p["level"])
        if k not in seen: seen.add(k); uniq_pending.append(p)
    return jsonify(cleared=cleared_out, pending=uniq_pending)


@app.get("/api/admin/slots/settings")
@admin_required
def admin_slots_settings():
    st = slot_settings_row(); target = slot_target_date().isoformat()
    slot_ensure_target()
    return jsonify(settings=dict(st), target_date=target, open_time=st["open_time"],
                   window_open=bool(slot_day_open(target)), booked=slot_booked_count(target),
                   capacity=SLOT_CAPACITY, days=slot_valid_days())


@app.post("/api/admin/slots/settings")
@admin_required
def admin_slots_settings_update():
    d = request.get_json(silent=True) or {}; db = get_db_connection()
    open_time = slot_settings_row()["open_time"]
    if "open_time" in d:
        ot = str(d["open_time"]).strip()
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", ot):
            db.close(); return jsonify(message="Time must be HH:MM (24-hour format)."), 400
        db.execute("UPDATE slot_settings SET open_time=? WHERE id=1", (ot,))
        open_time = ot
        target = slot_target_date().isoformat()
        db.execute("INSERT INTO slot_days(date,open_time,open) VALUES(?,?,1) ON CONFLICT (date) DO UPDATE SET open_time=excluded.open_time", (target, open_time))
    if d.get("cancel_today") is True:
        target = slot_target_date().isoformat()
        db.execute("INSERT INTO slot_days(date,open_time,open) VALUES(?,?,0) ON CONFLICT (date) DO UPDATE SET open=0", (target, open_time))
    if d.get("reopen_today") is True:
        target = slot_target_date().isoformat()
        db.execute("INSERT INTO slot_days(date,open_time,open) VALUES(?,?,1) ON CONFLICT (date) DO UPDATE SET open=1", (target, open_time))
    db.commit(); db.close()
    audit("Updated slot settings", None, f"open_time={open_time} cancel={bool(d.get('cancel_today'))}")
    return jsonify(message="Slot settings updated.")


@app.get("/api/admin/slots/register")
@admin_required
def admin_slots_register():
    ddate = request.args.get("date") or local_today().isoformat()
    if not valid_date(ddate): return jsonify(message="Invalid date."), 400
    db = get_db_connection()
    students = db.execute("SELECT id,name,register_no,department,year FROM students WHERE account_status='Active' ORDER BY name").fetchall()
    booked = db.execute("""SELECT b.student_id,b.course,b.level,r.status,r.score FROM slot_bookings b
        LEFT JOIN slot_reports r ON r.booking_id=b.id WHERE b.slot_date=?""", (ddate,)).fetchall()
    bmap = {r["student_id"]: r for r in booked}; db.close()
    rows = []
    for s in students:
        rec = bmap.get(s["id"])
        rows.append({
            "name": s["name"], "register_no": s["register_no"], "department": s["department"], "year": s["year"],
            "booked": bool(rec),
            "course": rec["course"] if rec else "", "level": rec["level"] if rec else "",
            "status": rec["status"] if rec and rec["status"] else "",
            "score": rec["score"] if rec and rec["status"] else "",
            "no_show": bool(rec and (rec["status"] == "not_attended" or not rec["status"]))
        })
    return jsonify(date=ddate, rows=rows, total=len(rows), booked_count=sum(1 for r in rows if r["booked"]),
                   reported=sum(1 for r in rows if r["status"]))


@app.get("/api/admin/slots/noshows")
@admin_required
def admin_slots_noshows():
    ddate = request.args.get("date") or local_today().isoformat()
    if not valid_date(ddate): return jsonify(message="Invalid date."), 400
    db = get_db_connection()
    rows = db.execute("""SELECT s.name,s.register_no,b.course,b.level
        FROM slot_bookings b JOIN students s ON s.id=b.student_id
        LEFT JOIN slot_reports r ON r.booking_id=b.id
        WHERE b.slot_date=? AND r.id IS NULL ORDER BY s.name""", (ddate,)).fetchall()
    db.close()
    names = [r["name"] for r in rows]
    msg = (f"⚠️ PS SLOT NOTICE – {ddate}\n"
           f"You have not reported your booked 3:10-4:10 PS slot. Until you report it, "
           f"you are BLOCKED from booking the next slot. Please report now:\n\n" +
           "\n".join(f"• {n}" for n in names))
    from urllib.parse import quote
    return jsonify(date=ddate, students=rows_dict(rows), names=names, message=msg, wa_link="https://wa.me/?text=" + quote(msg))


@app.get("/api/admin/slots/export")
@admin_required
def admin_slots_export():
    scope = request.args.get("scope", "day"); ref = request.args.get("date") or local_today().isoformat()
    if scope not in {"day", "week", "all"}: return jsonify(message="Invalid scope."), 400
    if not valid_date(ref): return jsonify(message="Invalid date."), 400
    if scope == "day":
        dates = [ref]
    elif scope == "week":
        d0 = valid_date(ref)
        dates = [(d0 - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
    else:
        db = get_db_connection()
        rows = db.execute("SELECT date FROM slot_days WHERE open=1 AND date<=? ORDER BY date", (local_today().isoformat(),)).fetchall()
        db.close(); dates = [r["date"] for r in rows]
    if not dates: return jsonify(message="No valid days to export."), 400
    green = PatternFill("solid", fgColor="C6EFCE"); red = PatternFill("solid", fgColor="FFC7CE"); amber = PatternFill("solid", fgColor="FFEB9C")
    head_fill = PatternFill("solid", fgColor="333333"); head_font = Font(bold=True, color="FFFFFF")
    wb = Workbook(); wb.remove(wb.active)
    for d in dates:
        db = get_db_connection()
        students = db.execute("SELECT id,name,register_no,department,year FROM students WHERE account_status='Active' ORDER BY name").fetchall()
        booked = db.execute("""SELECT b.student_id,b.course,b.level,r.status,r.score FROM slot_bookings b
            LEFT JOIN slot_reports r ON r.booking_id=b.id WHERE b.slot_date=?""", (d,)).fetchall()
        db.close()
        bmap = {r["student_id"]: r for r in booked}
        ws = wb.create_sheet(title=d)
        headers = ["Name", "Register No", "Department", "Year", "Course", "Level", "Status", "Score"]
        for c, h in enumerate(headers, 1):
            cell = ws.cell(row=1, column=c, value=h)
            cell.font = head_font; cell.fill = head_fill
        r_i = 2
        for s in students:
            rec = bmap.get(s["id"])
            status = rec["status"] if rec and rec["status"] else ""
            score = rec["score"] if rec and rec["status"] else ""
            vals = [s["name"], s["register_no"], s["department"], s["year"],
                    rec["course"] if rec else "", rec["level"] if rec else "", status, score if score != "" else ""]
            for c, v in enumerate(vals, 1):
                ws.cell(row=r_i, column=c, value=v)
            if status:
                fill = green if status == "completed" else (red if status == "not_attended" else amber)
                ws.cell(row=r_i, column=7).fill = fill
            r_i += 1
        widths = [28, 16, 14, 12, 22, 10, 16, 8]
        for i, w in enumerate(widths, 1): ws.column_dimensions[get_column_letter(i)].width = w
    bio = io.BytesIO(); wb.save(bio); bio.seek(0)
    fname = f"ps_register_{scope}_{local_today().isoformat()}.xlsx"
    return send_file(bio, as_attachment=True, download_name=fname, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


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
    except Exception as e:
        # Handle both Postgres UniqueViolation and SQLite IntegrityError
        msg=str(e).lower()
        if "unique" in msg or "already exists" in msg:
            db.close();return jsonify(message="Email or register number already exists."),409
        db.close();raise
    db.close();return jsonify(message="Account created successfully. Your profile is ready.")


@app.post("/api/login")
def login():
    d=request.get_json(silent=True) or {};email=str(d.get("email","")).strip().lower();password=str(d.get("password",""))
    db=get_db_connection();s=db.execute("SELECT * FROM students WHERE LOWER(email)=?",(email,)).fetchone();db.close()
    if not s or not check_password_hash(s["password_hash"],password): return jsonify(message="Invalid email or password."),401
    if s["account_status"]!="Active": return jsonify(message="Your account is inactive. Please contact Admin."),403
    session.clear();session["role"]="student";session["student_id"]=s["id"];session["account_status"]=s["account_status"]
    session.permanent=bool(d.get("remember"))
    return jsonify(message="Login successful.",student={k:s[k] for k in ["id","email","register_no","name","course","batch","mobile","program","department","year"]})


@app.post("/api/admin/login")
def admin_login():
    d=request.get_json(silent=True) or {}
    if str(d.get("email","")).strip().lower()==ADMIN_EMAIL and str(d.get("password",""))==ADMIN_PASSWORD:
        session.clear();session["role"]="admin";session.permanent=bool(d.get("remember"));return jsonify(message="Admin login successful.")
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
    return jsonify(student=current_student(),planners=rows_dict(plans),ps=rows_dict(ps),requests=rows_dict(req),stats={**calculate_stats(sid),"streak":calculate_streak(sid),"activity_points":activity_points_total(sid),"task_points":task_points_for(sid)})


@app.post("/api/planner")
@student_required
def save_planner():
    d=request.get_json(silent=True) or {}
    vals=[str(d.get(f"h{i}",""))[:120].strip() for i in range(1,8)]
    venues=[str(d.get(f"v{i}",""))[:120].strip() for i in range(1,8)]
    for i in range(1,8):
        if not vals[i-1]: return jsonify(message=f"H{i} needs a task."),400
        if not venues[i-1]: return jsonify(message=f"H{i} needs a venue."),400
    dt=(date.today()+timedelta(days=1)).isoformat();db=get_db_connection()
    db.execute("""INSERT INTO planners(student_id,date,h1,h2,h3,h4,h5,h6,h7,v1,v2,v3,v4,v5,v6,v7) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                  ON CONFLICT(student_id,date) DO UPDATE SET h1=excluded.h1,h2=excluded.h2,h3=excluded.h3,h4=excluded.h4,h5=excluded.h5,h6=excluded.h6,h7=excluded.h7,v1=excluded.v1,v2=excluded.v2,v3=excluded.v3,v4=excluded.v4,v5=excluded.v5,v6=excluded.v6,v7=excluded.v7,updated_at=to_char(now() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS')""",(session["student_id"],dt,*vals,*venues));db.commit();db.close();audit("Updated daily planner",session["student_id"],dt);return jsonify(message="Today's plan saved ✓")


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
        db.execute("UPDATE ps_progress SET status=?,attempt_no=?,score=?,verification_status='Pending',source='Daily Report',updated_at=to_char(now() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS') WHERE id=?",(status,attempt,score,existing["id"])); action="updated"
    else:
        db.execute("INSERT INTO ps_progress(student_id,date,course,level,status,attempt_no,score,verification_status,source) VALUES(?,?,?,?,?,?,?,'Pending','Daily Report')",(session["student_id"],dt,course,f"L{level}",status,attempt,score));action="saved"
    db.commit();db.close();audit(f"Daily PS report {action}",session["student_id"],f"{course} L{level}");return jsonify(message=f"{course} L{level} {action}. Awaiting verification.")


@app.post("/api/progress-request")
@student_required
def progress_request():
    d=request.get_json(silent=True) or {};course=normalize_course(d.get("course"));level=normalize_level(d.get("level"));note=str(d.get("proof_note","")).strip()[:1000];link=str(d.get("proof_link","")).strip()[:500]
    if not course or not level or level<1 or level>COURSES[course]:return jsonify(message="Select a valid course and level."),400
    db=get_db_connection();exists=db.execute("SELECT id FROM progress_requests WHERE student_id=? AND course=? AND level=? AND status='Pending'",(session["student_id"],course,f"L{level}")).fetchone()
    if exists:db.close();return jsonify(message="That level already has a pending verification request."),409
    db.execute("INSERT INTO progress_requests(student_id,course,level,proof_note,proof_link) VALUES(?,?,?,?,?)",(session["student_id"],course,f"L{level}",note,link));db.commit();db.close();audit("Submitted previous PS progress for verification",session["student_id"],f"{course} L{level}");return jsonify(message="Verification request sent to Admin.")


@app.get("/api/dashboard")
@student_required
def dashboard():
    sid=session["student_id"];dt=date.today().isoformat();db=get_db_connection();planner=db.execute("SELECT * FROM planners WHERE student_id=? AND date=?",(sid,dt)).fetchone();tm=(date.today()+timedelta(days=1)).isoformat();tomorrow=db.execute("SELECT * FROM planners WHERE student_id=? AND date=?",(sid,tm)).fetchone();ps=db.execute("SELECT id,date,course,level,status,attempt_no,score,verification_status,source FROM ps_progress WHERE student_id=? AND date=? ORDER BY id DESC",(sid,dt)).fetchall();db.close();streak=calculate_streak(sid)
    return jsonify(today={"planner":row_dict(planner),"ps_progress":rows_dict(ps)},tomorrow=row_dict(tomorrow),stats={**calculate_stats(sid),"current_streak":streak["current"],"best_streak":streak["best"],"activity_points":activity_points_total(sid)})


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
    plans=db.execute("SELECT id,date,h1,h2,h3,h4,h5,h6,h7 FROM planners WHERE student_id=? ORDER BY date DESC",(s["id"],)).fetchall();ps=db.execute("SELECT id,date,course,level,status,attempt_no,score,verification_status,source FROM ps_progress WHERE student_id=? ORDER BY date DESC,id DESC",(s["id"],)).fetchall();req=db.execute("SELECT id,course,level,proof_note,proof_link,status,admin_note,created_at FROM progress_requests WHERE student_id=? ORDER BY id DESC",(s["id"],)).fetchall();db.close();st=calculate_stats(s["id"])
    act=admin_activity_points().get_json(); task=task_points_leaderboard()
    act_rank=next((x["rank"] for x in act["students"] if x["register_no"]==s["register_no"]),None)
    task_rank=next((x["rank"] for x in task if x["name"] and True),None)
    task_student=next((x for x in task if x["id"]==s["id"]),None)
    return jsonify(student=dict(s),planners=rows_dict(plans),ps_progress=rows_dict(ps),progress_requests=rows_dict(req),stats={**st,"current_streak":calculate_streak(s["id"])["current"],"best_streak":calculate_streak(s["id"])["best"]},
        points={"activity_points":activity_points_total(s["id"]),"activity_rank":act_rank,"task_points":task_student["points"] if task_student else 0,"task_rank":task_student["rank"] if task_student else None})


@app.get("/api/admin/students")
@admin_required
def admin_students():
    q=str(request.args.get("q","")).strip();db=get_db_connection();rows=db.execute("SELECT id,name,register_no,email,course,batch,mobile,program,department,year,account_status FROM students WHERE register_no LIKE ? COLLATE NOCASE OR name LIKE ? COLLATE NOCASE OR email LIKE ? COLLATE NOCASE OR department LIKE ? COLLATE NOCASE ORDER BY name LIMIT 100",(f"%{q}%",f"%{q}%",f"%{q}%",f"%{q}%")).fetchall();db.close();out=[]
    for s in rows:
        st=calculate_stats(s["id"]);sr=calculate_streak(s["id"]);x=dict(s);x.update(progress=st["overall_progress"],levels=st["completed_levels"],total_levels=st["total_levels"],streak=sr["current"]);out.append(x)
    return jsonify(students=out)


@app.get("/api/admin/course-matrix")
@admin_required
def admin_course_matrix():
    # Admin sees only courses with verified completed levels.
    db=get_db_connection();rows=db.execute("""SELECT course, COUNT(DISTINCT student_id || ':' || level) completed_levels,
        COUNT(DISTINCT student_id) students FROM ps_progress
        WHERE status='Completed' AND verification_status='Verified' GROUP BY course ORDER BY completed_levels DESC""").fetchall();db.close();return jsonify(courses=rows_dict(rows))


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
    db=get_db_connection();cur=db.execute("UPDATE support_requests SET status=?,admin_note=?,updated_at=to_char(now() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS') WHERE id=?",(status,note,rid));db.commit();db.close()
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
    db.execute("UPDATE ps_progress SET verification_status=?,updated_at=to_char(now() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS') WHERE id=?",(decision,record_id));db.commit();db.close()
    audit(f"Daily PS verification {decision}",r["student_id"],f"{r['course']} {r['level']} · {note}");return jsonify(message=f"Daily PS report {decision.lower()}.")

@app.get("/api/admin/verifications")
@admin_required
def admin_verifications():
    db=get_db_connection();rows=db.execute("""SELECT r.*,s.name,s.register_no,s.email FROM progress_requests r JOIN students s ON s.id=r.student_id
        ORDER BY CASE WHEN r.status='Pending' THEN 0 ELSE 1 END,r.id DESC""").fetchall();db.close();return jsonify(requests=rows_dict(rows))


@app.get("/api/admin/ps-progress")
@admin_required
def admin_ps_progress():
    view=str(request.args.get("view","dept")); dept=str(request.args.get("dept","")).strip(); limit=None
    if view=="top": limit=25
    elif view=="bottom": limit=15
    if view=="dept" and dept not in DEPARTMENTS:
        dept=DEPARTMENTS[0] if DEPARTMENTS else ""
    audit("Viewed PS progress report",None,f"view={view} dept={dept} limit={limit}")
    students=admin_performance_report(sort="performance",dept=(dept if view=="dept" else None),limit=limit)
    students=[{**s,"done":sorted(f"{c} L{l}" for c,l in s["done"])} for s in students]
    return jsonify(view=view,dept=(dept if view=="dept" else None),students=students)


@app.get("/api/admin/report")
@admin_required
def admin_report():
    sort=str(request.args.get("sort","performance")); sort="department" if sort!="performance" else "performance"
    audit("Generated overall report",None,f"sort={sort}")
    students=admin_performance_report(sort=sort)
    students=[{**s,"done":sorted(f"{c} L{l}" for c,l in s["done"])} for s in students]
    return jsonify(sort=sort,students=students)


@app.get("/api/admin/report/export")
@admin_required
def admin_report_export():
    sort=str(request.args.get("sort","performance")); sort="department" if sort!="performance" else "performance"
    students=admin_performance_report(sort=sort)
    audit("Exported overall report (Excel)",None,f"sort={sort} students={len(students)}")
    bio=build_report_xlsx(students)
    return send_file(bio,as_attachment=True,download_name="elite_overall_report.xlsx",mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.post("/api/admin/verifications/<int:rid>")
@admin_required
def review_verification(rid):
    d=request.get_json(silent=True) or {};decision=str(d.get("decision",""));note=str(d.get("admin_note","")).strip()[:1000]
    if decision not in {"Approved","Rejected","Correction Required"}:return jsonify(message="Invalid verification decision."),400
    db=get_db_connection();r=db.execute("SELECT * FROM progress_requests WHERE id=?",(rid,)).fetchone()
    if not r:db.close();return jsonify(message="Request not found."),404
    db.execute("UPDATE progress_requests SET status=?,admin_note=?,updated_at=to_char(now() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS') WHERE id=?",(decision,note,rid))
    if decision=="Approved":
        # Official completion is created/updated by Admin, never directly by the student.
        db.execute("""INSERT INTO ps_progress(student_id,date,course,level,status,attempt_no,score,verification_status,source)
            VALUES(?,?,?,?,? ,1,0,'Verified','Previous Progress')
            ON CONFLICT DO NOTHING""",(r["student_id"],date.today().isoformat(),r["course"],r["level"],"Completed"))
        # SQLite has no matching unique constraint; prevent duplicates explicitly.
        existing=db.execute("SELECT id FROM ps_progress WHERE student_id=? AND course=? AND level=? AND source='Previous Progress'",(r["student_id"],r["course"],r["level"])).fetchone()
        if existing: db.execute("UPDATE ps_progress SET status='Completed',verification_status='Verified',updated_at=to_char(now() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS') WHERE id=?",(existing["id"],))
    db.commit();db.close();audit(f"Progress verification {decision}",r["student_id"],f"Request {rid} {r['course']} {r['level']}");return jsonify(message=f"Verification {decision.lower()}.")


@app.post("/api/admin/student/<int:sid>/reset-password")
@admin_required
def reset_password(sid):
    pw=str((request.get_json(silent=True) or {}).get("password",""));
    if len(pw)<8:return jsonify(message="Password must contain at least 8 characters."),400
    db=get_db_connection();cur=db.execute("UPDATE students SET password_hash=?,updated_at=to_char(now() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS') WHERE id=?",(generate_password_hash(pw),sid));db.commit();db.close()
    if not cur.rowcount:return jsonify(message="Student not found."),404
    audit("Admin reset student password",sid,"Temporary password set by Admin");return jsonify(message="Password reset successfully.")


@app.post("/api/admin/student/<int:sid>/update-name")
@admin_required
def update_name(sid):
    name=str((request.get_json(silent=True) or {}).get("name","" )).strip().upper()
    if len(name)<2:return jsonify(message="Enter a valid name."),400
    db=get_db_connection();cur=db.execute("UPDATE students SET name=?,updated_at=to_char(now() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS') WHERE id=?",(name,sid));db.commit();db.close()
    if not cur.rowcount:return jsonify(message="Student not found."),404
    audit("Admin corrected student name",sid,name);return jsonify(message="Student name updated.")


@app.post("/api/admin/student/<int:sid>/update-academic")
@admin_required
def update_academic(sid):
    d=request.get_json(silent=True) or {};program=str(d.get("program",""));department=str(d.get("department",""));year=str(d.get("year",""));batch=str(d.get("batch",""))
    if program not in PROGRAMS or department not in DEPARTMENTS or year not in YEARS:return jsonify(message="Invalid academic details."),400
    db=get_db_connection();cur=db.execute("UPDATE students SET program=?,department=?,year=?,batch=?,updated_at=to_char(now() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS') WHERE id=?",(program,department,year,batch,sid));db.commit();db.close()
    if not cur.rowcount:return jsonify(message="Student not found."),404
    audit("Admin corrected academic details",sid,f"{program} {department} {year} {batch}");return jsonify(message="Academic details updated.")


@app.post("/api/admin/student/<int:sid>/status")
@admin_required
def account_status(sid):
    status=str((request.get_json(silent=True) or {}).get("status",""))
    if status not in {"Active","Inactive"}:return jsonify(message="Invalid account status."),400
    db=get_db_connection();cur=db.execute("UPDATE students SET account_status=?,updated_at=to_char(now() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS') WHERE id=?",(status,sid));db.commit();db.close()
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


# === DEV-SKIP-TEMP (remove before release) ===
DEV_SKIP = os.environ.get("ALLOW_DEV_SKIP", "1") != "0"
TEST_STUDENT_EMAIL = "test.student@fxec.edu"

@app.get("/api/dev/flag")
def dev_flag(): return jsonify(enabled=DEV_SKIP)

@app.post("/api/dev/skip-student")
def dev_skip_student():
    if not DEV_SKIP: return jsonify(message="Dev skip disabled."), 403
    db=get_db_connection()
    s=db.execute("SELECT * FROM students WHERE email=?",(TEST_STUDENT_EMAIL,)).fetchone()
    if not s: s=db.execute("SELECT * FROM students WHERE account_status='Active' ORDER BY id LIMIT 1").fetchone()
    db.close()
    if not s: return jsonify(message="No active student found."), 404
    session.clear(); session["role"]="student"; session["student_id"]=s["id"]; session["account_status"]=s["account_status"]
    return jsonify(message="Dev skip successful.",student={k:s[k] for k in ["id","email","register_no","name","course","batch","mobile","program","department","year"]})

@app.post("/api/dev/skip-admin")
def dev_skip_admin():
    if not DEV_SKIP: return jsonify(message="Dev skip disabled."), 403
    session.clear(); session["role"]="admin"
    return jsonify(message="Dev admin skip successful.")
# === END DEV-SKIP-TEMP ===


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
    host = os.environ.get("HOST", "127.0.0.1")
    print("="*58);print("ELITE STUDENT PROGRESS TRACKER");print(f"URL: http://{host}:{port}");print(f"Admin: {ADMIN_EMAIL} / {ADMIN_PASSWORD}");print("="*58)
    app.run(host=host,port=port)
