// Optional shared-token auth + a tiny in-memory rate limiter for the MXL control
// routes (backend/mxl-routes.js). Dependency-free; plain Express-style
// (req, res, next) middleware, so it also works with connect/restify/etc.
//
// DEFAULT-SAFE: if MXL_CONTROL_TOKEN is unset, auth is a no-op (current behaviour)
// and a clear warning is logged at startup. Nothing breaks on deploy; you opt in by
// setting the env var. See SECURITY.md ("Control routes").
//
//   MXL_CONTROL_TOKEN=<secret>        enable: mutating routes need the token
//   MXL_CONTROL_REQUIRE_TOKEN=1       fail closed: if no token is configured,
//                                     mutating routes answer 503 instead of open
//   MXL_REPAIR_RATE_MAX=10            /repair calls allowed per window per client
//                                     (0 disables the limit)
//   MXL_REPAIR_RATE_WINDOW_S=60       window length, seconds
//
// Clients send the token as   Authorization: Bearer <token>
//                       or    X-MXL-Token: <token>
const crypto = require('crypto');

function _digest(s) { return crypto.createHash('sha256').update(String(s)).digest(); }

// constant-time compare (hash both sides so differing lengths don't leak/throw)
function tokensEqual(a, b) {
  return crypto.timingSafeEqual(_digest(a), _digest(b));
}

function tokenFromRequest(req) {
  const h = (req.headers && (req.headers.authorization || req.headers.Authorization)) || '';
  const m = /^Bearer\s+(.+)$/i.exec(String(h).trim());
  if (m) return m[1].trim();
  const x = req.headers && (req.headers['x-mxl-token'] || req.headers['X-MXL-Token']);
  return x ? String(x).trim() : '';
}

function _clientKey(req) {
  return req.ip || (req.socket && req.socket.remoteAddress) ||
    (req.connection && req.connection.remoteAddress) || 'unknown';
}

// Build the auth middleware from an env-like object. Returns
// { middleware, enabled, requireToken }.
function createAuth(env = process.env, log = console) {
  const token = (env.MXL_CONTROL_TOKEN || '').trim();
  const requireToken = ['1', 'true', 'yes'].includes(String(env.MXL_CONTROL_REQUIRE_TOKEN || '').toLowerCase());
  const enabled = token.length > 0;
  if (enabled) {
    log.log('mxl-routes: control-route auth ENABLED (MXL_CONTROL_TOKEN set) for input/key/pattern/repair');
  } else if (requireToken) {
    log.warn('mxl-routes: MXL_CONTROL_REQUIRE_TOKEN=1 but MXL_CONTROL_TOKEN is unset — control routes will answer 503');
  } else {
    log.warn('mxl-routes: WARNING — MXL_CONTROL_TOKEN is not set; /api/mxl/input, /key, /pattern and /repair ' +
      'are UNAUTHENTICATED (anyone who can reach this server can cut the program or rebuild the cascade). ' +
      'Set MXL_CONTROL_TOKEN to require a shared token. See SECURITY.md.');
  }
  function middleware(req, res, next) {
    if (!enabled) {
      if (requireToken) return res.status(503).json({ error: 'control token not configured' });
      return next();
    }
    const got = tokenFromRequest(req);
    if (got && tokensEqual(got, token)) return next();
    log.warn(`mxl-routes: rejected ${req.method} ${req.originalUrl || req.url} from ${_clientKey(req)} (missing/invalid token)`);
    if (res.set) res.set('WWW-Authenticate', 'Bearer realm="mxl-control"');
    return res.status(401).json({ error: 'unauthorized' });
  }
  return { middleware, enabled, requireToken };
}

// Fixed-window per-client limiter. max<=0 disables it.
function createRateLimiter({ max = 10, windowMs = 60000, now = Date.now } = {}) {
  const hits = new Map(); // key -> { start, count }
  return function rateLimit(req, res, next) {
    if (!(max > 0)) return next();
    const t = now();
    const key = _clientKey(req);
    let e = hits.get(key);
    if (!e || t - e.start >= windowMs) { e = { start: t, count: 0 }; hits.set(key, e); }
    e.count += 1;
    if (hits.size > 1000) { // opportunistic cleanup, bounds memory
      for (const [k, v] of hits) if (t - v.start >= windowMs) hits.delete(k);
    }
    if (e.count > max) {
      const retry = Math.max(1, Math.ceil((e.start + windowMs - t) / 1000));
      if (res.set) res.set('Retry-After', String(retry));
      return res.status(429).json({ error: 'rate limited', retry_after_s: retry });
    }
    return next();
  };
}

function rateLimiterFromEnv(env = process.env) {
  const max = env.MXL_REPAIR_RATE_MAX !== undefined && env.MXL_REPAIR_RATE_MAX !== '' ? Number(env.MXL_REPAIR_RATE_MAX) : 10;
  const win = env.MXL_REPAIR_RATE_WINDOW_S ? Number(env.MXL_REPAIR_RATE_WINDOW_S) : 60;
  return createRateLimiter({ max: Number.isFinite(max) ? max : 10, windowMs: (Number.isFinite(win) && win > 0 ? win : 60) * 1000 });
}

module.exports = { createAuth, createRateLimiter, rateLimiterFromEnv, tokenFromRequest, tokensEqual };
