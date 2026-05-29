# AKS Hospitality — Attendance System
## Python + HTML Version

---

## Folder Structure

```
attendance_system/
│
├── app.py                    ← Flask backend (main server)
│
├── templates/
│   ├── qr.html               ← QR display page (office screen pe lagao)
│   ├── attendance.html       ← Attendance form (scan karke khulta hai)
│   └── reports.html          ← Daily + Monthly reports
│
├── attendance_data.csv       ← Auto-created, sabka raw data
├── agent_colors.json         ← Auto-created, new agents ke colors
├── daily_calc.json           ← Auto-created cache
└── monthly_summary.json      ← Auto-created cache
```

---

## Setup (Ek Baar Karna Hai)

### 1. Python install karo (agar nahi hai)
Python 3.10+ required → https://python.org

### 2. Dependencies install karo
```bash
pip install flask flask-cors pandas openpyxl
```

### 3. Server chalao
```bash
cd attendance_system
python app.py
```

---

## URLs

| URL | Kya karta hai |
|-----|---------------|
| `http://localhost:5000/` | QR display page (office screen) |
| `http://localhost:5000/attendance?token=...` | Attendance form (QR scan se khulta hai) |
| `http://localhost:5000/reports` | Daily + Monthly reports |
| `http://localhost:5000/export/daily` | Daily Excel download |
| `http://localhost:5000/export/monthly` | Monthly Excel download |
| `http://localhost:5000/raw-data` | Raw CSV data as JSON |

---

## Network pe kaise use karein (Office LAN)

Agar sab office ke same WiFi pe hain:

1. Server wale PC ka IP dhundo:
   - Windows: `ipconfig` → IPv4 Address (e.g. `192.168.1.10`)
   - Mac/Linux: `ifconfig`

2. `app.py` already `host="0.0.0.0"` pe run karta hai, toh sab reach kar sakte hain.

3. QR page apne aap sahi URL generate karega — bas `BACKEND_BASE` empty rehne do `qr.html` mein.

---

## Agents add/remove karna

`app.py` mein yeh line dekho:

```python
AGENT_LIST = ["ANSHIKA", "IQRA", "KAIF", "RUCHIT", "SHREYA"]
```

Isme naam add ya remove karo, server restart karo.

---

## Secret key change karna (Important!)

`app.py` mein:
```python
TOKEN_SECRET = "SOME_SECRET_KEY_123"   # ← yeh badlo
```

Koi bhi strong random string daal sakte ho.

---

## Logic (same as Google Apps Script)

| Action | Tab baar kare |
|--------|---------------|
| LOGIN | Sirf ek baar per day, pehle karna hoga |
| BREAK | LOGIN ke baad |
| RESUME | BREAK ke baad |
| LOGOUT | LOGIN ya RESUME ke baad |
| LEAVE | Bina LOGIN ke bhi ho sakta hai |

**Day Type Rules:**
- < 2 hrs → ABSENT
- 2–4.5 hrs → SUBJECT TO MANAGEMENT APPROVAL  
- 4.5–5.5 hrs → HALF DAY
- 5.5+ hrs → FULL DAY
- Login after 1 PM → LATE LOGIN

---

## Data Backup

`attendance_data.csv` ko regularly Google Drive ya USB pe copy karo.
Yahi sab raw data store hota hai.
# AKSattenance
# AKSattendance
