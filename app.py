"""
AKS Hospitality - Attendance Management System
Flask Backend — app.py

Install dependencies:
    pip install flask flask-cors pandas openpyxl

Run:
    python app.py

Folder structure expected:
    app.py
    templates/
        qr.html
        attendance.html
    attendance_data.csv   ← auto-created
    agent_colors.json     ← auto-created
"""

from flask import Flask, request, jsonify, render_template, send_file
from flask_cors import CORS
import hashlib
import time
import threading
import csv
import json
import os
import io
import socket
import base64
from datetime import datetime, timezone, timedelta
import pandas as pd
import qrcode

app = Flask(__name__)
CORS(app)

def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def get_base_url() -> str:
    """Return the public base URL — Render's URL in production, local IP otherwise."""
    render_url = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
    if render_url:
        return render_url
    port = int(os.environ.get("PORT", 5001))
    return f"http://{get_local_ip()}:{port}"

IST = timezone(timedelta(hours=5, minutes=30))

# ─── CONFIG ────────────────────────────────────────────────────────────────────
TOKEN_SECRET         = "SOME_SECRET_KEY_123"     # 🔒 Change this in production!
TOKEN_VALIDITY_SEC   = 30
DATA_FILE            = "attendance_data.csv"
COLORS_FILE          = "agent_colors.json"
AGENTS_FILE          = "agents.json"
ADMIN_PASSWORD       = "aks@2025"
PHOTOS_DIR           = os.path.join(os.path.dirname(os.path.abspath(__file__)), "photos")

# ─── IN-MEMORY TOKEN CACHE ──────────────────────────────────────────────────────
token_cache: dict[str, float] = {}   # token → expiry_timestamp
cache_lock  = threading.Lock()


# ─── TOKEN HELPERS ─────────────────────────────────────────────────────────────

def generate_token() -> str:
    """Generate a time-based SHA-256 token valid for TOKEN_VALIDITY_SEC seconds."""
    now       = int(time.time())
    time_slot = now // TOKEN_VALIDITY_SEC
    raw       = f"{TOKEN_SECRET}_{time_slot}"
    token     = hashlib.sha256(raw.encode()).hexdigest()

    with cache_lock:
        # clean expired tokens
        _purge_expired()
        token_cache[token] = time.time() + TOKEN_VALIDITY_SEC

    return token


def is_token_valid(token: str) -> bool:
    with cache_lock:
        _purge_expired()
        return token in token_cache


def mark_token_used(token: str):
    with cache_lock:
        token_cache.pop(token, None)


def _purge_expired():
    now = time.time()
    expired = [t for t, exp in token_cache.items() if exp <= now]
    for t in expired:
        del token_cache[t]


# ─── AGENT HELPERS ─────────────────────────────────────────────────────────────

def load_agents_data() -> list:
    """Returns list of agent dicts: {name, email, phone, pin}"""
    if os.path.exists(AGENTS_FILE):
        with open(AGENTS_FILE, "r") as f:
            data = json.load(f)
        if data and isinstance(data[0], str):
            migrated = [{"name": n, "email": "", "phone": "", "pin": "0000"} for n in data]
            save_agents_data(migrated)
            return migrated
        return data
    save_agents_data([])
    return []


def save_agents_data(agents: list):
    agents_sorted = sorted(agents, key=lambda x: x["name"])
    with open(AGENTS_FILE, "w") as f:
        json.dump(agents_sorted, f, indent=2)


def load_agents() -> list:
    """Returns just agent names — for templates and CSV logic."""
    return [a["name"] for a in load_agents_data()]


# ─── DATA HELPERS ──────────────────────────────────────────────────────────────

def ensure_csv():
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["TIMESTAMP", "AGENT", "ACTION", "LEAVE_DATE", "PHOTO", "SOURCE"])


def append_row(row: list):
    ensure_csv()
    with open(DATA_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(row)


def read_all_rows() -> list[dict]:
    ensure_csv()
    rows = []
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)
    return rows


def save_photo(agent: str, timestamp: str, photo_b64: str) -> str:
    os.makedirs(PHOTOS_DIR, exist_ok=True)
    if "," in photo_b64:
        photo_b64 = photo_b64.split(",", 1)[1]
    try:
        img_data = base64.b64decode(photo_b64)
    except Exception:
        return ""
    safe_ts  = timestamp.replace(":", "-").replace(".", "-")
    filename = f"{agent}_{safe_ts}.jpg"
    with open(os.path.join(PHOTOS_DIR, filename), "wb") as f:
        f.write(img_data)
    return filename



