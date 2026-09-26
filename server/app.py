"""
MiniMon Server
A lightweight Zabbix-style monitoring server: ingests agent metrics,
evaluates threshold rules, fires alerts, and serves a simple dashboard.
"""
import hashlib
import hmac
import os
import smtplib
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from email.mime.text import MIMEText
from typing import Optional

import psycopg2
import psycopg2.extras
from fastapi import FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

DB_DSN = os.environ.get("MINIMON_DB_DSN", "dbname=minimon user=minimon password=changeme host=localhost")

SMTP_HOST = os.environ.get("MINIMON_SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("MINIMON_SMTP_PORT", "587"))
SMTP_USER = os.environ.get("MINIMON_SMTP_USER", "")
SMTP_PASS = os.environ.get("MINIMON_SMTP_PASS", "")
ALERT_TO = os.environ.get("MINIMON_ALERT_TO", "")

# A host is considered "down" if no metrics arrive within this many seconds
HOST_DOWN_THRESHOLD = int(os.environ.get("MINIMON_HOST_DOWN_SECONDS", "90"))


def db():
    return psycopg2.connect(DB_DSN)


class MetricPayload(BaseModel):
    cpu_percent: float
    mem_percent: float
    disk_percent: float
    net_sent_kb: float
    net_recv_kb: float
    load1: float


def send_alert_email(subject: str, body: str):
    if not (SMTP_HOST and ALERT_TO):
        return
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = SMTP_USER or "minimon@localhost"
    msg["To"] = ALERT_TO
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            if SMTP_USER:
                server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(msg["From"], [ALERT_TO], msg.as_string())
    except Exception as e:
        print(f"[alert email failed] {e}")


def evaluate_thresholds(conn, hostname: str, payload: MetricPayload):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM thresholds WHERE enabled = TRUE")
        rules = cur.fetchall()

        values = {
            "cpu_percent": payload.cpu_percent,
            "mem_percent": payload.mem_percent,
            "disk_percent": payload.disk_percent,
        }

        for rule in rules:
            metric = rule["metric"]
            if metric not in values:
                continue
            val = values[metric]
            triggered = (val > rule["value"]) if rule["operator"] == ">" else (val < rule["value"])
            if triggered:
                message = f"{hostname}: {metric} = {val:.1f} ({rule['operator']} {rule['value']})"
                cur.execute(
                    """INSERT INTO alerts (hostname, metric, value, severity, message)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (hostname, metric, val, rule["severity"], message),
                )
                conn.commit()
                send_alert_email(f"[MiniMon] {rule['severity'].upper()} alert on {hostname}", message)


def host_watchdog_loop():
    """Marks hosts down if their agent hasn't reported in HOST_DOWN_THRESHOLD seconds,
    and back up if it resumes reporting. Runs forever in a background thread."""
    while True:
        try:
            conn = db()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT hostname, status, last_seen FROM hosts")
                for h in cur.fetchall():
                    if h["last_seen"] is None:
                        continue
                    age = (datetime.now(timezone.utc) - h["last_seen"]).total_seconds()
                    if age > HOST_DOWN_THRESHOLD and h["status"] != "down":
                        cur.execute("UPDATE hosts SET status='down' WHERE hostname=%s", (h["hostname"],))
                        cur.execute(
                            "INSERT INTO alerts (hostname, metric, value, severity, message) VALUES (%s,%s,%s,%s,%s)",
                            (h["hostname"], "host_status", 0, "critical", f"{h['hostname']} stopped reporting (host down)"),
                        )
                        send_alert_email(f"[MiniMon] Host DOWN: {h['hostname']}", f"{h['hostname']} has not reported in {int(age)}s")
                    elif age <= HOST_DOWN_THRESHOLD and h["status"] != "up":
                        cur.execute("UPDATE hosts SET status='up' WHERE hostname=%s", (h["hostname"],))
                conn.commit()
            conn.close()
        except Exception as e:
            print(f"[watchdog error] {e}")
        time.sleep(30)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup logic: Start host watchdog thread
    watchdog_thread = threading.Thread(target=host_watchdog_loop, daemon=True)
    watchdog_thread.start()
    yield
    # Shutdown logic (optional cleanup tasks go here)


def _password_hash(password: str) -> str:
    salt = os.environ.get("MINIMON_PASSWORD_SALT", "minimon-salt")
    return hashlib.sha256(f"{salt}:{password}".encode("utf-8")).hexdigest()


DEFAULT_ADMIN_USERNAME = os.environ.get("MINIMON_ADMIN_USER", "justice")
DEFAULT_ADMIN_PASSWORD = os.environ.get("MINIMON_ADMIN_PASSWORD", "justice@2026")


def _fallback_admin_ok(username: str, password: str) -> bool:
    return username == DEFAULT_ADMIN_USERNAME and password == DEFAULT_ADMIN_PASSWORD


def ensure_default_admin_user():
    try:
        conn = db()
        try:
            username = DEFAULT_ADMIN_USERNAME
            password = DEFAULT_ADMIN_PASSWORD
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO users (username, password_hash, role)
                    VALUES (%s, %s, 'admin')
                    ON CONFLICT (username)
                    DO UPDATE SET password_hash = EXCLUDED.password_hash, role = 'admin'
                    """,
                    (username, _password_hash(password)),
                )
                conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        print(f"[auth bootstrap skipped: {exc}]")


def _session_secret() -> str:
    return os.environ.get("MINIMON_SESSION_SECRET", "minimon-session-secret-change-me")


def _make_session_token(username: str) -> str:
    signature = hmac.new(_session_secret().encode("utf-8"), username.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{username}:{signature}"


def _verify_session_token(token: str) -> Optional[str]:
    if not token:
        return None
    try:
        username, signature = token.split(":", 1)
    except ValueError:
        return None
    expected = hmac.new(_session_secret().encode("utf-8"), username.encode("utf-8"), hashlib.sha256).hexdigest()
    if hmac.compare_digest(signature, expected):
        return username
    return None


def _get_session_user(request: Request) -> Optional[str]:
    token = request.cookies.get("minimon_session")
    return _verify_session_token(token)


def _verify_user_password(username: str, password: str) -> bool:
    try:
        conn = db()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT password_hash FROM users WHERE username = %s", (username,))
                row = cur.fetchone()
                if not row:
                    return _fallback_admin_ok(username, password)
                return hmac.compare_digest(row[0], _password_hash(password))
        finally:
            conn.close()
    except Exception as exc:
        print(f"[auth fallback used: {exc}]")
        return _fallback_admin_ok(username, password)


app = FastAPI(title="MiniMon", lifespan=lifespan)


@app.on_event("startup")
def startup_seed_admin_user():
    ensure_default_admin_user()


@app.get("/login", response_class=HTMLResponse)
def login_page():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>MiniMon Login</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0" />
        <style>
            :root {
                --bg: #0b1020;
                --panel: rgba(17, 24, 39, 0.92);
                --panel-strong: #111827;
                --border: rgba(148, 163, 184, 0.2);
                --text: #edf6ff;
                --muted: #a7bcda;
                --accent: #60a5fa;
                --accent-2: #22c55e;
                --danger: #f87171;
            }
            * { box-sizing: border-box; }
            body {
                margin: 0;
                min-height: 100vh;
                display: grid;
                place-items: center;
                font-family: ui-sans-serif, system-ui, sans-serif;
                background: radial-gradient(circle at top, rgba(96,165,250,0.18), transparent 30%), linear-gradient(180deg, var(--bg), #0f172a);
                color: var(--text);
            }
            .card {
                width: min(420px, calc(100vw - 32px));
                background: linear-gradient(180deg, rgba(17,24,39,0.95), rgba(15,23,42,0.9));
                border: 1px solid var(--border);
                border-radius: 20px;
                box-shadow: 0 18px 45px rgba(15,23,42,0.5);
                padding: 28px 24px;
            }
            h1 {
                margin: 0 0 8px;
                font-size: 2rem;
                letter-spacing: -0.04em;
            }
            .subtitle {
                margin: 0 0 20px;
                color: var(--muted);
            }
            label {
                display: block;
                font-size: 0.82rem;
                letter-spacing: 0.04em;
                text-transform: uppercase;
                color: var(--muted);
                margin-bottom: 8px;
            }
            input {
                width: 100%;
                border-radius: 12px;
                border: 1px solid var(--border);
                background: rgba(15,23,42,0.9);
                color: var(--text);
                padding: 13px 14px;
                margin-bottom: 16px;
                font-size: 1rem;
            }
            input:focus {
                outline: 2px solid rgba(96,165,250,0.5);
                border-color: rgba(96,165,250,0.6);
            }
            button {
                width: 100%;
                border: none;
                border-radius: 12px;
                background: linear-gradient(90deg, var(--accent), var(--accent-2));
                color: white;
                font-size: 1rem;
                font-weight: 700;
                padding: 13px 16px;
                cursor: pointer;
            }
            .meta {
                margin-top: 16px;
                color: var(--muted);
                font-size: 0.8rem;
            }
            .error {
                background: rgba(248,113,113,0.12);
                color: #fecaca;
                border: 1px solid rgba(248,113,113,0.25);
                padding: 10px 12px;
                border-radius: 10px;
                margin-bottom: 18px;
            }
        </style>
    </head>
    <body>
        <div class="card">
            <h1>MiniMon</h1>
            <p class="subtitle">Sign in to view the monitoring dashboard</p>
            <form method="post" action="/login">
                <label for="username">Username</label>
                <input id="username" name="username" type="text" value="justice" required />

                <label for="password">Password</label>
                <input id="password" name="password" type="password" placeholder="Enter password" required />

                <button type="submit">Login</button>
            </form>
            <div class="meta">Default admin user: justice / justice@2026</div>
        </div>
    </body>
    </html>
    """


@app.post("/login")
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    if _verify_user_password(username, password):
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie(key="minimon_session", value=_make_session_token(username), httponly=True, samesite="lax", max_age=60 * 60 * 12)
        return response

    return HTMLResponse(
        """
        <!DOCTYPE html>
        <html>
        <head><title>MiniMon Login</title></head>
        <body style="font-family: sans-serif; background: #0b1020; color: white; display:grid; place-items:center; min-height:100vh;">
            <div style="width:420px; background:#111827; border:1px solid rgba(148,163,184,.2); border-radius:18px; padding:24px;">
                <h2 style="margin-top:0;">MiniMon</h2>
                <div style="background: rgba(248,113,113,0.12); color:#fecaca; border:1px solid rgba(248,113,113,.25); padding:10px 12px; border-radius:10px; margin-bottom:18px;">Invalid username or password.</div>
                <form method="post" action="/login">
                    <input name="username" type="text" placeholder="Username" style="width:100%; margin-bottom:12px; padding:12px; border-radius:10px; background:#0f172a; color:white; border:1px solid rgba(148,163,184,.2);" required />
                    <input name="password" type="password" placeholder="Password" style="width:100%; margin-bottom:12px; padding:12px; border-radius:10px; background:#0f172a; color:white; border:1px solid rgba(148,163,184,.2);" required />
                    <button type="submit" style="width:100%; padding:12px; border:none; border-radius:10px; background:linear-gradient(90deg,#60a5fa,#22c55e); color:white; font-weight:700;">Login</button>
                </form>
            </div>
        </body>
        </html>
        """
    )


@app.get("/logout")
def logout_response():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("minimon_session")
    return response


@app.get("/api/session")
def session_status(request: Request):
    return {"authenticated": bool(_get_session_user(request)), "user": _get_session_user(request)}


def require_admin_session(request: Request):
    username = _get_session_user(request)
    if not username:
        raise HTTPException(status_code=401, detail="authentication required")
    return username


@app.post("/api/admin/hosts/register")
def register_host_via_session(request: Request, payload: dict):
    require_admin_session(request)
    hostname = payload.get("hostname")
    if not hostname:
        raise HTTPException(status_code=400, detail="hostname is required")
    group_name = payload.get("group_name", "Ground Floor")
    import secrets
    api_key = secrets.token_hex(20)
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO hosts (hostname, api_key, group_name) VALUES (%s, %s, %s) ON CONFLICT (hostname) DO UPDATE SET api_key = EXCLUDED.api_key",
                (hostname, api_key, group_name),
            )
            conn.commit()
        return {"hostname": hostname, "api_key": api_key}
    finally:
        conn.close()


@app.post("/api/admin/devices/register")
def register_device_via_session(request: Request, payload: dict):
    require_admin_session(request)
    name = payload.get("name")
    ip_address = payload.get("ip_address")
    if not name or not ip_address:
        raise HTTPException(status_code=400, detail="name and ip_address are required")
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO devices (name, ip_address, snmp_community, snmp_version, device_type, group_name)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (name) DO UPDATE SET ip_address=EXCLUDED.ip_address,
                     snmp_community=EXCLUDED.snmp_community, snmp_version=EXCLUDED.snmp_version,
                     device_type=EXCLUDED.device_type, group_name=EXCLUDED.group_name""",
                (
                    name,
                    ip_address,
                    payload.get("snmp_community", "public"),
                    payload.get("snmp_version", "2c"),
                    payload.get("device_type", "switch"),
                    payload.get("group_name", "Ground Floor"),
                ),
            )
            conn.commit()
        return {"status": "registered", "name": name}
    finally:
        conn.close()


@app.post("/api/metrics")
def ingest_metrics(payload: MetricPayload, x_api_key: str = Header(...)):
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT hostname FROM hosts WHERE api_key = %s", (x_api_key,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=401, detail="invalid api key")
            hostname = row[0]

            cur.execute(
                """INSERT INTO metrics (time, hostname, cpu_percent, mem_percent, disk_percent, net_sent_kb, net_recv_kb, load1)
                   VALUES (now(), %s, %s, %s, %s, %s, %s, %s)""",
                (hostname, payload.cpu_percent, payload.mem_percent, payload.disk_percent,
                 payload.net_sent_kb, payload.net_recv_kb, payload.load1),
            )
            cur.execute("UPDATE hosts SET last_seen = now() WHERE hostname = %s", (hostname,))
            conn.commit()

        evaluate_thresholds(conn, hostname, payload)
        return {"status": "ok"}
    except Exception as exc:
        print(f"[metrics ingest error] {exc}")
        raise HTTPException(status_code=503, detail="database unavailable")
    finally:
        conn.close()


@app.get("/api/hosts")
def list_hosts():
    conn = db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT h.hostname, h.group_name, h.last_seen, h.status,
                       (SELECT cpu_percent FROM metrics m WHERE m.hostname = h.hostname ORDER BY time DESC LIMIT 1) AS cpu_percent,
                       (SELECT mem_percent FROM metrics m WHERE m.hostname = h.hostname ORDER by time DESC LIMIT 1) AS mem_percent,
                       (SELECT disk_percent FROM metrics m WHERE m.hostname = h.hostname ORDER BY time DESC LIMIT 1) AS disk_percent
                FROM hosts h ORDER BY h.hostname
            """)
            return cur.fetchall()
    except Exception as exc:
        print(f"[list_hosts error] {exc}")
        return []
    finally:
        conn.close()


@app.get("/api/metrics/{hostname}")
def host_metrics(hostname: str, limit: int = 100):
    conn = db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM metrics WHERE hostname = %s ORDER BY time DESC LIMIT %s",
                (hostname, limit),
            )
            return cur.fetchall()
    except Exception as exc:
        print(f"[host_metrics error] {exc}")
        return []
    finally:
        conn.close()


@app.get("/api/alerts")
def list_alerts(limit: int = 50):
    conn = db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM alerts ORDER BY triggered_at DESC LIMIT %s", (limit,))
            return cur.fetchall()
    except Exception as exc:
        print(f"[list_alerts error] {exc}")
        return []
    finally:
        conn.close()


@app.get("/api/devices")
def list_devices():
    """Returns every network device with its full port list nested inside, for the dashboard."""
    conn = db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM devices ORDER BY name")
            devices = cur.fetchall()
            for d in devices:
                cur.execute(
                    "SELECT if_index, if_descr, oper_status, admin_status, last_changed FROM interfaces WHERE device_id = %s ORDER BY if_index",
                    (d["id"],),
                )
                d["interfaces"] = cur.fetchall()
            return devices
    except Exception as exc:
        print(f"[list_devices error] {exc}")
        return []
    finally:
        conn.close()


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    if not _get_session_user(request):
        return RedirectResponse(url="/login", status_code=303)
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>MiniMon Dashboard</title>
        <style>
            :root {
                --bg: #0b1020;
                --bg-2: #111827;
                --panel: rgba(17, 24, 39, 0.88);
                --panel-strong: #121a2b;
                --panel-soft: #1b2335;
                --border: rgba(148, 163, 184, 0.18);
                --text: #e5eefb;
                --muted: #9fb0c9;
                --ok: #22c55e;
                --ok-soft: rgba(34, 197, 94, 0.12);
                --warn: #f59e0b;
                --warn-soft: rgba(245, 158, 11, 0.12);
                --bad: #ef4444;
                --bad-soft: rgba(239, 68, 68, 0.12);
                --info: #3b82f6;
                --shadow: 0 18px 50px rgba(15, 23, 42, 0.45);
            }

            * { box-sizing: border-box; }
            html, body {
                margin: 0;
                font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif;
                background:
                    radial-gradient(circle at top, rgba(59, 130, 246, 0.18), transparent 32%),
                    linear-gradient(180deg, var(--bg) 0%, #0f172a 100%);
                color: var(--text);
            }

            body { padding: 32px 18px 48px; }

            .wrap {
                max-width: 1280px;
                margin: 0 auto;
            }

            .topbar {
                display: flex;
                justify-content: space-between;
                align-items: center;
                gap: 16px;
                margin-bottom: 22px;
            }

            h1 {
                margin: 0;
                font-size: clamp(2rem, 3vw, 2.8rem);
                letter-spacing: -0.04em;
            }

            .subtitle {
                color: var(--muted);
                margin-top: 6px;
                font-size: 0.96rem;
            }

            .summary {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
                gap: 16px;
                margin: 18px 0 28px;
            }

            .card {
                background: linear-gradient(180deg, rgba(17,24,39,0.96), rgba(17,24,39,0.86));
                border: 1px solid var(--border);
                border-radius: 18px;
                box-shadow: var(--shadow);
            }

            .summary .card {
                padding: 18px 20px;
                display: flex;
                align-items: center;
                justify-content: space-between;
                min-height: 90px;
            }

            .summary .label {
                font-size: 0.8rem;
                color: var(--muted);
                text-transform: uppercase;
                letter-spacing: 0.06em;
            }

            .summary .value {
                font-size: 1.8rem;
                font-weight: 700;
                margin-top: 8px;
            }

            .summary .icon {
                width: 44px;
                height: 44px;
                border-radius: 12px;
                display: grid;
                place-items: center;
                background: rgba(59,130,246,0.12);
                color: #8ec5ff;
                font-size: 1.2rem;
            }

            .section {
                margin-top: 30px;
            }

            .section-head {
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 12px;
            }

            .section h2 {
                margin: 0;
                font-size: 1.2rem;
                letter-spacing: -0.02em;
            }

            table {
                width: 100%;
                border-collapse: separate;
                border-spacing: 0;
                overflow: hidden;
                border-radius: 18px;
                border: 1px solid var(--border);
                box-shadow: var(--shadow);
            }

            th, td {
                text-align: left;
                padding: 14px 16px;
                border-bottom: 1px solid rgba(148,163,184,0.12);
            }

            th {
                background: rgba(15,23,42,0.9);
                color: #b5c4dc;
                font-size: 0.74rem;
                text-transform: uppercase;
                letter-spacing: 0.08em;
                font-weight: 700;
            }

            tbody tr {
                background: rgba(17, 24, 39, 0.7);
            }

            tbody tr:hover {
                background: rgba(30, 41, 59, 0.9);
            }

            .status {
                display: inline-flex;
                align-items: center;
                gap: 8px;
                padding: 0.42rem 0.72rem;
                border-radius: 999px;
                font-size: 0.75rem;
                letter-spacing: 0.04em;
                text-transform: uppercase;
                font-weight: 700;
                border: 1px solid transparent;
            }

            .status::before {
                content: "";
                width: 8px;
                height: 8px;
                border-radius: 50%;
                display: inline-block;
                background: currentColor;
                box-shadow: 0 0 12px currentColor;
            }

            .status.up {
                color: var(--ok);
                background: var(--ok-soft);
                border-color: rgba(34,197,94,0.2);
            }

            .status.down {
                color: var(--bad);
                background: var(--bad-soft);
                border-color: rgba(239,68,68,0.2);
            }

            .status.unknown {
                color: var(--muted);
                background: rgba(148,163,184,0.08);
                border-color: rgba(148,163,184,0.15);
            }

            #devices {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
                gap: 18px;
            }

            .device-card {
                background: linear-gradient(180deg, rgba(17,24,39,0.96), rgba(15,23,42,0.9));
                border: 1px solid var(--border);
                border-radius: 20px;
                padding: 18px 18px 16px;
                box-shadow: var(--shadow);
            }

            .device-header {
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 12px;
                margin-bottom: 12px;
            }

            .device-meta {
                display: flex;
                flex-direction: column;
                gap: 4px;
                min-width: 0;
            }

            .device-name {
                font-weight: 700;
                font-size: 1.08rem;
                word-break: break-word;
            }

            .device-ip {
                color: var(--muted);
                font-size: 0.8rem;
            }

            .device-type {
                font-size: 0.72rem;
                color: #c0d4fa;
                background: rgba(59,130,246,0.08);
                border: 1px solid rgba(59,130,246,0.18);
                padding: 0.28rem 0.55rem;
                border-radius: 999px;
            }

            .device-footer {
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-top: 14px;
                color: var(--muted);
                font-size: 0.82rem;
            }

            .port-grid {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
                gap: 8px;
                margin-top: 12px;
            }

            .port {
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 8px;
                min-height: 44px;
                padding: 8px 10px;
                border-radius: 10px;
                font-size: 0.8rem;
                border: 1px solid rgba(148,163,184,0.15);
                background: rgba(148,163,184,0.04);
                color: var(--text);
            }

            .port .name {
                overflow: hidden;
                text-overflow: ellipsis;
                white-space: nowrap;
                flex: 1;
            }

            .port.up {
                background: rgba(34,197,94,0.10);
                border-color: rgba(34,197,94,0.2);
            }

            .port.down {
                background: rgba(239,68,68,0.10);
                border-color: rgba(239,68,68,0.2);
            }

            .port.unknown {
                background: rgba(148,163,184,0.08);
                border-color: rgba(148,163,184,0.15);
            }

            .dot {
                width: 8px;
                height: 8px;
                border-radius: 50%;
                display: inline-block;
                flex: 0 0 auto;
            }

            .dot.up { background: var(--ok); box-shadow: 0 0 10px var(--ok); }
            .dot.down { background: var(--bad); box-shadow: 0 0 10px var(--bad); }
            .dot.unknown { background: var(--muted); box-shadow: 0 0 10px var(--muted); }

            .empty {
                color: var(--muted);
                font-style: italic;
                padding: 16px 0;
            }

            .alert-table {
                margin-top: 18px;
            }

            .viz-grid {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
                gap: 16px;
                margin: 18px 0 28px;
            }

            .viz-card {
                background: linear-gradient(180deg, rgba(17,24,39,0.96), rgba(15,23,42,0.9));
                border: 1px solid var(--border);
                border-radius: 18px;
                padding: 18px;
                box-shadow: var(--shadow);
            }

            .viz-header {
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 12px;
            }

            .viz-title {
                font-size: 0.82rem;
                letter-spacing: 0.08em;
                text-transform: uppercase;
                color: var(--muted);
                margin: 0;
            }

            .bar-group {
                display: flex;
                flex-direction: column;
                gap: 10px;
            }

            .metric-row {
                display: grid;
                grid-template-columns: 56px 1fr 44px;
                align-items: center;
                gap: 10px;
                font-size: 0.8rem;
                color: var(--muted);
            }

            .progress {
                position: relative;
                height: 10px;
                background: rgba(148,163,184,0.12);
                border-radius: 999px;
                overflow: hidden;
            }

            .progress > span {
                position: absolute;
                inset: 0 auto 0 0;
                width: 0;
                border-radius: inherit;
                background: linear-gradient(90deg, #34d399, #fbbf24, #f87171);
            }

            .segmented {
                display: flex;
                height: 14px;
                border-radius: 999px;
                overflow: hidden;
                border: 1px solid rgba(148,163,184,0.16);
            }

            .segmented > span {
                display: block;
                height: 100%;
            }

            .seg-up { background: rgba(34,197,94,0.9); }
            .seg-down { background: rgba(239,68,68,0.9); }
            .seg-unknown { background: rgba(148,163,184,0.7); }

            .spark {
                width: 100%;
                height: 78px;
                display: block;
                border-radius: 12px;
                background: rgba(17,24,39,0.7);
                border: 1px solid rgba(148,163,184,0.12);
            }

            .legend {
                display: flex;
                justify-content: space-between;
                margin-top: 8px;
                font-size: 0.74rem;
                color: var(--muted);
            }

            @media (max-width: 800px) {
                body { padding: 18px 12px 40px; }
                .topbar { display: block; }
                th, td { padding: 12px 10px; }
            }
        </style>
    </head>
    <body>
        <div class="wrap">
            <div class="topbar">
                <div>
                    <h1>MiniMon</h1>
                    <div class="subtitle">System health, network devices, and alert overview</div>
                </div>
            </div>

            <div class="summary">
                <div class="card">
                    <div>
                        <div class="label">Hosts</div>
                        <div class="value" id="hostCount">0</div>
                    </div>
                    <div class="icon">🖥️</div>
                </div>
                <div class="card">
                    <div>
                        <div class="label">Switches</div>
                        <div class="value" id="deviceCount">0</div>
                    </div>
                    <div class="icon">🔌</div>
                </div>
                <div class="card">
                    <div>
                        <div class="label">Ports Up</div>
                        <div class="value" id="portsUp">0</div>
                    </div>
                    <div class="icon">✅</div>
                </div>
                <div class="card">
                    <div>
                        <div class="label">Ports Down</div>
                        <div class="value" id="portsDown">0</div>
                    </div>
                    <div class="icon">⚠️</div>
                </div>
            </div>

            <div class="section">
                <div class="section-head">
                    <h2>Servers</h2>
                </div>
                <table id="hosts">
                    <thead>
                        <tr>
                            <th>Host</th>
                            <th>Status</th>
                            <th>Floor</th>
                            <th>Last Seen</th>
                            <th>CPU %</th>
                            <th>Mem %</th>
                            <th>Disk %</th>
                        </tr>
                    </thead>
                    <tbody></tbody>
                </table>
            </div>

            <div class="section">
                <div class="section-head">
                    <h2>Network Devices</h2>
                </div>
                <div id="devices"></div>
            </div>

            <div class="section">
                <div class="section-head">
                    <h2>Recent Alerts</h2>
                </div>
                <table id="alerts" class="alert-table">
                    <thead>
                        <tr>
                            <th>Time</th>
                            <th>Source</th>
                            <th>Metric</th>
                            <th>Value</th>
                            <th>Severity</th>
                            <th>Message</th>
                        </tr>
                    </thead>
                    <tbody></tbody>
                </table>
            </div>

            <div class="section">
                <div class="section-head">
                    <h2>Live Visualization</h2>
                </div>
                <div class="viz-grid">
                    <div class="viz-card">
                        <div class="viz-header">
                            <div class="viz-title">CPU Usage</div>
                        </div>
                        <div class="bar-group">
                            <div class="metric-row">
                                <div>Host</div>
                                <div>CPU %</div>
                                <div>Status</div>
                            </div>
                            <div id="cpuUsage"></div>
                        </div>
                    </div>
                    <div class="viz-card">
                        <div class="viz-header">
                            <div class="viz-title">Memory Usage</div>
                        </div>
                        <div class="bar-group">
                            <div class="metric-row">
                                <div>Host</div>
                                <div>Memory %</div>
                                <div>Status</div>
                            </div>
                            <div id="memoryUsage"></div>
                        </div>
                    </div>
                    <div class="viz-card">
                        <div class="viz-header">
                            <div class="viz-title">Disk Usage</div>
                        </div>
                        <div class="bar-group">
                            <div class="metric-row">
                                <div>Host</div>
                                <div>Disk %</div>
                                <div>Status</div>
                            </div>
                            <div id="diskUsage"></div>
                        </div>
                    </div>
                    <div class="viz-card">
                        <div class="viz-header">
                            <div class="viz-title">Network Device Status</div>
                        </div>
                        <div class="bar-group">
                            <div class="metric-row">
                                <div>Device</div>
                                <div>Status</div>
                            </div>
                            <div id="deviceStatus"></div>
                        </div>
                    </div>
                </div>

                <div class="viz-grid">
                    <div class="viz-card">
                        <div class="viz-header">
                            <h3 class="viz-title">Host Utilization</h3>
                        </div>
                        <div class="bar-group" id="utilBars"></div>
                    </div>

                    <div class="viz-card">
                        <div class="viz-header">
                            <h3 class="viz-title">Network Health</h3>
                        </div>
                        <div id="deviceHealth"></div>
                    </div>
                </div>
            </div>

            <div class="section">
                <div class="section-head">
                    <h2>Register Host</h2>
                </div>
                <form id="registerHostForm">
                    <div class="error" id="hostRegisterError" style="display:none;"></div>
                    <label for="hostname">Hostname</label>
                    <input id="hostname" name="hostname" type="text" required />

                    <label for="group_name">Floor</label>
                    <input id="group_name" name="group_name" type="text" value="Ground Floor" />

                    <button type="submit">Register Host</button>
                </form>
            </div>

            <div class="section">
                <div class="section-head">
                    <h2>Register Device</h2>
                </div>
                <form id="registerDeviceForm">
                    <div class="error" id="deviceRegisterError" style="display:none;"></div>
                    <label for="deviceName">Device Name</label>
                    <input id="deviceName" name="name" type="text" required />

                    <label for="deviceIp">IP Address</label>
                    <input id="deviceIp" name="ip_address" type="text" required />

                    <label for="snmpCommunity">SNMP Community</label>
                    <input id="snmpCommunity" name="snmp_community" type="text" required />

                    <label for="snmpVersion">SNMP Version</label>
                    <select id="snmpVersion" name="snmp_version" required>
                        <option value="2c">2c</option>
                        <option value="3">3</option>
                    </select>

                    <label for="deviceType">Device Type</label>
                    <input id="deviceType" name="device_type" type="text" required />

                    <label for="deviceGroup">Floor</label>
                    <input id="deviceGroup" name="group_name" type="text" value="Ground Floor" />

                    <button type="submit">Register Device</button>
                </form>
            </div>
        </div>

        <script>
        function statusClass(v){
            if (v === 'up') return 'up';
            if (v === 'down') return 'down';
            return 'unknown';
        }

        function badge(v){
            return `<span class="status ${statusClass(v || 'unknown')}">${v || 'unknown'}</span>`;
        }

        function portDot(v){
            return `<span class="dot ${statusClass(v || 'unknown')}"></span>`;
        }

        function cls(v){
            if (v > 90) return 'bad';
            if (v > 75) return 'warn';
            return 'ok';
        }

        function renderPort(port) {
            const name = port.if_descr || ('if' + port.if_index);
            const state = (port.oper_status || 'unknown');
            return `
                <div class="port ${state}">
                    <span class="name">${name}</span>
                    ${portDot(state)}
                </div>
            `;
        }

        function renderDevice(device) {
            const ports = Array.isArray(device.interfaces) ? device.interfaces : [];
            const upCount = ports.filter(p => (p.oper_status || 'unknown') === 'up').length;
            const downCount = ports.filter(p => (p.oper_status || 'unknown') === 'down').length;
            const deviceState = device.status || 'unknown';
            const type = (device.device_type || 'switch').toLowerCase();
            return `
                <div class="device-card">
                    <div class="device-header">
                        <div class="device-meta">
                            <div class="device-name">${device.name}</div>
                            <div class="device-ip">${device.ip_address}</div>
                        </div>
                        ${badge(deviceState)}
                    </div>
                    <div class="device-footer">
                        <span class="device-type">${type}</span>
                        <span>${upCount} up / ${downCount} down</span>
                    </div>
                    <div class="port-grid">
                        ${ports.length ? ports.map(renderPort).join('') : '<div class="empty">No ports discovered yet</div>'}
                    </div>
                </div>
            `;
        }

        function renderMetricBars(targetId, rows, color) {
            const el = document.getElementById(targetId);
            if (!el) return;
            el.innerHTML = rows.length ? rows.map(r => `
                <div class="metric-row">
                    <div>${r.name}</div>
                    <div class="progress"><span style="width:${Math.min(100, r.value)}%; ${color}"></span></div>
                    <div>${r.value.toFixed(0)}%</div>
                </div>
            `).join('') : '<div class="empty">No data</div>';
        }

        function renderDeviceHealthBox(targetId, up, down, unknown, total) {
            const el = document.getElementById(targetId);
            if (!el) return;
            const totalValue = Math.max(1, total);
            el.innerHTML = `
                <div class="segmented">
                    <span class="seg-up" style="width:${(up / totalValue) * 100}%"></span>
                    <span class="seg-down" style="width:${(down / totalValue) * 100}%"></span>
                    <span class="seg-unknown" style="width:${(unknown / totalValue) * 100}%"></span>
                </div>
                <div class="legend">
                    <span>Up: ${up}</span>
                    <span>Down: ${down}</span>
                    <span>Unknown: ${unknown}</span>
                </div>
            `;
        }

        async function load(){
            try {
                const [hosts, devices, alerts] = await Promise.all([
                    fetch('/api/hosts').then(r => r.json()),
                    fetch('/api/devices').then(r => r.json()),
                    fetch('/api/alerts').then(r => r.json())
                ]);

                const sortedDevices = [...devices].sort((a, b) => {
                    const typeCompare = (a.device_type || 'switch').localeCompare(b.device_type || 'switch');
                    return typeCompare !== 0 ? typeCompare : (a.name || '').localeCompare(b.name || '');
                });

                document.querySelector('#hosts tbody').innerHTML = hosts.length ? hosts.map(h => `
                    <tr>
                        <td>${h.hostname}</td>
                        <td>${badge(h.status || 'unknown')}</td>
                        <td>${h.group_name || 'Ground Floor'}</td>
                        <td>${new Date(h.last_seen).toLocaleString()}</td>
                        <td>${(h.cpu_percent ?? 0).toFixed(1)}</td>
                        <td>${(h.mem_percent ?? 0).toFixed(1)}</td>
                        <td>${(h.disk_percent ?? 0).toFixed(1)}</td>
                    </tr>
                `).join('') : `<tr><td colspan="7">No hosts yet</td></tr>`}
            `;
        }

        document.getElementById('registerHostForm').onsubmit = async function(e) {
            e.preventDefault();
            const form = e.target;
            const errorDiv = document.getElementById('hostRegisterError');
            errorDiv.style.display = 'none';

            const data = new FormData(form);
            const json = Object.fromEntries(data.entries());

            try {
                const response = await fetch('/api/admin/hosts/register', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-Admin-Key': 'changeme-admin-key'
                    },
                    body: JSON.stringify(json)
                });

                if (!response.ok) {
                    throw new Error('Failed to register host');
                }

                const result = await response.json();
                form.reset();
                load();
                alert(`Host registered: ${result.hostname} (API Key: ${result.api_key})`);
            } catch (err) {
                errorDiv.textContent = err.message;
                errorDiv.style.display = 'block';
            }
        };

        document.getElementById('registerDeviceForm').onsubmit = async function(e) {
            e.preventDefault();
            const form = e.target;
            const errorDiv = document.getElementById('deviceRegisterError');
            errorDiv.style.display = 'none';

            const data = new FormData(form);
            const json = Object.fromEntries(data.entries());

            try {
                const response = await fetch('/api/admin/devices/register', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-Admin-Key': 'changeme-admin-key'
                    },
                    body: JSON.stringify(json)
                });

                if (!response.ok) {
                    throw new Error('Failed to register device');
                }

                const result = await response.json();
                form.reset();
                load();
                alert(`Device registered: ${result.name}`);
            } catch (err) {
                errorDiv.textContent = err.message;
                errorDiv.style.display = 'block';
            }
        };

        load();
        setInterval(load, 15000);
        </script>
    </body>
    </html>
    """