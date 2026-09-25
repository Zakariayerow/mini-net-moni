-- MiniMon Threshold Rules Seed Script
-- Run as: psql -U postgres -d minimon -f seed_thresholds.sql

-- Comprehensive metric threshold rules for automated alerting

-- CPU thresholds
INSERT INTO thresholds (metric, operator, value, severity, enabled) VALUES
    ('cpu_percent', '>', 85.0, 'high', TRUE),
    ('cpu_percent', '>', 95.0, 'critical', TRUE)
ON CONFLICT DO NOTHING;

-- Memory thresholds
INSERT INTO thresholds (metric, operator, value, severity, enabled) VALUES
    ('mem_percent', '>', 80.0, 'warning', TRUE),
    ('mem_percent', '>', 90.0, 'high', TRUE),
    ('mem_percent', '>', 95.0, 'critical', TRUE)
ON CONFLICT DO NOTHING;

-- Disk thresholds
INSERT INTO thresholds (metric, operator, value, severity, enabled) VALUES
    ('disk_percent', '>', 80.0, 'warning', TRUE),
    ('disk_percent', '>', 90.0, 'high', TRUE),
    ('disk_percent', '>', 95.0, 'critical', TRUE)
ON CONFLICT DO NOTHING;

-- Load average thresholds
INSERT INTO thresholds (metric, operator, value, severity, enabled) VALUES
    ('load1', '>', 4.0, 'warning', TRUE),
    ('load1', '>', 8.0, 'high', TRUE)
ON CONFLICT DO NOTHING;

-- Verify insertion
SELECT 'Threshold Rules Seeded Successfully' as status;
SELECT COUNT(*) as total_rules FROM thresholds WHERE enabled = TRUE;

-- Display all active thresholds
\echo '--- Active Thresholds ---'
SELECT 
    id,
    metric,
    operator,
    value,
    severity,
    enabled
FROM thresholds 
WHERE enabled = TRUE 
ORDER BY metric, severity DESC;
