// MXL demo control routes — mount into any Express app that can reach the MXL VM.
// The kiosk page (web/mxl.html) calls these; the browser can't reach the VM
// directly (NSG-locked), so this proxies from a host whose IP is allowed.
//
//   const registerMxlRoutes = require('./mxl-routes');
//   registerMxlRoutes(app);   // uses global fetch (Node 18+) or node-fetch
//
// Endpoints:
//   GET  /api/mxl/status            aggregated program/key/pattern state
//   POST /api/mxl/input  {input}    cut program: "cam"|0|1|2 (~30ms)
//   POST /api/mxl/key    {on}       toggle the keyer
//   POST /api/mxl/pattern{pattern}  set generator pattern (whitelisted)
//   POST /api/mxl/repair {slot,key} full downstream cascade rebuild
//
// Update the MXL_VM address and flow UUIDs for your deployment. Flow UUIDs are
// deterministic from label/description/grouphint — capture yours from the
// easy-mxl flows API once the writers are up.

const MXL_VM = process.env.MXL_VM_URL || 'http://YOUR_VM_IP';
const MXL_CAM_FLOW = '991e65d8-4fc4-58de-b22a-2d02f5952252';   // gateway cam flow (legacy)
const MXL_SEL_FLOW = '9437652d-20d9-565e-be6e-b98c36067930';   // Selector PGM
const MXL_KEYER_OUT = '5c73394e-85df-50a3-8988-5edde5b5522a';  // Keyer PGM
const MXL_PGM_AUDIO = 'a0d10000-aaaa-4bbb-8ccc-000000000001';  // audio_pgm.py output
const MXL_CAMLIVE_FLOW = 'ca111e00-aaaa-4bbb-8ccc-000000000001'; // cam_ingest.py output
const MXL_PATTERNS = ['100% bars','SMPTE 75%','SMPTE','Snow','Black','White','Red','Green','Blue',
  'Checkers 1','Checkers 2','Checkers 4','Checkers 8','Circular','Blink','Zone Plate','Gamut',
  'Chroma Zone Plate','Solid Color','Ball','Bar','Pinwheel','Spokes','Gradient','SMPTE RP-219'];

const mxlKeyerBody = (inputUuid) => ({ mode: 'key', domain_path: '/mxl-domain',
  input_flow_uuid: inputUuid, html5_url: 'http://host.docker.internal:8085/lower-third.html',
  grouphint: 'HTML5-Keyer', description: 'cam + graphics', label: 'Keyer PGM' });
const mxlEncoderBody = { domain_path: '/mxl-domain', video_flow_uuid: MXL_KEYER_OUT,
  audio_flow_uuid: MXL_PGM_AUDIO, use_mediamtx: true,
  encoder: { tune: 4, speed_preset: 2, bitrate: 6000, key_int_max: 30, intra_refresh: false } };
const MXL_CAM2LIVE_FLOW = 'ca222e00-aaaa-4bbb-8ccc-000000000001'; // CAM 2 Live (Makito X4 static cam, cam2_ingest.py)
const mxlSelectorBody = { domain_path: '/mxl-domain',
  input_flow_uuids: [MXL_CAMLIVE_FLOW, '2f34c189-64bf-5971-993a-332a28a7a6ee', '6b5d8d68-64ce-56f8-bea2-e79b6c282a86', MXL_CAM2LIVE_FLOW],
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

  async function mxlStatus() {
    const [keyer, sel, tg1] = await Promise.all([
      mxlApi(9605, '/pipeline/status'), mxlApi(9604, '/pipeline/status'),
      mxlApi(9600, '/pipeline/status')
    ]);
    const onCam = keyer.input_flow_uuid === MXL_CAM_FLOW;
    return { input: onCam ? 0 : sel.active_input, key: !!keyer.key_on, busy: mxlBusy,
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
