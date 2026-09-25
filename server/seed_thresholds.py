#!/usr/bin/env python3
"""
Seed initial threshold rules into MiniMon database.
Creates alert conditions for CPU, memory, disk, and interface metrics.
Run: python seed_thresholds.py
"""
import os
import psycopg2
import psycopg2.extras

DB_DSN = os.environ.get("MINIMON_DB_DSN", "dbname=minimon user=minimon password=changeme host=localhost")

# Comprehensive threshold rules
THRESHOLDS = [
    # CPU thresholds
    {"metric": "cpu_percent", "operator": ">", "value": 85.0, "severity": "high"},
    {"metric": "cpu_percent", "operator": ">", "value": 95.0, "severity": "critical"},
    
    # Memory thresholds
    {"metric": "mem_percent", "operator": ">", "value": 80.0, "severity": "warning"},
    {"metric": "mem_percent", "operator": ">", "value": 90.0, "severity": "high"},
    {"metric": "mem_percent", "operator": ">", "value": 95.0, "severity": "critical"},
    
    # Disk thresholds
    {"metric": "disk_percent", "operator": ">", "value": 80.0, "severity": "warning"},
    {"metric": "disk_percent", "operator": ">", "value": 90.0, "severity": "high"},
    {"metric": "disk_percent", "operator": ">", "value": 95.0, "severity": "critical"},
    
    # Load average thresholds (warning if load1 > 4.0)
    {"metric": "load1", "operator": ">", "value": 4.0, "severity": "warning"},
    {"metric": "load1", "operator": ">", "value": 8.0, "severity": "high"},
]

def seed_thresholds():
    """Insert threshold rules into database."""
    try:
        conn = psycopg2.connect(DB_DSN)
        print(f"[✓] Connected to database: {DB_DSN}")
        
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Clear existing thresholds (optional, comment out to preserve)
            # cur.execute("DELETE FROM thresholds;")
            # print("[✓] Cleared existing thresholds")
            
            # Insert thresholds
            for rule in THRESHOLDS:
                cur.execute(
                    """INSERT INTO thresholds (metric, operator, value, severity, enabled)
                       VALUES (%s, %s, %s, %s, TRUE)
                       ON CONFLICT DO NOTHING""",
                    (rule["metric"], rule["operator"], rule["value"], rule["severity"]),
                )
            
            conn.commit()
            print(f"[✓] Inserted {len(THRESHOLDS)} threshold rules")
            
            # Verify insertion
            cur.execute("SELECT COUNT(*) as count FROM thresholds WHERE enabled = TRUE")
            count = cur.fetchone()["count"]
            print(f"[✓] Database now has {count} active threshold rules\n")
            
            # Display all thresholds
            cur.execute("""
                SELECT id, metric, operator, value, severity 
                FROM thresholds 
                WHERE enabled = TRUE 
                ORDER BY metric, severity DESC
            """)
            print("Active Thresholds:")
            print("-" * 70)
            for row in cur.fetchall():
                print(f"  {row['metric']:15} {row['operator']:2} {row['value']:6.1f}  [{row['severity'].upper():8}] (id={row['id']})")
            print("-" * 70)
        
        conn.close()
        print("[✓] Thresholds seeded successfully!")
        
    except psycopg2.Error as e:
        print(f"[✗] Database error: {e}")
        return False
    except Exception as e:
        print(f"[✗] Error: {e}")
        return False
    
    return True

if __name__ == "__main__":
    success = seed_thresholds()
    exit(0 if success else 1)
