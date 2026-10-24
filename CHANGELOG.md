# Changelog

## 0.1.2

- Setup is now a numbered provider menu: pick only the providers you want instead of answering a prompt for all 19.
- Providers with a known endpoint ask only for the key. Gateways still ask for their URL; `setup --url` changes any provider's endpoint. A saved custom endpoint is kept on later runs.
- `python relay.py` offers to start the server when setup finishes, so first run is a single command. The offer appears only in an interactive terminal.
- Clearer messages: the server prints the base URL, model and API key to use; a gateway with no URL gets a specific error; setup errors in the menu no longer end the session.
- Rewrote `README.md` as a quickstart with client examples, a provider table, troubleshooting and a settings reference.
- Router, routing behaviour, config format and provider catalogue are unchanged.

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
