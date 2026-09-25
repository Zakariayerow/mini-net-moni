# MiniMon — A Zabbix-style Monitoring System (Build & Deploy Guide)

This is a minimal, working clone of Zabbix's core model: **agents** collect host metrics
and ship them to a central **server**, which stores time-series data, evaluates **threshold
rules**, fires **alerts**, and shows a live **dashboard**. It's a good base you can extend
(more metrics, SNMP polling, Slack/Telegram alerts, Grafana, etc.).

## Architecture

```
 [Monitored Host 1]      [Monitored Host 2]      [Network Device]
   agent.py (systemd)      agent.py (systemd)      (future: SNMP poller)
        │  HTTPS POST            │  HTTPS POST              │
        └───────────────┬────────┴──────────────────────────┘
                         ▼
              ┌─────────────────────┐
              │   MiniMon Server     │   FastAPI + Uvicorn
              │  (ingest, alerting,  │
              │   dashboard, API)    │
              └──────────┬───────────┘
                         ▼
              ┌─────────────────────┐
              │ PostgreSQL +         │
              │ TimescaleDB          │  (metrics history)
              └─────────────────────┘
                         ▲
              ┌──────────┴───────────┐
              │  Nginx (reverse proxy │
              │  + TLS via certbot)   │
              └───────────────────────┘
```

**Stack:** Python/FastAPI (server) · Python/psutil (agent) · PostgreSQL+TimescaleDB (storage)
· systemd (process management) · Nginx (reverse proxy/TLS).

---

## Part A — Deploy the Server (on your monitoring Ubuntu server)

### 1. Install prerequisites
```bash
sudo apt update
sudo apt install -y python3-venv python3-pip postgresql postgresql-contrib nginx curl gnupg
```

### 2. Install TimescaleDB
```bash
echo "deb https://packagecloud.io/timescale/timescaledb/ubuntu/ $(lsb_release -c -s) main" | \
  sudo tee /etc/apt/sources.list.d/timescaledb.list
curl -L https://packagecloud.io/timescale/timescaledb/gpgkey | sudo apt-key add -
sudo apt update
sudo apt install -y timescaledb-2-postgresql-16
sudo timescaledb-tune --yes
sudo systemctl restart postgresql
```

### 3. Create the database and user
```bash
sudo -u postgres psql -c "CREATE USER minimon WITH PASSWORD 'changeme';"
sudo -u postgres psql -c "CREATE DATABASE minimon OWNER minimon;"
sudo -u postgres psql -d minimon -c "CREATE EXTENSION IF NOT EXISTS timescaledb;"
```

### 4. Deploy the app code
```bash
sudo useradd -r -s /bin/false minimon
sudo mkdir -p /opt/minimon
sudo cp -r server /opt/minimon/server
sudo chown -R minimon:minimon /opt/minimon

cd /opt/minimon/server
sudo -u minimon python3 -m venv venv
sudo -u minimon ./venv/bin/pip install -r requirements.txt

# Load schema (run as your own sudo user, not the service account)
sudo -u postgres psql -d minimon -f /opt/minimon/server/schema.sql
```

### 5. Set real secrets
Edit `/opt/minimon/server` env vars — either export them in the systemd unit
(`systemd/minimon-server.service`) or put them in `/etc/minimon/server.env` and
reference with `EnvironmentFile=` in the unit. At minimum change:
- `MINIMON_DB_DSN` password
- `MINIMON_ADMIN_KEY` (used to register new hosts)
- SMTP settings if you want email alerts

### 6. Install and start the systemd service
```bash
sudo cp systemd/minimon-server.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now minimon-server
sudo systemctl status minimon-server
```

### 7. Put Nginx in front (and add TLS)
```bash
sudo cp nginx/minimon.conf /etc/nginx/sites-available/minimon
sudo ln -s /etc/nginx/sites-available/minimon /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# Optional but recommended: HTTPS
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d monitor.example.com
```

### 8. Open the firewall
```bash
sudo ufw allow 'Nginx Full'
sudo ufw allow 8000/tcp   # only if agents connect directly, bypassing nginx
```

Visit `http://<server-ip>` (or your domain) — you should see the empty MiniMon dashboard.

---

## Part B — Register and Deploy an Agent (on each monitored host)

