#!/usr/bin/env node
// local-server.js — the self-contained control plane for an MXL quickstart box.
//
// Mounts the portable mxl-routes.js (cut / key / pattern / status / repair) and
// serves the web/local.html operator UI. No prodbots, no external services — an
// adopter who cloned the repo gets a real browser switcher, not curl commands.
//
//   node backend/local-server.js
//   # then open http://127.0.0.1:3100/   (localhost-only by default; set
//   # MXL_CONTROL_BIND=0.0.0.0 to reach it on the LAN, and only behind a reverse
//   # proxy or SSH tunnel if the box is public — the API has no auth. See SECURITY.md)
//
// Config (all optional):
//   MXL_CONTROL_PORT   listen port                 (default 3100)
//   MXL_CONTROL_BIND   bind address                (default 127.0.0.1 — localhost only)
//   MXL_VM_URL         where the easy-mxl control APIs live (default from the
//                      facility manifest's network.mxl_vm, else http://127.0.0.1)
//   MXL_PROGRAM_URL    WebRTC program page to embed (default /program proxy below)
//   MXL_THUMBS_DIR     local dir of per-flow JPEGs to serve (default /mxl-domain/thumbs)
//   MXL_THUMBS_ORIGIN  if the dir isn't local, proxy thumbs from here (e.g. :8086)
//
// Only dependency is express. The repo ships backend/package.json pinning it; run
// `npm install --prefix backend` once (or `npm i express`).

const express = require('express');
const path = require('path');
const http = require('http');
const fs = require('fs');

const registerMxlRoutes = require('./mxl-routes');
let facility = null;
try { facility = require('./facility').load(); } catch { /* optional */ }

const PORT = parseInt(process.env.MXL_CONTROL_PORT || '3100', 10);
const BIND = process.env.MXL_CONTROL_BIND || '127.0.0.1';
const WEB_DIR = path.join(__dirname, '..', 'web');
const THUMBS_DIR = process.env.MXL_THUMBS_DIR || '/mxl-domain/thumbs';
const THUMBS_ORIGIN = process.env.MXL_THUMBS_ORIGIN || '';  // e.g. http://127.0.0.1:8086

const app = express();
app.use(express.json());

// The portable control routes (/api/mxl/status|input|key|pattern|repair).
registerMxlRoutes(app);

// Expose the facility's slot layout so local.html builds its buttons from the
// manifest (labels + count) instead of hardcoding — the single source of truth.
app.get('/api/mxl/slots', (req, res) => {
  if (!facility) return res.json({ slots: [], note: 'no facility manifest found' });
  const p = facility.program || {};
  const names = p.layout_inputs || p.selector_inputs || [];
  const labels = p.layout_input_labels || null;
  const slots = names.map((role, i) => ({
    slot: i,
    role,
    label: labels && labels[i] ? labels[i] : role,
  }));
  res.json({ slots });
});

// Per-flow thumbnails for the multiview grid. tools/mxl_thumbs.py writes one small
// JPEG per source (no decode, tiny CPU) into the MXL domain's thumbs dir; the grid
// polls /api/mxl/thumbs/<name>.jpg ~every 1.5s. Two modes:
//   - local: sendFile from MXL_THUMBS_DIR (the default, when the server runs on a
//     box with the domain mounted)
//   - proxy: if MXL_THUMBS_ORIGIN is set, forward to that static server (e.g. :8086)
// A missing thumb returns 404 so the UI shows its own "no signal" slate — the grid
// stays honest about which sources are actually producing frames.
app.get('/api/mxl/thumbs/:name', (req, res) => {
  // reject path traversal — only a bare filename is allowed
  const name = req.params.name;
  if (!/^[A-Za-z0-9_.\-]+\.jpg$/.test(name)) return res.status(400).end();
  if (THUMBS_ORIGIN) {
    const t = new URL(THUMBS_ORIGIN);
    // Honor a base path in the origin (e.g. http://host:8086/thumbs) — some thumbs
    // servers serve the JPEGs under a subpath rather than at the root.
    const base = t.pathname.replace(/\/$/, '');
    const pr = http.request({
      hostname: t.hostname, port: t.port || 80, method: 'GET',
      path: `${base}/${name}`, headers: { host: t.host },
    }, (r) => { res.writeHead(r.statusCode, r.headers); r.pipe(res); });
    pr.on('error', () => res.status(502).end());
    pr.end();
    return;
  }
  const file = path.join(THUMBS_DIR, name);
  fs.access(file, fs.constants.R_OK, (err) => {
    if (err) return res.status(404).end();
    res.sendFile(file);
  });
});

