"""
MiniMon SNMP Poller
Runs continuously. For every registered network device, polls the standard
IF-MIB over SNMP to find every interface (port) and whether it's up or down.
Detects transitions (up->down, device unreachable) and writes alerts.

Standard OIDs used (IF-MIB, present on virtually every switch/router):
  1.3.6.1.2.1.2.2.1.2   ifDescr        - port name, e.g. "GigabitEthernet0/1"
  1.3.6.1.2.1.2.2.1.7   ifAdminStatus  - configured state: 1=up, 2=down
  1.3.6.1.2.1.2.2.1.8   ifOperStatus   - actual link state: 1=up, 2=down
  1.3.6.1.2.1.1.3.0     sysUpTime      - used as a simple reachability check
"""
import os
import time
import logging
import psycopg2
import psycopg2.extras
from pysnmp.hlapi import (
    SnmpEngine, CommunityData, UdpTransportTarget, ContextData,
    ObjectType, ObjectIdentity, nextCmd, getCmd,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [minimon-snmp] %(message)s")
log = logging.getLogger(__name__)

DB_DSN = os.environ.get("MINIMON_DB_DSN", "dbname=minimon user=minimon password=changeme host=localhost")
POLL_INTERVAL = int(os.environ.get("MINIMON_SNMP_POLL_SECONDS", "60"))

IF_DESCR = "1.3.6.1.2.1.2.2.1.2"
IF_ADMIN_STATUS = "1.3.6.1.2.1.2.2.1.7"
IF_OPER_STATUS = "1.3.6.1.2.1.2.2.1.8"
SYS_UPTIME = "1.3.6.1.2.1.1.3.0"

STATUS_MAP = {"1": "up", "2": "down"}


def db():
    return psycopg2.connect(DB_DSN)


def snmp_walk(ip, community, version, base_oid):
    """Yields (index, value) pairs for a walk of base_oid on the target device."""
    mp_model = 1 if version == "2c" else 0
    for (errInd, errStat, errIdx, varBinds) in nextCmd(
        SnmpEngine(),
        CommunityData(community, mpModel=mp_model),
        UdpTransportTarget((ip, 161), timeout=3, retries=1),
        ContextData(),
        ObjectType(ObjectIdentity(base_oid)),
        lexicographicMode=False,
    ):
        if errInd or errStat:
            return
        for name, val in varBinds:
            oid_str = str(name)
            if_index = oid_str.split(".")[-1]
            yield if_index, str(val)


def snmp_is_reachable(ip, community, version):
    mp_model = 1 if version == "2c" else 0
    errInd, errStat, errIdx, varBinds = next(
        getCmd(
            SnmpEngine(),
            CommunityData(community, mpModel=mp_model),
            UdpTransportTarget((ip, 161), timeout=3, retries=1),
            ContextData(),
            ObjectType(ObjectIdentity(SYS_UPTIME)),
        )
    )
    return not errInd and not errStat


def insert_alert(cur, source, metric, message, severity="high"):
    cur.execute(
        "INSERT INTO alerts (hostname, metric, value, severity, message) VALUES (%s, %s, %s, %s, %s)",
        (source, metric, 0, severity, message),
    )


def poll_device(conn, device):
    device_id, name, ip, community, version = (
        device["id"], device["name"], device["ip_address"],
        device["snmp_community"], device["snmp_version"],
    )
    with conn.cursor() as cur:
        reachable = snmp_is_reachable(ip, community, version)

        if not reachable:
            if device["status"] != "down":
                insert_alert(cur, name, "device_status", f"{name} ({ip}) is UNREACHABLE via SNMP", "critical")
                log.warning(f"{name} is DOWN (unreachable)")
            cur.execute("UPDATE devices SET status='down', last_polled=now() WHERE id=%s", (device_id,))
            conn.commit()
            return

        if device["status"] != "up":
            log.info(f"{name} is UP")
        cur.execute("UPDATE devices SET status='up', last_polled=now() WHERE id=%s", (device_id,))

        descrs = dict(snmp_walk(ip, community, version, IF_DESCR))
        admin = dict(snmp_walk(ip, community, version, IF_ADMIN_STATUS))
        oper = dict(snmp_walk(ip, community, version, IF_OPER_STATUS))

        cur.execute("SELECT if_index, oper_status FROM interfaces WHERE device_id = %s", (device_id,))
        known = {str(r[0]): r[1] for r in cur.fetchall()}

        for if_index, descr in descrs.items():
            new_oper = STATUS_MAP.get(oper.get(if_index), "unknown")
            new_admin = STATUS_MAP.get(admin.get(if_index), "unknown")
            old_oper = known.get(if_index)

            cur.execute(
                """INSERT INTO interfaces (device_id, if_index, if_descr, oper_status, admin_status, last_polled, last_changed)
                   VALUES (%s, %s, %s, %s, %s, now(), now())
                   ON CONFLICT (device_id, if_index) DO UPDATE SET
                     if_descr = EXCLUDED.if_descr,
                     oper_status = EXCLUDED.oper_status,
                     admin_status = EXCLUDED.admin_status,
                     last_polled = now(),
                     last_changed = CASE WHEN interfaces.oper_status <> EXCLUDED.oper_status
                                          THEN now() ELSE interfaces.last_changed END""",
                (device_id, if_index, descr, new_oper, new_admin),
            )

            # Only alert on a real transition into "down", not on first discovery or already-known-down ports
            if old_oper is not None and old_oper != "down" and new_oper == "down" and new_admin == "up":
                msg = f"{name}: port {descr} (if {if_index}) went DOWN"
                insert_alert(cur, name, "port_status", msg, "high")
                log.warning(msg)

        conn.commit()


def watchdog_loop():
    log.info(f"SNMP poller starting, interval={POLL_INTERVAL}s")
    while True:
        conn = db()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM devices")
                devices = cur.fetchall()
            for device in devices:
                try:
                    poll_device(conn, device)
                except Exception as e:
                    log.error(f"error polling {device['name']}: {e}")
        finally:
            conn.close()
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    watchdog_loop()
