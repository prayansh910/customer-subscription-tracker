import os, io, csv, json, secrets, sqlite3
from pathlib import Path
from datetime import date, datetime, timedelta
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file, abort, session
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "subscriptions.db"
DATA_DIR.mkdir(exist_ok=True)

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
IS_POSTGRES = bool(DATABASE_URL)

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=IS_POSTGRES,
    MAX_CONTENT_LENGTH=8 * 1024 * 1024,
)

STATUSES = ["Active", "Expired", "Pending", "Inactive", "Removed"]
TABLES = ["customers", "subscriptions", "parent_accounts", "parent_subscriptions", "monthly_assignments"]

def db():
    if IS_POSTGRES:
        import psycopg
        from psycopg.rows import dict_row
        return psycopg.connect(DATABASE_URL, row_factory=dict_row)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def exec_sql(conn, sql, params=()):
    if IS_POSTGRES:
        # PostgreSQL uses %s placeholders; application SQL uses ? for readability.
        sql = sql.replace("?", "%s")
    return conn.execute(sql, params)

def init_db():
    schema = (BASE_DIR / ("schema_postgres.sql" if IS_POSTGRES else "schema.sql")).read_text(encoding="utf-8")
    conn = db()
    conn.execute(schema)
    conn.commit()
    conn.close()
    ensure_admin()

def ensure_admin():
    email = os.environ.get("ADMIN_EMAIL", "").strip().lower()
    password = os.environ.get("ADMIN_PASSWORD", "")
    if not email or not password:
        return
    conn = db()
    exists = exec_sql(conn, "SELECT id FROM users WHERE email=?", (email,)).fetchone()
    if not exists:
        exec_sql(conn, "INSERT INTO users(email,password_hash,is_active) VALUES(?,?,?)",
                 (email, generate_password_hash(password), True))
        conn.commit()
    conn.close()

def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login", next=request.path))
        return fn(*args, **kwargs)
    return wrapped

def csrf_token():
    token = session.get("_csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf"] = token
    return token

def csrf_input():
    return f'<input type="hidden" name="_csrf" value="{csrf_token()}">'
app.jinja_env.globals["csrf_token"] = csrf_token
app.jinja_env.globals["csrf_input"] = csrf_input

@app.before_request
def before_request():
    init_db()
    if request.method == "POST" and request.endpoint not in {"login"}:
        supplied = request.form.get("_csrf", "")
        if not supplied or not secrets.compare_digest(supplied, session.get("_csrf", "")):
            abort(400, description="Invalid or missing CSRF token.")

def today():
    return date.today().isoformat()

def month_key(value=None):
    return (value or date.today()).strftime("%Y-%m")

def parse_date(v):
    return datetime.strptime(v, "%Y-%m-%d").date() if v else None

@app.context_processor
def globals():
    return {"today": today(), "current_month": month_key(), "STATUSES": STATUSES, "datetime": datetime,
            "logged_in": bool(session.get("user_id")), "user_email": session.get("user_email"), "config": app.config}

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        conn = db()
        user = exec_sql(conn, "SELECT * FROM users WHERE email=? AND is_active=1", (email,)).fetchone()
        conn.close()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["user_email"] = user["email"]
            session["_csrf"] = secrets.token_urlsafe(32)
            return redirect(request.args.get("next") or url_for("dashboard"))
        flash("Invalid email or password.", "error")
    return render_template("login.html")

