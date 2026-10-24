# Verification

## Automated checks

0.1.2, Linux Python 3.13: 172 automated tests passed (168 existing tests, setup prompt sequences updated, plus 4 new tests for the provider menu, gateway URL errors, `setup --url` and the start-now offer). Setup and start-up were also driven through a real pseudo-terminal: hidden key entry, menu selection, anonymous opt-in, config file mode 0600 and directory mode 0700, the start-now offer, `/live` and `/v1/models` responses, and Ctrl+C shutdown. No live provider inference was performed.

0.1.1, native Windows Python 3.12: 168 automated tests passed. The suite covers provider setup, hidden-key entry, configuration persistence, HTTP requests, streaming, failover, authentication, request validation, redaction, provider accounting and opt-in response styles. Tests use disposable credentials and local mock providers.

## Privacy checks

- Source files, the complete local Git object database and the downloadable ZIP were compared against known active credential values without displaying them. No real credential matches were found.
- Personal account references, deployment fingerprints, historical account observations and source-system notes were removed from distributable files.
- Published branch history uses a generic project author and contains no prior deployment history.
- Runtime secrets, environment files, private databases, logs and backups are excluded from version control and releases.
- Response-style directives are opt-in. A new installation starts without private configuration or stored credentials.

## Verification limits

Live provider inference requires each user's own API keys and account quota. Catalogue access, inference, streaming, tool use, vision and context capacity are separate checks. Local mock tests do not establish live provider capabilities.

Windows has not been re-run since the 0.1.2 setup changes. macOS runtime, full model contexts and Docker execution have not been verified locally. The optional manual CI matrix can test supported operating systems. Gateway deployment and account login are configured separately.
