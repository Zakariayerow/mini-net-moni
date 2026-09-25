-- MiniMon database schema
-- Run as: sudo -u postgres psql -d minimon -f schema.sql

CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS hosts (
    id            SERIAL PRIMARY KEY,
    hostname      TEXT UNIQUE NOT NULL,
    api_key       TEXT UNIQUE NOT NULL,
    group_name    TEXT DEFAULT 'default',
    status        TEXT DEFAULT 'unknown',   -- up | down | unknown, set by the watchdog
    last_seen     TIMESTAMPTZ,
    created_at    TIMESTAMPTZ DEFAULT now()
);

-- Network devices polled over SNMP (routers, switches, firewalls)
CREATE TABLE IF NOT EXISTS devices (
    id              SERIAL PRIMARY KEY,
    name            TEXT UNIQUE NOT NULL,
    ip_address      TEXT NOT NULL,
    snmp_community  TEXT DEFAULT 'public',
    snmp_version    TEXT DEFAULT '2c',
    device_type     TEXT DEFAULT 'switch',   -- switch | router | firewall | other
    group_name      TEXT DEFAULT 'network',
    status          TEXT DEFAULT 'unknown',  -- up | down | unknown
    last_polled     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- One row per physical/logical port on a device
CREATE TABLE IF NOT EXISTS interfaces (
    id              SERIAL PRIMARY KEY,
    device_id       INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    if_index        INTEGER NOT NULL,
    if_descr        TEXT,
    oper_status     TEXT DEFAULT 'unknown',  -- up | down | unknown
    admin_status    TEXT DEFAULT 'unknown',
    last_changed    TIMESTAMPTZ DEFAULT now(),
    last_polled     TIMESTAMPTZ,
    UNIQUE(device_id, if_index)
);

CREATE TABLE IF NOT EXISTS metrics (
    time          TIMESTAMPTZ NOT NULL,
    hostname      TEXT NOT NULL,
    cpu_percent   DOUBLE PRECISION,
    mem_percent   DOUBLE PRECISION,
    disk_percent  DOUBLE PRECISION,
    net_sent_kb   DOUBLE PRECISION,
    net_recv_kb   DOUBLE PRECISION,
    load1         DOUBLE PRECISION
);

-- Turn metrics into a TimescaleDB hypertable for efficient time-series storage
SELECT create_hypertable('metrics', 'time', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_metrics_hostname_time ON metrics (hostname, time DESC);

CREATE TABLE IF NOT EXISTS thresholds (
    id            SERIAL PRIMARY KEY,
    metric        TEXT NOT NULL,         -- cpu_percent | mem_percent | disk_percent
    operator      TEXT NOT NULL,         -- '>' or '<'
    value         DOUBLE PRECISION NOT NULL,
    severity      TEXT DEFAULT 'warning',
    enabled       BOOLEAN DEFAULT TRUE
);

INSERT INTO thresholds (metric, operator, value, severity) VALUES
    ('cpu_percent', '>', 85, 'high'),
    ('mem_percent', '>', 90, 'high'),
    ('disk_percent', '>', 90, 'critical')
ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS alerts (
    id            SERIAL PRIMARY KEY,
    hostname      TEXT NOT NULL,
    metric        TEXT NOT NULL,
    value         DOUBLE PRECISION,
    severity      TEXT,
    message       TEXT,
    triggered_at  TIMESTAMPTZ DEFAULT now(),
    resolved_at   TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role          TEXT DEFAULT 'admin',
    created_at    TIMESTAMPTZ DEFAULT now()
);