# ─── BUSINESS LOGIC ────────────────────────────────────────────────────────────

def get_last_action_today(agent: str) -> str | None:
    rows   = read_all_rows()
    today  = datetime.now(IST).date().isoformat()
    last   = None

    for row in rows:
        try:
            ts        = datetime.fromisoformat(row["TIMESTAMP"])
            row_date  = ts.date().isoformat()
            row_agent = row["AGENT"].strip().upper()

            if row_agent == agent.upper() and row_date == today:
                last = row["ACTION"].upper()
        except Exception:
            continue

    return last


def is_action_allowed(last_action: str | None, new_action: str) -> dict:
    last   = last_action.upper() if last_action else None
    new    = new_action.upper()

    if last is None:
        if new in ("LOGIN", "LEAVE"):
            return {"allowed": True}
        return {"allowed": False, "message": "❌ Please LOGIN first"}

    if last == "LOGIN":
        if new in ("BREAK", "LOGOUT"):
            return {"allowed": True}
        return {"allowed": False, "message": "❌ You are already logged in"}

    if last == "BREAK":
        if new == "RESUME":
            return {"allowed": True}
        return {"allowed": False, "message": "❌ You must RESUME before other actions"}

    if last == "RESUME":
        if new in ("BREAK", "LOGOUT"):
            return {"allowed": True}
        return {"allowed": False, "message": "❌ Invalid action after RESUME"}

    if last == "LOGOUT":
        return {"allowed": False, "message": "❌ You have already LOGGED OUT for today"}

    if last == "LEAVE":
        return {"allowed": False, "message": "❌ You are on LEAVE today"}

    return {"allowed": False, "message": "❌ Invalid action sequence"}


# ─── REPORT GENERATORS ─────────────────────────────────────────────────────────

def generate_daily_calc() -> list[dict]:
    rows = read_all_rows()
    mapping: dict[str, dict] = {}

    for r in rows:
        try:
            ts     = datetime.fromisoformat(r["TIMESTAMP"])
            agent  = r["AGENT"].strip()
            action = r["ACTION"].upper()
            if not agent:
                continue

            dt  = ts.date().strftime("%d-%m-%Y")
            key = f"{dt}_{agent}"

            if key not in mapping:
                mapping[key] = {
                    "date":   dt,
                    "agent":  agent,
                    "login":  None,
                    "logout": None,
                    "breaks": [],
                    "resume": [],
                }

            entry = mapping[key]
            if action == "LOGIN"  and not entry["login"]:  entry["login"]  = ts
            if action == "LOGOUT":                         entry["logout"] = ts
            if action == "BREAK":                          entry["breaks"].append(ts)
            if action == "RESUME":                         entry["resume"].append(ts)

        except Exception:
            continue

    daily_rows = []
    for d in mapping.values():
        break_minutes = 0
        for i, b in enumerate(d["breaks"]):
            if i < len(d["resume"]):
                break_minutes += (d["resume"][i] - b).total_seconds() / 60

        total_hours = ""
        day_type    = ""
        login_status = ""

        if d["login"] and d["logout"]:
            th = (d["logout"] - d["login"]).total_seconds() / 3600 - (break_minutes / 60)
            th = max(0.0, th)

            if th < 2:      day_type = "ABSENT"
            elif th < 4.5:  day_type = "SUBJECT TO MANAGEMENT APPROVAL"
            elif th < 5.5:  day_type = "HALF DAY"
            else:           day_type = "FULL DAY"

            login_hour = d["login"].hour + d["login"].minute / 60
            login_status = "LATE LOGIN" if login_hour > 13 else "OK"
            total_hours  = round(th, 2)

        daily_rows.append({
            "DATE":              d["date"],
            "AGENT":             d["agent"],
            "LOGIN_TIME":        str(d["login"])  if d["login"]  else "",
            "LOGOUT_TIME":       str(d["logout"]) if d["logout"] else "",
            "BREAK_MINUTES":     round(break_minutes, 2),
            "TOTAL_LOGIN_HOURS": total_hours,
            "DAY_TYPE":          day_type,
            "LOGIN_STATUS":      login_status,
        })

    daily_rows.sort(key=lambda x: (x["AGENT"], x["DATE"]))
    return daily_rows