// ── "Add your camera" ingest helper ─────────────────────────────────────────
// A tester's phone/encoder needs THIS box's SRT publish point. The box knows its
// own public IP (quickstart computes it and passes MXL_PUBLIC_IP); the browser
// can't derive it reliably (it may be hitting us through an SSH tunnel, so
// window.location is 127.0.0.1). So we surface it here. No public IP set (e.g.
// not launched via quickstart) → public_ip:null and the UI shows a fallback.
const PUBLIC_IP = process.env.MXL_PUBLIC_IP || '';
const SRT_PORT = parseInt(process.env.MXL_GUEST_SRT_PORT || '8890', 10);
// Guest-ingest info (SRT URL / streamid / Larix deep-link) lives in a pure,
// dependency-free module so it can be unit-tested without express (see
// backend/ingest-info.js + tests/js/mxl-ingest-transport.test.js). srt-direct is the
// default; MXL_GUEST_TRANSPORT=srt-listen switches to per-guest listener ports.
const { ingestConfig, ingestInfo, larixUrl } = require('./ingest-info');
const INGEST_CFG = ingestConfig({
  publicIp: PUBLIC_IP,
  srtPort: SRT_PORT,
  transport: process.env.MXL_GUEST_TRANSPORT || 'srt-listen',
});

app.get('/api/mxl/ingest', (req, res) => {
  res.json(ingestInfo(INGEST_CFG));
});

// Server-rendered QR PNG for a guest slot. Uses python3 + segno (pure-python,
// installed by quickstart). If the dep is missing we return 501 so the UI falls
// back to the copy-URL + typed fields — a missing QR is never a wall.
const { spawn } = require('child_process');
app.get('/api/mxl/ingest/qr/:slot.png', (req, res) => {
  const slot = req.params.slot;
  if (!/^guest[12]$/.test(slot)) return res.status(400).end();
  if (!PUBLIC_IP) return res.status(503).end();   // no IP → nothing to encode
  const payload = larixUrl(INGEST_CFG, slot);
  // segno writes a PNG to stdout; -o - with --scale for a crisp phone-scannable size.
  const py = spawn('python3', ['-c',
    'import sys,segno; segno.make(sys.argv[1], error="m").save(sys.stdout.buffer, kind="png", scale=6, border=2)',
    payload]);
  const chunks = [];
  py.stdout.on('data', (d) => chunks.push(d));
  py.on('error', () => { if (!res.headersSent) res.status(501).end(); });
  py.on('close', (code) => {
    if (code !== 0 || !chunks.length) { if (!res.headersSent) res.status(501).end(); return; }
    res.set('Content-Type', 'image/png');
    res.set('Cache-Control', 'no-store');
    res.end(Buffer.concat(chunks));
  });
});

