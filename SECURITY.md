# Security Policy

## Supported release

The public Personal Pilot branch is supported on a best-effort basis. It is a
research prototype and remains `scientific_release_pending`.

## Reporting a vulnerability

Please use GitHub's private security-advisory channel after the repository is
published. Do not post credentials, private data, exploit details, unpublished
research materials, or identifiable run artifacts in a public issue.

Include the affected commit, operating system, minimal reproduction steps, and
the expected and observed behavior. Synthetic reproductions are preferred.

## Local-data safety

- Keep API keys and credentials in local environment variables or an approved
  secret manager. Never add them to configuration files committed to Git.
- Keep real datasets, private papers, personal knowledge bases, `runs/`, and
  generated artifacts outside the repository.
- Check access, license, provenance, privacy, and budget before acquiring or
  sharing an external dataset or replication package.
- Treat downloaded author code and container images as untrusted until their
  exact bytes and execution policy have been reviewed.
- Do not publish principal capability files or owner-private authority roots.

Repository CI is designed to use only small, repository-owned synthetic
fixtures. The included compressed replication fixture is synthetic test data,
not a third-party research archive.

The public repository must be created from the sanitized, single-commit release
branch. The private development history previously contained machine-local
project-memory paths and is not approved for public push.
