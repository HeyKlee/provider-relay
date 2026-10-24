# Provider Relay

One local, OpenAI-compatible endpoint in front of many AI providers. You bring your own API keys; Relay picks a provider for each request and moves on to the next one if it fails.

It is plain Python 3.11+ with no packages to install.

## Quick start

1. Install [Python 3.11 or newer](https://www.python.org/downloads/).
2. Download this repository (green **Code** button → **Download ZIP**, then unzip) or `git clone` it.
3. Open a terminal in the folder and run:

   ```sh
   python relay.py        # Windows
   python3 relay.py       # macOS / Linux
   ```

4. Type the number of a provider, open the link it shows, create a key and paste it. The key is hidden as you paste. Add more providers if you like, then press **Enter**.
5. Answer **Y** to "Start the relay now?".

That's it. Relay is now running at `http://127.0.0.1:11437/v1`.

## Connect your app

Use these settings in any OpenAI-compatible app or SDK (Open WebUI, the OpenAI libraries, and so on):

| Setting  | Value |
|----------|-------|
| Base URL | `http://127.0.0.1:11437/v1` |
| Model    | `Relay` for automatic routing, or a specific alias from `/v1/models` |
| API key  | Anything, e.g. `relay` (your real provider keys stay in Relay) |

Python example:

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:11437/v1", api_key="relay")
reply = client.chat.completions.create(
    model="Relay",
    messages=[{"role": "user", "content": "Hello"}],
)
print(reply.choices[0].message.content)
```

curl:

```sh
curl http://127.0.0.1:11437/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "Relay", "messages": [{"role": "user", "content": "Hello"}]}'
```

## Everyday commands

Use `python3` instead of `python` on macOS and Linux.

| Command | What it does |
|---------|--------------|
| `python relay.py` | Add, replace or remove provider keys, then optionally start |
| `python relay.py serve` | Start the relay |
| `python relay.py providers` | List providers and which ones are ready |
| `python relay.py check` | Check each ready provider responds (asks for its model list; no chat request) |
| `python relay.py check --chat` | Send each one a tiny real message (uses a little quota) |
| `python relay.py setup --only groq` | Set up just one provider |
| `python relay.py setup --only ovh --url` | Also change a provider's endpoint URL |

Press **Ctrl+C** to stop the server.

## Providers

You only need one. Re-run `python relay.py` any time to add more.

| # | Provider (key page) | ID | Environment variable |
|---|---------------------|----|----------------------|
| 1 | [OpenRouter](https://openrouter.ai/settings/keys) | `openrouter` | `OPENROUTER_API_KEY` |
| 2 | [Groq](https://console.groq.com/keys) | `groq` | `GROQ_API_KEY` |
| 3 | [NVIDIA NIM](https://build.nvidia.com/) | `nim` | `NVIDIA_API_KEY` |
| 4 | [xKiro](https://xkiro.com/login) | `xkiro` | `XKIRO_API_KEY` |
| 5 | [Dahl](https://inference.dahl.global/) | `dahl` | `DAHL_API_KEY` |
| 6 | [Codiv](https://codiv.ai/signup) | `codiv` | `CODIV_API_KEY` |
| 7 | [Kilo Gateway](https://kilo.ai/docs/gateway/authentication) (key optional) | `kilo` | `KILO_API_KEY` |
| 8 | [BlockRun free models](https://blockrun.ai/models?filter=free) (key optional) | `blockrun` | `BLOCKRUN_API_KEY` |
| 9 | [OVH AI Endpoints](https://endpoints.ai.cloud.ovh.net/) | `ovh` | `OVH_API_KEY` |
| 10 | [LLM7](https://llm7.io/) | `llm7` | `LLM7_API_KEY` |
| 11 | [Typhoon](https://docs.opentyphoon.ai/en/quickstart/) | `typhoon` | `TYPHOON_API_KEY` |
| 12 | [Ollama Cloud](https://ollama.com/settings/keys) | `ollama` | `OLLAMA_API_KEY` |
| 13 | [Public AI](https://publicai.co/) | `publicai` | `PUBLICAI_API_KEY` |
| 14 | [Agnes API Hub](https://apihub.agnes-ai.com/) | `agnes` | `AGNES_API_KEY` |
| 15 | [Onomeo](https://onomeo.com/) | `onomeo` | `ONOMEO_API_KEY` |

**Bring your own endpoint.** These need a URL as well as a key. Setup asks for both.

| # | Gateway | ID | Environment variable |
|---|---------|----|----------------------|
| 16 | [OpenAI-compatible account gateway](https://platform.openai.com/api-keys) | `chatgpt` | `CHATGPT_API_KEY` |
| 17 | [FreeLLMAPI](https://github.com/tashfeenahmed/freellmapi) | `freellmapi` | `OMNI_API_KEY` |
| 18 | [OmniRoute](https://github.com/diegosouzapw/OmniRoute) | `omniroute` | `OMNIROUTE_API_KEY` |
| 19 | [9Router](https://github.com/decolua/9router) | `9router` | `9ROUTER_API_KEY` |

- **FreeLLMAPI, OmniRoute and 9Router** are separate projects you install and configure yourself. Give Relay the gateway's own URL and key, not an upstream provider key. A gateway on the same machine can use `http://127.0.0.1:...`; remote gateways must use HTTPS.
- **OpenAI-compatible account gateway** works with the OpenAI API directly: enter `https://api.openai.com/v1` and change this slot's model IDs in `models.json` to models your account can use. A ChatGPT subscription is not an API key.
- **Dahl** models are never picked automatically. Choose a Dahl alias by name.
- Provider defaults (links, endpoints, models) live in `providers.json`; the model list lives in `models.json`. What a given account can actually use (models, free allowance, tools, vision, context size) depends on the provider. Check your provider dashboard.

## How routing works

When you ask for model `Relay`:

- Relay tries your ready providers in the order listed in `models.json`, **free models first**.
- If a provider errors, times out or returns an empty answer, Relay moves to the next one. Providers that keep failing are skipped for a short while.
- **Paid models are off by default.** Start with `python relay.py serve --allow-paid` to allow them. Free labels are best-effort metadata, not a billing guarantee, so set spending limits in each provider's dashboard.
- Streaming (`"stream": true`) is supported.

Asking for a specific alias (for example `xKiro:qwen/qwen3.8-max:free`) sends the request to that model only.

## Keys and security

- Keys are saved in `.provider-relay/config.json` inside this folder. Git and Docker ignore it. The folder is readable only by your user account. Keys are protected by file permissions, not encryption.
- Setup never sends requests and never prints a key. Logs and error messages hide configured keys.
- Relay listens only on this computer (`127.0.0.1`) by default. To accept connections from other machines, set `PROXY_API_KEY` to a secret of your choice and start with `--host 0.0.0.0`; clients then send that value as their API key. Relay does not set up firewalls, TLS or tunnels for you.
- You can supply keys with the environment variables above instead of running setup. A provider you disabled in setup stays disabled even if its variable is set.
- `python relay.py --config /some/private/path/config.json <command>` uses a different config file. Keep it outside this folder, and pass the same `--config` to every command.

## Troubleshooting

| Message | Fix |
|---------|-----|
| `A terminal with hidden input is required` | Run setup in a normal terminal (Windows Terminal, Terminal.app, etc.), not an IDE output pane or a pipe. |
| `No providers configured` | Run `python relay.py` and set up at least one provider. |
| `check` says `FAIL (HTTP 401 ...)` | The key is wrong or expired. Run `python relay.py setup --only <id>` and choose **r** to replace it. |
| `check` fails but chat works | Some providers have no model list. Try `python relay.py check --only <id> --chat`. |
| `Automatic routing has no eligible provider` | None of your providers has a free model in the `Relay` chain (Dahl never counts). Pick a specific alias, add another provider, or use `--allow-paid`. |
| Address already in use | Another program has port 11437. Use `python relay.py serve --port 11500`. |

`/live` shows whether the server process is up. `/health` shows how each provider in the `Relay` chain has been doing since start-up (it can report 503 until the first successful request).

## Advanced settings

All optional. Set them as environment variables before `serve`.

| Variable | Default | Effect |
|----------|---------|--------|
| `PROXY_API_KEY` | unset | Require this bearer token on every request. Needed for `--host` other than loopback. |
| `PROXY_HOST` / `PROXY_PORT` | `127.0.0.1` / `11437` | Listen address (same as `--host` / `--port`). |
| `ALLOW_PAID` | `0` | `1` is the same as `--allow-paid`. |
| `HEDGE_ENABLED` | `1` | If the first provider is slow (non-streaming requests only), also ask the next one and keep the faster answer. Set `0` to avoid spending quota twice. |
| `TASK_ROUTING` | `1` | Prefer models suited to the request (code, images, long context). Never removes a provider. |
| `DIRECTIVES_ENABLED` | `0` | `1` adds a built-in "concise answers" system message to every request. |
| `RELAY_PLAIN_LINKS` | unset | `1` prints plain URLs instead of clickable terminal links. |
| `GROQ_BASE`, `NIM_BASE`, … | from `providers.json` | Override a provider's endpoint (names are in each provider's `base_env`). |
| `DAHL_API_KEYS` | unset | JSON array of Dahl keys to rotate between. |

Requests may include `"relay_task"` set to `coding`, `title`, `general`, `compression`, `review` or `classification` to hint the task. Relay removes it before forwarding.

## Docker (optional)

Native Python is simpler. If you prefer Docker, run setup natively first, then:

```sh
docker build -t provider-relay .
docker run --rm -p 127.0.0.1:11437:11437 \
  -e PROXY_API_KEY=choose-a-secret \
  -v "$(pwd)/.provider-relay:/data" \
  provider-relay
```

The container needs `PROXY_API_KEY` because it listens on all interfaces inside the container; your apps then use that secret as their API key. `http://127.0.0.1` gateway URLs from setup point at the container itself, not your computer, so use reachable HTTPS endpoints. Docker runs have not been verified by the project (see `VERIFICATION.md`).

## Development

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Tests use fake keys and local mock providers; no real accounts are needed. Code map: `relay.py` (commands and setup), `relay_config.py` (config, permissions, redaction), `router.py` (the HTTP server and routing). See `CHANGELOG.md` for changes and `VERIFICATION.md` for what has been tested.
