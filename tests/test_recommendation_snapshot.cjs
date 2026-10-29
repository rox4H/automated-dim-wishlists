'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const snapshot = require('../scripts/recommendation_snapshot.cjs');
function issue(overrides = {}) {
  return { number: 123, created_at: '2026-10-29T09:00:00Z',
    title: '[Recommendation proposal] Solar add clear',
    body: '### Mode\nPvE\n\n### Weapon\navalanche_s5',
    user: { login: 'sample-contributor' }, ...overrides };
}
const botComment = body => ({ user: { login: 'github-actions[bot]' }, body });
test('snapshot hash is stable and SHA-256 shaped', () => {
  const submitted = issue();
  assert.equal(snapshot.computeSnapshotHash(submitted), snapshot.computeSnapshotHash({ ...submitted }));
  assert.match(snapshot.computeSnapshotHash(submitted), /^[a-f0-9]{64}$/);
});
test('snapshot binds issue number, author, creation time, title and body', () => {
  const original = snapshot.computeSnapshotHash(issue());
  for (const changed of [
    issue({ number: 124 }), issue({ user: { login: 'another-contributor' } }),
    issue({ created_at: '2026-10-29T09:01:00Z' }),
    issue({ title: '[Recommendation proposal] different title' }),
    issue({ body: 'edited after submission' }),
  ]) assert.notEqual(snapshot.computeSnapshotHash(changed), original);
});
test('valid original snapshot passes; edited body fails closed', () => {
  const submitted = issue(), comments = [botComment(snapshot.createSnapshotComment(submitted))];
  assert.equal(snapshot.verifySnapshot(submitted, comments).valid, true);
  assert.equal(snapshot.verifySnapshot(issue({ body: 'different body' }), comments).valid, false);
});
test('a contributor cannot forge the trusted bot snapshot comment', () => {
  const submitted = issue();
  const comments = [{ user: { login: 'sample-contributor' }, body: snapshot.createSnapshotComment(submitted) }];
  assert.equal(snapshot.findSnapshotAnchor(comments, submitted.number).status, 'missing');
  assert.equal(snapshot.verifySnapshot(submitted, comments).valid, false);
});
test('missing and conflicting snapshot anchors fail closed', () => {
  const submitted = issue();
  assert.equal(snapshot.verifySnapshot(submitted, []).status, 'missing');
  const comments = [botComment(snapshot.createSnapshotComment(submitted)),
    botComment(snapshot.createSnapshotComment(issue({ body: 'conflicting version' })))];
  assert.equal(snapshot.findSnapshotAnchor(comments, submitted.number).status, 'conflict');
  assert.equal(snapshot.verifySnapshot(submitted, comments).valid, false);
});
test('invalidation marker detection only trusts the bot author', () => {
  const marker = snapshot.INVALIDATION_MARKER;
  assert.equal(snapshot.hasBotCommentMarker([{ user: { login: 'sample-contributor' }, body: marker }], marker), false);
  assert.equal(snapshot.hasBotCommentMarker([botComment(marker)], marker), true);
});
