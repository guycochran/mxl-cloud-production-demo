// Pure, dependency-free builders for the "Add your camera" guest-ingest info:
// the per-guest SRT URL, streamid, and Larix deep-link. Extracted from
// local-server.js so it can be unit-tested WITHOUT express / npm deps (CI runs
// `node --test` with no `npm install`). No requires, no side effects.
//
// Guest transport:
//   srt-direct (default) — contributor publishes to mediamtx on one shared port
//     with a streamid (publish:guestN); mediamtx demuxes by streamid.
//   srt-listen — the ingest IS the SRT listener; the contributor dials STRAIGHT
//     into a per-guest port (srtPort + index) with NO streamid. Mirrors
//     quickstart's MXL_GUEST_TRANSPORT=srt-listen (one listener owns one port).

const GUESTS = [
  { slot: 'guest1', label: 'Guest 1' },
  { slot: 'guest2', label: 'Guest 2' },
];

// Resolve the config once from a plain object (e.g. process.env), so callers and
// tests share identical logic. transport defaults to srt-direct.
function ingestConfig({ publicIp = '', srtPort = 8890, transport = 'srt-direct' } = {}) {
  const listen = transport === 'srt-listen';
  return {
    publicIp,
    srtPort,
    transport,
    listen,
    // listen: each guest owns srtPort + index; direct: all share srtPort.
    guestPort: (i) => (listen ? srtPort + i : srtPort),
  };
}

// The JSON body for GET /api/mxl/ingest.
function ingestInfo(cfg) {
  return {
    public_ip: cfg.publicIp || null,
    srt_port: cfg.srtPort,
    transport: cfg.transport,
    guests: GUESTS.map((g, i) => ({
      ...g,
      port: cfg.guestPort(i),
      streamid: cfg.listen ? null : 'publish:' + g.slot,
      srt_url: !cfg.publicIp
        ? null
        : cfg.listen
          ? `srt://${cfg.publicIp}:${cfg.guestPort(i)}?latency=200`
          : `srt://${cfg.publicIp}:${cfg.srtPort}?streamid=publish:${g.slot}&latency=200`,
      qr: cfg.publicIp ? `/api/mxl/ingest/qr/${g.slot}.png` : null,
    })),
  };
}

// The Larix Broadcaster deep-link the QR encodes.
//   larix://set/v1 (v1) · conn[] empty-index arrays · srtstreamid LOWERCASE ·
//   mode=av (the string) · name + url percent-encoded.
// ⚠️ The srtstreamid value keeps a LITERAL colon (publish:guestN), NOT %3A — Larix
// passes it straight to the SRT handshake and mediamtx rejects "publish%3AguestN" as
// an invalid stream ID. (HW-verified 2026-10-05.) A colon is legal unencoded in a URL
// query value, so the deep-link still parses. In listen mode there is no streamid.
function larixUrl(cfg, slot) {
  const name = encodeURIComponent('MXL ' + slot.replace(/^guest/, 'Guest '));
  const idx = Math.max(0, (parseInt(slot.replace(/^guest/, ''), 10) || 1) - 1);
  if (cfg.listen) {
    const srt = encodeURIComponent(`srt://${cfg.publicIp}:${cfg.guestPort(idx)}`);
    return `larix://set/v1?conn[][name]=${name}&conn[][url]=${srt}`
      + `&conn[][mode]=av&conn[][srtlatency]=1000`;
  }
  const srt = encodeURIComponent(`srt://${cfg.publicIp}:${cfg.srtPort}`);
  const sid = 'publish:' + slot;   // literal colon — see note above
  return `larix://set/v1?conn[][name]=${name}&conn[][url]=${srt}`
    + `&conn[][mode]=av&conn[][srtstreamid]=${sid}&conn[][srtlatency]=1000`;
}

module.exports = { GUESTS, ingestConfig, ingestInfo, larixUrl };
