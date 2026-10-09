// Verifies the "Add your camera" ingest builders produce the right SRT URL / streamid
// / Larix deep-link for each guest transport: srt-direct (one shared port + streamid)
// vs srt-listen (per-guest port, no streamid). Tests the pure backend/ingest-info.js
// module directly — no express, no server, no npm deps (CI runs `node --test` without
// `npm install`).
const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');

const { ingestConfig, ingestInfo, larixUrl } =
  require(path.join(__dirname, '..', '..', 'backend', 'ingest-info.js'));

const PUBLIC_IP = '203.0.113.7';   // TEST-NET-3 documentation IP
const BASE_PORT = 8890;

test('default transport is srt-listen', () => {
  // ingestConfig with no transport uses the project default.
  assert.equal(ingestConfig({}).transport, 'srt-listen');
  assert.equal(ingestConfig({}).listen, true);
});

test('srt-direct (explicit): one shared port + publish:guestN streamid', () => {
  const cfg = ingestConfig({ publicIp: PUBLIC_IP, srtPort: BASE_PORT, transport: 'srt-direct' });
  const j = ingestInfo(cfg);
  assert.equal(j.transport, 'srt-direct');
  assert.equal(j.guests.length, 2);
  const [g1, g2] = j.guests;
  assert.equal(g1.port, BASE_PORT);
  assert.equal(g2.port, BASE_PORT);                      // SHARED port
  assert.equal(g1.streamid, 'publish:guest1');
  assert.equal(g2.streamid, 'publish:guest2');
  assert.match(g1.srt_url, /streamid=publish:guest1/);   // LITERAL colon, not %3A
  assert.ok(!g1.srt_url.includes('%3A'), 'streamid must keep a literal colon');
  // Larix deep-link carries the streamid with a literal colon.
  const lx = larixUrl(cfg, 'guest1');
  assert.match(lx, /srtstreamid\]=publish:guest1/);
  assert.ok(lx.includes('srtstreamid]=publish:guest1'), 'streamid keeps a literal colon');
});

test('srt-listen: per-guest port, NO streamid', () => {
  const cfg = ingestConfig({ publicIp: PUBLIC_IP, srtPort: BASE_PORT, transport: 'srt-listen' });
  const j = ingestInfo(cfg);
  assert.equal(j.transport, 'srt-listen');
  const [g1, g2] = j.guests;
  assert.equal(g1.port, BASE_PORT);         // guest1 = base
  assert.equal(g2.port, BASE_PORT + 1);     // guest2 = base + 1 (its own port)
  assert.equal(g1.streamid, null);          // listener needs no streamid
  assert.equal(g2.streamid, null);
  assert.match(g1.srt_url, new RegExp(`:${BASE_PORT}\\?latency=`));
  assert.match(g2.srt_url, new RegExp(`:${BASE_PORT + 1}\\?latency=`));
  assert.ok(!g1.srt_url.includes('streamid'), 'listen-mode URL carries no streamid');
  // Larix deep-link: per-guest port, no srtstreamid field.
  const lx2 = larixUrl(cfg, 'guest2');
  assert.match(lx2, new RegExp(`%3A${BASE_PORT + 1}`));   // the port colon IS %-encoded (inside the url value)
  assert.ok(!lx2.includes('srtstreamid'), 'listen-mode deep-link carries no streamid');
});

test('no public IP: srt_url + qr are null, no crash', () => {
  const cfg = ingestConfig({ srtPort: BASE_PORT });   // publicIp defaults to ''
  const j = ingestInfo(cfg);
  assert.equal(j.public_ip, null);
  assert.equal(j.guests[0].srt_url, null);
  assert.equal(j.guests[0].qr, null);
});

// ── Configurable guest list + explicit ports (public phone-inject flow) ─────────
// A real deployment may run a 3rd "phone" slot on a NON-contiguous port (the box's
// guest3 listens on :8895, not the computed :8892). The advertised QR/SRT URL MUST
// match the real listener or the scan sends the phone to a dead port.
const { parseGuests, parsePorts } = require(path.join(__dirname, '..', '..', 'backend', 'ingest-info.js'));

test('MXL_GUESTS adds a third slot (guest3) to the advertised list', () => {
  const cfg = ingestConfig({ publicIp: PUBLIC_IP, srtPort: BASE_PORT, guests: 'guest1,guest2,guest3' });
  const j = ingestInfo(cfg);
  assert.equal(j.guests.length, 3);
  assert.equal(j.guests[2].slot, 'guest3');
  assert.equal(j.guests[2].label, 'Guest 3');
});

test('MXL_GUEST_PORTS pins non-contiguous real ports (guest3 on :8895, not :8892)', () => {
  const cfg = ingestConfig({
    publicIp: PUBLIC_IP, srtPort: BASE_PORT, transport: 'srt-listen',
    guests: 'guest1,guest2,guest3', guestPorts: '8890,8891,8895',
  });
  const j = ingestInfo(cfg);
  assert.equal(j.guests[0].port, 8890);
  assert.equal(j.guests[1].port, 8891);
  assert.equal(j.guests[2].port, 8895, 'guest3 must advertise its REAL port 8895');
  assert.match(j.guests[2].srt_url, /:8895\?/);
  // the Larix deep-link for guest3 must carry :8895 too
  const lx = larixUrl(cfg, 'guest3');
  assert.match(lx, /%3A8895/, 'guest3 deep-link must point at the real :8895');
});

test('parseGuests: accepts "Label:slot" and bare slots; bad entries dropped', () => {
  assert.deepEqual(parseGuests('guest1,guest2'), [{ slot: 'guest1', label: 'Guest 1' }, { slot: 'guest2', label: 'Guest 2' }]);
  assert.deepEqual(parseGuests('Phone:guest3'), [{ slot: 'guest3', label: 'Phone' }]);
  assert.deepEqual(parseGuests('nonsense,guest4'), [{ slot: 'guest4', label: 'Guest 4' }]);
  assert.equal(parseGuests('').length, 2, 'empty falls back to the 2 defaults');
});

test('parsePorts: parses a CSV port list, null on empty/garbage', () => {
  assert.deepEqual(parsePorts('8890,8891,8895'), [8890, 8891, 8895]);
  assert.equal(parsePorts(''), null);
  assert.equal(parsePorts('abc'), null);
});

// ── Per-guest relay host (IP-hiding: a guest publishes to an SRT relay, not the box) ──
test('MXL_GUEST_HOSTS routes a guest slot to a relay host (home IP stays private)', () => {
  const cfg = ingestConfig({
    publicIp: '192.168.1.254', srtPort: 8890, transport: 'srt-listen',
    guests: 'guest3,guest5', guestPorts: '8895,8897',
    guestHosts: 'guest5=20.230.141.204',   // guest5 -> Azure SRT relay
  });
  const j = ingestInfo(cfg);
  const g3 = j.guests.find((g) => g.slot === 'guest3');
  const g5 = j.guests.find((g) => g.slot === 'guest5');
  // guest3 stays on the LAN/public IP; guest5 advertises the relay host
  assert.match(g3.srt_url, /192\.168\.1\.254:8895/);
  assert.match(g5.srt_url, /20\.230\.141\.204:8897/, 'guest5 must advertise the relay host, not the box IP');
  // the Larix deep-link must ALSO carry the relay host (the QR a phone scans)
  assert.match(larixUrl(cfg, 'guest5'), /20\.230\.141\.204/, 'guest5 QR must point at the relay, not the box');
  assert.ok(!larixUrl(cfg, 'guest5').includes('192.168.1.254'), 'guest5 QR must NOT leak the box IP');
});
