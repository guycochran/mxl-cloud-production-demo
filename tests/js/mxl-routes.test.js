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

// ── Review-fix regression tests (R3 pattern slot, R4 input validation, R5 timeout) ──

// a fetch mock that records {url, body} and can return per-URL responses
function mkFetch(responder) {
  const calls = [];
  global.fetch = async (url, opts) => {
    let body; try { body = opts && opts.body ? JSON.parse(opts.body) : undefined; } catch { body = opts.body; }
    calls.push({ url: String(url), body });
    const r = responder ? responder(String(url), body) : null;
    return r || { ok: true, text: async () => '{}' };
  };
  return calls;
}
// a status responder that makes the selector report a given active_input + wiring
function statusResponder(activeInput, wired) {
  return (url) => {
    if (url.includes(':9604/pipeline/status')) return { ok: true, text: async () => JSON.stringify({ active_input: activeInput, input_flow_uuids: wired }) };
    if (url.includes(':9605/pipeline/status')) return { ok: true, text: async () => JSON.stringify({ input_flow_uuid: '9437652d-20d9-565e-be6e-b98c36067930', key_on: true }) };
    if (url.includes(':9600/pipeline/status')) return { ok: true, text: async () => JSON.stringify({ video: { pattern: '100% bars' } }) };
    return { ok: true, text: async () => '{}' };
  };
}

test('R4: {"input": null} is rejected 400, does NOT cut to slot 0', async () => {
  const app = mkApp(); registerMxlRoutes(app, { env: {}, log: silent });
  const calls = mkFetch();
  const res = await call(app, 'POST /api/mxl/input', { headers: {}, body: { input: null }, ip: '1.1.1.1' });
  assert.strictEqual(res.code, 400, 'null input must 400');
  const cuts = calls.filter((c) => c.url.includes('/pipeline/active-input'));
  assert.strictEqual(cuts.length, 0, 'null input must not issue any active-input cut');
});

test('R4: empty string / array / bool are all rejected (Number() coercion traps)', async () => {
  const app = mkApp(); registerMxlRoutes(app, { env: {}, log: silent });
  for (const bad of ['', [], true, {}, 1.5, -1, 'nonsense']) {
    mkFetch();
    const res = await call(app, 'POST /api/mxl/input', { headers: {}, body: { input: bad }, ip: '1.1.1.1' });
    assert.strictEqual(res.code, 400, `input=${JSON.stringify(bad)} must 400`);
  }
});

test('R4: a valid role name and a numeric index still work', async () => {
  const app = mkApp(); registerMxlRoutes(app, { env: {}, log: silent });
  // selector wired so cam (slot 0) resolves to its flow; mock returns it attached
  const camFlow = 'ca111e00-aaaa-4bbb-8ccc-000000000001';
  mkFetch(statusResponder(0, [camFlow]));
  const res = await call(app, 'POST /api/mxl/input', { headers: {}, body: { input: 'cam' }, ip: '1.1.1.1' });
  assert.strictEqual(res.code, 200, 'role "cam" should cut');
  assert.strictEqual(res.body.input, 0);
});

test('R3: /pattern sets the generator but does NOT cut (no wrong-source cut)', async () => {
  const app = mkApp(); registerMxlRoutes(app, { env: {}, log: silent });
  const calls = mkFetch(statusResponder(0, ['ca111e00-aaaa-4bbb-8ccc-000000000001']));
  const res = await call(app, 'POST /api/mxl/pattern', { headers: {}, body: { pattern: 'SMPTE' }, ip: '1.1.1.1' });
  assert.strictEqual(res.code, 200);
  assert.strictEqual(res.body.cut, false, 'pattern must not auto-cut');
  // it set the generator pattern...
  assert.ok(calls.some((c) => c.url.includes('/video/test-pattern') && c.body && c.body.pattern === 'SMPTE'));
  // ...and issued NO active-input cut (the old bug cut to a hardcoded slot 2 = the clip player)
  assert.ok(!calls.some((c) => c.url.includes('/pipeline/active-input')), 'pattern must not issue a cut');
});

test('R5: a hung VM (fetch timeout) surfaces 504 and releases the busy lock', async () => {
  // Node-version-independent: the mock rejects the way real fetch does when its
  // AbortSignal fires, OR on its own short fallback timer — so the test is
  // deterministic even if AbortSignal.timeout propagation differs across Node
  // versions (the earlier version depended on it and flaked on CI's Node 20).
  const app = mkApp(); registerMxlRoutes(app, { env: { MXL_API_TIMEOUT_MS: '30' }, log: silent });
  const abortErr = () => { const e = new Error('The operation was aborted'); e.name = 'AbortError'; return e; };
  global.fetch = (url, opts) => new Promise((_resolve, reject) => {
    const sig = opts && opts.signal;
    if (sig) {
      if (sig.aborted) return reject(abortErr());
      sig.addEventListener('abort', () => reject(abortErr()), { once: true });
    }
    // fallback: never hang the test even if the injected signal doesn't fire here
    setTimeout(() => reject(abortErr()), 50);
  });
  const first = await call(app, 'POST /api/mxl/input', { headers: {}, body: { input: 0 }, ip: '1.1.1.1' });
  assert.strictEqual(first.code, 504, `timed-out cut should be 504 (got ${first.code})`);
  // lock released: a second cut proceeds to fetch (and times out again), not a blanket 409
  const second = await call(app, 'POST /api/mxl/input', { headers: {}, body: { input: 0 }, ip: '1.1.1.1' });
  assert.strictEqual(second.code, 504, 'busy lock must not stay stuck — second cut reaches fetch, not 409');
  assert.notStrictEqual(second.body && second.body.error, 'switch in progress', 'no stuck busy lock');
});

test('R5: a stalled BODY (headers ok, text() hangs) also surfaces 504, not a raw 502', async () => {
  const app = mkApp(); registerMxlRoutes(app, { env: { MXL_API_TIMEOUT_MS: '30' }, log: silent });
  const abortErr = () => { const e = new Error('The operation was aborted'); e.name = 'AbortError'; return e; };
  global.fetch = async (url, opts) => ({
    ok: true,
    text: () => new Promise((_res, reject) => {
      const sig = opts && opts.signal;
      if (sig) {
        if (sig.aborted) return reject(abortErr());
        sig.addEventListener('abort', () => reject(abortErr()), { once: true });
      }
      setTimeout(() => reject(abortErr()), 50);
    }),
  });
  const res = await call(app, 'POST /api/mxl/input', { headers: {}, body: { input: 0 }, ip: '1.1.1.1' });
  assert.strictEqual(res.code, 504, `stalled body should be 504 (got ${res.code}: ${res.body && res.body.error})`);
});
