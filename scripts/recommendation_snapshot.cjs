'use strict';
const crypto = require('node:crypto');
const BOT_LOGIN = 'github-actions[bot]';
const INVALIDATION_MARKER = '<!-- d2wd-submission-invalidated:v1 -->';
const SNAPSHOT_MARKER_RE = /<!-- d2wd-submission-snapshot:v1; issue=(\d+); sha256=([a-f0-9]{64}) -->/g;
function snapshotPayload(issue) {
  const issueNumber = Number(issue && issue.number);
  const authorLogin = issue && issue.user && issue.user.login;
  const createdAt = issue && issue.created_at;
  if (!Number.isSafeInteger(issueNumber) || issueNumber < 1) throw new TypeError('A positive integer issue number is required.');
  if (typeof authorLogin !== 'string' || authorLogin.length === 0) throw new TypeError('A GitHub issue author is required.');
  if (typeof createdAt !== 'string' || createdAt.length === 0) throw new TypeError('The original issue created_at timestamp is required.');
  return JSON.stringify({
    version: 1, issue_number: issueNumber, author_login: authorLogin,
    created_at: createdAt, title: String((issue && issue.title) ?? ''),
    body: String((issue && issue.body) ?? ''),
  });
}
function computeSnapshotHash(issue) {
  return crypto.createHash('sha256').update(snapshotPayload(issue), 'utf8').digest('hex');
}
function createSnapshotMarker(issue) {
  return '<!-- d2wd-submission-snapshot:v1; issue=' + String(Number(issue.number)) +
    '; sha256=' + computeSnapshotHash(issue) + ' -->';
}
function createSnapshotComment(issue) {
  const digest = computeSnapshotHash(issue);
  return [
    createSnapshotMarker(issue), '### Original submission recorded', '',
    'This is a one-shot recommendation submission. Automation checks the title and body against this original snapshot.',
    'Editing the title/body or reopening the issue invalidates the submission. Create a new issue to propose a revision.',
    'GitHub may still let an issue author edit their own issue; edited or reopened submissions will not be republished.',
    '', 'Snapshot SHA-256: ' + digest,
  ].join('\n');
}
function findSnapshotAnchor(comments, issueNumber) {
  const target = Number(issueNumber), hashes = new Set();
  let count = 0;
  for (const comment of Array.isArray(comments) ? comments : []) {
    if (String(comment && comment.user && comment.user.login || '').toLowerCase() !== BOT_LOGIN) continue;
    const matcher = new RegExp(SNAPSHOT_MARKER_RE.source, 'g');
    let match;
    while ((match = matcher.exec(String(comment && comment.body || ''))) !== null) {
      if (Number(match[1]) === target) { hashes.add(match[2]); count += 1; }
    }
  }
  if (hashes.size === 0) return { status: 'missing', sha256: null, count: 0 };
  if (hashes.size !== 1) return { status: 'conflict', sha256: null, count };
  return { status: 'ok', sha256: [...hashes][0], count };
}
function verifySnapshot(issue, comments) {
  let actualHash;
  try { actualHash = computeSnapshotHash(issue); }
  catch (error) { return { valid: false, status: 'invalid-issue', sha256: null, actualHash: null, error: error.message }; }
  const anchor = findSnapshotAnchor(comments, issue && issue.number);
  if (anchor.status !== 'ok') return { valid: false, status: anchor.status, sha256: null, actualHash, count: anchor.count };
  return { valid: anchor.sha256 === actualHash, status: anchor.sha256 === actualHash ? 'ok' : 'mismatch',
    sha256: anchor.sha256, actualHash, count: anchor.count };
}
function hasBotCommentMarker(comments, marker) {
  return (Array.isArray(comments) ? comments : []).some(comment =>
    String(comment && comment.user && comment.user.login || '').toLowerCase() === BOT_LOGIN &&
    String(comment && comment.body || '').includes(marker));
}
module.exports = { BOT_LOGIN, INVALIDATION_MARKER, computeSnapshotHash, createSnapshotMarker,
  createSnapshotComment, findSnapshotAnchor, verifySnapshot, hasBotCommentMarker };