def generate_monthly_summary(daily_rows: list[dict]) -> list[dict]:
    mapping: dict[str, dict] = {}

    MONTH_MAP = {
        1:"JAN", 2:"FEB", 3:"MAR", 4:"APR", 5:"MAY", 6:"JUN",
        7:"JUL", 8:"AUG", 9:"SEP", 10:"OCT", 11:"NOV", 12:"DEC"
    }

    for r in daily_rows:
        if not r["DATE"] or not r["AGENT"]:
            continue
        try:
            dd, mm, yyyy = r["DATE"].split("-")
            month = MONTH_MAP[int(mm)]
        except Exception:
            continue

        agent = r["AGENT"].strip()
        key   = f"{month}_{agent}"

        hours     = float(r["TOTAL_LOGIN_HOURS"]) if r["TOTAL_LOGIN_HOURS"] != "" else 0
        break_min = float(r["BREAK_MINUTES"])     if r["BREAK_MINUTES"]     != "" else 0
        day_type  = r["DAY_TYPE"].strip().upper()

        if key not in mapping:
            mapping[key] = {
                "month":        month,
                "agent":        agent,
                "hours":        0,
                "breaks":       0,
                "full":         0,
                "half":         0,
                "absent":       0,
                "approval":     0,
                "leaves":       0,
                "login_sum":    0,
                "login_count":  0,
                "logout_sum":   0,
                "logout_count": 0,
            }

        m = mapping[key]
        m["hours"]  += hours
        m["breaks"] += break_min

        if day_type == "FULL DAY":                          m["full"]     += 1
        elif day_type == "HALF DAY":                        m["half"]     += 1
        elif day_type == "ABSENT":                          m["absent"]   += 1
        elif day_type == "SUBJECT TO MANAGEMENT APPROVAL":  m["approval"] += 1

        if r["LOGIN_TIME"]:
            try:
                lt = datetime.fromisoformat(r["LOGIN_TIME"])
                m["login_sum"]   += lt.hour * 60 + lt.minute
                m["login_count"] += 1
            except Exception:
                pass

        if r["LOGOUT_TIME"]:
            try:
                lo = datetime.fromisoformat(r["LOGOUT_TIME"])
                m["logout_sum"]   += lo.hour * 60 + lo.minute
                m["logout_count"] += 1
            except Exception:
                pass

    # Add leaves from raw data
    for row in read_all_rows():
        if row["ACTION"].upper() == "LEAVE" and row["LEAVE_DATE"]:
            try:
                ld    = datetime.fromisoformat(row["LEAVE_DATE"])
                month = MONTH_MAP[ld.month]
                agent = row["AGENT"].strip()
                key   = f"{month}_{agent}"
                if key in mapping:
                    mapping[key]["leaves"] += 1
            except Exception:
                pass

    summary = []
    for m in mapping.values():
        days_present = m["full"]

        def fmt_time(mins, count):
            if not count:
                return ""
            avg = round(mins / count)
            return f"{avg // 60:02d}:{avg % 60:02d}"

        summary.append({
            "MONTH":                       m["month"],
            "AGENT":                       m["agent"],
            "TOTAL_WORKING_HOURS":         round(m["hours"],  2),
            "DAYS_PRESENT":                days_present,
            "AVG_HOURS_PER_DAY":           round(m["hours"] / days_present, 2) if days_present else 0,
            "LEAVES":                      m["leaves"],
            "BREAK_MINUTES":               round(m["breaks"], 2),
            "FULL_DAYS":                   m["full"],
            "HALF_DAYS":                   m["half"],
            "ABSENT_DAYS":                 m["absent"],
            "SUBJECT_TO_APPROVAL_DAYS":    m["approval"],
            "AVG_LOGIN_TIME":              fmt_time(m["login_sum"],  m["login_count"]),
            "AVG_LOGOUT_TIME":             fmt_time(m["logout_sum"], m["logout_count"]),
            "AVG_WORKING_HOURS":           round(m["hours"] / days_present, 2) if days_present else "",
            "AVG_BREAK_HOURS":             round(m["breaks"] / 60 / days_present, 2) if days_present else "",
        })

    summary.sort(key=lambda x: x["AGENT"])
    return summary


# ─── PERIOD SUMMARY (agent-level aggregation for any date range) ───────────────

