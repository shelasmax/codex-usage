# Codex Usage

**English** | [Русский](README.ru.md)

Local usage analytics and API-equivalent cost reporting for OpenAI Codex.

Codex Usage reads the local Codex SQLite database and rollout telemetry, reconstructs root-agent and subagent activity, and generates an Excel dashboard with token usage, cache efficiency, task economics, and estimated API-equivalent costs.

All analysis runs locally. Your Codex database, prompts, task history, and generated reports are not uploaded anywhere by this tool.

## Features

- Reads the local Codex SQLite state database
- Parses current and legacy rollout telemetry
- Tracks root agents, subagents, and auto-review activity
- Reconstructs task trees from Codex spawn relationships
- Daily, weekly, monthly, project, model, and task-level analytics
- Input, cached input, uncached input, output, and reasoning token metrics
- Prompt-cache hit rate analysis
- Model × reasoning-effort economics
- API-equivalent cost estimates in USD and RUB
- Pricing coverage and explicit unpriced-token tracking
- Excel dashboard with:
  - current period KPIs
  - daily usage
  - top projects by estimated API cost
  - top tasks by estimated API cost
  - model / effort economics
  - root-agent effort economics

## How it works

Codex Usage uses two local data sources:

1. `~/.codex/state_5.sqlite`
   - threads
   - project working directories
   - models and reasoning effort
   - parent/child agent relationships

2. Codex rollout JSONL files
   - token usage events
   - input tokens
   - cached input tokens
   - output tokens
   - reasoning tokens

The data is aggregated into:

```text
Codex SQLite + rollouts
        ↓
usage parser
        ↓
thread / agent hierarchy
        ↓
task economics
        ↓
daily / weekly / monthly analytics
        ↓
Excel dashboard
```

## Installation

Requires Python 3.13+.

```bash
git clone https://github.com/shelasmax/codex-usage.git
cd codex-usage

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
```

## Configuration

Copy the example configuration:

```bash
cp config.example.yaml config.yaml
```

Edit `config.yaml` for your environment.

Example:

```yaml
timezone: "Europe/Moscow"

codex:
  database: "~/.codex/state_5.sqlite"

output:
  excel: "output/Codex_Usage.xlsx"

pricing:
  usd_rub: 80.00

project_aliases:
  "/Users/yourname/Documents/Projects/example-project": "Example Project"
```

Project aliases are optional. Codex Usage discovers working directories automatically.

## Pricing

API-equivalent token prices are stored in:

```text
pricing.yaml
```

Prices are expressed in USD per 1 million tokens.

Unknown models are deliberately left unpriced instead of being assigned an assumed cost.

This makes pricing coverage visible in the generated report.

## Generate the report

```bash
python build_excel.py
```

The report is written to:

```text
output/Codex_Usage.xlsx
```

## Diagnostic tools

Inspect the local Codex database and parsed usage:

```bash
python main.py
```

Inspect daily usage:

```bash
python inspect_daily.py
```

Inspect reconstructed task economics:

```bash
python inspect_tasks.py
```

## Cost-estimation methodology

For a priced model:

```text
uncached input cost
+ cached input cost
+ output cost
= API-equivalent cost
```

Cached input is priced separately from uncached input.

Reasoning tokens are reported separately but are not added again on top of output tokens when calculating cost.

### Important

The generated cost is an **API-equivalent estimate**.

It is **not** a reconstruction of ChatGPT or Codex subscription billing.

Current limitations include:

- long-context request surcharges are not yet applied
- internal models without a public/known rate remain unpriced
- tool-specific charges are not included
- foreign-exchange conversion uses the manually configured rate

The dashboard therefore reports both:

- priced API-equivalent cost
- pricing coverage / unpriced tokens

## Privacy

Codex Usage is designed to operate locally.

The following should never be committed to Git:

- `config.yaml`
- `.venv/`
- generated Excel reports
- local Codex databases
- rollout files
- logs

The included `.gitignore` excludes local configuration and generated reports.

Task titles written to Excel are truncated to a safe display length. The original Codex data is not modified.

## Status

Early open-source release.

The local Codex storage and rollout formats are implementation details and may change between Codex versions. Parser compatibility may therefore require updates over time.

## License

MIT