@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/")
@login_required
def dashboard():
    conn = db()
    total = exec_sql(conn, "SELECT COUNT(*) c FROM customers").fetchone()["c"]
    active = exec_sql(conn, "SELECT COUNT(*) c FROM customers WHERE subscription_end >= ?", (today(),)).fetchone()["c"]
    expired = exec_sql(conn, "SELECT COUNT(*) c FROM customers WHERE subscription_end < ?", (today(),)).fetchone()["c"]
    soon_date = (date.today() + timedelta(days=7)).isoformat()
    expiring = exec_sql(conn, "SELECT COUNT(*) c FROM customers WHERE subscription_end >= ? AND subscription_end <= ?",
                        (today(), soon_date)).fetchone()["c"]
    pending = exec_sql(conn, """
        SELECT COUNT(*) c FROM customers c
        WHERE NOT EXISTS (
            SELECT 1 FROM monthly_assignments ma
            WHERE ma.customer_id = c.id AND ma.status='Active'
              AND ma.customer_removed_date IS NULL
              AND ma.family_subscription_expiry_date >= ?
        )
    """, (today(),)).fetchone()["c"]
    parents = exec_sql(conn, "SELECT COUNT(*) c FROM parent_accounts WHERE status='Active'").fetchone()["c"]
    slot_rows = exec_sql(conn, """
        SELECT COALESCE(ps.capacity, pa.capacity) cap,
               (SELECT COUNT(*) FROM monthly_assignments ma
                WHERE ma.parent_subscription_id=ps.id AND ma.status='Active'
                  AND ma.customer_removed_date IS NULL
                  AND ma.family_subscription_expiry_date >= ?) members
        FROM parent_accounts pa
        LEFT JOIN parent_subscriptions ps ON ps.parent_id=pa.id
        WHERE pa.status='Active' AND ps.status='Active' AND ps.expiry_date >= ?
    """, (today(), today())).fetchall()
    slots = sum(max((r["cap"] or 0) - r["members"], 0) for r in slot_rows)
    recent = exec_sql(conn, """
        SELECT c.id,c.name,c.email,c.subscription_end,
               pa.email parent_email,ma.family_subscription_expiry_date family_expiry
        FROM customers c
        LEFT JOIN monthly_assignments ma ON ma.id=(
            SELECT id FROM monthly_assignments x WHERE x.customer_id=c.id
            AND x.status='Active' AND x.customer_removed_date IS NULL ORDER BY x.id DESC LIMIT 1)
        LEFT JOIN parent_subscriptions ps ON ps.id=ma.parent_subscription_id
        LEFT JOIN parent_accounts pa ON pa.id=ps.parent_id
        ORDER BY c.id DESC LIMIT 8
    """).fetchall()
    conn.close()
    return render_template("dashboard.html", total=total, active=active, expired=expired,
                           expiring=expiring, pending=pending, parents=parents, slots=slots, recent=recent)

@app.route("/customers")
@login_required
def customers():
    q=request.args.get("q","").strip()
    conn=db()
    sql="""
        SELECT c.*,pa.email parent_email,ma.family_subscription_expiry_date family_expiry
        FROM customers c
        LEFT JOIN monthly_assignments ma ON ma.id=(
            SELECT id FROM monthly_assignments x WHERE x.customer_id=c.id
            AND x.status='Active' AND x.customer_removed_date IS NULL ORDER BY x.id DESC LIMIT 1)
        LEFT JOIN parent_subscriptions ps ON ps.id=ma.parent_subscription_id
        LEFT JOIN parent_accounts pa ON pa.id=ps.parent_id
    """
    if q:
        sql += " WHERE c.name LIKE ? OR c.phone LIKE ? OR c.email LIKE ? OR CAST(c.id AS TEXT)=?"
        rows=exec_sql(conn,sql+" ORDER BY c.id DESC",(f"%{q}%",f"%{q}%",f"%{q}%",q)).fetchall()
    else:
        rows=exec_sql(conn,sql+" ORDER BY c.id DESC").fetchall()
    conn.close()
    return render_template("customers.html",customers=rows,q=q)

@app.route("/customers/add",methods=["GET","POST"])
@login_required
def add_customer():
    if request.method=="POST":
        f=request.form
        conn=db()
        try:
            cur=exec_sql(conn, """
                INSERT INTO customers(name,phone,email,amount_paid,payment_date,subscription_start,subscription_end,plan_duration,status,notes)
                VALUES(?,?,?,?,?,?,?,?,?,?) RETURNING id
            """,(f["name"].strip(),f.get("phone","").strip(),f["email"].strip().lower(),float(f.get("amount_paid") or 0),
                 f.get("payment_date") or None,f.get("subscription_start") or None,f.get("subscription_end") or None,
                 f.get("plan_duration","").strip(),f.get("status","Active"),f.get("notes","").strip()))
            cid=cur.fetchone()["id"]
            if f.get("subscription_start") and f.get("subscription_end"):
                exec_sql(conn,"""INSERT INTO subscriptions(customer_id,start_date,end_date,plan_duration,amount_paid,payment_date,status,notes)
                    VALUES(?,?,?,?,?,?,?,?)""",(cid,f["subscription_start"],f["subscription_end"],f.get("plan_duration",""),
                    float(f.get("amount_paid") or 0),f.get("payment_date") or None,f.get("status","Active"),f.get("notes","").strip()))
            conn.commit(); conn.close()
            flash("Customer added.","success")
            return redirect(url_for("customer_detail",customer_id=cid))
        except Exception as e:
            conn.rollback(); conn.close()
            if "unique" in str(e).lower():
                flash("A customer with that email already exists.","error")
            else:
                raise
    return render_template("customer_form.html",customer=None)

