# MiniMon Full Stack Launch & Deployment Guide

## 🚀 Quick Start (Complete Setup)

### Prerequisites
- Python 3.8+
- PostgreSQL 12+
- TCP ports 5432 (PostgreSQL), 8000 (FastAPI server)

---

## Step 1: PostgreSQL Database Setup

### Windows
```powershell
# If PostgreSQL is installed, it should be running as a service
# Check service status:
Get-Service postgresql-x64-15

# If not running:
Start-Service postgresql-x64-15

# Or manually start:
pg_ctl -D "C:\Program Files\PostgreSQL\15\data" start
```

### Linux
```bash
sudo systemctl start postgresql
# OR
sudo service postgresql start
```

### macOS
```bash
brew services start postgresql
```

---

## Step 2: Initialize Database Schema

```bash
cd server/
psql -U postgres -d minimon -f schema.sql
```

**Expected output:**
```
CREATE EXTENSION
CREATE TABLE
...
INSERT 0 3
```

---

## Step 3: Seed Threshold Rules

```bash
python seed_thresholds.py
```

**Expected output:**
```
[✓] Connected to database
[✓] Inserted 10 threshold rules
[✓] Database now has 10 active threshold rules
```

---

## Step 4: Start FastAPI Server

```bash
# From server/ directory
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

**Expected output:**
```
INFO:     Uvicorn running on http://0.0.0.0:8000
INFO:     Application startup complete
```

**Access Dashboard:**
- Dashboard: http://localhost:8000
- API Docs (Swagger): http://localhost:8000/docs
- API Redoc: http://localhost:8000/redoc

---

## Step 5: Start SNMP Poller (Optional)

For monitoring network devices (switches, routers, firewalls):

```bash
# From server/ directory
MINIMON_SNMP_POLL_SECONDS=60 python snmp_poller.py
```

This requires network devices to be registered via the API.

---

## Step 6: Deploy Agents on Monitored Servers

### On each monitored server/machine:

1. **Create agent configuration:**
```bash
sudo mkdir -p /etc/minimon
sudo vi /etc/minimon/agent.yaml
```

2. **Config template (`agent.yaml`):**
```yaml
server_url: http://monitoring-server:8000
api_key: YOUR_AGENT_API_KEY_HERE
interval_seconds: 30
```

3. **Register agent with server:**
```bash
# From server directory, register the agent:
curl -X POST http://localhost:8000/api/hosts/register \
  -H "X-Admin-Key: changeme-admin-key" \
  -d "hostname=app-server-01&group_name=production"
```

Output will include the `api_key` to put in agent.yaml.

4. **Start agent:**
```bash
cd agent/
python agent.py
```

---

## 🔗 API Quick Reference

### Register a Server/Host
```bash
curl -X POST http://localhost:8000/api/hosts/register \
  -H "X-Admin-Key: changeme-admin-key" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "hostname=web-server-01&group_name=production"
```

### Submit Agent Metrics
```bash
curl -X POST http://localhost:8000/api/metrics \
  -H "X-API-Key: <agent-api-key>" \
  -H "Content-Type: application/json" \
  -d '{
    "cpu_percent": 45.2,
    "mem_percent": 62.1,
    "disk_percent": 78.5,
    "net_sent_kb": 1024.5,
    "net_recv_kb": 2048.3,
    "load1": 2.1
  }'
```

### Get All Hosts with Latest Metrics
```bash
curl http://localhost:8000/api/hosts
```

### Get Host Metric History
```bash
curl http://localhost:8000/api/metrics/web-server-01?limit=100
```

### Get Recent Alerts
```bash
curl http://localhost:8000/api/alerts?limit=50
```

### List Network Devices
```bash
curl http://localhost:8000/api/devices
```

### Register Network Device (SNMP)
```bash
curl -X POST http://localhost:8000/api/devices/register \
  -H "X-Admin-Key: changeme-admin-key" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "core-switch-01",
    "ip_address": "192.168.1.10",
    "snmp_community": "public",
    "snmp_version": "2c",
    "device_type": "switch",
    "group_name": "network"
  }'
