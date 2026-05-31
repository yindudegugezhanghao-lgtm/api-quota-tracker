# Security

API Quota Tracker is designed to avoid storing API keys in committed files or run outputs, but you still need to handle test results carefully.

## What The Tool Does

- Keeps API keys in environment variables when running the CLI.
- Passes the GUI API key to the child process through an environment variable.
- Does not write the API key to `metadata.json`, `summary.json`, `results.jsonl`, or `report.txt`.
- Ignores local configs and run outputs through `.gitignore`.

## What You Should Not Commit

Do not commit:

- `.env`
- `config.toml`
- `runs/`
- `kimi_cli_runs/`
- provider logs
- raw support bundles
- screenshots that show account IDs, private endpoints, or keys

## Before Opening A Public Issue

Remove:

- API keys
- bearer tokens
- account IDs
- organization IDs
- private endpoint URLs
- billing screenshots
- request IDs if your provider treats them as sensitive

## Safer Testing Habits

- Run a small smoke test before a long probe.
- Do not run multiple quota tests on the same account at the same time.
- Use the minimum `max_tokens` that still exercises the endpoint.
- Review your provider's terms before running high-volume tests.
- Stop the test if the provider sends warnings or account-risk messages.

## Maintainer Checklist

Before publishing a release:

- Run `python -m py_compile plan_probe.py gui.py kimi_cli_probe.py`.
- Run `python plan_probe.py --config config.example.toml --dry-run` with a dummy environment key.
- Search the repository for local paths and secret-looking values.
- Confirm that screenshots contain fake data only.
