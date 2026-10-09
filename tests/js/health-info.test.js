// Unit tests for backend/health-info.js — the pure (no-network, no-express) health
// aggregation used by the read-only Health Skin. Run with `node --test`.
//
// The marquee case: a flow can tick grains at full rate while the picture is
// FROZEN (a repeat-wedge). classifyGrains must flag that as 'frozen' from the
// unique-fps collapse — the exact failure we hit live with the keyer.
const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');

const h = require(path.join(__dirname, '..', '..', 'backend', 'health-info.js'));

// ── classifyGrains ────────────────────────────────────────────────────────────
test('grains: healthy flow -> ok', () => {
  const now = 1000;
  const snap = { ts: 999.5, flows: { cam: { bps: 30, ufps: 29.7 } } };
  const out = h.classifyGrains(snap, { expectedRate: 30, nowS: now });
  assert.equal(out.stale, false);
  assert.equal(out.flows.cam.state, 'ok');
});

test('grains: FROZEN flow (full rate, unique collapsed) -> frozen', () => {
  // 30 grains/s but only ~3 unique frames/s = repeat-wedge.
  const snap = { ts: 1000, flows: { guest1: { bps: 30, ufps: 3 } } };
  const out = h.classifyGrains(snap, { expectedRate: 30, nowS: 1000 });
  assert.equal(out.flows.guest1.state, 'frozen',
    'a flow ticking at rate with collapsed unique-fps must read as frozen');
});

test('grains: dead flow (null) -> dead', () => {
  const snap = { ts: 1000, flows: { playout: null } };
  const out = h.classifyGrains(snap, { expectedRate: 30, nowS: 1000 });
  assert.equal(out.flows.playout.state, 'dead');
  assert.equal(out.flows.playout.bps, 0);
});

test('grains: slow flow (bps below half rate) -> slow', () => {
  const snap = { ts: 1000, flows: { cam2: { bps: 10, ufps: 10 } } };
  const out = h.classifyGrains(snap, { expectedRate: 30, nowS: 1000 });
  assert.equal(out.flows.cam2.state, 'slow');
});

test('grains: stale snapshot flagged by age', () => {
  const snap = { ts: 900, flows: { cam: { bps: 30, ufps: 30 } } };
  const out = h.classifyGrains(snap, { expectedRate: 30, nowS: 1000, staleAfterS: 12 });
  assert.equal(out.stale, true, 'a 100s-old snapshot is stale');
  assert.equal(out.age_s, 100);
});

test('grains: missing/garbage snapshot -> stale, empty', () => {
  const out = h.classifyGrains(null, { nowS: 1000 });
  assert.equal(out.stale, true);
  assert.deepEqual(out.flows, {});
});

// ── system parsing ────────────────────────────────────────────────────────────
test('cpuPercent: computes busy fraction from two /proc/stat samples', () => {
  const prev = { total: 1000, idle: 800 };
  const cur = { total: 1100, idle: 850 };    // +100 total, +50 idle -> 50% busy
  assert.equal(h.cpuPercent(prev, cur), 50);
});

test('cpuPercent: null when no delta / missing sample', () => {
  assert.equal(h.cpuPercent(null, { total: 1, idle: 0 }), null);
  assert.equal(h.cpuPercent({ total: 10, idle: 5 }, { total: 10, idle: 5 }), null);
});

test('readMem: parses MemTotal/MemAvailable into used %', () => {
  const fake = () => 'MemTotal:       8000000 kB\nMemAvailable:   2000000 kB\nMemFree: 1000000 kB\n';
  const m = h.readMem(fake);
  assert.equal(m.total_mb, Math.round(8000000 / 1024));
  assert.equal(m.used_pct, 75); // (8000000-2000000)/8000000
});

test('readProcStat: aggregate cpu line -> total/idle', () => {
  const fake = () => 'cpu  100 0 100 700 100 0 0 0\ncpu0 1 2 3 4\n';
  const s = h.readProcStat(fake);
  // fields: user100 nice0 system100 idle700 iowait100 irq0 softirq0 steal0
  // idle = idle(700)+iowait(100) = 800; total = sum of all = 1000
  assert.equal(s.idle, 800);
  assert.equal(s.total, 1000);
});

test('cpuCount: counts per-core lines', () => {
  const fake = () => 'cpu  1 2\ncpu0 1\ncpu1 1\ncpu2 1\n';
  assert.equal(h.cpuCount(fake), 3);
});

// ── pipeline shaping ──────────────────────────────────────────────────────────
test('shapePipelines: normalizes selector/keyer/encoder; marks unreachable', () => {
  const out = h.shapePipelines({
    selector: { running: true, active_input: 2, input_flow_uuids: ['a', 'b', 'c'],
      format: { frame_width: 1920, frame_height: 1080, grain_rate: { numerator: 30, denominator: 1 } } },
    keyer: { running: true, key_on: false, mode: 'key' },
    encoder: null, // unreachable
  });
  assert.equal(out.selector.active_input, 2);
  assert.equal(out.selector.inputs, 3);
  assert.deepEqual(out.selector.format, { w: 1920, h: 1080, rate: 30 });
  assert.equal(out.keyer.key_on, false);
  assert.equal(out.encoder.unreachable, true);
  assert.equal(out.encoder.running, false);
});

test('shapePipelines: encoder viewers + bitrate surfaced', () => {
  const out = h.shapePipelines({
    encoder: { running: true, viewers: 4, encoder: { bitrate: 6000, tune: 4 } },
  });
  assert.equal(out.encoder.viewers, 4);
  assert.equal(out.encoder.bitrate_kbps, 6000);
});

// ── compose ───────────────────────────────────────────────────────────────────
test('composeHealth: assembles a stable top-level shape', () => {
  const out = h.composeHealth({ ts: 123, system: { cpu_pct: 10 }, grains: { stale: false, flows: {} }, pipelines: {} });
  assert.equal(out.ts, 123);
  assert.equal(out.system.cpu_pct, 10);
  assert.ok('grains' in out && 'pipelines' in out);
});
