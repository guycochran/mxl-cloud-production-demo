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

// ── Thumbs health.json (the probe that's ALREADY running on the box) ──────────
// tools/mxl_thumbs.py writes health.json beside the thumbnails — same cadence, no
// extra process — with per-slot {age, frozen} where:
//   age    = seconds since the LAST frame arrived      (delivery stalled -> dead)
//   frozen = seconds since the CONTENT last CHANGED    (picture stuck -> frozen)
// This is the cheaper, already-live source for the Health Skin: it carries the SAME
// frozen-reader detection as grain_probe's unique-fps, plus system + viewers, so we
// don't run a second set of MXL readers. classifyGrains (grains.json / grain_probe)
// stays supported for adopters who run that probe instead — both normalize to the
// same shape the UI consumes.
// staleAfterS default is 25: tools/mxl_thumbs.py's health loop writes on a ~15s
// cadence (its slots[].age/frozen are themselves sub-2s fresh, but the summary file
// is rewritten every 15s), so a poll landing late in that cycle can legitimately see
// an ~18-20s-old file — only flag stale past 25s (probe actually dead). grain_probe's
// grains.json is 3s — callers using it can pass a tighter staleAfterS.
function classifyThumbsHealth(h, { nowS, staleAfterS = 25, frozenAfterS = 5, deadAfterS = 5 } = {}) {
  if (!h || typeof h !== 'object' || !h.slots) {
    return { stale: true, age_s: null, flows: {}, system: null };
  }
  const now = nowS != null ? nowS : Date.now() / 1000;
  const ageS = h.ts != null ? Math.max(0, now - h.ts) : null;
  const stale = ageS == null ? true : ageS > staleAfterS;
  const flows = {};
  for (const [name, v] of Object.entries(h.slots)) {
    // v == null / age == null means the PROBE has no reader for this slot (rebuilding,
    // or it couldn't attach) — NOT that the source is confirmed dead. Report 'unknown' so
    // the UI doesn't paint a live-but-unprobed camera red. A slot with real stale `age`
    // data below IS classified dead/frozen. (HW Oct 10: the thumbs reader for a live
    // Makito/PTZ can sit null while mxl-info shows the flow advancing — false-dead fixed.)
    if (!v || v.age == null) { flows[name] = { state: 'unknown', age: null, frozen: null }; continue; }
    let state = 'ok';
    if (v.age > deadAfterS) state = 'dead';          // no new frames at all
    else if (v.frozen != null && v.frozen > frozenAfterS) state = 'frozen'; // frames arrive, picture stuck
    flows[name] = { state, age: v.age, frozen: v.frozen != null ? v.frozen : null };
  }
  // System block, assembled from the same file (load/mem are host-true in the probe).
  const system = {
    load: (h.load1 != null) ? { load1: h.load1, load5: h.load5, load15: h.load15 } : null,
    cpus: h.cores != null ? h.cores : null,
    mem: (h.mem_total_mb != null && h.mem_avail_mb != null) ? {
      total_mb: h.mem_total_mb,
      used_mb: h.mem_total_mb - h.mem_avail_mb,
      used_pct: Math.round(((h.mem_total_mb - h.mem_avail_mb) / h.mem_total_mb) * 100),
    } : null,
    viewers: (h.viewers && typeof h.viewers === 'object') ? (h.viewers.count != null ? h.viewers.count : null)
      : (typeof h.viewers === 'number' ? h.viewers : null),
  };
  return { stale, age_s: ageS != null ? Math.round(ageS * 10) / 10 : null, flows, system, procs: h.procs || null };
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
      input_flow_uuids: Array.isArray(sel.input_flow_uuids) ? sel.input_flow_uuids : [],
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

// ── Sources panel: one row per wired selector input, joined with liveness. ────
// Pure. Joins three things we ALREADY have — no new probes:
//   • the selector's wired UUID list + active_input (which slot is PGM)
//   • the thumbs per-flow {state} map (keyed by thumbs slot name, e.g. "guest1")
//   • a caller-supplied roleMap: { "<uuid>": {name, role}, ... } for friendly labels
//     and a flowSlot map { "<uuid>": "<thumbs-slot-name>" } to look up liveness.
// roleMap/flowSlot are built from config on the server (box-specific), so this stays
// generic/committable. A uuid starting with the stable prefix (57ab) is flagged as a
// never-interrupt correspondent slot. Unknown uuids still render (short uuid + "input").
function buildSources({ selector, flows = {}, roleMap = {}, flowSlot = {}, stablePrefix = '57ab' } = {}) {
  const uuids = (selector && Array.isArray(selector.input_flow_uuids)) ? selector.input_flow_uuids : [];
  const active = selector && selector.active_input != null ? selector.active_input : null;
  return uuids.map((uuid, i) => {
    const meta = roleMap[uuid] || {};
    const slotName = flowSlot[uuid] || meta.slot || null;
    const liveness = slotName && flows[slotName] ? flows[slotName].state : null;
    const stable = typeof uuid === 'string' && uuid.startsWith(stablePrefix);
    // A stable slot with no live correspondent reads 'dead'/none from thumbs but is
    // correctly "standing by", not broken — surface that distinctly.
    let state = liveness || 'unknown';
    if (stable && (state === 'dead' || state === 'unknown')) state = 'standby';
    return {
      slot: i,
      uuid: typeof uuid === 'string' ? uuid.slice(0, 8) : null,
      name: meta.name || (stable ? `Correspondent ${i}` : `Input ${i}`),
      role: meta.role || (stable ? 'correspondent' : 'input'),
      state,
      is_pgm: active != null && i === active,
      stable,
    };
  });
}

// ── Compose the whole payload (pure; takes already-read inputs). ──────────────
function composeHealth({ system, grains, pipelines, sources, ts }) {
  return {
    ts, system: system || null,
    grains: grains || { stale: true, flows: {} },
    pipelines: pipelines || {},
    sources: sources || [],
  };
}

module.exports = {
  readProcStat, cpuPercent, readMem, readLoad, cpuCount,
  classifyGrains, classifyThumbsHealth, readGrainsSnapshot, shapePipelines, buildSources, composeHealth,
};
