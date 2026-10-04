// Load the facility manifest (config/facility.json) — the single source of truth
// for domain, network, control ports, and flow UUIDs. JS counterpart to
// tools/facility.py so the backend and the Python tools read the SAME file.
//
//   const facility = require('./facility');
//   const sel = facility.videoFlow('selector');      // one UUID by role
//   const dst = facility.audioFlow('pgm');
//   const port = facility.controlPort('selector');    // 9604
//
// Resolution order for the manifest file:
//   1. $MXL_FACILITY_JSON if set
//   2. config/facility.json, walking up from this file (repo checkout)
//   3. ./facility.json / ./config/facility.json in the cwd
//
// Unlike the tools (which fall back to baked-in UUIDs), callers decide how to
// handle a missing manifest — load() returns null rather than throwing, so a
// consumer can keep its own fallback constants. Use loadOrThrow() to fail hard.

const fs = require('fs');
const path = require('path');

function candidates() {
  const out = [];
  if (process.env.MXL_FACILITY_JSON) out.push(process.env.MXL_FACILITY_JSON);
  let d = __dirname;
  for (let i = 0; i < 6; i++) {
    out.push(path.join(d, 'config', 'facility.json'));
    out.push(path.join(d, 'facility.json'));
    const parent = path.dirname(d);
    if (parent === d) break;
    d = parent;
  }
  out.push(path.join(process.cwd(), 'config', 'facility.json'));
  out.push(path.join(process.cwd(), 'facility.json'));
  return out;
}

// Env overrides for the manifest's network section (manifest values stay the defaults,
// so nothing changes unless a variable is set). See docs/CONFIG.md.
const NETWORK_ENV = { MXL_VM_IP: 'mxl_vm', MXL_VM_INTERNAL_IP: 'mxl_vm_internal', MXL_DOCKER_GATEWAY: 'docker_gateway' };
function applyEnvOverrides(data) {
  if (!data.network) return data;
  for (const [envName, key] of Object.entries(NETWORK_ENV)) {
    const v = (process.env[envName] || '').trim();
    if (v) data.network[key] = v;
  }
  return data;
}

function load() {
  for (const p of candidates()) {
    try {
      if (p && fs.existsSync(p)) {
        const data = JSON.parse(fs.readFileSync(p, 'utf8'));
        data._path = p;
        return applyEnvOverrides(data);
      }
    } catch (e) {
      // malformed at this path — keep looking, surface via loadOrThrow if needed
    }
  }
  return null;
}

function loadOrThrow() {
  const f = load();
  if (!f) {
    throw new Error(
      'facility manifest not found. Set $MXL_FACILITY_JSON or place ' +
      'config/facility.json in the repo. Looked in:\n  ' + candidates().join('\n  ')
    );
  }
  return f;
}

const FACILITY = load();

function section(name) {
  if (!FACILITY) throw new Error('facility manifest not loaded');
  return FACILITY[name];
}

function videoFlow(name) {
  return section('video_flows')[name].uuid;
}
function audioFlow(name) {
  return section('audio_flows')[name].uuid;
}
function controlPort(name) {
  return section('control_api').ports[name].port;
}
function domainPath() {
  return section('facility').domain_path;
}
function selectorInputs() {
  // resolve the program.selector_inputs role names -> UUIDs, in order
  return section('program').selector_inputs.map(videoFlow);
}
function layoutInputs() {
  // the full live facility input list (role names -> UUIDs, in slot order)
  return section('program').layout_inputs.map(videoFlow);
}
function layoutInputLabels() {
  return section('program').layout_input_labels.slice();
}

module.exports = {
  FACILITY, load, loadOrThrow, applyEnvOverrides,
  videoFlow, audioFlow, controlPort, domainPath,
  selectorInputs, layoutInputs, layoutInputLabels,
};
