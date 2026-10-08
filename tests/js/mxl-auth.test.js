// SPDX-License-Identifier: Apache-2.0
// Run with:  node --test tests/js/*.test.js  (Node >= 18, no npm deps)
const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createAuth, createRateLimiter, rateLimiterFromEnv, tokenFromRequest, tokensEqual } =
  require(path.join(__dirname, '..', '..', 'backend', 'mxl-auth.js'));

// built at runtime so the repo's secret tripwire (tests/test_no_secrets.py) sees no literal
const TOK = ['tok', '123'].join('-');
const BIG = ['super', 'secret', 'value'].join('-');
const quiet = () => { const l = { out: [], log: (m) => l.out.push(['log', m]), warn: (m) => l.out.push(['warn', m]) }; return l; };
function mkRes() {
  const r = { code: 200, body: null, headers: {} };
  r.status = (c) => { r.code = c; return r; };
  r.json = (b) => { r.body = b; return r; };
  r.set = (k, v) => { r.headers[k] = v; return r; };
  return r;
}
function run(mw, req) { const res = mkRes(); let nexted = false; mw(req, res, () => { nexted = true; }); return { res, nexted }; }

test('tokensEqual: constant-time compare handles different lengths', () => {
  assert.ok(tokensEqual('abc', 'abc'));
  assert.ok(!tokensEqual('abc', 'abcd'));
  assert.ok(!tokensEqual('', 'x'));
});

test('tokenFromRequest: Bearer and X-MXL-Token', () => {
  assert.strictEqual(tokenFromRequest({ headers: { authorization: 'Bearer s3cret' } }), 's3cret');
  assert.strictEqual(tokenFromRequest({ headers: { authorization: 'bearer   s3cret ' } }), 's3cret');
  assert.strictEqual(tokenFromRequest({ headers: { 'x-mxl-token': 's3cret' } }), 's3cret');
  assert.strictEqual(tokenFromRequest({ headers: {} }), '');
  assert.strictEqual(tokenFromRequest({ headers: { authorization: 'Basic abc' } }), '');
});

test('unset token: open (backwards compatible) + loud startup warning', () => {
  const log = quiet();
  const a = createAuth({}, log);
  assert.strictEqual(a.enabled, false);
  assert.ok(log.out.some(([k, m]) => k === 'warn' && /MXL_CONTROL_TOKEN is not set/.test(m) && /UNAUTHENTICATED/.test(m)));
  const { nexted } = run(a.middleware, { headers: {}, method: 'POST', url: '/api/mxl/input' });
  assert.ok(nexted);
});

test('token set: missing / wrong rejected 401, right accepted (both header styles)', () => {
  const log = quiet();
  const a = createAuth({ MXL_CONTROL_TOKEN: TOK }, log);
  assert.strictEqual(a.enabled, true);
  let r = run(a.middleware, { headers: {}, method: 'POST', url: '/x' });
  assert.strictEqual(r.res.code, 401); assert.ok(!r.nexted);
  assert.ok(/Bearer/.test(r.res.headers['WWW-Authenticate']));
  r = run(a.middleware, { headers: { authorization: 'Bearer nope' }, method: 'POST', url: '/x' });
  assert.strictEqual(r.res.code, 401);
  r = run(a.middleware, { headers: { authorization: `Bearer ${TOK}` } });
  assert.ok(r.nexted);
  r = run(a.middleware, { headers: { 'x-mxl-token': TOK } });
  assert.ok(r.nexted);
});

test('token is never logged', () => {
  const log = quiet();
  const a = createAuth({ MXL_CONTROL_TOKEN: BIG }, log);
  run(a.middleware, { headers: { authorization: 'Bearer wrong-guess' }, method: 'POST', url: '/x' });
  assert.ok(!JSON.stringify(log.out).includes(BIG));
  assert.ok(!JSON.stringify(log.out).includes('wrong-guess'));
});

test('MXL_CONTROL_REQUIRE_TOKEN=1 without a token fails closed (503)', () => {
  const a = createAuth({ MXL_CONTROL_REQUIRE_TOKEN: '1' }, quiet());
  const r = run(a.middleware, { headers: {}, method: 'POST', url: '/x' });
  assert.strictEqual(r.res.code, 503); assert.ok(!r.nexted);
});

test('rate limiter: allows max then 429 with Retry-After, resets after window', () => {
  let t = 1000;
  const rl = createRateLimiter({ max: 2, windowMs: 10000, now: () => t });
  const req = { ip: '1.2.3.4', headers: {} };
  assert.ok(run(rl, req).nexted);
  assert.ok(run(rl, req).nexted);
  const r = run(rl, req);
  assert.strictEqual(r.res.code, 429); assert.ok(!r.nexted);
  assert.ok(Number(r.res.headers['Retry-After']) >= 1);
  // other client unaffected
  assert.ok(run(rl, { ip: '5.6.7.8', headers: {} }).nexted);
  t += 10001;
  assert.ok(run(rl, req).nexted);
});

test('rate limiter: max=0 disables; env parsing falls back to defaults', () => {
  const off = rateLimiterFromEnv({ MXL_REPAIR_RATE_MAX: '0' });
  for (let i = 0; i < 50; i++) assert.ok(run(off, { ip: 'a', headers: {} }).nexted);
  const dflt = rateLimiterFromEnv({});
  let ok = 0; for (let i = 0; i < 14; i++) if (run(dflt, { ip: 'b', headers: {} }).nexted) ok++;
  assert.strictEqual(ok, 10);
  const junk = rateLimiterFromEnv({ MXL_REPAIR_RATE_MAX: 'abc', MXL_REPAIR_RATE_WINDOW_S: '-5' });
  ok = 0; for (let i = 0; i < 14; i++) if (run(junk, { ip: 'c', headers: {} }).nexted) ok++;
  assert.strictEqual(ok, 10);
});

