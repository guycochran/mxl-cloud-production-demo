#!/bin/bash
# start-jonas-leg.sh — send our live Keyer PGM (or PGM Lite) to a remote
# MXL target as raw grains over the fabric.   Run on VM1 as guy.
#
#   ./start-jonas-leg.sh <target.json> [public-ip] [flow-uuid]
#
#   target.json = the file the REMOTE side's target printed/wrote (-t @file)
#   public-ip   = patch the embedded sockaddr to this IP first (NAT case)
#   flow-uuid   = default 5c73394e-... (full 1080p30 Keyer PGM, ~1.3 Gbps);
#                 use 119070e0-aaaa-4bbb-8ccc-000000000001 for PGM Lite
#                 (960x540, ~0.33 Gbps — fits GigE) after starting pgm_lite.
set -euo pipefail
TJ=${1:?target.json required}
PUB=${2:-}
FLOW=${3:-5c73394e-85df-50a3-8988-5edde5b5522a}
cd ~/fabric/jonas
if [ -n "$PUB" ]; then
  python3 patch-target-ip.py "$TJ" "$PUB" target-live.json
  TJ=target-live.json
fi
pkill -f "jonas-leg" 2>/dev/null || true
LD_LIBRARY_PATH=$HOME/fabric/lib nohup $HOME/fabric/bin/mxl-fabrics-demo/mxl-fabrics-demo \
  -i -d /dev/shm/mxl/domain_1 -p tcp -n 10.0.0.4 -f "$FLOW" -t @"$PWD/$TJ" \
  > jonas-leg.log 2>&1 &
echo "initiator started (flow $FLOW) — watch: tail -f ~/fabric/jonas/jonas-leg.log"
sleep 6
tail -2 jonas-leg.log
