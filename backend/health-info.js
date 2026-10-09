// backend/health-info.js — read-only health aggregation for the Health Skin.
//
// This is deliberately SEPARATE from the control plane (mxl-routes.js). The Core
// Skin (web/local.html) is what a TD touches on air — cut/arm/take. Grain
// internals, CPU/mem and pipeline-writer state belong on their own read-only
// page (web/health.html, served at /health). Nothing here mutates the facility.
//
// Cost note: the expensive part — measuring per-flow grain rate AND unique-frames
// /s (to catch repeat-wedges where the head advances but the picture is frozen) —
// is already done by tools/grain_probe.py, which writes a rolling-window snapshot
// to <thumbs>/grains.json every 3s and has hard load guards (pauses >26, exits
// >29). We only READ that file here, so N health viewers add ~one file read each,
// never a container spawn. The live PGM/encode path is never touched.

'use strict';

const fs = require('fs');

// ── System: CPU / mem / load from /proc (Linux). Pure reads, microseconds. ──
// CPU % needs two samples of /proc/stat; the caller holds the previous snapshot
// so a single scrape is cheap and the delta is computed across polls.
function readProcStat(readFile = fs.readFileSync) {
  // Returns {total, idle} jiffies for the aggregate "cpu " line, or null.
  try {
    const line = String(readFile('/proc/stat', 'utf8')).split('\n')
      .find((l) => l.startsWith('cpu '));
    if (!line) return null;
    const n = line.trim().split(/\s+/).slice(1).map(Number);
    // user nice system idle iowait irq softirq steal ...
    const idle = (n[3] || 0) + (n[4] || 0); // idle + iowait
    const total = n.reduce((a, b) => a + (Number.isFinite(b) ? b : 0), 0);
    return { total, idle };
  } catch { return null; }
}

function cpuPercent(prev, cur) {
  if (!prev || !cur) return null;
  const dt = cur.total - prev.total;
  const di = cur.idle - prev.idle;
  if (dt <= 0) return null;
  return Math.max(0, Math.min(100, Math.round((1 - di / dt) * 100)));
}

function readMem(readFile = fs.readFileSync) {
  try {
    const info = {};
    String(readFile('/proc/meminfo', 'utf8')).split('\n').forEach((l) => {
      const m = l.match(/^(\w+):\s+(\d+)/);
      if (m) info[m[1]] = Number(m[2]); // kB
    });
    const totalKb = info.MemTotal || 0;
    const availKb = info.MemAvailable != null ? info.MemAvailable : info.MemFree || 0;
    if (!totalKb) return null;
    const usedKb = totalKb - availKb;
    return {
      total_mb: Math.round(totalKb / 1024),
      used_mb: Math.round(usedKb / 1024),
      used_pct: Math.round((usedKb / totalKb) * 100),
    };
  } catch { return null; }
}

function readLoad(readFile = fs.readFileSync) {
  try {
    const p = String(readFile('/proc/loadavg', 'utf8')).trim().split(/\s+/);
    return { load1: Number(p[0]), load5: Number(p[1]), load15: Number(p[2]) };
  } catch { return null; }
}

function cpuCount(readFile = fs.readFileSync) {
  try {
    return String(readFile('/proc/stat', 'utf8')).split('\n')
      .filter((l) => /^cpu\d+ /.test(l)).length || null;
  } catch { return null; }
}

// ── Grain flow: read grain_probe's snapshot (already on disk, atomic-renamed). ─
// Shape written by tools/grain_probe.py: {ts, flows: {name: {bps, ufps}|null}}.
// We classify each flow so the UI can paint green/amber/red without logic:
//   dead   — null (probe read nothing in the window)
//   frozen — bps healthy but ufps collapsed (repeat-wedge: head moves, picture
//            doesn't). This is the exact keyer-bypass symptom we hit live.
//   ok     — bps and ufps both near the expected rate
// expectedRate defaults to 30 (the facility is 1080p30). stale = snapshot older
// than staleAfterS (probe died / not running).
function classifyGrains(snapshot, { expectedRate = 30, nowS, staleAfterS = 12 } = {}) {
  if (!snapshot || typeof snapshot !== 'object' || !snapshot.flows) {
    return { stale: true, age_s: null, flows: {} };
  }
  const now = nowS != null ? nowS : Date.now() / 1000;
  const ageS = snapshot.ts != null ? Math.max(0, now - snapshot.ts) : null;
  const stale = ageS == null ? true : ageS > staleAfterS;
  const flows = {};
  for (const [name, v] of Object.entries(snapshot.flows)) {
    if (!v || v.bps == null) {
      flows[name] = { state: 'dead', bps: 0, ufps: 0 };
      continue;
    }
    const bps = v.bps;
    const ufps = v.ufps != null ? v.ufps : null;
    // frozen: grains tick at ~rate but unique frames are a fraction of them.
    // Use 60% of expected as the unique-fps floor for "moving".
    let state = 'ok';
    if (bps < expectedRate * 0.5) state = 'slow';
    if (ufps != null && ufps < expectedRate * 0.6 && bps >= expectedRate * 0.5) state = 'frozen';
    flows[name] = { state, bps, ufps };
  }
  return { stale, age_s: ageS != null ? Math.round(ageS * 10) / 10 : null, flows };
}

function readGrainsSnapshot(grainsPath, readFile = fs.readFileSync) {
  try { return JSON.parse(String(readFile(grainsPath, 'utf8'))); }
  catch { return null; }
}

// ── Pipeline writers: normalize the three pipeline/status payloads for the UI. ─
// Pure — the caller fetches the JSON (or passes nulls when a port is unreachable)
// and we shape it. No network here, so it's unit-testable.
function shapePipelines({ selector, keyer, encoder } = {}) {
  const sel = selector || {};
  const key = keyer || {};
  const enc = encoder || {};
  const fmt = (f) => (f && f.format
    ? {
      w: f.format.frame_width, h: f.format.frame_height,
      rate: f.format.grain_rate
        ? Math.round((f.format.grain_rate.numerator || 0) / (f.format.grain_rate.denominator || 1))
        : null,
    }
    : null);
  return {
    selector: selector ? {
      running: !!sel.running, error: sel.error || null,
      active_input: sel.active_input != null ? sel.active_input : null,
      inputs: Array.isArray(sel.input_flow_uuids) ? sel.input_flow_uuids.length : null,
      format: fmt(sel),
    } : { running: false, unreachable: true },
    keyer: keyer ? {
      running: !!key.running, error: key.error || null,
      key_on: !!key.key_on, mode: key.mode || null,
      format: fmt(key),
    } : { running: false, unreachable: true },
    encoder: encoder ? {
      running: !!enc.running, error: enc.error || null,
      viewers: enc.viewers != null ? enc.viewers : null,
      bitrate_kbps: enc.encoder && enc.encoder.bitrate != null ? enc.encoder.bitrate : null,
      tune: enc.encoder ? enc.encoder.tune : null,
    } : { running: false, unreachable: true },
  };
}

// ── Compose the whole payload (pure; takes already-read inputs). ──────────────
function composeHealth({ system, grains, pipelines, ts }) {
  return { ts, system: system || null, grains: grains || { stale: true, flows: {} }, pipelines: pipelines || {} };
}

module.exports = {
  readProcStat, cpuPercent, readMem, readLoad, cpuCount,
  classifyGrains, readGrainsSnapshot, shapePipelines, composeHealth,
};
