'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const {
  STATUS,
  classifyRecommendationIntake,
  getRecommendationIntakeStatus,
  shouldAutoCloseFromReportStatus,
} = require('../scripts/recommendation_intake_outcome.cjs');

const validReport = {
  errors: [],
  warnings: ['Perk definitions still need maintainer review.'],
  duplicate_matches: [],
  checks: [
    { name: 'Mode', status: 'pass' },
    { name: 'Exact weapon version', status: 'pass' },
    { name: 'Weapon perk pool', status: 'pass' },
    { name: 'Duplicate recommendation guard', status: 'pass' },
  ],
};

test('a valid intake stays open when repository preflight/export fails', () => {
  const outcome = classifyRecommendationIntake({
    report: validReport,
    validationOutcome: 'false',
    integrationOutcome: 'success',
    integrationResult: 'maintainer-blocked',
    preflightOutcome: 'failure',
    refreshOutcome: 'failure',
  });

  assert.equal(outcome.status, STATUS.MAINTAINER_BLOCKED);
  assert.equal(outcome.intakePassed, true);
  assert.equal(outcome.integrationComplete, false);
  assert.equal(outcome.shouldCloseIssue, false);
});

test('a fully integrated valid intake stays open for maintainer review', () => {
  const outcome = classifyRecommendationIntake({
    report: validReport,
    validationOutcome: 'false',
    integrationOutcome: 'success',
    integrationResult: 'integrated',
    preflightOutcome: 'success',
    refreshOutcome: 'success',
  });

  assert.equal(outcome.status, STATUS.INTEGRATED);
  assert.equal(outcome.shouldCloseIssue, false);
});

test('a real mechanical intake error is classified as intake-failed', () => {
  const outcome = classifyRecommendationIntake({
    report: { ...validReport, errors: ['Perk is not in the selected weapon-version pool.'] },
    validationOutcome: 'true',
    integrationOutcome: 'skipped',
    integrationResult: 'blocked',
    preflightOutcome: 'skipped',
    refreshOutcome: 'skipped',
  });

  assert.equal(outcome.status, STATUS.INTAKE_FAILED);
  assert.equal(outcome.shouldCloseIssue, true);
});

test('a duplicate roll is a blocking intake result even if the checker output is otherwise green', () => {
  const outcome = classifyRecommendationIntake({
    report: { ...validReport, duplicate_matches: [{ path: 'recommendations/curated/pve/example.json' }] },
    validationOutcome: 'false',
    integrationOutcome: 'success',
    integrationResult: 'duplicate-blocked',
    preflightOutcome: 'success',
    refreshOutcome: 'success',
  });

  assert.equal(outcome.status, STATUS.INTAKE_FAILED);
  assert.equal(outcome.duplicateBlocked, true);
  assert.equal(outcome.shouldCloseIssue, true);
});

test('an incomplete/system validation result is not blamed on the submitter', () => {
  const outcome = classifyRecommendationIntake({
    report: null,
    validationOutcome: '',
    integrationOutcome: 'skipped',
    integrationResult: '',
    preflightOutcome: 'skipped',
    refreshOutcome: 'skipped',
  });

  assert.equal(outcome.status, STATUS.VALIDATION_INCOMPLETE);
  assert.equal(outcome.shouldCloseIssue, false);
});

test('automatic closure requires an explicit intake-failed report marker', () => {
  assert.equal(getRecommendationIntakeStatus('<!-- recommendation-intake-status: maintainer-blocked -->'), STATUS.MAINTAINER_BLOCKED);
  assert.equal(getRecommendationIntakeStatus('old report with no status'), null);
  assert.equal(shouldAutoCloseFromReportStatus(STATUS.INTAKE_FAILED), true);
  assert.equal(shouldAutoCloseFromReportStatus(STATUS.MAINTAINER_BLOCKED), false);
  assert.equal(shouldAutoCloseFromReportStatus(null), false);
});