### 1. Register the host with the server (run once, from anywhere with curl)
```bash
curl -X POST "http://<server-ip>/api/hosts/register?hostname=web-01&group_name=production" \
     -H "X-Admin-Key: changeme-admin-key"
```
This returns an `api_key` — save it for the next step.

### 2. Install the agent on the target host
```bash
sudo apt update && sudo apt install -y python3-venv
sudo useradd -r -s /bin/false minimon
sudo mkdir -p /opt/minimon/agent /etc/minimon
sudo cp -r agent/* /opt/minimon/agent/
sudo chown -R minimon:minimon /opt/minimon

cd /opt/minimon/agent
sudo -u minimon python3 -m venv venv
sudo -u minimon ./venv/bin/pip install -r requirements.txt

sudo cp agent.yaml.example /etc/minimon/agent.yaml
sudo nano /etc/minimon/agent.yaml   # set server_url + the api_key from step 1
```

### 3. Start the agent as a service
```bash
sudo cp systemd/minimon-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now minimon-agent
sudo systemctl status minimon-agent
journalctl -u minimon-agent -f   # confirm it's sending metrics
```

Repeat Part B for every server you want monitored. Within ~30 seconds the host will
appear on the dashboard with live CPU/memory/disk figures.

---

## Part C — Monitor Routers/Switches (SNMP, no agent needed)

Network devices don't run your agent, so they're monitored differently: the server
polls them over **SNMP** and reads their standard interface table (works on virtually
any managed switch/router/firewall — Cisco, MikroTik, Ubiquiti, HP/Aruba, etc.).

### 1. Enable SNMP on the device
On the switch/router itself (varies by vendor), enable SNMP v2c and set a community
string, e.g. on Cisco IOS:
```
snmp-server community minimonRO RO
```
Use a **read-only** community — the poller never needs write access.

### 2. Register the device with MiniMon
```bash
curl -X POST http://<server-ip>/api/devices/register \
  -H "X-Admin-Key: changeme-admin-key" \
  -H "Content-Type: application/json" \
  -d '{
        "name": "core-switch-01",
        "ip_address": "192.168.1.1",
        "snmp_community": "minimonRO",
        "snmp_version": "2c",
        "device_type": "switch",
        "group_name": "network"
      }'
```
Repeat for every router/switch/firewall you want tracked.

### 3. Install and start the SNMP poller (on the monitoring server)
It reuses the same venv as the server app:
```bash
cd /opt/minimon/server
sudo -u minimon ./venv/bin/pip install -r requirements.txt   # picks up pysnmp

sudo cp systemd/minimon-snmp-poller.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now minimon-snmp-poller
journalctl -u minimon-snmp-poller -f   # confirm it's finding ports
```
Within one poll cycle (60s by default) the device and every discovered port appear
on the dashboard, each port tagged `up` or `down`.

### What "down" means for a device vs. a host vs. a port
| Entity | How MiniMon decides it's down |
|---|---|
| **Server (agent host)** | No metrics received for 90s (`MINIMON_HOST_DOWN_SECONDS`) — a background watchdog thread checks this every 30s |
| **Network device** | SNMP request times out — the device itself isn't answering |
| **Port** | Device answers SNMP fine, but that specific port's `ifOperStatus` reads down while `ifAdminStatus` is up (i.e., it's supposed to be up but the link is actually down — this excludes intentionally disabled ports) |

Each of these writes a row into the same `alerts` table, and the dashboard's
**Network Devices** section shows every device with its live port grid — down ports
are highlighted red with the port name and status.

---

## Part D — Onboarding Your Institution's Whole Network

Registering devices one `curl` at a time doesn't scale past a handful. Here's how to
plan and bulk-load a real campus/institution network.

### 1. Build an inventory first
Before touching MiniMon, get (or make) a spreadsheet of everything you want watched:
- **Core network**: internet router/edge firewall, core switch(es)
- **Distribution layer**: one switch per building or floor
- **Servers**: anything in your server room / data closet — web, database, file,
  mail, DNS, LMS/e-learning, portal servers
- For each network device: a **name**, its **management IP**, and whether SNMP is
  already enabled (ask your network admin if unsure — many institutional switches
  have it on by default with a default community string that should be changed)

