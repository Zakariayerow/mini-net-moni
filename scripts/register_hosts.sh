#!/usr/bin/env bash
# Bulk-register server hosts with MiniMon from a CSV file, printing each
# resulting api_key so you can hand them out for agent installs.
# Usage: ./register_hosts.sh hosts.csv http://<server-ip> <admin-key>
#
# CSV columns (header required): hostname,group_name
#
# Output: hosts_registered.csv with hostname,api_key — keep this file safe,
# it's the credential each agent needs to authenticate.

set -euo pipefail

CSV_FILE="${1:?Usage: $0 hosts.csv <server-url> <admin-key>}"
SERVER_URL="${2:?Usage: $0 hosts.csv <server-url> <admin-key>}"
ADMIN_KEY="${3:?Usage: $0 hosts.csv <server-url> <admin-key>}"
OUT_FILE="hosts_registered.csv"

echo "hostname,api_key" > "$OUT_FILE"

tail -n +2 "$CSV_FILE" | while IFS=',' read -r hostname group; do
  [ -z "$hostname" ] && continue
  echo "Registering host: $hostname..."
  RESPONSE=$(curl -s -X POST "${SERVER_URL}/api/hosts/register?hostname=${hostname}&group_name=${group}" \
    -H "X-Admin-Key: ${ADMIN_KEY}")
  API_KEY=$(echo "$RESPONSE" | grep -o '"api_key":"[^"]*"' | cut -d'"' -f4)
  echo "  -> api_key: ${API_KEY:-FAILED}"
  echo "${hostname},${API_KEY}" >> "$OUT_FILE"
done

echo "Done. Per-host api_keys written to ${OUT_FILE} — distribute each key"
echo "to its matching server's /etc/minimon/agent.yaml before starting the agent."
