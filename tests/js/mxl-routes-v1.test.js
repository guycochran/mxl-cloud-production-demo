// Unit tests for the /v1 shim's pure logic (backend/mxl-routes-v1.js). The route wiring
// is integration-tested live on HW; here we lock the id derivation, kind inference,
// health mapping, and error-code translation — the parts a refactor could silently break.
const { test } = require('node:test');
const assert = require('node:assert');
const { idFromUuid, kindFor, healthFromSlot, codeForLegacyError } = require('../../backend/mxl-routes-v1');

test('idFromUuid: src_ + first 8 hex, dashes stripped, lowercased', () => {
  assert.strictEqual(idFromUuid('9998DA48-9041-5ae1-b730-d0a918563107'), 'src_9998da48');
  assert.strictEqual(idFromUuid('d3e15194-6d1f-5955-8d52-52e7076b7a99'), 'src_d3e15194');
});

test('idFromUuid: null/empty -> null', () => {
  assert.strictEqual(idFromUuid(null), null);
  assert.strictEqual(idFromUuid(''), null);
  assert.strictEqual(idFromUuid(undefined), null);
});

test('idFromUuid is stable + identical for the same uuid (the join-key property)', () => {
  const u = 'ca111e00-aaaa-4bbb-8ccc-000000000001';
  assert.strictEqual(idFromUuid(u), idFromUuid(u));
});

test('kindFor: maps role/label to a source kind', () => {
  assert.strictEqual(kindFor('cam', 'CAM Live'), 'camera');
  assert.strictEqual(kindFor('guest1', 'Guest 1'), 'guest');
  assert.strictEqual(kindFor('playout', 'Clip Video'), 'playout');
  assert.strictEqual(kindFor('pattern', 'Pattern Video'), 'generator');
  assert.strictEqual(kindFor('selector', 'Selector PGM'), 'other');  // not a switchable source kind
  assert.strictEqual(kindFor('zzz', 'Mystery'), 'other');
});

test('healthFromSlot: wired=ok, not-wired=no_signal, missing=unknown', () => {
  assert.strictEqual(healthFromSlot({ live: true }).state, 'ok');
  assert.strictEqual(healthFromSlot({ live: false }).state, 'no_signal');
  assert.strictEqual(healthFromSlot(null).state, 'unknown');
});

test('codeForLegacyError: translates legacy thrown errors to v1 codes', () => {
  assert.strictEqual(codeForLegacyError(Object.assign(new Error('source "Guest 1" is not attached to the selector'), { status: 409 })), 'source_not_attached');
  assert.strictEqual(codeForLegacyError(Object.assign(new Error('switch in progress'), { status: 409 })), 'busy');
  assert.strictEqual(codeForLegacyError(Object.assign(new Error('unknown input'), { status: 400 })), 'unknown_source');
  assert.strictEqual(codeForLegacyError(new Error('request aborted (timeout)')), 'timeout');
  assert.strictEqual(codeForLegacyError(new Error('MXL :9604 -> 502')), 'selector_down');
});