def generate_period_summary(daily_rows: list[dict]) -> list[dict]:
    mapping: dict[str, dict] = {}

    for r in daily_rows:
        agent = r["AGENT"].strip()
        if not agent:
            continue
        if agent not in mapping:
            mapping[agent] = {
                "agent": agent, "hours": 0, "breaks": 0,
                "full": 0, "half": 0, "absent": 0, "approval": 0,
                "login_sum": 0, "login_count": 0,
                "logout_sum": 0, "logout_count": 0,
            }
        m = mapping[agent]
        hours     = float(r["TOTAL_LOGIN_HOURS"]) if r["TOTAL_LOGIN_HOURS"] != "" else 0
        break_min = float(r["BREAK_MINUTES"])     if r["BREAK_MINUTES"]     != "" else 0
        day_type  = r["DAY_TYPE"].strip().upper()
        m["hours"] += hours
        m["breaks"] += break_min
        if   day_type == "FULL DAY":                         m["full"]     += 1
        elif day_type == "HALF DAY":                         m["half"]     += 1
        elif day_type == "ABSENT":                           m["absent"]   += 1
        elif day_type == "SUBJECT TO MANAGEMENT APPROVAL":  m["approval"] += 1
        if r["LOGIN_TIME"]:
            try:
                lt = datetime.fromisoformat(r["LOGIN_TIME"])
                m["login_sum"]   += lt.hour * 60 + lt.minute
                m["login_count"] += 1
            except Exception: pass
        if r["LOGOUT_TIME"]:
            try:
                lo = datetime.fromisoformat(r["LOGOUT_TIME"])
                m["logout_sum"]   += lo.hour * 60 + lo.minute
                m["logout_count"] += 1
            except Exception: pass

    def fmt_time(mins, count):
        if not count: return ""
        avg = round(mins / count)
        return f"{avg // 60:02d}:{avg % 60:02d}"

    result = []
    for m in mapping.values():
        dp = m["full"]
        result.append({
            "AGENT":            m["agent"],
            "TOTAL_HOURS":      round(m["hours"], 2),
            "DAYS_PRESENT":     dp,
            "FULL_DAYS":        m["full"],
            "HALF_DAYS":        m["half"],
            "ABSENT_DAYS":      m["absent"],
            "APPROVAL_DAYS":    m["approval"],
            "BREAK_MINUTES":    round(m["breaks"], 2),
            "AVG_HOURS_PER_DAY": round(m["hours"] / dp, 2) if dp else 0,
            "AVG_LOGIN_TIME":   fmt_time(m["login_sum"],  m["login_count"]),
            "AVG_LOGOUT_TIME":  fmt_time(m["logout_sum"], m["logout_count"]),
        })
    result.sort(key=lambda x: x["AGENT"])
    return result


def filter_daily_rows(range_type: str, from_date: str = "", to_date: str = "") -> list[dict]:
    daily_calc = generate_daily_calc()
    today      = datetime.now(IST).date()

    if range_type == "today":
        target = today.strftime("%d-%m-%Y")
        return [r for r in daily_calc if r["DATE"] == target]

    if range_type == "monthly":
        mm, yyyy = today.strftime("%m"), str(today.year)
        return [r for r in daily_calc
                if r["DATE"].split("-")[1] == mm and r["DATE"].split("-")[2] == yyyy]

    if range_type == "quarterly":
        q        = (today.month - 1) // 3
        q_months = {str(q * 3 + i + 1).zfill(2) for i in range(3)}
        yyyy     = str(today.year)
        return [r for r in daily_calc
                if r["DATE"].split("-")[1] in q_months and r["DATE"].split("-")[2] == yyyy]

    if range_type == "yearly":
        yyyy = str(today.year)
        return [r for r in daily_calc if r["DATE"].split("-")[2] == yyyy]

    if range_type == "custom" and from_date and to_date:
        try:
            fd, td = (datetime.strptime(from_date, "%Y-%m-%d").date(),
                      datetime.strptime(to_date,   "%Y-%m-%d").date())
            out = []
            for r in daily_calc:
                try:
                    rd = datetime.strptime(r["DATE"], "%d-%m-%Y").date()
                    if fd <= rd <= td:
                        out.append(r)
                except Exception: pass
            return out
        except Exception:
            return []

    return []


# ─── FLASK ROUTES ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("qr.html", server_base=get_base_url())


