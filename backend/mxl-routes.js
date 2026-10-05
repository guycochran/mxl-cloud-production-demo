// MXL demo control routes — mount into any Express app that can reach the MXL VM.
// The kiosk page (web/mxl.html) calls these; the browser can't reach the VM
// directly (NSG-locked), so this proxies from a host whose IP is allowed.
//
//   const registerMxlRoutes = require('./mxl-routes');
//   registerMxlRoutes(app);   // uses global fetch (Node 18+) or node-fetch
//
// Endpoints:
//   GET  /api/mxl/status            aggregated program/preview/key/pattern state
//                                   + slots[] with per-slot live signal
//   POST /api/mxl/input  {input}    cut program (hot-cut); pre-warms the target
//                                   reader first so a cut can't stick on the old
//                                   source (MXL_PREWARM=0 / MXL_PREWARM_MS to tune)
//   POST /api/mxl/preview{input}    arm the preview bus (PVW) — no device call
//   POST /api/mxl/take   {}         take: cut the armed PVW to PGM
//   POST /api/mxl/warmup {input?}   FALLBACK: prime EVERY reader (full sweep).
//                                   Per-cut pre-warm handles the normal case; use
//                                   this only if a cut still looks stuck (it briefly
//                                   flashes sources and can blip the WebRTC relay)
//   POST /api/mxl/key    {on}       toggle the keyer
//   POST /api/mxl/pattern{pattern}  set generator pattern (whitelisted)
//   POST /api/mxl/repair {slot,key} full downstream cascade rebuild (rate limited)
//
// Auth (optional, default off): set MXL_CONTROL_TOKEN to require
// `Authorization: Bearer <token>` or `X-MXL-Token: <token>` on the four POST routes
// (GET /status stays open). See backend/mxl-auth.js and SECURITY.md.
//
// PVW/PGM dual-bus: PGM is the live selector slot (what's on air); PVW is a
// server-side "armed" slot the operator stages before a TAKE. Preview is pure
// state — arming costs nothing until TAKE cuts it to program. This mirrors the
// ATEM preview/program model broadcast operators expect.
//
// Flow UUIDs come from the facility manifest (config/facility.json) — the single
// source of truth shared with the Python tools (grain_probe, audio_pgm, ...).
// The baked-in fallbacks keep this module working if the manifest can't be found
// (e.g. mounted standalone without the repo's config/ dir). Change a UUID in the
// manifest and both the backend and the tools follow; capture yours from the
// easy-mxl flows API once the writers are up.
const facility = require('./facility');
const { createAuth, rateLimiterFromEnv } = require('./mxl-auth');
const { registerV1Routes } = require('./mxl-routes-v1');
const _fac = facility.load();
const _vf = (name, fallback) => { try { return facility.videoFlow(name); } catch { return fallback; } };
const _af = (name, fallback) => { try { return facility.audioFlow(name); } catch { return fallback; } };

const MXL_VM = process.env.MXL_VM_URL || (_fac && _fac.network && `http://${_fac.network.mxl_vm}`) || 'http://YOUR_VM_IP';
const MXL_CAM_FLOW = (_fac && _fac.program && _fac.program.legacy_cam_flow) || '991e65d8-4fc4-58de-b22a-2d02f5952252'; // gateway cam flow (legacy)
const MXL_SEL_FLOW = _vf('selector', '9437652d-20d9-565e-be6e-b98c36067930');  // Selector PGM
const MXL_KEYER_OUT = _vf('keyer', '5c73394e-85df-50a3-8988-5edde5b5522a');    // Keyer PGM
const MXL_PGM_AUDIO = _af('pgm', 'a0d10000-aaaa-4bbb-8ccc-000000000001');      // audio_pgm.py output
const MXL_CAMLIVE_FLOW = _vf('cam', 'ca111e00-aaaa-4bbb-8ccc-000000000001');   // cam_ingest.py output
if (_fac) console.log(`mxl-routes: flow UUIDs from facility manifest (${_fac._path})`);
else console.log('mxl-routes: facility manifest not found; using baked-in UUIDs');
const MXL_PATTERNS = ['100% bars','SMPTE 75%','SMPTE','Snow','Black','White','Red','Green','Blue',
  'Checkers 1','Checkers 2','Checkers 4','Checkers 8','Circular','Blink','Zone Plate','Gamut',
  'Chroma Zone Plate','Solid Color','Ball','Bar','Pinwheel','Spokes','Gradient','SMPTE RP-219'];

