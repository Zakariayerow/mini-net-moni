"""
MiniMon Agent
Runs on each monitored host. Collects CPU, memory, disk, network, and load
metrics and ships them to the MiniMon server on an interval.
"""
import time
import yaml
import psutil
import requests
import logging
import os

logging.basicConfig(level=logging.INFO, format="%(asctime)s [minimon-agent] %(message)s")
log = logging.getLogger(__name__)

CONFIG_PATH = os.environ.get("MINIMON_AGENT_CONFIG", "/etc/minimon/agent.yaml")


def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def collect_metrics(prev_net):
    cpu = psutil.cpu_percent(interval=1)
    mem = psutil.virtual_memory().percent
    disk = psutil.disk_usage("/").percent
    net = psutil.net_io_counters()
    load1 = os.getloadavg()[0] if hasattr(os, "getloadavg") else 0.0

    sent_kb = (net.bytes_sent - prev_net.bytes_sent) / 1024 if prev_net else 0
    recv_kb = (net.bytes_recv - prev_net.bytes_recv) / 1024 if prev_net else 0

    return {
        "cpu_percent": cpu,
        "mem_percent": mem,
        "disk_percent": disk,
        "net_sent_kb": round(sent_kb, 2),
        "net_recv_kb": round(recv_kb, 2),
        "load1": load1,
    }, net


def main():
    cfg = load_config()
    server_url = cfg["server_url"].rstrip("/")
    api_key = cfg["api_key"]
    interval = cfg.get("interval_seconds", 30)

    prev_net = None
    log.info(f"starting agent, reporting to {server_url} every {interval}s")

    while True:
        try:
            payload, prev_net = collect_metrics(prev_net)
            resp = requests.post(
                f"{server_url}/api/metrics",
                json=payload,
                headers={"X-API-Key": api_key},
                timeout=10,
            )
            if resp.status_code != 200:
                log.warning(f"server rejected metrics: {resp.status_code} {resp.text}")
            else:
                log.info(f"sent metrics: {payload}")
        except Exception as e:
            log.error(f"failed to send metrics: {e}")

        time.sleep(interval)


if __name__ == "__main__":
    main()
