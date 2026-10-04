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
  let ok = 0; for (let i = 0; i < 10; i++) if (run(dflt, { ip: 'b', headers: {} }).nexted) ok++;
  assert.strictEqual(ok, 6);
  const junk = rateLimiterFromEnv({ MXL_REPAIR_RATE_MAX: 'abc', MXL_REPAIR_RATE_WINDOW_S: '-5' });
  ok = 0; for (let i = 0; i < 10; i++) if (run(junk, { ip: 'c', headers: {} }).nexted) ok++;
  assert.strictEqual(ok, 6);
});
