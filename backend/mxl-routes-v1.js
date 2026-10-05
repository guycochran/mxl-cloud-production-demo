// /api/mxl/v1 — the versioned contract (contracts/v1/*, docs/V1-CONTRACT.md).
//
// Thin wrappers over the existing handlers in mxl-routes.js. The media behaviour is
// unchanged; v1 only (a) addresses sources by a STABLE opaque id instead of a layout
// index/role name, (b) reports a `health` object instead of the overloaded `live`,
// (c) returns the structured error model, and (d) adds /v1/meta capability discovery.
//
// It's registered FROM INSIDE mxl-routes.js so it can reach that module's closures
// (mxlStatus, mxlSetInput, _layout, the pvw/busy state). Deps are passed in explicitly
// so this file stays unit-testable with fakes.
//
// Legacy /api/mxl/* routes stay registered alongside as aliases for the overlap window.

const CORE_VERSION = process.env.MXL_CORE_VERSION || '0.3.0';

// Stable id from a flow UUID: "src_" + first 8 hex (dashes stripped). Opaque to clients.
function idFromUuid(uuid) {
  if (!uuid) return null;
  return 'src_' + String(uuid).replace(/-/g, '').slice(0, 8).toLowerCase();
}

// Map a layout role -> source kind for the UI. Heuristic from role/label names; a real
// facility could carry `kind` in the manifest later (additive).
function kindFor(role, label) {
  const s = ((role || '') + ' ' + (label || '')).toLowerCase();
  if (/cam/.test(s)) return 'camera';
  if (/guest|panelist|seat|zoom/.test(s)) return 'guest';
  if (/playout|clip|file|program|bed/.test(s)) return 'playout';
  if (/pattern|generator|bars|tg\b|test/.test(s)) return 'generator';
  if (/layout|multiview|composite|super/.test(s)) return 'layout';
  return 'other';
}

// status.slots[i].live is "wired into selector". Map it to a v1 health object.
// (v1.0: wired => ok, not-wired => no_signal. Richer states are additive once
// mxl_thumbs.py health.json is served.)
function healthFromSlot(slot) {
  if (!slot) return { state: 'unknown', last_frame_age_ms: null };
  if (slot.live) return { state: 'ok', last_frame_age_ms: null };
  return { state: 'no_signal', last_frame_age_ms: null };
}

// error.code -> HTTP status (docs/V1-CONTRACT.md).
const CODE_STATUS = {
  unknown_source: 400, source_not_attached: 409, busy: 409,
  selector_down: 502, timeout: 504, rate_limited: 429,
  unauthorized: 401, not_configured: 503,
};
function sendErr(res, code, message, extra) {
  const status = CODE_STATUS[code] || 502;
  const body = { error: Object.assign({ code, message }, extra || {}) };
  if (res.set && code === 'rate_limited' && extra && extra.retry_after_s) res.set('Retry-After', String(extra.retry_after_s));
  return res.status(status).json(body);
}
// Translate a thrown legacy error (status + message) into the v1 code.
function codeForLegacyError(e) {
  const m = (e && e.message || '').toLowerCase();
  if (e && e.status === 409 && /not attached|attached to the selector/.test(m)) return 'source_not_attached';
  if (e && e.status === 409) return 'busy';
  if (e && e.status === 400) return 'unknown_source';
  if (/timed out|timeout|aborted/.test(m)) return 'timeout';
  return 'selector_down';
}

