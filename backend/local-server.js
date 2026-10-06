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
// Guest transport determines how a contributor connects:
//   srt-direct (default) — publish to mediamtx on one shared port (8890) with a
//     streamid (publish:guestN). mediamtx demuxes by streamid.
//   srt-listen — the ingest IS the SRT listener; the contributor dials STRAIGHT into
//     a per-guest port (SRT_PORT + index) with NO streamid. Mirrors quickstart's
//     MXL_GUEST_TRANSPORT=srt-listen (one listener owns one UDP port).
const GUEST_TRANSPORT = process.env.MXL_GUEST_TRANSPORT || 'srt-direct';
const LISTEN_MODE = GUEST_TRANSPORT === 'srt-listen';
const GUESTS = [
  { slot: 'guest1', label: 'Guest 1' },
  { slot: 'guest2', label: 'Guest 2' },
];
// In listen mode each guest gets its own port; in direct mode they share SRT_PORT.
const guestPort = (i) => (LISTEN_MODE ? SRT_PORT + i : SRT_PORT);

app.get('/api/mxl/ingest', (req, res) => {
  res.json({
    public_ip: PUBLIC_IP || null,
    srt_port: SRT_PORT,
    transport: GUEST_TRANSPORT,
    guests: GUESTS.map((g, i) => ({
      ...g,
      port: guestPort(i),
      // Listen mode carries no streamid (the port identifies the guest); direct mode
      // multiplexes on one port by streamid.
      streamid: LISTEN_MODE ? null : 'publish:' + g.slot,
      // SRT URL to paste into OBS / ffmpeg / any SRT source.
      srt_url: !PUBLIC_IP
        ? null
        : LISTEN_MODE
          ? `srt://${PUBLIC_IP}:${guestPort(i)}?latency=200`
          : `srt://${PUBLIC_IP}:${SRT_PORT}?streamid=publish:${g.slot}&latency=200`,
      qr: PUBLIC_IP ? `/api/mxl/ingest/qr/${g.slot}.png` : null,
    })),
  });
});

// Build the Larix Broadcaster deep-link the QR encodes.
//   larix://set/v1 (v1) · conn[] empty-index arrays · srtstreamid LOWERCASE ·
//   mode=av (the string) · name + url percent-encoded.
// ⚠️ The srtstreamid value keeps a LITERAL colon (publish:guestN), NOT %3A.
// Larix passes srtstreamid straight to the SRT handshake, and mediamtx requires
// the form "action:pathname" with a real colon — it rejects "publish%3AguestN"
// as an invalid stream ID. (Verified on hardware 2026-10-05: literal colon →
// "is publishing"; %3A → "invalid stream ID".) A colon is legal unencoded in a
// URL query value, so the deep-link still parses.
function larixUrl(slot, publicIp) {
  const name = encodeURIComponent('MXL ' + slot.replace(/^guest/, 'Guest '));
  const idx = Math.max(0, parseInt(slot.replace(/^guest/, ''), 10) - 1) || 0;
  if (LISTEN_MODE) {
    // Direct-listener: dial straight into the per-guest port, NO streamid (the port
    // identifies the guest). Larix connects as a caller to the ingest's listener.
    const srt = encodeURIComponent(`srt://${publicIp}:${guestPort(idx)}`);
    return `larix://set/v1?conn[][name]=${name}&conn[][url]=${srt}`
      + `&conn[][mode]=av&conn[][srtlatency]=1000`;
  }
  const srt = encodeURIComponent(`srt://${publicIp}:${SRT_PORT}`);
  const sid = 'publish:' + slot;   // literal colon — see note above
  return `larix://set/v1?conn[][name]=${name}&conn[][url]=${srt}`
    + `&conn[][mode]=av&conn[][srtstreamid]=${sid}&conn[][srtlatency]=1000`;
}

// Server-rendered QR PNG for a guest slot. Uses python3 + segno (pure-python,
// installed by quickstart). If the dep is missing we return 501 so the UI falls
// back to the copy-URL + typed fields — a missing QR is never a wall.
const { spawn } = require('child_process');
app.get('/api/mxl/ingest/qr/:slot.png', (req, res) => {
  const slot = req.params.slot;
  if (!/^guest[12]$/.test(slot)) return res.status(400).end();
  if (!PUBLIC_IP) return res.status(503).end();   // no IP → nothing to encode
  const payload = larixUrl(slot, PUBLIC_IP);
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
