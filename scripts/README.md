# scripts

- `deploy.sh` — refuses to run with a dirty working tree, then runs `fly deploy --ha=false` with the current git SHA baked in as `BOT_VERSION`.
- `export_fly_secrets.py` — reads `.env` with `dotenv.dotenv_values` (the same parser `Settings` uses) and prints `NAME=value` lines for the required secrets (`TELEGRAM_BOT_TOKEN`, `GOOGLE_SERVICE_ACCOUNT_JSON`, `ANTHROPIC_API_KEY`, `WHOPAID_CONTROL_SHEET_ID`, derived from `Settings`), never `LOG_LEVEL` or `BOT_VERSION`. Validates `.env` with `Settings()` first. `--names` prints names only. Not meant to be run by hand; `set-fly-secrets.sh` calls it.
- `set-fly-secrets.sh` — feeds `export_fly_secrets.py`'s output into `fly secrets import` in one call, so a secret value never hits argv or the terminal. Prints only `Setting: NAME` lines, and only once the import has succeeded. Pass `--dry-run` to preview (`would set: NAME`) without calling Fly.