// ── Health Skin (read-only) ──────────────────────────────────────────────────
// A SEPARATE page from the control Skin: System (CPU/mem/load), Grain flow (bps +
// unique-fps per flow, catching repeat-wedges), per-source fps and pipeline-writer
// state. Nothing here mutates the facility — the Core stays lean and on-air-focused.
//
// The grain measurement is already done by tools/grain_probe.py (writes
// <thumbs>/grains.json every 3s, with its own load guards). We only read that file
// + /proc + the three pipeline/status endpoints, and cache the aggregate for a
// couple seconds so N open Health tabs collapse to one scrape.
const health = require('./health-info');
// Two possible probe snapshots, in preference order:
//   health.json — written by tools/mxl_thumbs.py, which is ALREADY running beside the
//     multiview thumbnails (no extra process, no extra MXL readers). Carries per-slot
//     {age, frozen} (same frozen-reader detection) + system + viewers.
//   grains.json — written by tools/grain_probe.py (bps + unique-fps). Supported for
//     adopters who run that probe; we fall back to it if health.json isn't present.
const THUMBS_HEALTH_PATH = process.env.MXL_THUMBS_HEALTH_PATH || path.join(THUMBS_DIR, 'health.json');
const GRAINS_PATH = process.env.MXL_GRAINS_PATH || path.join(THUMBS_DIR, 'grains.json');
const HEALTH_RATE = parseInt(process.env.MXL_GRAIN_RATE || '30', 10);
const HEALTH_TTL_MS = parseInt(process.env.MXL_HEALTH_TTL_MS || '2000', 10);
// The probe snapshots live wherever the thumbnails do. In file mode we read
// <THUMBS_DIR>/*.json; in proxy mode (MXL_THUMBS_ORIGIN set — e.g. the domain is only
// mounted in a container and a tiny http server exposes it) we fetch the same JSON over
// HTTP, exactly like the /api/mxl/thumbs route. This keeps the Core off the Docker
// socket and portable to either layout.
const THUMBS_SNAP_ORIGIN = THUMBS_ORIGIN; // e.g. http://127.0.0.1:8086/thumbs

async function readSnapshot(fileName, filePath) {
  if (THUMBS_SNAP_ORIGIN) {
    try {
      const base = THUMBS_SNAP_ORIGIN.replace(/\/$/, '');
      const r = await fetch(`${base}/${fileName}`, { signal: AbortSignal.timeout(3000) });
      return r.ok ? await r.json() : null;
    } catch { return null; }
  }
  return health.readGrainsSnapshot(filePath); // file mode (sync read, cheap)
}

// Resolve the MXL control-API base the same way mxl-routes does, so the pipeline
// status probes hit the same VM the cuts do.
const HEALTH_MXL_VM = process.env.MXL_VM_URL
  || (process.env.MXL_VM_FROM_MANIFEST === '1' && facility && facility.network && `http://${facility.network.mxl_vm}`)
  || 'http://127.0.0.1';

function fetchJson(port, apiPath, timeoutMs = 3000) {
  return fetch(`${HEALTH_MXL_VM}:${port}${apiPath}`, { signal: AbortSignal.timeout(timeoutMs) })
    .then((r) => (r.ok ? r.json() : null))
    .catch(() => null); // unreachable port -> null; shapePipelines marks it so
}

let _prevCpu = health.readProcStat();
let _healthCache = { at: 0, payload: null };

async function buildHealth() {
  const [selector, keyer, encoder] = await Promise.all([
    fetchJson(9604, '/pipeline/status'),
    fetchJson(9605, '/pipeline/status'),
    fetchJson(9601, '/pipeline/status'),
  ]);
  // Prefer the already-running thumbs health.json; fall back to grain_probe's grains.json.
  const thumbs = await readSnapshot('health.json', THUMBS_HEALTH_PATH);
  let grains, probeSystem = null;
  if (thumbs && thumbs.slots) {
    const t = health.classifyThumbsHealth(thumbs, {});
    grains = { stale: t.stale, age_s: t.age_s, flows: t.flows, source: 'thumbs' };
    probeSystem = t.system;      // load/mem/viewers measured where the probe runs (the box)
  } else {
    const gr = await readSnapshot('grains.json', GRAINS_PATH);
    grains = health.classifyGrains(gr, { expectedRate: HEALTH_RATE });
    grains.source = 'grains';
  }
  // System: use the probe's host-true numbers when available (Core may run off-box);
  // otherwise read local /proc. CPU% always comes from local /proc deltas.
  const curCpu = health.readProcStat();
  const system = {
    cpu_pct: health.cpuPercent(_prevCpu, curCpu),
    cpus: (probeSystem && probeSystem.cpus) || health.cpuCount(),
    mem: (probeSystem && probeSystem.mem) || health.readMem(),
    load: (probeSystem && probeSystem.load) || health.readLoad(),
    viewers: probeSystem ? probeSystem.viewers : null,
  };
  _prevCpu = curCpu;
  const pipelines = health.shapePipelines({ selector, keyer, encoder });
  return health.composeHealth({ ts: Date.now() / 1000, system, grains, pipelines });
}

