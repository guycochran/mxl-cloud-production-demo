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
//   POST /api/mxl/input  {input}    cut program (hot-cut): "cam"|0|1|2 (~30ms)
//   POST /api/mxl/preview{input}    arm the preview bus (PVW) — no device call
//   POST /api/mxl/take   {}         take: cut the armed PVW to PGM (~30ms)
//   POST /api/mxl/key    {on}       toggle the keyer
//   POST /api/mxl/pattern{pattern}  set generator pattern (whitelisted)
//   POST /api/mxl/repair {slot,key} full downstream cascade rebuild
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

module.exports = function registerMxlRoutes(app) {
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
  // active_input tells us which one is actually on air. This is the manifest-driven
  // fallback for per-slot live when the server can't read the MXL domain directly
  // (documented in docs/LOCAL-CONTROL-PLANE.md).
  const _slotUuids = _selInputs.slice();

  async function mxlStatus() {
    const [keyer, sel, tg1] = await Promise.all([
      mxlApi(9605, '/pipeline/status'), mxlApi(9604, '/pipeline/status'),
      mxlApi(9600, '/pipeline/status')
    ]);
    const onCam = keyer.input_flow_uuid === MXL_CAM_FLOW;
    const input = onCam ? 0 : sel.active_input;
    // Per-slot live: the selector reports the flow UUIDs currently wired to each
    // input. A slot is "live" if the selector has a real flow on it; the active
    // slot is additionally the on-air one. Falls back to the configured inputs if
    // the selector doesn't echo its wiring.
    const wired = Array.isArray(sel.input_flow_uuids) ? sel.input_flow_uuids : _slotUuids;
    const slots = _slotUuids.map((uuid, i) => ({
      slot: i,
      flow: wired[i] || uuid || null,
      live: !!(wired[i] || uuid),   // a real flow is wired to this input
      pgm: i === input,
      pvw: i === pvw,
    }));
    return { input, pvw, key: !!keyer.key_on, busy: mxlBusy, slots,
      patterns: { tg1: tg1.video && tg1.video.pattern }, available: MXL_PATTERNS };
  }

  // Cut the selector. Every switch — camera included — is a ~30ms cut with the
  // key staying up, because cam_ingest.py aligns the camera flow's grain index
  // with the locally-generated flows.
  async function mxlSetInput(input) {
    const slot = input === 'cam' ? 0 : input === 'cam2' ? 3 : Number(input);
    if (![0, 1, 2, 3].includes(slot)) throw Object.assign(new Error('input must be "cam", "cam2", 0, 1, 2 or 3'), { status: 400 });
    if (mxlBusy) throw Object.assign(new Error('switch in progress'), { status: 409 });
    mxlBusy = true;
    try {
      await mxlApi(9604, '/pipeline/active-input', { slot });
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

  app.get('/api/mxl/status', async (req, res) => {
    try { res.json(await mxlStatus()); }
    catch (e) { res.status(502).json({ error: e.message }); }
  });

  app.post('/api/mxl/input', async (req, res) => {
    try { res.json(await mxlSetInput(req.body.input)); }
    catch (e) { res.status(e.status || 502).json({ error: e.message }); }
  });

  // Arm the preview bus. Pure server-side state — no device call, so it's instant
  // and never conflicts with an in-flight cut. Accepts the same slot forms as /input.
  app.post('/api/mxl/preview', (req, res) => {
    const input = req.body.input;
    const slot = input === 'cam' ? 0 : input === 'cam2' ? 3 : Number(input);
    if (![0, 1, 2, 3].includes(slot)) {
      return res.status(400).json({ error: 'input must be "cam", "cam2", 0, 1, 2 or 3' });
    }
    pvw = slot;
    res.json({ ok: true, pvw });
  });

  // Take: cut the armed preview to program (PVW -> PGM), the way an operator
  // presses TAKE after staging a source on preview.
  app.post('/api/mxl/take', async (req, res) => {
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

  app.post('/api/mxl/key', async (req, res) => {
    try {
      await mxlApi(9605, '/pipeline/key', { on: !!req.body.on });
      res.json({ ok: true, key: !!req.body.on });
    } catch (e) { res.status(502).json({ error: e.message }); }
  });

  app.post('/api/mxl/pattern', async (req, res) => {
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
  app.post('/api/mxl/repair', async (req, res) => {
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
};