// ── R6a: real-client keying behind a proxy (CF-Connecting-IP / X-Forwarded-For) ──
const { clientKeyFactory } = require(path.join(__dirname, '..', '..', 'backend', 'mxl-auth.js'));

test('R6a: clientKey prefers CF-Connecting-IP, then XFF, then req.ip', () => {
  const key = clientKeyFactory({});
  assert.strictEqual(key({ ip: '10.0.0.1', headers: { 'cf-connecting-ip': '203.0.113.9' } }), '203.0.113.9');
  assert.strictEqual(key({ ip: '10.0.0.1', headers: { 'x-forwarded-for': '198.51.100.7, 10.0.0.1' } }), '198.51.100.7');
  assert.strictEqual(key({ ip: '10.0.0.1', headers: {} }), '10.0.0.1');
});

test('R6a: MXL_TRUST_PROXY_HEADERS=0 ignores the headers (direct-exposure case)', () => {
  const key = clientKeyFactory({ MXL_TRUST_PROXY_HEADERS: '0' });
  assert.strictEqual(key({ ip: '10.0.0.1', headers: { 'cf-connecting-ip': '203.0.113.9' } }), '10.0.0.1');
});

test('R6a: the /repair limiter buckets per REAL client, not the shared tunnel IP', () => {
  // two clients behind the same tunnel (same req.ip) but different CF-Connecting-IP
  const limit = rateLimiterFromEnv({ MXL_REPAIR_RATE_MAX: '2' });
  const mk = (cf) => ({ ip: '172.17.0.1', headers: { 'cf-connecting-ip': cf } });
  // client A: 2 ok then 429
  assert.ok(run(limit, mk('1.1.1.1')).nexted);
  assert.ok(run(limit, mk('1.1.1.1')).nexted);
  assert.strictEqual(run(limit, mk('1.1.1.1')).res.code, 429);
  // client B shares the tunnel IP but should have its OWN bucket — not already limited
  assert.ok(run(limit, mk('2.2.2.2')).nexted, 'second client must not inherit the first client\'s count');
});

// ── R6b: failed-auth throttle ──
test('R6b: repeated bad tokens get throttled (429) after MXL_AUTH_FAIL_MAX', () => {
  const { middleware } = createAuth({ MXL_CONTROL_TOKEN: TOK, MXL_AUTH_FAIL_MAX: '3' }, quiet());
  const bad = { ip: '9.9.9.9', headers: { 'x-mxl-token': 'wrong' } };
  assert.strictEqual(run(middleware, bad).res.code, 401); // 1
  assert.strictEqual(run(middleware, bad).res.code, 401); // 2
  assert.strictEqual(run(middleware, bad).res.code, 401); // 3 -> hits max
  const locked = run(middleware, bad).res;                 // 4 -> locked out
  assert.strictEqual(locked.code, 429);
  assert.ok(locked.headers['Retry-After']);
});

test('R6b: a correct token clears the failure count (not locked after success)', () => {
  const { middleware } = createAuth({ MXL_CONTROL_TOKEN: TOK, MXL_AUTH_FAIL_MAX: '3' }, quiet());
  const bad = { ip: '8.8.8.8', headers: { 'x-mxl-token': 'wrong' } };
  const good = { ip: '8.8.8.8', headers: { 'x-mxl-token': TOK } };
  run(middleware, bad); run(middleware, bad);           // 2 fails
  assert.ok(run(middleware, good).nexted, 'correct token should pass');
  // count reset — two more bad attempts should NOT yet lock (would have at 3 cumulative)
  assert.strictEqual(run(middleware, bad).res.code, 401);
  assert.strictEqual(run(middleware, bad).res.code, 401);
});

test('R6b: MXL_AUTH_FAIL_MAX=0 disables the throttle (always 401, never 429)', () => {
  const { middleware } = createAuth({ MXL_CONTROL_TOKEN: TOK, MXL_AUTH_FAIL_MAX: '0' }, quiet());
  const bad = { ip: '7.7.7.7', headers: { 'x-mxl-token': 'wrong' } };
  for (let i = 0; i < 30; i++) assert.strictEqual(run(middleware, bad).res.code, 401);
});

test('R6b: failed-auth throttle is per-client (CF-IP), so one attacker can\'t lock out others', () => {
  const { middleware } = createAuth({ MXL_CONTROL_TOKEN: TOK, MXL_AUTH_FAIL_MAX: '2' }, quiet());
  const attacker = { ip: '172.17.0.1', headers: { 'cf-connecting-ip': '6.6.6.6', 'x-mxl-token': 'wrong' } };
  run(middleware, attacker); run(middleware, attacker);
  assert.strictEqual(run(middleware, attacker).res.code, 429); // attacker locked
  // a different real client (same tunnel) is NOT locked
  const victim = { ip: '172.17.0.1', headers: { 'cf-connecting-ip': '5.5.5.5', 'x-mxl-token': 'wrong' } };
  assert.strictEqual(run(middleware, victim).res.code, 401, 'different client must not be locked by the attacker');
});
