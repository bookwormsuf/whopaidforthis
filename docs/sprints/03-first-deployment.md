# Sprint 3 — First deployment

## Goal
The bot runs on Fly.io and keeps answering with your laptop closed. From here on you test against the
deployed bot, not your laptop.

## In scope
Master spec §15. `fly.toml`, deploy docs, health logging, graceful shutdown.

## Out of scope
Any feature work.

## Sprint pins
- App name `whopaid-bot` (Fly names are global; fall back to `whopaid-bot-<4 random chars>` if taken). Region `sin`.
- `fly.toml`: `[processes] app = "python -m app"`, no `[http_service]` section (nothing listens on a port), `auto_stop_machines = false`, `auto_start_machines = true`, `min_machines_running = 1`, VM `shared-cpu-1x`, 256 MB.
- Exactly one machine, ever. Two machines polling the same token make Telegram return 409 and both go deaf. README states: stop the local bot before deploying and never run both.
- On SIGTERM the bot stops polling and exits within 10 s.
- Startup logs one line `whopaid started` with bot username and version; nothing else about config.
- Version = short git SHA baked into the image via build arg, shown in `/help` footer.
- The agent writes `fly.toml` and the README steps; **you** run the commands below, because they need your Fly login.

## Before you start (you)
Make sure Sprint 2 is signed off and the local bot is stopped.

## Agent builds
`fly.toml`, Dockerfile build-arg for version, shutdown handling, README section "Deployment" with the exact commands below and a "Stopping after the trip" subsection, `scripts/set-fly-secrets.sh` that reads `.env` and calls `fly secrets set` for each variable (so you never paste secrets by hand).

## Tests required
Shutdown handler unit test; version string present in `/help`.

## After: your checklist
1. `fly auth login` (opens a browser; you already have flyctl installed).
2. `fly launch --no-deploy --copy-config --name whopaid-bot --region sin`. Answer **No** to Postgres, Redis and any other add-on.
3. `./scripts/set-fly-secrets.sh` . It prints the variable names it set, never the values.
4. `fly deploy`. Wait for "deployed successfully".
5. `fly status`. Exactly one machine, state `started`.
6. `fly logs`. You see `whopaid started`.
7. Close your laptop. From your phone, send `/help` and `500 coffee` in the test group, confirm it. Reopen the laptop; the Sheet has the row.
8. `fly machine list` shows one machine. If two, run `fly scale count 1`.
9. Make a trivial change with the agent, `fly deploy` again, confirm `/help` shows the new version.

## Exit criteria
- [ ] Bot answered with laptop closed
- [ ] One machine only
- [ ] Secrets set via the script; none in git (`git log -p | grep -c sk-ant` is 0)
- [ ] `STATUS.md` entry with your sign-off
