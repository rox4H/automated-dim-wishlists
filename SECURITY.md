# Security Policy

## Supported versions

Only the latest commit on the `main` branch is supported. Security fixes are handled on a best-effort basis.

## Report a vulnerability privately

Please do not report security vulnerabilities in public issues, discussions, or pull requests.

Use GitHub's private vulnerability reporting feature: open this repository's **Security** tab and choose **Report a vulnerability**. Include what you found, steps to reproduce it, the potential impact, and any suggested mitigation. Avoid including real secrets or other people's private information in your report.

This is a small, volunteer-run project, so acknowledgement and remediation are best effort.

## Scope

The following areas are particularly relevant:

- GitHub Actions workflows, especially recommendation intake and publication workflows.
- Scripts under `scripts/` that validate, process, or publish community recommendations.
- Permissions and token handling in CI workflows.
- Secrets, tokens, or private keys accidentally committed to the repository or its history.
- Unsafe handling of untrusted issue or recommendation content.