const mxlKeyerBody = (inputUuid) => ({ mode: 'key', domain_path: '/mxl-domain',
  input_flow_uuid: inputUuid, html5_url: 'http://host.docker.internal:8085/lower-third.html',
  grouphint: 'HTML5-Keyer', description: 'cam + graphics', label: 'Keyer PGM' });
const mxlEncoderBody = { domain_path: '/mxl-domain', video_flow_uuid: MXL_KEYER_OUT,
  use_mediamtx: true,
  encoder: { tune: 4, speed_preset: 2, bitrate: 6000, key_int_max: 30, intra_refresh: false } };
const MXL_CAM2LIVE_FLOW = _vf('cam2', 'ca222e00-aaaa-4bbb-8ccc-000000000001'); // CAM 2 Live (Makito X4 static cam, cam2_ingest.py)
// Selector inputs come from the manifest's program.selector_inputs (role names
// -> UUIDs, in slot order). Fallback preserves the original [cam, playout,
// pattern, cam2] order so slot numbers (0=cam,1=playout,2=pattern,3=cam2) hold.
const _selInputs = (() => {
  try { return facility.selectorInputs(); }
  catch { return [MXL_CAMLIVE_FLOW, '2f34c189-64bf-5971-993a-332a28a7a6ee', '6b5d8d68-64ce-56f8-bea2-e79b6c282a86', MXL_CAM2LIVE_FLOW]; }
})();
const mxlSelectorBody = { domain_path: '/mxl-domain',
  input_flow_uuids: _selInputs,
  grouphint: 'Input-Selector', description: 'program out', label: 'Selector PGM' };