A reasonable naming/grouping convention for an institution: use `group_name` for the
building or department (`library`, `admin-block`, `hostel-a`, `cs-dept`) — this is
what lets you scan the dashboard by location later.

### 2. Get SNMP enabled and credentials issued
Coordinate with whoever manages the switches:
- Turn on **SNMP v2c, read-only**, with a community string that isn't the vendor
  default (e.g. not "public") — treat it like a password
- If devices are managed (Cisco, Aruba, MikroTik, Ubiquiti, etc.), this is typically
  one config line per device, no downtime required
- If SNMP is centrally blocked by firewall/ACL, you'll need a rule allowing the
  MiniMon server's IP to reach UDP port 161 on each device

### 3. Fill in the CSV templates
Two templates ship in `scripts/`:
- `devices.csv.example` — one row per router/switch/firewall
- `hosts.csv.example` — one row per server (just hostname + group; the agent itself
  generates the metrics, so no IP/SNMP info is needed here)

Copy them, rename (drop `.example`), and fill in your real inventory.

### 4. Bulk-register everything
```bash
cd scripts
chmod +x register_devices.sh register_hosts.sh

./register_devices.sh devices.csv http://<server-ip> <your-admin-key>
sudo systemctl restart minimon-snmp-poller

./register_hosts.sh hosts.csv http://<server-ip> <your-admin-key>
# writes hosts_registered.csv with a unique api_key per hostname
```

### 5. Roll out agents to servers
For each row in `hosts_registered.csv`, install the agent on that physical/virtual
server (Part B, step 2 onward) using the matching `api_key`. If you manage servers
with Ansible/Puppet/etc., this is a good candidate to automate — the agent install
is just: copy `agent/`, create venv, pip install, drop in `agent.yaml` with that
host's key, enable the systemd unit.

### 6. Phase it in rather than all at once
For a first rollout, a sensible order is: **core switch + edge router/firewall first**
(these are the highest-impact single points of failure), then **one building** end to
end (its distribution switch + its servers) to validate the process, then expand
building by building.

### Scaling and security notes for institution-scale deployments
- The default 60s SNMP poll interval and 30s host-watchdog check comfortably handle
  a few hundred devices/hosts on one server; beyond that, consider running a second
  MiniMon server per campus/site and pointing agents at their nearest one
- Put the dashboard behind your institution's SSO/VPN, or at minimum restrict it with
  Nginx `allow`/`deny` rules — it exposes internal IP layout and infrastructure names
- Rotate the `MINIMON_ADMIN_KEY` and SNMP community strings periodically, and don't
  commit the filled-in CSVs (they contain SNMP credentials) to a public repo

---

## How alerting works

- Thresholds live in the `thresholds` table (`cpu_percent > 85`, `mem_percent > 90`,
  `disk_percent > 90` by default). Add more with plain SQL:
  ```sql
  INSERT INTO thresholds (metric, operator, value, severity) VALUES ('load1', '>', 4, 'warning');
  ```
- Every time an agent posts metrics, the server checks them against all enabled
  thresholds and inserts a row into `alerts` (+ sends an email if SMTP is configured).
- View recent alerts at `/api/alerts` or on the dashboard.

## Extending this into something closer to full Zabbix

- **SNMP polling** for switches/routers/firewalls: add a poller process using `pysnmp`
  that writes into the same `metrics` table.
- **More notification channels**: add Slack/Telegram webhook calls next to
  `send_alert_email` in `app.py`.
- **Historical graphs**: point Grafana at the same Postgres/TimescaleDB instance for
  richer dashboards than the built-in one.
- **High availability**: run two `minimon-server` instances behind Nginx/HAProxy, and
  use TimescaleDB's native replication.
- **Auto-discovery**: add a scheduled job that pings a subnet and auto-registers hosts
  that respond, similar to Zabbix's network discovery.

## Security notes
- Change `MINIMON_ADMIN_KEY` and the Postgres password before exposing this to a network.
- Put the server behind HTTPS (see certbot step) — agent API keys are sent in a header
  and should not travel over plain HTTP on an untrusted network.
- Restrict inbound port 8000 to only the Nginx proxy (or to your agent IP ranges) via `ufw`.
