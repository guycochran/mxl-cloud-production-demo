#!/usr/bin/env sh
# mxl-flows.sh — runtime flow-UUID discovery for the MXL switcher healers.
#
# WHY: pgm-heal and selfheal used to carry HARDCODED flow UUIDs ("G1=9e111e00…",
# "PATTERN=d3e15194…"). Those UUIDs are box-specific — they are whatever the
# quickstart happened to mint on THIS facility — so a script full of them cannot
# be committed to the shared repo (reviewer issue #2): on any other facility the
# UUIDs are wrong, the selector gets wired to flows that do not exist, and the
# down-selector rebuild wires only pattern/playout while 400ing on the phantom
# guest UUIDs (HW Oct 10).
#
# The facility is already self-describing. `mxl-info -d DOMAIN -l` prints, per
# device group, a stable ROLE name and the flows under it:
#
#     Guest1: mxl:///mxl-domain?id=9e111e00-…
#         Video : 9e111e00-… - Guest 1
#     Pattern: mxl:///mxl-domain?id=d3e15194-…&id=7c1dd9ba-…
#         Video : d3e15194-… - Pattern Video
#         Audio : 7c1dd9ba-… - Pattern Audio
#
# The group names (Pattern, Playout, Guest1..GuestN, Guest5Stable, Keyer,
# Selector) are the role identity; the UUID under each is discovered at runtime.
# So the healers name ROLES, never UUIDs, and work on any facility.
#
# Sourced, not executed. POSIX sh. Requires: DOMAIN set, DOCKER set (the caller's
# `docker` or `sudo docker`), SEL_CTR set (the selector container name).

# mxl_flows_dump — cache a single `mxl-info -l` snapshot in MXL_FLOWS_CACHE so
# repeated lookups in one heal pass cost one docker exec, not one per role.
# Call mxl_flows_refresh at the top of each pass to invalidate it.
MXL_FLOWS_CACHE=""
mxl_flows_refresh(){ MXL_FLOWS_CACHE=""; }
mxl_flows_dump(){
  [ -n "$MXL_FLOWS_CACHE" ] && { printf '%s' "$MXL_FLOWS_CACHE"; return 0; }
  MXL_FLOWS_CACHE=$($DOCKER exec "$SEL_CTR" sh -c "/opt/mxl/tools/mxl-info/mxl-info -d $DOMAIN -l" 2>/dev/null)
  [ -n "$MXL_FLOWS_CACHE" ] || return 1
  printf '%s' "$MXL_FLOWS_CACHE"
}

# flow_by_role ROLE [MEDIA]  — UUID of the flow for a device group ($1, e.g.
# "Guest1", "Pattern", "Guest5Stable") and media kind ($2, Video|Audio, default
# Video). Empty string + rc1 if that role/media is not present in the domain.
#
#   The group header line is "ROLE: mxl://…"; the flow lines under it are
#   "<ws>Video : <uuid> - <label>". We locate the group block, then the first
#   matching media line within it. Portable awk — no bashisms.
flow_by_role(){
  _role="$1"; _media="${2:-Video}"
  mxl_flows_dump | awk -v role="$_role" -v media="$_media" '
    $0 ~ "^"role":" { inblk=1; next }
    /^[A-Za-z0-9]+:/  { inblk=0 }           # next group header ends the block
    inblk && $1==media && $2==":" { print $3; exit }
  '
}

# flow_present ROLE [MEDIA]  — true if the role/media flow exists right now.
flow_present(){ [ -n "$(flow_by_role "$1" "${2:-Video}")" ]; }

# all_video_roles — every device group that currently offers a Video flow, one
# role per line, in mxl-info order. Lets a healer enumerate "whatever sources
# exist" instead of hardcoding a slot list.
all_video_roles(){
  mxl_flows_dump | awk '
    /^[A-Za-z0-9]+:/ { role=$1; sub(":","",role); next }
    $1=="Video" && $2==":" && role!="" { print role; role="" }
  '
}