module.exports = function registerMxlRoutes(app, opts = {}) {
  // Optional shared-token auth (MXL_CONTROL_TOKEN) on the mutating routes, plus a
  // rate limit on /repair. Unset token => unchanged open behaviour + startup warning.
  // `opts.env` / `opts.log` exist for tests; production passes nothing.
  const env = opts.env || process.env;
  const auth = createAuth(env, opts.log || console).middleware;
  const repairLimit = rateLimiterFromEnv(env);
  async function mxlApi(port, apiPath, body) {
    const opts = body !== undefined
      ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
      : {};
    const r = await fetch(`${MXL_VM}:${port}${apiPath}`, opts);
    if (!r.ok) throw new Error(`MXL :${port}${apiPath} -> ${r.status}`);
    const text = await r.text();
    try { return JSON.parse(text); } catch { return text; }
  }

  let mxlBusy = false;
  let pvw = null;   // armed preview slot (0..3) — server-side, no device call

  // Slot roles for the status slots[] array — the configured selector inputs, in
  // order (slot 0..N). A slot whose UUID is a real flow is "wired"; the selector's
  // active_input tells us which one is actually on air.
  //
  // CANONICAL SLOT SPACE: the UI builds its tiles from /api/mxl/slots (the full
  // facility layout — program.layout_inputs, or selector_inputs when no layout is
  // defined). status[] must use the SAME slot space, or the tally paints the wrong
  // tile. The trap: the selector's own input_flow_uuids are indexed by its CURRENT
  // wiring (which changes as guests attach/detach), NOT by the stable layout slot.
  // So we resolve pgm/pvw/live by matching the selector's active flow UUID against
  // each layout slot's manifest UUID — the flow UUID is the join key, never the
  // array position. (This is how the pro server-enhanced.js maps logical slots.)
  const _layout = (() => {
    const p = (_fac && _fac.program) || {};
    const roles = p.layout_inputs || p.selector_inputs || [];
    const labels = p.layout_input_labels || null;
    return roles.map((role, i) => ({
      slot: i, role,
      label: (labels && labels[i]) || role,
      uuid: _vf(role, null),
    }));
  })();

  async function mxlStatus() {
    const [keyer, sel, tg1] = await Promise.all([
      mxlApi(9605, '/pipeline/status'), mxlApi(9604, '/pipeline/status'),
      mxlApi(9600, '/pipeline/status')
    ]);
    // Which flow UUID is actually on program right now? The selector reports its
    // active_input as an INDEX into its own live input_flow_uuids, so translate
    // that index to a UUID before matching it to a layout slot.
    const wired = Array.isArray(sel.input_flow_uuids) ? sel.input_flow_uuids : [];
    const onCam = keyer.input_flow_uuid === MXL_CAM_FLOW;
    const pgmUuid = onCam ? MXL_CAM_FLOW
      : (sel.active_input != null && wired[sel.active_input]) || null;
    const pvwUuid = pvw != null ? (_layout[pvw] && _layout[pvw].uuid) : null;
    const wiredSet = new Set(wired);

    const slots = _layout.map((s) => ({
      slot: s.slot,
      role: s.role,
      flow: s.uuid,
      // live = this layout slot's flow is currently wired into the selector (a real
      // source is attached). Degrades gracefully if the selector doesn't echo wiring.
      live: wiredSet.size ? wiredSet.has(s.uuid) : false,
      pgm: !!(s.uuid && s.uuid === pgmUuid),
      pvw: !!(s.uuid && s.uuid === pvwUuid),
    }));
    // input = the layout slot index on program (stable UI slot), or null if the
    // on-air flow isn't in the layout.
    const pgmSlot = slots.findIndex((s) => s.pgm);
    return { input: pgmSlot >= 0 ? pgmSlot : null, pvw,
      key: !!keyer.key_on, busy: mxlBusy, slots,
      patterns: { tg1: tg1.video && tg1.video.pattern }, available: MXL_PATTERNS };
  }

  // Resolve a UI slot number (role name, cam/cam2 alias, or layout index) to the
  // stable LAYOUT slot index — the canonical space the UI and status[] share.
  function toLayoutSlot(input) {
    if (typeof input === 'string') {
      const byRole = _layout.findIndex((s) => s.role === input);
      if (byRole >= 0) return byRole;
    }
    const n = input === 'cam' ? 0 : input === 'cam2' ? 3 : Number(input);
    return Number.isInteger(n) ? n : -1;
  }

  // Cut the selector. The UI sends a stable LAYOUT slot; the selector's own
  // active-input is an index into its CURRENT wiring (which shifts as guests
  // attach), so translate layout slot -> flow UUID -> the selector's live index.
  // Every switch is a ~30ms cut with the key staying up.
  async function mxlSetInput(input) {
    const slot = toLayoutSlot(input);
    const layoutEntry = _layout[slot];
    if (slot < 0 || !layoutEntry) {
      throw Object.assign(new Error('unknown input (use a role name, "cam"/"cam2", or a layout slot index)'), { status: 400 });
    }
    if (mxlBusy) throw Object.assign(new Error('switch in progress'), { status: 409 });
    mxlBusy = true;
    try {
      // Map the layout slot's flow UUID to the selector's current input index.
      const sel = await mxlApi(9604, '/pipeline/status');
      const wired = Array.isArray(sel.input_flow_uuids) ? sel.input_flow_uuids : [];
      let selIndex = layoutEntry.uuid ? wired.indexOf(layoutEntry.uuid) : -1;
      if (selIndex < 0) {
        // Flow isn't wired into the selector right now — can't cut to it.
        throw Object.assign(new Error(`source "${layoutEntry.role}" is not attached to the selector`), { status: 409 });
      }
      // Pre-warm ONLY the destination reader, then cut. The input-selector keeps
      // one reader per input; a reader not activated since its flow was (re)created
      // is COLD, and the first activation shows stale/late content until it catches
      // up — the "cut sticks on the previous source" wedge (HW Oct 4). We fix it by
      // activating the TARGET slot, waiting a beat for its reader to lock, then
      // activating it AGAIN so the cut the operator sees lands on a warm reader.
      // Both activations target the SAME destination slot, so the program only ever
      // moves toward where we're cutting — unlike a full warmup sweep, it never
      // flashes other sources through program or drops the downstream WebRTC relay.
      // Skip the pre-warm when we're already on this slot, or via MXL_PREWARM=0.
      const prewarm = process.env.MXL_PREWARM !== '0';
      const prewarmMs = parseInt(process.env.MXL_PREWARM_MS || '250', 10);
      if (prewarm && sel.active_input !== selIndex) {
        await mxlApi(9604, '/pipeline/active-input', { slot: selIndex }).catch(() => {});
        await new Promise(r => setTimeout(r, prewarmMs));
      }
      await mxlApi(9604, '/pipeline/active-input', { slot: selIndex });
      // Force an IDR right after the cut. With a fixed GOP, a mid-GOP source
      // change smears (P-frames predict from the old scene) until the next
      // keyframe. Best-effort: /pipeline/keyframe only exists on a patched
      // encoder (stock mxl2webrtc ignores it), so never block the cut on it.
      mxlApi(9601, '/pipeline/keyframe', {}).catch(() => {});
      // self-heal: if the keyer is wired cam-direct (pre-relay topology), move it
      const keyer = await mxlApi(9605, '/pipeline/status');
      if (keyer.input_flow_uuid !== MXL_SEL_FLOW) {
        const keyWas = !!keyer.key_on;
        await mxlApi(9605, '/pipeline/stop', {}).catch(() => {});
        await mxlApi(9605, '/pipeline/start', mxlKeyerBody(MXL_SEL_FLOW));
        await mxlApi(9605, '/pipeline/key', { on: keyWas });
        await new Promise(r => setTimeout(r, 1500));
        await mxlApi(9601, '/pipeline/stop', {}).catch(() => {});
        await mxlApi(9601, '/pipeline/start', mxlEncoderBody);
      }
      return { ok: true, input: slot };
    } finally { mxlBusy = false; }
  }

  // Warm-up sweep. The input-selector keeps one reader per input; a reader for a
  // slot that hasn't been activated since its flow was (re)created is COLD, and
  // the FIRST cut to it lands at the selector yet shows stale/late content until
  // the reader catches up — the "switch works once then the program sticks on the
  // old source" wedge (seen on HW Oct 4 after an SRT guest re-ingest recreated a
  // flow). Momentarily activating every attached slot (~400ms each) warms every
  // reader, then we restore program. Kept MANUAL (not run on every cut): doing it
  // per-cut flashes every source through program. Call it once after sources
  // attach/reconnect, or when a cut looks stuck. Runs inside the busy lock.
  async function mxlWarmup(restoreLayoutSlot) {
    const sel = await mxlApi(9604, '/pipeline/status');
    const flows = Array.isArray(sel.input_flow_uuids) ? sel.input_flow_uuids : [];
    for (let i = 0; i < flows.length; i++) {
      await mxlApi(9604, '/pipeline/active-input', { slot: i }).catch(() => {});
      await new Promise(r => setTimeout(r, 400));
    }
    // restore program to the requested layout slot (or the first wired slot)
    const entry = restoreLayoutSlot != null ? _layout[restoreLayoutSlot] : null;
    const restoreIdx = entry && entry.uuid ? flows.indexOf(entry.uuid) : 0;
    if (restoreIdx >= 0) await mxlApi(9604, '/pipeline/active-input', { slot: restoreIdx }).catch(() => {});
    mxlApi(9601, '/pipeline/keyframe', {}).catch(() => {});
  }

  app.get('/api/mxl/status', async (req, res) => {
    try { res.json(await mxlStatus()); }
    catch (e) { res.status(502).json({ error: e.message }); }
  });

  // Prime every source's reader so no cut lands on a cold/wedged reader. Sweeps
  // all attached slots, then restores program (to the current PGM slot, or the
  // optional {input} if given). Run after guests attach/reconnect. Mutating, so
  // it carries the same optional auth as the other control routes.
  app.post('/api/mxl/warmup', auth, async (req, res) => {
    const waitStart = Date.now();
    while (mxlBusy && Date.now() - waitStart < 12000) await new Promise(r => setTimeout(r, 250));
    if (mxlBusy) return res.status(409).json({ error: 'busy' });
    mxlBusy = true;
    try {
      let restore = req.body && req.body.input != null ? toLayoutSlot(req.body.input) : null;
      if (restore == null || restore < 0) {
        const st = await mxlStatus().catch(() => null);
        restore = st && st.input != null ? st.input : null;
      }
      await mxlWarmup(restore);
      res.json({ ok: true, warmed: true, restored: restore });
    } catch (e) { res.status(e.status || 502).json({ error: e.message }); }
    finally { mxlBusy = false; }
  });

  app.post('/api/mxl/input', auth, async (req, res) => {
    try { res.json(await mxlSetInput(req.body.input)); }
    catch (e) { res.status(e.status || 502).json({ error: e.message }); }
  });

  // Arm the preview bus. Pure server-side state — no device call, so it's instant
  // and never conflicts with an in-flight cut. Accepts a role name, "cam"/"cam2",
  // or a layout slot index — the same canonical slot space as /input and status[].
  // Mutating (changes the armed bus), so it carries the same optional auth.
  app.post('/api/mxl/preview', auth, (req, res) => {
    const slot = toLayoutSlot(req.body.input);
    if (slot < 0 || !_layout[slot]) {
      return res.status(400).json({ error: 'unknown input (use a role name, "cam"/"cam2", or a layout slot index)' });
    }
    pvw = slot;
    res.json({ ok: true, pvw });
  });

  // Take: cut the armed preview to program (PVW -> PGM), the way an operator
  // presses TAKE after staging a source on preview.
  app.post('/api/mxl/take', auth, async (req, res) => {
    if (pvw === null) return res.status(409).json({ error: 'nothing armed on preview' });
    try {
      const st = await mxlStatus();
      if (pvw === st.input) {
        // already on air — nothing to cut, but clear the arm so the UI settles
        return res.json({ ok: true, input: st.input, pvw, noop: true });
      }
      const result = await mxlSetInput(pvw);   // the ~30ms cut
      pvw = result.input;                        // armed source is now PGM
      res.json({ ok: true, input: result.input, pvw });
    } catch (e) { res.status(e.status || 502).json({ error: e.message }); }
  });

  app.post('/api/mxl/key', auth, async (req, res) => {
    try {
      await mxlApi(9605, '/pipeline/key', { on: !!req.body.on });
      res.json({ ok: true, key: !!req.body.on });
    } catch (e) { res.status(502).json({ error: e.message }); }
  });

  app.post('/api/mxl/pattern', auth, async (req, res) => {
    const pattern = req.body.pattern;
    if (!MXL_PATTERNS.includes(pattern)) return res.status(400).json({ error: 'unknown pattern' });
    try {
      const st = await mxlStatus();
      await mxlApi(9600, '/video/test-pattern', { pattern });
      let cut = false;
      if (st.input !== 2) { await mxlSetInput(2); cut = true; }
      res.json({ ok: true, pattern, cut });
    } catch (e) { res.status(e.status || 502).json({ error: e.message }); }
  });

  // Full downstream cascade rebuild — needed whenever a source flow is
  // recreated (readers wedge on recreated flows; see docs/FINDINGS.md).
  app.post('/api/mxl/repair', auth, repairLimit, async (req, res) => {
    if (mxlBusy) return res.status(409).json({ error: 'busy' });
    mxlBusy = true;
    try {
      const slot = [0, 1, 2, 3].includes(req.body.slot) ? req.body.slot : 0;
      await mxlApi(9604, '/pipeline/stop', {}).catch(() => {});
      await mxlApi(9604, '/pipeline/start', mxlSelectorBody);
      await new Promise(r => setTimeout(r, 1000));
      await mxlApi(9604, '/pipeline/active-input', { slot });
      await mxlApi(9605, '/pipeline/stop', {}).catch(() => {});
      await mxlApi(9605, '/pipeline/start', mxlKeyerBody(MXL_SEL_FLOW));
      await mxlApi(9605, '/pipeline/key', { on: req.body.key === false ? false : true });
      await new Promise(r => setTimeout(r, 2000));
      await mxlApi(9601, '/pipeline/stop', {}).catch(() => {});
      await mxlApi(9601, '/pipeline/start', mxlEncoderBody);
      res.json({ ok: true, slot });
    } catch (e) { res.status(502).json({ error: e.message }); }
    finally { mxlBusy = false; }
  });

  // ── /api/mxl/v1/* — the versioned contract (contracts/v1/*), thin wrappers over
  // the handlers above. Registered here so it can reach these closures; the legacy
  // /api/mxl/* routes stay as aliases for the overlap window. ──────────────────────
  registerV1Routes({
    app, auth,
    capabilities: ['state.read', 'sources.read', 'thumbs.read',
      'control.cut', 'control.preview', 'control.take', 'control.key'],
    layout: _layout,
    status: mxlStatus,
    setInput: mxlSetInput,
    setKey: (on) => mxlApi(9605, '/pipeline/key', { on: !!on }),
    getPvw: () => pvw,
    setPvw: (s) => { pvw = s; },
    programUrl: env.MXL_PROGRAM_URL || null,
    authInfo: {
      required: !!(env.MXL_CONTROL_TOKEN || '').trim(),
      scopes: ['read', 'control', 'ops'],
    },
  });
};
