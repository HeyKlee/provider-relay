# Changelog

## 0.1.1

- Removed personal account references, deployment fingerprints and historical account observations from documentation, catalogue labels and source comments.
- Replaced release history with a clean project commit using a generic author.
- Audited source, Git objects and release contents against known active credential values. No real credential matches were found.
- Made response-style directives opt-in and expanded exclusions for credential files, databases and backups.
- Rebuilt the source archive from the cleaned repository.

## 0.1.0

- Added guided terminal setup for 19 provider connections with clickable links and hidden API-key entry.
- Added independent endpoint configuration, private credential storage, atomic writes and secret redaction.
- Added automatic model routing, ordered fallback, streaming, task selection, circuit breakers, affinity, provider budget tracking and bounded-context processing.
- Defaulted to loopback access and explicit opt-in for paid routes.
- Added catalogue and inference checks, isolated tests, optional Docker packaging and manually triggered cross-platform CI.
