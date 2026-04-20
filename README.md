# viking — AI menu picker for [panel.kuchniavikinga.pl](https://panel.kuchniavikinga.pl)

CLI: fetches your catering menu, asks an LLM to pick dishes that satisfy
freeform criteria from a Markdown file (macros, calories, allergens,
preferences, variety), prints the plan, and — only after explicit
confirmation — applies the selection back to the panel.

## Setup

```bash
cd /Users/alex/code/viking
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env  # then edit it
```

`.env`:

```env
VIKING_EMAIL=you@example.com
VIKING_PASSWORD=...

# LLM provider — "github" (default, free for personal use) or "openai"
VIKING_LLM_PROVIDER=github
VIKING_LLM_MODEL=openai/gpt-4o-mini
GITHUB_TOKEN=ghp_...   # PAT with `models:read` scope
# OPENAI_API_KEY=sk-... # only if VIKING_LLM_PROVIDER=openai

# Optional: override default prompt file
# VIKING_PROMPT_FILE=~/.viking/prompt.md
```

Tests:

```bash
pytest
```

## Workflow

```bash
# 1. (optional) inspect available menu without LLM
viking fetch --from 2026-04-23 --to 2026-04-29

# 2. let the LLM pick dishes; uses prompts/default.md unless overridden
viking plan --from 2026-04-23 --to 2026-04-29

# 3. preview what would be sent to the panel
viking apply --dry-run

# 4. actually apply (asks y/N)
viking apply
```

`viking plan` saves each run to [plans/](plans/) as
`<timestamp>_<from>_<to>.json`. `viking apply` picks the newest file by default;
use `--plan plans/...json` to apply a specific one.

### Prompt resolution

`viking plan` looks for the criteria prompt in this order:

1. `--prompt "..."` or `--prompt-file <path>` flag
2. `$VIKING_PROMPT_FILE`
3. `$VIKING_STATE_DIR/prompt.md` (default `~/.viking/prompt.md`)
4. [prompts/default.md](prompts/default.md) bundled in the repo

### LLM providers

- **GitHub Models** (default): free for personal use. Create a PAT with the
  `models:read` scope at <https://github.com/settings/tokens>. Set
  `VIKING_LLM_PROVIDER=github` and `VIKING_LLM_MODEL=<id>` from the list
  below. Catalog: <https://github.com/marketplace?type=models>.
- **OpenAI**: `VIKING_LLM_PROVIDER=openai`, `OPENAI_API_KEY`. Default model
  `gpt-4o-mini`.
- **Anthropic** (Claude): `VIKING_LLM_PROVIDER=anthropic`,
  `ANTHROPIC_API_KEY`. Default model `claude-sonnet-4-5`.
  > Note: Anthropic models are **not** available via GitHub Models — Copilot
  > Chat and GitHub Models inference are separate catalogs.

#### Available models on GitHub Models

Recommended for this tool (need `tool-calling` / structured output):

- **OpenAI** — best instruction-following:
  `openai/gpt-5`, `openai/gpt-5-mini`, `openai/gpt-5-nano`,
  `openai/gpt-4.1`, `openai/gpt-4.1-mini`, `openai/gpt-4.1-nano`,
  `openai/gpt-4o`, `openai/gpt-4o-mini`,
  `openai/o4-mini`, `openai/o3`, `openai/o3-mini`, `openai/o1`
- **DeepSeek**: `deepseek/deepseek-v3-0324`, `deepseek/deepseek-r1`,
  `deepseek/deepseek-r1-0528`
- **Meta**: `meta/llama-4-maverick-17b-128e-instruct-fp8`,
  `meta/llama-4-scout-17b-16e-instruct`
- **Mistral**: `mistral-ai/mistral-medium-2505`,
  `mistral-ai/mistral-small-2503`, `mistral-ai/ministral-3b`
- **Cohere**: `cohere/cohere-command-r-plus-08-2024`
- **AI21**: `ai21-labs/ai21-jamba-1.5-large`
- **xAI**: `xai/grok-3`, `xai/grok-3-mini` (no tool-calling, may need retry)

Other publishers without tool-calling (will rely on JSON-prompt + retry):
`microsoft/phi-4*`, `meta/llama-3.*`, `cohere/cohere-command-a`,
`mistral-ai/codestral-2501`.

For best results with strict constraints (kcal limits, exclusions) use
`openai/gpt-5` or `openai/gpt-4.1`. `*-mini` and `*-nano` are cheaper and
faster but more likely to bend rules.

To get the live catalog with your token:

```bash
curl -sS -H "Authorization: Bearer $GITHUB_TOKEN" \
  https://models.github.ai/catalog/models | jq -r '.[].id'
```

The LLM gets a compact JSON of the menu (per day, per slot, with macros) and
the prompt; the response is constrained by JSON Schema to a list of
`{date, slot, dish_id}`. Selections are validated against the actual menu;
on mismatch the LLM is retried once with the validation error as feedback.

## Layout

- [src/viking/cli.py](src/viking/cli.py) — Typer CLI (`fetch`, `plan`, `apply`)
- [src/viking/ai/selector.py](src/viking/ai/selector.py) — prompt building, validation, retry
- [src/viking/ai/schema.py](src/viking/ai/schema.py) — JSON Schema for the LLM response
- [src/viking/api/client.py](src/viking/api/client.py) — HTTP client (login + fetch + switch)
- [src/viking/api/models.py](src/viking/api/models.py) — Pydantic models
- [src/viking/config.py](src/viking/config.py) — `.env` settings
- [scripts/inspect_har.py](scripts/inspect_har.py) — HAR analyser used during API discovery
- [prompts/default.md](prompts/default.md) — bundled default criteria prompt
- [tests/test_selector.py](tests/test_selector.py) — selector tests on a fixture

## Re-running API discovery

If the panel changes its API and the client breaks, capture a fresh HAR:

1. Open [panel.kuchniavikinga.pl](https://panel.kuchniavikinga.pl) in Chrome,
   log in.
2. DevTools → Network → enable **Preserve log**.
3. Switch a date, change a dish, save.
4. Right-click any request → **Save all as HAR with content** → save into
   `captures/` (gitignored).
5. `python scripts/inspect_har.py captures/<file>.har` — prints discovered
   endpoints. Update [src/viking/api/client.py](src/viking/api/client.py) to match.

## Security notes

- **HAR files contain plaintext credentials** (the login POST body has your
  email and password). Treat any HAR as a secret. `*.har` is gitignored.
- `.env`, `.env.*` (except `.env.example`), `plans/`, `captures/`, and
  `~/.viking/` are gitignored.
- The LLM call sends only the menu JSON and your prompt — no panel
  credentials, no email, no order ids leave your machine for the LLM.
- The HTTP client uses TLS verification (httpx default). It does not log
  request bodies or cookies.
- `viking apply` is the **only** code path that mutates server state. It is
  invoked exclusively from the `apply` CLI command and requires interactive
  confirmation unless `--yes` is passed.
- If a HAR was ever captured (or shared) with a real password, **rotate the
  password** at <https://panel.kuchniavikinga.pl/przypomnienie-hasla>.
