#!/usr/bin/env bash
# run-stabilizer.sh <guestN> — supervise a flow_stabilizer for one guest slot.
#
# The stabilizer reads the VOLATILE guest flow (9eNNNe00) and writes a STABLE flow
# (57abNe00) that exists from boot and never dies — so the input-selector can be wired
# ONCE to the stable flows and NEVER re-wired when a correspondent joins/drops. That's
# what keeps PGM from ever being interrupted on a live join (docs/NEVER-INTERRUPT-PGM.md).
#
# Runs flow_stabilizer.py inside a container that has mxlsrc/mxlsink + python-gi (the
# html5-keyer image). systemd supervises this wrapper; the stabilizer exits on a deep
# wedge and gets relaunched.
#
# Flow UUID convention: guestN video = 9e{N}{N}{N}e00-...; stable = 57ab{N}e00-...
set -eu

N="${1:?usage: run-stabilizer.sh <guestN e.g. guest3>}"
N="${N#guest}"                                  # accept "guest3" or "3"
CONTAINER="${STAB_CONTAINER:-html5-keyer}"      # image with mxlsrc/mxlsink + gi
DOMAIN="${MXL_DOMAIN:-/mxl-domain}"
VOL="9e${N}${N}${N}e00-aaaa-4bbb-8ccc-000000000001"
STABLE="57ab${N}e00-aaaa-4bbb-8ccc-000000000001"
SCRIPT_HOST="${STAB_SCRIPT:-/home/guycochran/mxl-switcher/tools/flow_stabilizer.py}"

for i in $(seq 1 30); do
  docker ps --format '{{.Names}}' | grep -qx "$CONTAINER" && break
  sleep 2
done
docker ps --format '{{.Names}}' | grep -qx "$CONTAINER" || { echo "stabilizer guest$N: $CONTAINER not up" >&2; exit 1; }

# refresh the script into the container, reap any stale stabilizer for THIS flow only
docker cp "$SCRIPT_HOST" "$CONTAINER":/tmp/flow_stabilizer.py 2>/dev/null || true
docker exec "$CONTAINER" pkill -9 -f "flow_stabilizer.py guest$N " 2>/dev/null || true
sleep 1
echo "stabilizer guest$N: $VOL -> $STABLE"
# foreground so systemd owns the lifecycle; exits on deep wedge -> unit restarts it
exec docker exec "$CONTAINER" python3 -u /tmp/flow_stabilizer.py "guest$N" "$VOL" "$STABLE" "Guest $N Stable"
