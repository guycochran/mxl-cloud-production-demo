// Verifies the "Add your camera" ingest endpoint builds the right SRT URL/QR payload
// for each guest transport: srt-direct (one shared port + streamid) vs srt-listen
// (per-guest port, no streamid). Spawns the real local-server.js on an ephemeral port
// with the transport env set, hits /api/mxl/ingest, and asserts the shape — no MXL VM
// needed (the ingest route is pure config).
const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { spawn } = require('node:child_process');

const SERVER = path.join(__dirname, '..', '..', 'backend', 'local-server.js');
const PUBLIC_IP = '203.0.113.7';           // TEST-NET-3 documentation IP
const BASE_PORT = 8890;

// Start local-server.js with a given env, wait until it answers, return {port, stop}.
async function startServer(extraEnv) {
  // Pick a high control port unlikely to collide; retry-free (one per test).
  const ctrlPort = 34000 + Math.floor(BASE_PORT % 1000) + Object.keys(extraEnv).length
    + (extraEnv.MXL_GUEST_TRANSPORT === 'srt-listen' ? 1 : 0);
  const env = {
    ...process.env,
    MXL_CONTROL_PORT: String(ctrlPort),
    MXL_CONTROL_BIND: '127.0.0.1',
    MXL_PUBLIC_IP: PUBLIC_IP,
    MXL_GUEST_SRT_PORT: String(BASE_PORT),
    ...extraEnv,
  };
  const proc = spawn('node', [SERVER], { env, stdio: ['ignore', 'pipe', 'pipe'] });
  // wait for the "mxl local control: http://..." banner (or timeout)
  await new Promise((resolve, reject) => {
    const to = setTimeout(() => reject(new Error('server did not start')), 5000);
    proc.stdout.on('data', (d) => {
      if (String(d).includes('mxl local control')) { clearTimeout(to); resolve(); }
    });
    proc.on('exit', (c) => { clearTimeout(to); reject(new Error('server exited ' + c)); });
  });
  return {
    port: ctrlPort,
    stop: () => new Promise((r) => { proc.once('exit', r); proc.kill(); }),
  };
}

async function getIngest(port) {
  const res = await fetch(`http://127.0.0.1:${port}/api/mxl/ingest`);
  assert.equal(res.status, 200);
  return res.json();
}

test('srt-direct (default): one shared port + publish:guestN streamid', async () => {
  const srv = await startServer({});   // no MXL_GUEST_TRANSPORT -> default
  try {
    const j = await getIngest(srv.port);
    assert.equal(j.transport, 'srt-direct');
    assert.equal(j.guests.length, 2);
    const [g1, g2] = j.guests;
    assert.equal(g1.port, BASE_PORT);
    assert.equal(g2.port, BASE_PORT);                      // SHARED port
    assert.equal(g1.streamid, 'publish:guest1');
    assert.equal(g2.streamid, 'publish:guest2');
    assert.match(g1.srt_url, /streamid=publish:guest1/);   // LITERAL colon, not %3A
    assert.ok(!g1.srt_url.includes('%3A'), 'streamid must keep a literal colon');
  } finally { await srv.stop(); }
});

test('srt-listen: per-guest port, NO streamid', async () => {
  const srv = await startServer({ MXL_GUEST_TRANSPORT: 'srt-listen' });
  try {
    const j = await getIngest(srv.port);
    assert.equal(j.transport, 'srt-listen');
    const [g1, g2] = j.guests;
    assert.equal(g1.port, BASE_PORT);         // guest1 = base
    assert.equal(g2.port, BASE_PORT + 1);     // guest2 = base + 1 (its own port)
    assert.equal(g1.streamid, null);          // listener needs no streamid
    assert.equal(g2.streamid, null);
    assert.match(g1.srt_url, new RegExp(`:${BASE_PORT}\\?latency=`));
    assert.match(g2.srt_url, new RegExp(`:${BASE_PORT + 1}\\?latency=`));
    assert.ok(!g1.srt_url.includes('streamid'), 'listen-mode URL carries no streamid');
  } finally { await srv.stop(); }
});