@app.route("/generate-token")
def api_generate_token():
    token = generate_token()
    return jsonify({"token": token})


@app.route("/attendance")
def attendance_page():
    token = request.args.get("token", "")
    if not token or not is_token_valid(token):
        return "<h2 style='font-family:sans-serif;color:red;text-align:center;margin-top:40px'>❌ Access Denied — Please scan QR again</h2>", 403
    return render_template("attendance.html", session_token=token, agents=load_agents())


@app.route("/save-attendance", methods=["POST"])
def save_attendance():
    data       = request.get_json(force=True)
    agent      = str(data.get("agent", "")).strip().upper()
    new_action = str(data.get("action", "")).upper()
    token      = data.get("token", "")
    date_val   = data.get("date", "")
    photo      = data.get("photo", "")

    if not agent or not new_action:
        return jsonify({"status": "ERROR", "message": "❌ Missing agent or action"}), 400

    last_action = get_last_action_today(agent)
    check       = is_action_allowed(last_action, new_action)

    if not check["allowed"]:
        return jsonify({"status": "ERROR", "message": check["message"]}), 200

    if token and is_token_valid(token):
        mark_token_used(token)

    leave_date = date_val if new_action == "LEAVE" else ""
    now_ts     = datetime.now(IST).replace(tzinfo=None)

    photo_file = ""
    if photo and new_action == "LOGIN":
        photo_file = save_photo(agent, now_ts.isoformat(), photo)

    append_row([
        now_ts.isoformat(),
        agent,
        new_action,
        leave_date,
        photo_file,
        "OFFICE_NETWORK",
    ])

    # Async reports generation (fire-and-forget)
    threading.Thread(target=_run_post_jobs, daemon=True).start()

    return jsonify({"status": "OK", "message": "✅ Action recorded"})


def _run_post_jobs():
    try:
        daily   = generate_daily_calc()
        monthly = generate_monthly_summary(daily)
        # Save JSON caches (optional, used by /reports)
        with open("daily_calc.json",    "w") as f: json.dump(daily,   f)
        with open("monthly_summary.json","w") as f: json.dump(monthly, f)
    except Exception as e:
        print(f"Post-job error: {e}")


@app.route("/reports")
def reports_page():
    daily   = generate_daily_calc()
    monthly = generate_monthly_summary(daily)
    return render_template("reports.html", daily=daily, monthly=monthly)


@app.route("/export/daily")
def export_daily():
    daily = generate_daily_calc()
    df    = pd.DataFrame(daily)
    buf   = io.BytesIO()
    df.to_excel(buf, index=False, sheet_name="DAILY_CALC")
    buf.seek(0)
    return send_file(buf, as_attachment=True,
                     download_name="daily_calc.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/export/monthly")
def export_monthly():
    daily   = generate_daily_calc()
    monthly = generate_monthly_summary(daily)
    df      = pd.DataFrame(monthly)
    buf     = io.BytesIO()
    df.to_excel(buf, index=False, sheet_name="MONTHLY_SUMMARY")
    buf.seek(0)
    return send_file(buf, as_attachment=True,
                     download_name="monthly_summary.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/raw-data")
def raw_data():
    rows = read_all_rows()
    return jsonify(rows)


@app.route("/photos/<path:filename>")
def serve_photo(filename):
    return send_file(os.path.join(PHOTOS_DIR, filename))