@app.route("/customers/<int:customer_id>")
@login_required
def customer_detail(customer_id):
    conn=db()
    c=exec_sql(conn,"SELECT * FROM customers WHERE id=?",(customer_id,)).fetchone()
    if not c: abort(404)
    subs=exec_sql(conn,"SELECT * FROM subscriptions WHERE customer_id=? ORDER BY id DESC",(customer_id,)).fetchall()
    history=exec_sql(conn,"""SELECT ma.*,pa.email parent_email,ps.capacity
        FROM monthly_assignments ma JOIN parent_subscriptions ps ON ps.id=ma.parent_subscription_id
        JOIN parent_accounts pa ON pa.id=ps.parent_id WHERE ma.customer_id=? ORDER BY ma.id DESC""",(customer_id,)).fetchall()
    conn.close()
    current=history[0] if history and history[0]["status"]=="Active" and not history[0]["customer_removed_date"] and history[0]["family_subscription_expiry_date"]>=today() else None
    return render_template("customer_detail.html",customer=c,subscriptions=subs,history=history,current=current)

@app.route("/customers/<int:customer_id>/edit",methods=["GET","POST"])
@login_required
def edit_customer(customer_id):
    conn=db(); c=exec_sql(conn,"SELECT * FROM customers WHERE id=?",(customer_id,)).fetchone()
    if not c: conn.close(); abort(404)
    if request.method=="POST":
        f=request.form
        try:
            exec_sql(conn,"""UPDATE customers SET name=?,phone=?,email=?,amount_paid=?,payment_date=?,subscription_start=?,
                subscription_end=?,plan_duration=?,status=?,notes=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (f["name"].strip(),f.get("phone","").strip(),f["email"].strip().lower(),float(f.get("amount_paid") or 0),
                 f.get("payment_date") or None,f.get("subscription_start") or None,f.get("subscription_end") or None,
                 f.get("plan_duration","").strip(),f.get("status","Active"),f.get("notes","").strip(),customer_id))
            conn.commit(); conn.close(); flash("Customer updated.","success")
            return redirect(url_for("customer_detail",customer_id=customer_id))
        except Exception as e:
            conn.rollback(); conn.close(); flash("Could not update customer. Check that the email is unique.","error")
    conn.close()
    return render_template("customer_form.html",customer=c)

@app.route("/customers/<int:customer_id>/delete",methods=["POST"])
@login_required
def delete_customer(customer_id):
    conn=db()
    count=exec_sql(conn,"SELECT COUNT(*) c FROM monthly_assignments WHERE customer_id=?",(customer_id,)).fetchone()["c"]
    if count:
        conn.close(); flash("Customer has assignment history and cannot be deleted.","error")
        return redirect(url_for("customer_detail",customer_id=customer_id))
    exec_sql(conn,"DELETE FROM subscriptions WHERE customer_id=?",(customer_id,))
    exec_sql(conn,"DELETE FROM customers WHERE id=?",(customer_id,))
    conn.commit(); conn.close(); flash("Customer deleted (no assignment history existed).","success")
    return redirect(url_for("customers"))

@app.route("/parents")
@login_required
def parents():
    conn=db()
    rows=exec_sql(conn,"""SELECT pa.*,(SELECT COUNT(*) FROM parent_subscriptions ps WHERE ps.parent_id=pa.id) periods,
        (SELECT COUNT(*) FROM parent_subscriptions ps WHERE ps.parent_id=pa.id AND ps.status='Active' AND ps.expiry_date>=?) active_periods
        FROM parent_accounts pa ORDER BY pa.id DESC""",(today(),)).fetchall()
    conn.close(); return render_template("parents.html",parents=rows)

@app.route("/parents/add",methods=["GET","POST"])
@login_required
def add_parent():
    if request.method=="POST":
        f=request.form; conn=db()
        try:
            cur=exec_sql(conn,"INSERT INTO parent_accounts(email,capacity,status,notes) VALUES(?,?,?,?) RETURNING id",
                          (f["email"].strip().lower(),int(f.get("capacity") or 5),f.get("status","Active"),f.get("notes","").strip()))
            pid=cur.fetchone()["id"]; conn.commit(); conn.close()
            flash("Parent account added.","success"); return redirect(url_for("parent_detail",parent_id=pid))
        except Exception:
            conn.rollback(); conn.close(); flash("That parent email already exists.","error")
    return render_template("parent_form.html",parent=None)

@app.route("/parents/<int:parent_id>")
@login_required
def parent_detail(parent_id):
    conn=db(); p=exec_sql(conn,"SELECT * FROM parent_accounts WHERE id=?",(parent_id,)).fetchone()
    if not p: conn.close(); abort(404)
    periods=exec_sql(conn,"""SELECT ps.*,(SELECT COUNT(*) FROM monthly_assignments ma
        WHERE ma.parent_subscription_id=ps.id AND ma.status='Active'
        AND ma.customer_removed_date IS NULL AND ma.family_subscription_expiry_date>=?) members
        FROM parent_subscriptions ps WHERE ps.parent_id=? ORDER BY ps.id DESC""",(today(),parent_id)).fetchall()
    members=exec_sql(conn,"""SELECT c.*,ma.customer_added_date,ma.family_subscription_expiry_date,ma.month
        FROM monthly_assignments ma JOIN customers c ON c.id=ma.customer_id
        JOIN parent_subscriptions ps ON ps.id=ma.parent_subscription_id
        WHERE ps.parent_id=? AND ma.status='Active' AND ma.customer_removed_date IS NULL
        AND ma.family_subscription_expiry_date>=? ORDER BY ma.family_subscription_expiry_date""",(parent_id,today())).fetchall()
    conn.close(); return render_template("parent_detail.html",parent=p,periods=periods,members=members)

@app.route("/parents/<int:parent_id>/subscription/add",methods=["POST"])
@login_required
def add_parent_subscription(parent_id):
    f=request.form; conn=db()
    if not exec_sql(conn,"SELECT 1 FROM parent_accounts WHERE id=?",(parent_id,)).fetchone():
        conn.close(); abort(404)
    exec_sql(conn,"""INSERT INTO parent_subscriptions(parent_id,start_date,expiry_date,capacity,status,notes)
        VALUES(?,?,?,?,?,?)""",(parent_id,f["start_date"],f["expiry_date"],int(f.get("capacity") or 0) or None,
        f.get("status","Active"),f.get("notes","").strip()))
    conn.commit(); conn.close()
    flash("Parent subscription period added. Its expiry is stored exactly as entered.","success")
    return redirect(url_for("parent_detail",parent_id=parent_id))

@app.route("/assignments")
@login_required
def assignments():
    m=request.args.get("month",month_key()); q=request.args.get("q",""); conn=db()
    rows=exec_sql(conn,"""SELECT ma.*,c.name customer_name,c.email customer_email,c.phone,pa.email parent_email
        FROM monthly_assignments ma JOIN customers c ON c.id=ma.customer_id
        JOIN parent_subscriptions ps ON ps.id=ma.parent_subscription_id JOIN parent_accounts pa ON pa.id=ps.parent_id
        WHERE ma.month=? AND (?='' OR c.name LIKE ? OR c.email LIKE ? OR c.phone LIKE ? OR CAST(c.id AS TEXT)=? OR pa.email LIKE ?)
        ORDER BY ma.id DESC""",(m,q,f"%{q}%",f"%{q}%",f"%{q}%",q,f"%{q}%")).fetchall()
    conn.close(); return render_template("assignments.html",rows=rows,selected_month=m,q=q)

@app.route("/assign",methods=["GET","POST"])
@login_required
def assign_customer():
    conn=db()
    customers=exec_sql(conn,"SELECT id,name,email FROM customers ORDER BY name").fetchall()
    parents=exec_sql(conn,"""SELECT pa.id parent_id,pa.email,ps.id subscription_id,ps.start_date,ps.expiry_date,
        COALESCE(ps.capacity,pa.capacity) capacity,
        (SELECT COUNT(*) FROM monthly_assignments ma WHERE ma.parent_subscription_id=ps.id AND ma.status='Active'
        AND ma.customer_removed_date IS NULL AND ma.family_subscription_expiry_date>=?) members
        FROM parent_accounts pa JOIN parent_subscriptions ps ON ps.parent_id=pa.id
        WHERE pa.status='Active' AND ps.status='Active' AND ps.expiry_date>=?
        ORDER BY ps.expiry_date,pa.email""",(today(),today())).fetchall()
    if request.method=="POST":
        f=request.form; cid=int(f["customer_id"]); psid=int(f["parent_subscription_id"])
        ps=exec_sql(conn,"""SELECT ps.*,pa.email parent_email,COALESCE(ps.capacity,pa.capacity) cap
            FROM parent_subscriptions ps JOIN parent_accounts pa ON pa.id=ps.parent_id WHERE ps.id=?""",(psid,)).fetchone()
        if not ps:
            conn.close(); flash("Parent subscription not found.","error"); return redirect(url_for("assign_customer"))
        existing=exec_sql(conn,"""SELECT ma.id FROM monthly_assignments ma WHERE ma.customer_id=? AND ma.status='Active'
            AND ma.customer_removed_date IS NULL AND ma.family_subscription_expiry_date>=?""",(cid,today())).fetchone()
        if existing:
            conn.close(); flash("Customer already has a current active assignment. Use Change Parent from the customer profile.","error")
            return redirect(url_for("customer_detail",customer_id=cid))
        members=exec_sql(conn,"""SELECT COUNT(*) c FROM monthly_assignments
            WHERE parent_subscription_id=? AND status='Active' AND customer_removed_date IS NULL
            AND family_subscription_expiry_date>=?""",(psid,today())).fetchone()["c"]
        if members >= ps["cap"]:
            conn.close(); flash("That parent subscription has no available slots.","error")
            return redirect(url_for("assign_customer"))
        added=f["customer_added_date"]
        exec_sql(conn,"""INSERT INTO monthly_assignments
            (month,customer_id,parent_subscription_id,customer_added_date,family_subscription_start_date,
             family_subscription_expiry_date,status,notes) VALUES(?,?,?,?,?,?,?,?)""",
            (f.get("month") or month_key(parse_date(added)),cid,psid,added,ps["start_date"],ps["expiry_date"],"Active",f.get("notes","").strip()))
        conn.commit(); conn.close()
        flash(f"Assigned to {ps['parent_email']}. Family expires {ps['expiry_date']}.","success")
        return redirect(url_for("customer_detail",customer_id=cid))
    conn.close(); return render_template("assign_form.html",customers=customers,parent_options=parents,
                                         selected_customer=request.args.get("customer_id",""))

@app.route("/assignments/<int:assignment_id>/change",methods=["POST"])
@login_required
def change_assignment(assignment_id):
    f=request.form; conn=db()
    old=exec_sql(conn,"SELECT * FROM monthly_assignments WHERE id=?",(assignment_id,)).fetchone()
    if not old: conn.close(); abort(404)
    if old["customer_removed_date"] is not None:
        conn.close(); flash("This assignment has already been removed.","error")
        return redirect(url_for("customer_detail",customer_id=old["customer_id"]))
    removal=f.get("removed_date") or today()
    exec_sql(conn,"UPDATE monthly_assignments SET customer_removed_date=?,status='Removed',updated_at=CURRENT_TIMESTAMP WHERE id=?",
             (removal,assignment_id))
    psid=int(f["new_parent_subscription_id"])
    ps=exec_sql(conn,"""SELECT ps.*,pa.email parent_email,COALESCE(ps.capacity,pa.capacity) cap
        FROM parent_subscriptions ps JOIN parent_accounts pa ON pa.id=ps.parent_id WHERE ps.id=?""",(psid,)).fetchone()
    if not ps:
        conn.rollback(); conn.close(); flash("New parent subscription not found.","error")
        return redirect(url_for("customer_detail",customer_id=old["customer_id"]))
    members=exec_sql(conn,"""SELECT COUNT(*) c FROM monthly_assignments WHERE parent_subscription_id=?
        AND status='Active' AND customer_removed_date IS NULL AND family_subscription_expiry_date>=?""",(psid,today())).fetchone()["c"]
    if members >= ps["cap"]:
        conn.rollback(); conn.close(); flash("New parent subscription has no available slots.","error")
        return redirect(url_for("customer_detail",customer_id=old["customer_id"]))
    added=f.get("added_date") or today()
    exec_sql(conn,"""INSERT INTO monthly_assignments
        (month,customer_id,parent_subscription_id,customer_added_date,family_subscription_start_date,
         family_subscription_expiry_date,status,notes) VALUES(?,?,?,?,?,?,?,?)""",
        (f.get("month") or month_key(parse_date(added)),old["customer_id"],psid,added,ps["start_date"],ps["expiry_date"],"Active",f.get("notes","").strip()))
    conn.commit(); conn.close()
    flash("Parent changed. Previous assignment remains permanently in history.","success")
    return redirect(url_for("customer_detail",customer_id=old["customer_id"]))

@app.route("/history")
@login_required
def history():
    conn=db()
    rows=exec_sql(conn,"""SELECT ma.*,c.name customer_name,c.email customer_email,pa.email parent_email,ps.capacity
        FROM monthly_assignments ma JOIN customers c ON c.id=ma.customer_id
        JOIN parent_subscriptions ps ON ps.id=ma.parent_subscription_id JOIN parent_accounts pa ON pa.id=ps.parent_id
        ORDER BY ma.id DESC""").fetchall()
    conn.close(); return render_template("history.html",rows=rows)

def full_backup_payload():
    conn=db()
    payload={"format":"subtrack-json-v1","created_at":datetime.utcnow().isoformat()+"Z","tables":{}}
    for table in TABLES:
        rows=exec_sql(conn,f"SELECT * FROM {table} ORDER BY id").fetchall()
        payload["tables"][table]=[dict(r) for r in rows]
    conn.close()
    return payload

@app.route("/backup")
@login_required
def backup_settings():
    return render_template("backup.html")

@app.route("/backup/download")
@login_required
def backup_download():
    payload=json.dumps(full_backup_payload(),indent=2,ensure_ascii=False,default=str).encode("utf-8")
    bio=io.BytesIO(payload); bio.seek(0)
    return send_file(bio,mimetype="application/json",as_attachment=True,
                     download_name=f"subtrack_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")

@app.route("/backup/restore",methods=["POST"])
@login_required
def backup_restore():
    upload=request.files.get("backup_file")
    if not upload or not upload.filename.lower().endswith(".json"):
        flash("Please select a SubTrack JSON backup file.","error")
        return redirect(url_for("backup_settings"))
    try:
        payload=json.load(upload.stream)
        if payload.get("format")!="subtrack-json-v1": raise ValueError("Unsupported backup format")
        tables=payload["tables"]
        if set(TABLES)-set(tables): raise ValueError("Backup is missing required tables")
        conn=db()
        # Restore in dependency order. User data is never deleted automatically except during explicit restore.
        for table in reversed(TABLES):
            exec_sql(conn,f"DELETE FROM {table}")
        for table in TABLES:
            rows=tables[table]
            if not rows: continue
            columns=list(rows[0].keys())
            placeholders=",".join(["?"]*len(columns))
            colsql=",".join(columns)
            for row in rows:
                exec_sql(conn,f"INSERT INTO {table} ({colsql}) VALUES ({placeholders})",
                         tuple(row.get(c) for c in columns))
        if IS_POSTGRES:
            # Keep SERIAL sequences ahead of restored explicit IDs.
            for table in TABLES:
                exec_sql(conn, f"""SELECT setval(
                    pg_get_serial_sequence('{table}', 'id'),
                    GREATEST(COALESCE((SELECT MAX(id) FROM {table}), 1), 1),
                    true
                )""")
        conn.commit(); conn.close()
        flash("Database restored from the uploaded backup.","success")
    except Exception as e:
        try: conn.rollback(); conn.close()
        except Exception: pass
        flash(f"Restore failed: {e}","error")
    return redirect(url_for("backup_settings"))

def csv_response(filename,headers,rows):
    out=io.StringIO(); w=csv.writer(out); w.writerow(headers)
    for row in rows: w.writerow(row)
    bio=io.BytesIO(out.getvalue().encode("utf-8-sig")); bio.seek(0)
    return send_file(bio,mimetype="text/csv",as_attachment=True,download_name=filename)

@app.route("/export/<kind>")
@login_required
def export_csv(kind):
    conn=db()
    if kind=="customers":
        rows=exec_sql(conn,"SELECT id,name,phone,email,amount_paid,payment_date,subscription_start,subscription_end,plan_duration,status,notes,created_at FROM customers ORDER BY id").fetchall()
        headers=["id","name","phone","email","amount_paid","payment_date","subscription_start","subscription_end","plan_duration","status","notes","created_at"]
        filename="customers.csv"
    elif kind=="history":
        rows=exec_sql(conn,"""SELECT ma.id,ma.month,c.id customer_id,c.name,c.phone,c.email,pa.email parent_email,
            ma.customer_added_date,ma.customer_removed_date,ma.family_subscription_start_date,
            ma.family_subscription_expiry_date,ma.status,ma.notes,ma.created_at
            FROM monthly_assignments ma JOIN customers c ON c.id=ma.customer_id
            JOIN parent_subscriptions ps ON ps.id=ma.parent_subscription_id JOIN parent_accounts pa ON pa.id=ps.parent_id ORDER BY ma.id""").fetchall()
        headers=["id","month","customer_id","name","phone","email","parent_email","customer_added_date","customer_removed_date","family_subscription_start_date","family_subscription_expiry_date","status","notes","created_at"]
        filename="assignment_history.csv"
    elif kind=="month":
        m=request.args.get("month",month_key())
        rows=exec_sql(conn,"""SELECT ma.id,ma.month,c.id customer_id,c.name,c.phone,c.email,pa.email parent_email,
            ma.customer_added_date,ma.customer_removed_date,ma.family_subscription_start_date,ma.family_subscription_expiry_date,
            ma.status,ma.notes FROM monthly_assignments ma JOIN customers c ON c.id=ma.customer_id
            JOIN parent_subscriptions ps ON ps.id=ma.parent_subscription_id JOIN parent_accounts pa ON pa.id=ps.parent_id
            WHERE ma.month=? ORDER BY ma.id""",(m,)).fetchall()
        headers=["id","month","customer_id","name","phone","email","parent_email","customer_added_date","customer_removed_date","family_subscription_start_date","family_subscription_expiry_date","status","notes"]
        filename=f"assignments_{m}.csv"
    elif kind=="parents":
        rows=exec_sql(conn,"SELECT id,email,capacity,status,notes,created_at FROM parent_accounts ORDER BY id").fetchall()
        headers=["id","email","capacity","status","notes","created_at"]; filename="parent_accounts.csv"
    else:
        conn.close(); abort(404)
    conn.close()
    return csv_response(filename,headers,[tuple(r.values()) for r in rows])

@app.route("/api/parent-subscriptions/<int:parent_id>")
@login_required
def parent_subscription_api(parent_id):
    conn=db()
    rows=exec_sql(conn,"""SELECT ps.id,ps.start_date,ps.expiry_date,COALESCE(ps.capacity,pa.capacity) capacity,
        (SELECT COUNT(*) FROM monthly_assignments ma WHERE ma.parent_subscription_id=ps.id AND ma.status='Active'
        AND ma.customer_removed_date IS NULL AND ma.family_subscription_expiry_date>=?) members
        FROM parent_subscriptions ps JOIN parent_accounts pa ON pa.id=ps.parent_id
        WHERE ps.parent_id=? AND ps.status='Active' AND ps.expiry_date>=? ORDER BY ps.expiry_date""",(today(),parent_id,today())).fetchall()
    conn.close(); return jsonify([dict(r) for r in rows])

@app.errorhandler(400)
def bad_request(e):
    return render_template("error.html", code=400, message=str(e.description)), 400

if __name__=="__main__":
    init_db()
    port=int(os.environ.get("PORT",5000))
    app.run(host="0.0.0.0",port=port,debug=False)