// deps: { app, auth, capabilities, layout, status, setInput, setKey,
//         getPvw, setPvw, programUrl, authInfo }
//  - layout:    the _layout array [{slot, role, label, uuid}]
//  - status:    async () => mxlStatus() result
//  - setInput:  async (layoutSlot) => mxlSetInput(slot)
//  - setKey:    async (on) => void
//  - getPvw/setPvw: read/write the shared pvw slot
function registerV1Routes(deps) {
  const { app, auth, capabilities, layout, status, setInput, setKey,
          getPvw, setPvw, programUrl, authInfo } = deps;

  // --- id <-> layout slot resolution (flow UUID is the join key) ---
  const idToSlot = new Map();   // "src_xxxx" -> layout slot index
  layout.forEach((e) => { const id = idFromUuid(e.uuid); if (id) idToSlot.set(id, e.slot); });
  const slotToId = (slot) => (layout[slot] ? idFromUuid(layout[slot].uuid) : null);

  // --- GET /v1/meta : version + capability + auth discovery ---
  app.get('/api/mxl/v1/meta', (req, res) => {
    res.json({
      api_version: '1.0',
      core_version: CORE_VERSION,
      capabilities,
      program_url: programUrl || null,
      auth: authInfo,
    });
  });

  // --- GET /v1/sources : stable ids + kind + health ---
  app.get('/api/mxl/v1/sources', async (req, res) => {
    try {
      const st = await status();
      const bySlot = new Map((st.slots || []).map((s) => [s.slot, s]));
      const sources = layout
        .map((e) => {
          const id = idFromUuid(e.uuid);
          if (!id) return null;
          return {
            id, label: e.label, kind: kindFor(e.role, e.label),
            order: e.slot, health: healthFromSlot(bySlot.get(e.slot)),
          };
        })
        .filter(Boolean);
      res.json({ sources });
    } catch (e) { sendErr(res, 'selector_down', e.message); }
  });

  // --- GET /v1/state : pgm/pvw by id + key + busy ---
  app.get('/api/mxl/v1/state', async (req, res) => {
    try {
      const st = await status();
      res.json(stateBody(st));
    } catch (e) { sendErr(res, 'selector_down', e.message); }
  });
  function stateBody(st) {
    const pvwSlot = getPvw();
    return {
      pgm: st.input != null ? slotToId(st.input) : null,
      pvw: pvwSlot != null ? slotToId(pvwSlot) : null,
      key: !!st.key, busy: !!st.busy,
    };
  }

  // --- POST /v1/cut {source} : hot-cut by id ---
  app.post('/api/mxl/v1/cut', auth, async (req, res) => {
    const id = req.body && req.body.source;
    const slot = idToSlot.has(id) ? idToSlot.get(id) : -1;
    if (slot < 0) return sendErr(res, 'unknown_source', `no source "${id}"`);
    try {
      await setInput(slot);
      res.json(stateBody(await status()));
    } catch (e) { sendErr(res, codeForLegacyError(e), e.message); }
  });

  // --- POST /v1/preview {source} : arm pvw by id (pure state) ---
  app.post('/api/mxl/v1/preview', auth, async (req, res) => {
    const id = req.body && req.body.source;
    const slot = idToSlot.has(id) ? idToSlot.get(id) : -1;
    if (slot < 0) return sendErr(res, 'unknown_source', `no source "${id}"`);
    setPvw(slot);
    try { res.json(stateBody(await status())); }
    catch (e) { sendErr(res, 'selector_down', e.message); }
  });

  // --- POST /v1/take : cut armed pvw -> pgm ---
  app.post('/api/mxl/v1/take', auth, async (req, res) => {
    const pvwSlot = getPvw();
    try {
      const st = await status();
      if (pvwSlot == null || pvwSlot === st.input) {
        return res.json(stateBody(st));   // no-op, not an error (contract)
      }
      await setInput(pvwSlot);
      setPvw(pvwSlot);                      // armed source is now pgm
      res.json(stateBody(await status()));
    } catch (e) { sendErr(res, codeForLegacyError(e), e.message); }
  });

  // --- POST /v1/key {on} : graphics keyer ---
  app.post('/api/mxl/v1/key', auth, async (req, res) => {
    try {
      await setKey(!!(req.body && req.body.on));
      res.json(stateBody(await status()));
    } catch (e) { sendErr(res, 'selector_down', e.message); }
  });
}

module.exports = { registerV1Routes, idFromUuid, kindFor, healthFromSlot, codeForLegacyError };
