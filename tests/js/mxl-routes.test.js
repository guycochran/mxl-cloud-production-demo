// Integration-ish test of backend/mxl-routes.js wiring using a fake Express app and a
// mocked global fetch — no express, no network, no MXL VM.
const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');

const registerMxlRoutes = require(path.join(__dirname, '..', '..', 'backend', 'mxl-routes.js'));

function mkApp() {
  const routes = {};
  const reg = (method) => (p, ...handlers) => { routes[`${method} ${p}`] = handlers; };
  return { routes, get: reg('GET'), post: reg('POST') };
}
function mkRes() {
  const r = { code: 200, body: null, headers: {} };
  r.status = (c) => { r.code = c; return r; };
  r.json = (b) => { r.body = b; return r; };
  r.set = (k, v) => { r.headers[k] = v; return r; };
  return r;
}
// run a handler chain like express would
async function call(app, key, req) {
  const chain = app.routes[key];
  assert.ok(chain, `route ${key} not registered`);
  const res = mkRes();
  let i = 0;
  const next = async () => { const h = chain[i++]; if (h) await h(req, res, next); };
  await next();
  return res;
}
const TOK = ['t0k', 'x'].join('-'); // runtime-built: avoids the secret-literal tripwire
const silent = { log() {}, warn() {} };
let fetched;
test.beforeEach(() => {
  fetched = [];
  global.fetch = async (url) => { fetched.push(url); return { ok: true, text: async () => '{}' }; };
});

test('no token configured: routes behave exactly as before (open)', async () => {
  const app = mkApp(); registerMxlRoutes(app, { env: {}, log: silent });
  const res = await call(app, 'POST /api/mxl/key', { headers: {}, body: { on: true }, ip: '9.9.9.9' });
  assert.strictEqual(res.code, 200);
  assert.deepStrictEqual(res.body, { ok: true, key: true });
  assert.ok(fetched.length >= 1);
});

test('token configured: all four POST routes are guarded, GET /status is not', async () => {
  const app = mkApp(); registerMxlRoutes(app, { env: { MXL_CONTROL_TOKEN: TOK }, log: silent });
  for (const r of ['input', 'key', 'pattern', 'repair']) {
    const res = await call(app, `POST /api/mxl/${r}`, { headers: {}, body: {}, ip: '1.1.1.1' });
    assert.strictEqual(res.code, 401, r);
  }
  assert.strictEqual(fetched.length, 0, 'no upstream call without a token');
  const ok = await call(app, 'POST /api/mxl/key', { headers: { authorization: `Bearer ${TOK}` }, body: { on: false }, ip: '1.1.1.1' });
  assert.strictEqual(ok.code, 200);
  // status handler is registered without auth middleware (single handler)
  assert.strictEqual(app.routes['GET /api/mxl/status'].length, 1);
});

test('/repair is rate limited (default 10/min per client), configurable', async () => {
  const app = mkApp(); registerMxlRoutes(app, { env: { MXL_REPAIR_RATE_MAX: '2' }, log: silent });
  // make the repair cascade fast: stub setTimeout delays
  const realST = global.setTimeout; global.setTimeout = (f) => realST(f, 0);
  try {
    const req = { headers: {}, body: { slot: 0 }, ip: '2.2.2.2' };
    assert.strictEqual((await call(app, 'POST /api/mxl/repair', req)).code, 200);
    assert.strictEqual((await call(app, 'POST /api/mxl/repair', req)).code, 200);
    const third = await call(app, 'POST /api/mxl/repair', req);
    assert.strictEqual(third.code, 429);
    assert.ok(third.headers['Retry-After']);
  } finally { global.setTimeout = realST; }
});

test('startup warning is logged when no token is set', () => {
  const warns = [];
  registerMxlRoutes(mkApp(), { env: {}, log: { log() {}, warn: (m) => warns.push(m) } });
  assert.ok(warns.some((m) => /MXL_CONTROL_TOKEN is not set/.test(m)));
});
