#!/usr/bin/env bash
# Bulk-register network devices with MiniMon from a CSV file.
# Usage: ./register_devices.sh devices.csv http://<server-ip> <admin-key>
#
# CSV columns (header required): name,ip_address,snmp_community,device_type,group_name

set -euo pipefail

CSV_FILE="${1:?Usage: $0 devices.csv <server-url> <admin-key>}"
SERVER_URL="${2:?Usage: $0 devices.csv <server-url> <admin-key>}"
ADMIN_KEY="${3:?Usage: $0 devices.csv <server-url> <admin-key>}"

tail -n +2 "$CSV_FILE" | while IFS=',' read -r name ip community dtype group; do
  [ -z "$name" ] && continue
  echo "Registering device: $name ($ip)..."
  curl -s -o /dev/null -w "  -> HTTP %{http_code}\n" -X POST "${SERVER_URL}/api/devices/register" \
    -H "X-Admin-Key: ${ADMIN_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"name\":\"${name}\",\"ip_address\":\"${ip}\",\"snmp_community\":\"${community}\",\"device_type\":\"${dtype}\",\"group_name\":\"${group}\"}"
done

echo "Done. Start/restart the SNMP poller so it picks up the new devices:"
echo "  sudo systemctl restart minimon-snmp-poller"
