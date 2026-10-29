'use strict';

const STATUS = Object.freeze({
  INTAKE_FAILED: 'intake-failed',
  INTEGRATED: 'integrated',
  MAINTAINER_BLOCKED: 'maintainer-blocked',
  VALIDATION_INCOMPLETE: 'validation-incomplete',
});

/**
 * Keep proposal validity separate from the repository's ability to publish it.
 * A repository-wide validator/exporter failure is a maintainer-side blocker,
 * not an error in an otherwise valid proposal.
 */
function classifyRecommendationIntake({
  report,
  validationOutcome,
  integrationOutcome,
  integrationResult,
  preflightOutcome,
  refreshOutcome,
}) {
  const validReport = Boolean(report && typeof report === 'object' &&
    !Array.isArray(report) && Array.isArray(report.errors));
  const duplicateBlocked = Boolean(
    (Array.isArray(report?.duplicate_matches) && report.duplicate_matches.length > 0) ||
    integrationResult === 'duplicate-blocked' ||
    (Array.isArray(report?.checks) && report.checks.some((check) =>
      check && check.name === 'Duplicate recommendation guard' && check.status === 'fail'
    ))
  );

  // Only explicit intake errors and duplicate proposals are submitter failures.
  // Missing reports or unknown outcomes are treated as system failures.
  const intakeFailed = validationOutcome === 'true' ||
    (validReport && report.errors.length > 0) ||
    duplicateBlocked;
  const intakePassed = validationOutcome === 'false' &&
    validReport &&
    report.errors.length === 0 &&
    !duplicateBlocked;

  const integrationComplete = intakePassed &&
    integrationOutcome === 'success' &&
    integrationResult === 'integrated' &&
    preflightOutcome === 'success' &&
    refreshOutcome === 'success';

  let status = STATUS.VALIDATION_INCOMPLETE;
  if (intakeFailed) status = STATUS.INTAKE_FAILED;
  else if (integrationComplete) status = STATUS.INTEGRATED;
  else if (intakePassed) status = STATUS.MAINTAINER_BLOCKED;

  return {
    status,
    duplicateBlocked,
    intakeFailed,
    intakePassed,
    integrationComplete,
    shouldCloseIssue: status === STATUS.INTAKE_FAILED,
  };
}

function getRecommendationIntakeStatus(commentBody) {
  const match = String(commentBody || '').match(
    /<!-- recommendation-intake-status: (intake-failed|integrated|maintainer-blocked|validation-incomplete) -->/
  );
  return match ? match[1] : null;
}

function shouldAutoCloseFromReportStatus(status) {
  return status === STATUS.INTAKE_FAILED;
}

module.exports = {
  STATUS,
  classifyRecommendationIntake,
  getRecommendationIntakeStatus,
  shouldAutoCloseFromReportStatus,
};
