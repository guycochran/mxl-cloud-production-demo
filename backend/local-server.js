#!/usr/bin/env node
// local-server.js — the self-contained control plane for an MXL quickstart box.
//
// Mounts the portable mxl-routes.js (cut / key / pattern / status / repair) and
// serves the web/local.html operator UI. No prodbots, no external services — an
// adopter who cloned the repo gets a real browser switcher, not curl commands.
//
//   node backend/local-server.js
//   # then open http://<box>:3100/   (bound to all interfaces; put behind a
//   # reverse proxy or SSH tunnel if the box is public — see SECURITY.md)
//
// Config (all optional):
//   MXL_CONTROL_PORT   listen port                 (default 3100)
//   MXL_CONTROL_BIND   bind address                (default 0.0.0.0 — LAN-reachable)
//   MXL_VM_URL         where the easy-mxl control APIs live (default from the
//                      facility manifest's network.mxl_vm, else http://127.0.0.1)
//   MXL_PROGRAM_URL    WebRTC program page to embed (default /program proxy below)
//
// Only dependency is express. The repo ships backend/package.json pinning it; run
// `npm install --prefix backend` once (or `npm i express`).

const express = require('express');
const path = require('path');
const http = require('http');

const registerMxlRoutes = require('./mxl-routes');
let facility = null;
try { facility = require('./facility').load(); } catch { /* optional */ }

const PORT = parseInt(process.env.MXL_CONTROL_PORT || '3100', 10);
const BIND = process.env.MXL_CONTROL_BIND || '0.0.0.0';
const WEB_DIR = path.join(__dirname, '..', 'web');

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
  const vm = (facility && facility.network && facility.network.mxl_vm) || '127.0.0.1';
  console.log(`mxl local control: http://${BIND}:${PORT}/  (controlling MXL VM ${vm})`);
  if (BIND === '0.0.0.0') {
    console.log('  NOTE: bound to all interfaces. If this box is public, put it behind');
    console.log('  a reverse proxy / SSH tunnel — the control API has no auth of its own.');
  }
});
