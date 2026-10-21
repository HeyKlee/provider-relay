# Provider Relay

A self-contained, OpenAI-compatible Python router with guided terminal setup. Bring your own provider keys. Runtime uses Python's standard library and has no package dependencies.

## Start

Install Python 3.11 or newer. Download or clone this repository using its Code menu, then open a terminal in the repository directory.

```powershell
python relay.py setup
if ($LASTEXITCODE -ne 0) { throw 'Setup did not finish' }
python relay.py serve
```

Linux and macOS:

```sh
python3 relay.py setup && python3 relay.py serve
```

The setup walks through every provider. Each page shows a clickable website link and instructions for obtaining a key. Choose a provider, confirm its endpoint, and paste the key at the hidden prompt. Press Enter to skip providers. You can use one provider or several.

Links use OSC 8 in interactive terminals and include a visible URL. Older terminals can use the visible URL. Set `RELAY_PLAIN_LINKS=1` to turn off OSC 8. API-key input refuses to fall back to visible input.

The API listens at `http://127.0.0.1:11437/v1`. Choose model `Relay` for automatic routing, or an individual alias from `/v1/models`. `default` is an alias for `Relay`.

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:11437/v1/chat/completions' -Method Post -ContentType 'application/json' -Body '{"model":"Relay","messages":[{"role":"user","content":"Hello"}]}'
```

For SDKs that require a client API key, use a placeholder on loopback unless you set `PROXY_API_KEY`. Provider keys belong in setup, not in the client.

## Providers

Setup covers OpenRouter, Groq, NVIDIA NIM, xKiro, Dahl, Codiv, Kilo, BlockRun, OVH AI Endpoints, LLM7, Typhoon, Ollama Cloud, Public AI, Agnes, Onomeo, an OpenAI-compatible account gateway, FreeLLMAPI, OmniRoute and 9Router.

The last three are optional, separately installed gateways. Setup links to their upstream projects and asks for your own API base URL and gateway key. It does not install them or configure their upstream accounts. Configure their dashboards separately. Gateway combo and model names must match your own installation. A gateway on the same machine can use an HTTP loopback URL. Remote gateways require HTTPS.

The OpenAI-compatible account gateway slot supports configurable model routing. Its model names are examples. If connecting directly to the OpenAI API, enter `https://api.openai.com/v1` and update this slot's model IDs in `models.json` to models available through your API account. A ChatGPT subscription does not supply an API key.

Each direct provider has an independent endpoint and key. Typhoon, Ollama Cloud, Public AI, Agnes and Onomeo call their native endpoints directly. Their model IDs have no gateway-specific wrappers. Ollama Cloud sends inference to Ollama's cloud and does not start a local model.

Provider websites, endpoint and model defaults are in `providers.json`. The catalogue contains 77 model aliases plus `default`. Model availability, free allowances, tools, vision and context limits depend on the provider and account. Catalogue metadata is configuration, not a claim that every model works with a new account. Confirm account details in your provider's dashboard.

## Reconfigure and verify

```powershell
python relay.py setup --only groq
python relay.py providers
python relay.py check
python relay.py check --only groq --chat
```

`setup --only` accepts IDs shown by `providers`. Re-running setup keeps an existing key unless you update or disable that provider. Disable removes the saved key. Each completed provider is saved immediately; cancelling preserves completed entries.

`check` tests catalogue access. `--chat` sends a small real inference request and may consume provider quota. These are different checks. No provider API requests occur during setup. Nonzero exit status means a check failed or no provider was configured. If an endpoint lacks `/models`, catalogue checking can fail even when chat works.

The router supports ordered failover, streaming, task selection, free-first ordering, conversation affinity, circuit breakers, bounded upstream concurrency, optional delayed parallel requests, Codiv bounded-context processing, and Dahl allowance tracking. Optional response-style directives are disabled by default; set `DIRECTIVES_ENABLED=1` to enable them. Set `HEDGE_ENABLED=0` to disable delayed parallel requests, which can consume quota on more than one provider.

Routes marked paid are blocked by default. `python relay.py serve --allow-paid` permits them, and `check --chat --allow-paid` permits paid probe requests. Free-first metadata does not enforce an account's billing plan or guarantee that a provider remains free. Set account spending limits with each provider.

The router recognises optional `relay_task` hints: `coding`, `title`, `general`, `compression`, `review` and `classification`. Hints are stripped before forwarding to providers.

`/live` reports process liveness. `/health` reports observed provider results and can return 503 before any successful inference. It is not an active probe. Health and affinity data are per process. Credentials and Dahl accounting state persist on disk; conversation affinity does not.

## Credentials and access

Keys are saved in `.provider-relay/config.json`, excluded from Git and Docker builds. On Windows the credentials directory has inheritance removed and access granted to the current user and SYSTEM. On Linux/macOS the directory uses mode 0700 and the config uses 0600. Keys are protected by filesystem permissions, not encrypted at rest. The setup never prints a key. Logs and JSON error responses redact configured keys.

Provider-specific environment variables can supply credentials instead of setup. Their names are listed in `providers.json`. An explicitly disabled saved provider remains disabled even if a matching environment variable exists. Dahl also accepts a JSON array in `DAHL_API_KEYS` for funded-key rotation.

`python relay.py --config /path/outside/repo/config.json setup` selects another private config. Use the same option when starting or checking. Keep custom config paths outside the repository. Do not commit credentials, logs, runtime state or private config backups.

The default is local access. `PROXY_API_KEY` enables bearer authentication on all endpoints. Listening beyond loopback requires this key when using the launcher. This repository does not configure firewall rules, tunnels, LAN access or TLS termination.

## Docker (optional)

Native Python is the simplest startup. To run in Docker, complete setup first and supply a client key in `PROXY_API_KEY`. Build the image from this directory. Bind the published port to `127.0.0.1` and mount `.provider-relay` at `/data`; the image reads `/data/config.json`. Upstream HTTP loopback URLs from native setup do not point to the host inside a container. Configure reachable HTTPS endpoints for container use.

The image requires `PROXY_API_KEY` because its internal listener uses `0.0.0.0`. Docker execution is separate from native runtime verification.

## Tests and project record

```powershell
python -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed' }
& '.\.venv\Scripts\python.exe' -m pip install -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { throw 'Test dependency installation failed' }
& '.\.venv\Scripts\python.exe' -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }
```

Tests use temporary fake credentials and loopback mock providers. They never need real provider accounts. See `VERIFICATION.md` for what was tested and `CHANGELOG.md` for changes. Project execution context is in `.agents/skills/project-execution-context/SKILL.md`.

To stop, press Ctrl+C. The clone has no dependency on an existing deployment. Rollback is stopping this process and restoring your own prior private config backup if you changed it. No system services are installed by setup.
