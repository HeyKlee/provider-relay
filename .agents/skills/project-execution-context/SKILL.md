---
name: project-execution-context
description: Read when changing Provider Relay setup, provider wiring, routing, tests or packaging.
---

# Provider Relay execution context

The repository root is the ancestor containing `relay.py` and `providers.json`. Resolve it from the checked-out files. Runtime is Python 3.11+ with no third-party runtime dependencies. The server runs on the user's machine and listens on loopback by default.

`relay.py` owns setup, catalogue/inference checks and server startup. `relay_config.py` owns provider wiring, credential persistence, permissions and redaction. `providers.json` owns onboarding links and endpoint defaults. `models.json` owns model aliases and route order. `router.py` owns failover, SSE, task routing, affinity, budget tracking and health telemetry.

Credentials and state belong in `.provider-relay/`, which is excluded from Git and Docker. Test credentials belong in temporary directories. Read configured values only when required and keep secret values out of output. Preserve existing private config when setup is cancelled or a write fails. Every provider must be independently skippable and configurable. Anonymous access requires explicit opt-in.

Paid routes require explicit opt-in. Keep the default listener private. Configure external gateways through user-supplied URLs and credentials; gateway deployment and account login are separate from this router.

Before editing, read the relevant module and its tests. Run `python -m pytest -q` with the development dependencies installed. The HTTP tests require local socket access. Run focused checks after corrections and the complete suite before publishing. Verify terminal hyperlinks and hidden input when changing onboarding.

Update `CHANGELOG.md` and `VERIFICATION.md` with changed paths, commands, observed results and untested acceptance criteria. Catalogue access, usable inference, streaming, tool roundtrips, vision and model context limits are separate claims. Local mock tests do not establish live provider capability.

Rollback for code is returning to the prior Git commit after preserving private configuration. Stop the running process before switching code. Restore private config only from the user's own permission-protected backup. Keep the current service independent of other installations.