@app.route("/qr-image")
def qr_image():
    text = request.args.get("text", "")
    if not text:
        return "Missing text", 400
    qr = qrcode.QRCode(box_size=6, border=2)
    qr.add_data(text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")


# ─── ADMIN ROUTES ──────────────────────────────────────────────────────────────

@app.route("/admin")
def admin_page():
    return render_template("admin.html")


@app.route("/admin/login", methods=["POST"])
def admin_login():
    data = request.get_json(force=True)
    if data.get("password") == ADMIN_PASSWORD:
        return jsonify({"status": "OK"})
    return jsonify({"status": "ERROR", "message": "Wrong password"}), 401


@app.route("/admin/agents", methods=["GET"])
def api_get_agents():
    agents = load_agents_data()
    return jsonify([{"name": a["name"], "email": a.get("email",""), "phone": a.get("phone","")} for a in agents])


@app.route("/admin/agents", methods=["POST"])
def api_add_agent():
    data  = request.get_json(force=True)
    name  = str(data.get("name",  "")).strip().upper()
    phone = str(data.get("phone", "")).strip()
    email = str(data.get("email", "")).strip()
    pin   = str(data.get("pin",   "")).strip()

    if not name:
        return jsonify({"status": "ERROR", "message": "Name is required"}), 400
    if not pin or not pin.isdigit():
        return jsonify({"status": "ERROR", "message": "PIN is required (digits only)"}), 400

    agents = load_agents_data()
    if any(a["name"] == name for a in agents):
        return jsonify({"status": "ERROR", "message": f"{name} already exists"}), 400

    agents.append({"name": name, "email": email, "phone": phone, "pin": pin})
    save_agents_data(agents)
    return jsonify({"status": "OK", "agents": [{"name": a["name"], "email": a.get("email",""), "phone": a.get("phone","")} for a in load_agents_data()]})


@app.route("/admin/agents/<name>", methods=["DELETE"])
def api_delete_agent(name):
    name   = name.upper()
    agents = load_agents_data()
    if not any(a["name"] == name for a in agents):
        return jsonify({"status": "ERROR", "message": "Agent not found"}), 404
    agents = [a for a in agents if a["name"] != name]
    save_agents_data(agents)

    rows     = read_all_rows()
    new_rows = [r for r in rows if r["AGENT"].strip().upper() != name]
    with open(DATA_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["TIMESTAMP","AGENT","ACTION","LEAVE_DATE","PHOTO","SOURCE"])
        writer.writeheader()
        writer.writerows(new_rows)

    return jsonify({"status": "OK",
                    "agents": [{"name": a["name"], "email": a.get("email",""), "phone": a.get("phone","")} for a in load_agents_data()],
                    "deleted_records": len(rows) - len(new_rows)})


@app.route("/admin/live-status")
def admin_live_status():
    agents = load_agents()
    today  = datetime.now(IST).date().isoformat()
    rows   = read_all_rows()

    data = {a: {"last_action": None, "login_time": None, "timeline": [], "photo": ""} for a in agents}

    for row in rows:
        try:
            ts     = datetime.fromisoformat(row["TIMESTAMP"])
            if ts.date().isoformat() != today:
                continue
            agent  = row["AGENT"].strip().upper()
            action = row["ACTION"].upper()
            photo  = row.get("PHOTO", "")
            if agent not in data:
                data[agent] = {"last_action": None, "login_time": None, "timeline": [], "photo": ""}
            data[agent]["last_action"] = action
            data[agent]["timeline"].append({"action": action, "time": ts.strftime("%H:%M")})
            if action == "LOGIN" and not data[agent]["login_time"]:
                data[agent]["login_time"] = ts.strftime("%H:%M")
                if photo and photo not in ("[photo]", ""):
                    data[agent]["photo"] = photo
        except Exception:
            continue

    result = []
    for agent in agents:
        d = data.get(agent, {"last_action": None, "login_time": None, "timeline": [], "photo": ""})
        result.append({
            "agent":       agent,
            "last_action": d["last_action"] or "NOT IN",
            "login_time":  d["login_time"]  or "—",
            "timeline":    d["timeline"],
            "photo":       d.get("photo", ""),
        })
    return jsonify(result)


@app.route("/admin/report")
def admin_report():
    range_type = request.args.get("range", "today")
    from_date  = request.args.get("from",  "")
    to_date    = request.args.get("to",    "")
    rows       = filter_daily_rows(range_type, from_date, to_date)
    summary    = generate_period_summary(rows)
    return jsonify({"rows": rows, "summary": summary})


@app.route("/admin/agent-records/<agent>")
def agent_all_dates(agent):
    agent = agent.upper()
    rows  = read_all_rows()
    dates: dict[str, int] = {}
    for row in rows:
        if row["AGENT"].strip().upper() != agent:
            continue
        try:
            ts       = datetime.fromisoformat(row["TIMESTAMP"])
            date_str = ts.strftime("%Y-%m-%d")
            dates[date_str] = dates.get(date_str, 0) + 1
        except Exception:
            continue
    return jsonify({"agent": agent, "dates": dates})


@app.route("/admin/agent-records/<agent>/<date_str>")
def agent_date_records(agent, date_str):
    agent = agent.upper()
    rows  = read_all_rows()
    result = []
    for row in rows:
        if row["AGENT"].strip().upper() != agent:
            continue
        try:
            ts = datetime.fromisoformat(row["TIMESTAMP"])
            if ts.strftime("%Y-%m-%d") != date_str:
                continue
            result.append({
                "timestamp":  row["TIMESTAMP"],
                "action":     row["ACTION"],
                "leave_date": row.get("LEAVE_DATE", ""),
                "photo":      row.get("PHOTO", ""),
                "source":     row.get("SOURCE", ""),
            })
        except Exception:
            continue
    return jsonify(result)


@app.route("/admin/records", methods=["POST"])
def admin_add_record():
    data       = request.get_json(force=True)
    agent      = str(data.get("agent", "")).strip().upper()
    action     = str(data.get("action", "")).upper()
    timestamp  = data.get("timestamp", datetime.now(IST).replace(tzinfo=None).isoformat())
    leave_date = data.get("leave_date", "")
    photo      = data.get("photo", "")

    if not agent or not action:
        return jsonify({"status": "ERROR", "message": "Missing agent or action"}), 400

    photo_file = ""
    if photo and action == "LOGIN":
        photo_file = save_photo(agent, timestamp, photo)

    append_row([timestamp, agent, action, leave_date, photo_file, "ADMIN"])
    return jsonify({"status": "OK"})


@app.route("/admin/records/<agent>/<path:timestamp>", methods=["DELETE"])
def admin_delete_record(agent, timestamp):
    agent    = agent.upper()
    rows     = read_all_rows()
    new_rows = [r for r in rows
                if not (r["AGENT"].strip().upper() == agent
                        and r["TIMESTAMP"] == timestamp)]
    if len(new_rows) == len(rows):
        return jsonify({"status": "ERROR", "message": "Record not found"}), 404
    with open(DATA_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["TIMESTAMP","AGENT","ACTION","LEAVE_DATE","PHOTO","SOURCE"])
        writer.writeheader()
        writer.writerows(new_rows)
    return jsonify({"status": "OK"})


@app.route("/admin/records/<agent>/<path:timestamp>", methods=["PUT"])
def admin_edit_record(agent, timestamp):
    data       = request.get_json(force=True)
    agent      = agent.upper()
    new_action = str(data.get("action", "")).upper()
    new_date   = data.get("date",       "")   # YYYY-MM-DD
    new_time   = data.get("time",       "")   # HH:MM
    new_leave  = data.get("leave_date", "")

    rows  = read_all_rows()
    found = False
    new_rows = []
    for row in rows:
        if row["AGENT"].strip().upper() == agent and row["TIMESTAMP"] == timestamp:
            found = True
            old_date = timestamp[:10]
            old_time = timestamp[11:16]
            nd = new_date or old_date
            nt = new_time or old_time
            row["TIMESTAMP"]  = f"{nd}T{nt}:00"
            row["ACTION"]     = new_action or row["ACTION"]
            row["LEAVE_DATE"] = new_leave
        new_rows.append(row)

    if not found:
        return jsonify({"status": "ERROR", "message": "Record not found"}), 404

    with open(DATA_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["TIMESTAMP","AGENT","ACTION","LEAVE_DATE","PHOTO","SOURCE"])
        writer.writeheader()
        writer.writerows(new_rows)
    return jsonify({"status": "OK"})


@app.route("/my-attendance")
def my_attendance_page():
    return render_template("my_attendance.html")


@app.route("/agent-login", methods=["POST"])
def agent_login():
    data       = request.get_json(force=True)
    identifier = str(data.get("identifier", "")).strip()
    pin        = str(data.get("pin", "")).strip()

    if not identifier or not pin:
        return jsonify({"status": "ERROR", "message": "Please enter your phone/email and PIN"}), 400

    agents  = load_agents_data()
    matched = None
    for a in agents:
        if (a.get("phone") and a["phone"].strip() == identifier) or \
           (a.get("email") and a["email"].strip().lower() == identifier.lower()) or \
           a["name"].upper() == identifier.upper():
            matched = a
            break

    if not matched:
        return jsonify({"status": "ERROR", "message": "Agent not found"}), 401
    if matched.get("pin", "") != pin:
        return jsonify({"status": "ERROR", "message": "Incorrect PIN"}), 401

    return jsonify({"status": "OK", "agent": matched["name"]})


@app.route("/agent-change-pin", methods=["POST"])
def agent_change_pin():
    data       = request.get_json(force=True)
    agent_name = str(data.get("agent", "")).strip().upper()
    old_pin    = str(data.get("old_pin", "")).strip()
    new_pin    = str(data.get("new_pin", "")).strip()

    if not new_pin or not new_pin.isdigit():
        return jsonify({"status": "ERROR", "message": "New PIN must be digits only"}), 400

    agents = load_agents_data()
    found  = False
    for a in agents:
        if a["name"] == agent_name:
            if a.get("pin", "") != old_pin:
                return jsonify({"status": "ERROR", "message": "Current PIN is incorrect"}), 401
            a["pin"] = new_pin
            found = True
            break

    if not found:
        return jsonify({"status": "ERROR", "message": "Agent not found"}), 404

    save_agents_data(agents)
    return jsonify({"status": "OK"})


@app.route("/admin/agents/<name>/pin", methods=["PUT"])
def admin_reset_pin(name):
    data    = request.get_json(force=True)
    name    = name.upper()
    new_pin = str(data.get("pin", "")).strip()

    if not new_pin or not new_pin.isdigit():
        return jsonify({"status": "ERROR", "message": "PIN must be digits only"}), 400

    agents = load_agents_data()
    found  = False
    for a in agents:
        if a["name"] == name:
            a["pin"] = new_pin
            found = True
            break

    if not found:
        return jsonify({"status": "ERROR", "message": "Agent not found"}), 404

    save_agents_data(agents)
    return jsonify({"status": "OK"})


@app.route("/ping")
def ping():
    return jsonify({"ok": True})


@app.route("/agent-data/<agent>")
def agent_data(agent):
    agent_upper = agent.strip().upper()
    if agent_upper not in load_agents():
        return jsonify({"status": "UNAUTHORIZED", "message": "Your account has been removed. Please contact admin."}), 403
    today = datetime.now(IST).date()
    all_rows    = read_all_rows()

    today_timeline = []
    for row in all_rows:
        if row["AGENT"].strip().upper() != agent_upper:
            continue
        try:
            ts = datetime.fromisoformat(row["TIMESTAMP"])
            if ts.date() == today:
                today_timeline.append({
                    "time":   ts.strftime("%H:%M"),
                    "action": row["ACTION"].upper(),
                    "photo":  row.get("PHOTO", ""),
                })
        except Exception:
            pass

    last_action = today_timeline[-1]["action"] if today_timeline else "NOT IN"

    daily_calc   = generate_daily_calc()
    mm, yyyy     = today.strftime("%m"), str(today.year)
    monthly_rows = [r for r in daily_calc
                    if r["AGENT"].strip().upper() == agent_upper
                    and r["DATE"].split("-")[1] == mm
                    and r["DATE"].split("-")[2] == yyyy]

    summary = generate_period_summary(monthly_rows)

    return jsonify({
        "agent":            agent_upper,
        "today_status":     last_action,
        "today_timeline":   today_timeline,
        "monthly_rows":     monthly_rows,
        "monthly_summary":  summary[0] if summary else {},
    })


@app.route("/admin/photo-base64/<path:filename>")
def photo_base64_endpoint(filename):
    filepath = os.path.join(PHOTOS_DIR, filename)
    if not os.path.exists(filepath):
        return jsonify({"error": "Not found"}), 404
    with open(filepath, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return jsonify({"base64": f"data:image/jpeg;base64,{b64}", "filename": filename})


@app.route("/admin/export")
def admin_export():
    range_type = request.args.get("range", "today")
    from_date  = request.args.get("from",  "")
    to_date    = request.args.get("to",    "")
    rows       = filter_daily_rows(range_type, from_date, to_date)
    summary    = generate_period_summary(rows)

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        pd.DataFrame(rows).to_excel(writer,    index=False, sheet_name="Detail")
        pd.DataFrame(summary).to_excel(writer, index=False, sheet_name="Summary")
    buf.seek(0)
    fname = f"aks_attendance_{range_type}.xlsx"
    if range_type == "custom" and from_date and to_date:
        fname = f"aks_attendance_{from_date}_to_{to_date}.xlsx"
    return send_file(buf, as_attachment=True, download_name=fname,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ─── ENTRY POINT ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ensure_csv()
    port = int(os.environ.get("PORT", 5001))
    print(f"🚀 AKS Attendance System running → {get_base_url()}")
    app.run(debug=True, host="0.0.0.0", port=port)
