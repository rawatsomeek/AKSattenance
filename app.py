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
from datetime import datetime, date
import pandas as pd

app = Flask(__name__)
CORS(app)

# ─── CONFIG ────────────────────────────────────────────────────────────────────
TOKEN_SECRET         = "SOME_SECRET_KEY_123"     # 🔒 Change this in production!
TOKEN_VALIDITY_SEC   = 20
DATA_FILE            = "attendance_data.csv"
COLORS_FILE          = "agent_colors.json"

# ─── IN-MEMORY TOKEN CACHE ──────────────────────────────────────────────────────
token_cache: dict[str, float] = {}   # token → expiry_timestamp
cache_lock  = threading.Lock()

AGENT_LIST = ["ANSHIKA", "IQRA", "KAIF", "RUCHIT", "SHREYA"]

AGENT_COLORS_DEFAULT = {
    "ANSHIKA": "#8FD3E8",
    "SHREYA":  "#F7B6D2",
    "KAIF":    "#4CAF50",
    "RUCHIT":  "#FFA726",
}

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


def get_agent_color(agent: str) -> str:
    agent = agent.upper()
    if agent in AGENT_COLORS_DEFAULT:
        return AGENT_COLORS_DEFAULT[agent]

    # load / create persistent color file
    colors = {}
    if os.path.exists(COLORS_FILE):
        with open(COLORS_FILE, "r") as f:
            colors = json.load(f)

    if agent not in colors:
        import random
        r = random.randint(120, 254)
        g = random.randint(120, 254)
        b = random.randint(120, 254)
        colors[agent] = f"#{r:02x}{g:02x}{b:02x}"
        with open(COLORS_FILE, "w") as f:
            json.dump(colors, f, indent=2)

    return colors[agent]


# ─── BUSINESS LOGIC ────────────────────────────────────────────────────────────

def get_last_action_today(agent: str) -> str | None:
    rows   = read_all_rows()
    today  = date.today().isoformat()
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


# ─── FLASK ROUTES ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("qr.html")


@app.route("/generate-token")
def api_generate_token():
    token = generate_token()
    return jsonify({"token": token})


@app.route("/attendance")
def attendance_page():
    token = request.args.get("token", "")
    if not token or not is_token_valid(token):
        return "<h2 style='font-family:sans-serif;color:red;text-align:center;margin-top:40px'>❌ Access Denied — Please scan QR again</h2>", 403
    return render_template("attendance.html", session_token=token, agents=AGENT_LIST)


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

    append_row([
        datetime.now().isoformat(),
        agent,
        new_action,
        leave_date,
        "[photo]" if photo else "",   # Don't store full base64 in CSV — save separately if needed
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


# ─── ENTRY POINT ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ensure_csv()
    print("🚀 AKS Attendance System running → http://localhost:5000")
    app.run(debug=True, host="0.0.0.0", port=5001)