app.get('/api/mxl/health', async (req, res) => {
  try {
    const now = Date.now();
    if (!_healthCache.payload || now - _healthCache.at > HEALTH_TTL_MS) {
      _healthCache = { at: now, payload: await buildHealth() };
    }
    res.set('Cache-Control', 'no-store');
    // Read-only public telemetry — allow cross-origin reads so the public site
    // (mxlswitcher.com) can embed the live numbers. GET-only, no secrets, no control.
    res.set('Access-Control-Allow-Origin', process.env.MXL_HEALTH_CORS || '*');
    res.json(_healthCache.payload);
  } catch (e) {
    res.status(500).json({ error: String((e && e.message) || e) });
  }
});

// The Health Skin page (read-only). Separate URL from the control Skin.
app.get('/health', (req, res) => res.sendFile(path.join(WEB_DIR, 'health.html')));

// Serve the operator UI + its static assets.
app.get('/', (req, res) => res.sendFile(path.join(WEB_DIR, 'local.html')));
app.use(express.static(WEB_DIR));

// Reverse proxy to the local mediamtx WebRTC server so the UI can embed the
// program feed same-origin (no mixed-content / CORS). The mediamtx player page
// requests its WHEP negotiation at the ROOT (/mxl2webrtc/whep/...), so we proxy
// BOTH the page (/program -> /mxl2webrtc) and that path (/mxl2webrtc/*) through.
const PROGRAM_ORIGIN = process.env.MXL_PROGRAM_ORIGIN || 'http://127.0.0.1:8889';
const PROGRAM_PATH = process.env.MXL_PROGRAM_PATH || 'mxl2webrtc';
function proxyTo(targetPath) {
  return (req, res) => {
    const target = new URL(PROGRAM_ORIGIN);
    const opts = {
      hostname: target.hostname, port: target.port || 80,
      path: targetPath(req), method: req.method,
      headers: { ...req.headers, host: target.host },
    };
    const pr = http.request(opts, (r) => { res.writeHead(r.statusCode, r.headers); r.pipe(res); });
    pr.on('error', (e) => res.status(502).send('program feed unavailable: ' + e.message));
    req.pipe(pr);
  };
}
// /program[/...] -> the mediamtx player page for the program path
app.use('/program', proxyTo((req) => `/${PROGRAM_PATH}` + (req.url === '/' ? '/' : req.url)));
// the player's WHEP + asset requests go to the root path — proxy them straight through
app.use('/' + PROGRAM_PATH, proxyTo((req) => `/${PROGRAM_PATH}${req.url}`));

app.listen(PORT, BIND, () => {
  // Report the ACTUAL target mxl-routes resolved (Review R6d: this used to print the
  // manifest IP even when the routes defaulted to 127.0.0.1 — a misleading log).
  const vm = process.env.MXL_VM_URL
    || (process.env.MXL_VM_FROM_MANIFEST === '1' && facility && facility.network && `http://${facility.network.mxl_vm}`)
    || 'http://127.0.0.1';
  console.log(`mxl local control: http://${BIND}:${PORT}/  (controlling MXL VM ${vm})`);
  if (BIND === '127.0.0.1') {
    console.log('  localhost-only. Set MXL_CONTROL_BIND=0.0.0.0 to reach it on the LAN.');
  } else {
    console.log('  NOTE: bound to all interfaces. If this box is public, put it behind');
    console.log('  a reverse proxy / SSH tunnel — the control API has no auth of its own.');
  }
});