```

---

## 🔧 Environment Variables

### Required
```bash
export MINIMON_DB_DSN="dbname=minimon user=minimon password=<password> host=localhost"
export MINIMON_ADMIN_KEY="your-secure-admin-key"
```

### Optional
```bash
# SMTP for email alerts
export MINIMON_SMTP_HOST="smtp.example.com"
export MINIMON_SMTP_PORT="587"
export MINIMON_SMTP_USER="alerts@example.com"
export MINIMON_SMTP_PASS="password"
export MINIMON_ALERT_TO="ops@example.com"

# SNMP polling interval (seconds)
export MINIMON_SNMP_POLL_SECONDS="60"

# Host down detection threshold (seconds)
export MINIMON_HOST_DOWN_SECONDS="90"

# Agent configuration path
export MINIMON_AGENT_CONFIG="/etc/minimon/agent.yaml"
```

---

## 📊 Dashboard Features

**Servers (Host Metrics):**
- Hostname, Status (up/down), Group
- Last seen timestamp
- Latest CPU, Memory, Disk percentages
- Color-coded thresholds (OK/Warning/Critical)

**Network Devices (SNMP):**
- Device name, IP address, Status
- Device type (switch, router, firewall)
- Port status overview
- Number of down ports

**Alerts:**
- Recent critical and warning alerts
- Severity levels (warning, high, critical)
- Message and trigger timestamp

---

## 🐛 Troubleshooting

### FastAPI Server Won't Start
**Issue:** `Address already in use`
```bash
# Kill the process using port 8000
lsof -i :8000 | grep LISTEN | awk '{print $2}' | xargs kill -9
```

### PostgreSQL Connection Refused
**Issue:** `connection to server at "localhost" (127.0.0.1), port 5432 failed`
- Ensure PostgreSQL is running: `psql --version`
- Check connection settings: `MINIMON_DB_DSN`
- Default: `dbname=minimon user=minimon password=changeme host=localhost`

### SNMP Poller Errors
**Issue:** `ImportError: cannot import name 'SnmpEngine'`
```bash
pip install --upgrade pysnmp>=5.0.0
```

### No Data on Dashboard
1. Verify at least one agent is registered and running
2. Check agent logs: running with `-v` flag
3. Verify API key matches the registered agent
4. Monitor server logs for errors

---

## 📈 Typical Architecture

```
┌─────────────────────────────────────┐
│      MiniMon Server (FastAPI)       │
│  ├─ REST API (/api/hosts,metrics)   │
│  ├─ Dashboard (/)                   │
│  ├─ Background Watchdog             │
│  └─ Alert Engine                    │
└─────────────┬───────────────────────┘
              │
              ├─→ PostgreSQL + TimescaleDB
              │   (Metrics, Alerts, Config)
              │
    ┌─────────┴─────────┐
    │                   │
┌───v────────┐    ┌───v────────┐
│   Agents   │    │   SNMP      │
│ (Servers)  │    │  Poller     │
│            │    │ (Switches)  │
└────────────┘    └────────────┘

Agents push metrics → Server stores → Dashboard displays
Server polls SNMP → Storage → Dashboard shows device status
```

---

## ✅ Verification Checklist

Run the verification script:
```bash
python verify_stack.py
```

Should show all green checkmarks (✓):
- [ ] FastAPI Server running
- [ ] Dashboard accessible
- [ ] API Documentation available
- [ ] PostgreSQL connected
- [ ] Database configured

---

## Support & Logs

**Server Logs:**
```bash
# Check for errors
tail -f /tmp/minimon-server.log  # Linux
```

**Agent Logs:**
```bash
tail -f /var/log/minimon-agent.log  # Linux
```

**View Recent Alerts via API:**
```bash
curl http://localhost:8000/api/alerts?limit=20 | jq '.'
```
