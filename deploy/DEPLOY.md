# Deploying PrintQ to the remote server

This deploys the code that is on your PC, straight over SSH. GitHub is not involved: the server never clones or fetches anything, and you do not have to commit or push first. (CI on GitHub still runs on pushes, but it is separate from deployment.)

Nothing in git contains the server address, user, port or key. The script reads those from your `FreeRemote.cmd` file each time, and it builds the app settings from your local `.env`.

## What it does

1. Runs `manage.py check`, `makemigrations --check` and the test suite locally (skip with `-SkipChecks`, which prints a warning).
2. Packs your working folder into a `.tar.gz` using the files git would track or show as new, minus anything in `.gitignore`.
3. Uploads the archive, the production settings and `remote_deploy.sh` over SSH.
4. On the server: unpacks into a new release folder, installs dependencies, runs migrations, collects static files, seeds the starter accounts, switches the `current` link to the new release, and reloads the PM2 app named `printq` (gunicorn, 2 workers, port 8010).
5. Checks that the login page answers, runs the smoke test, and saves the PM2 process list.

## What gets uploaded, and what never does

Uploaded: every tracked file plus new files that are not ignored, except the `.github/` folder.

Never uploaded: `.env`, `db.sqlite3`, `private_uploads/`, `staticfiles/`, virtualenvs, `.agents/`, `.claude/`, `PLAN.md` and anything else in `.gitignore`. Because git decides this, the folder must be a git repository.

The release label looks like `20260101-120000-abc1234-dirty`: date and time, the short commit if there is one, and `-dirty` if you had uncommitted changes.

## Server layout

Everything lives under `/root/services/printq`:

- `releases/<label>/` one folder per deploy, the 3 newest are kept
- `current` a symlink to the active release
- `incoming/` where the archive lands before it is unpacked
- `venv/` the Python virtual environment
- `shared/.env` the live settings (permissions 600), plus up to 3 older `.env.bak-*` copies
- `shared/private_uploads/` uploaded print files when Supabase storage is not configured
- `logs/` gunicorn output and error logs
- `deploy.log` a timestamped record of every deploy step
- `last_good_release` the last label that deployed successfully

## Python environment

PrintQ gets its own virtual environment at `/root/services/printq/venv`. Every install and every Python command the deploy runs goes through that environment's own Python (`python -m pip`, never a bare `pip`). The system Python and the other apps on the server are never touched. System `python3` is used only to create the venv and to read PM2's process list. The deploy refuses to continue if the environment is not a real venv, and it rebuilds the venv from scratch if it is broken or was built for a different Python version (for example after an OS upgrade). PM2 starts gunicorn from the venv with the user site packages disabled.

To inspect it:

    /root/services/printq/venv/bin/python -m pip list

## One-time setup

1. Make sure your local `.env` (repo root, never committed) has a `DATABASE_URL` pointing at your Supabase session pooler. Production refuses SQLite, and the script stops before connecting if the URL is missing.
2. Put the other settings you want live in the same `.env`: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `SUPABASE_BUCKET`, and the `SEED_*` accounts. Your local `DJANGO_SECRET_KEY` is not sent. The server generates its own key the first time and keeps it on later deploys.
3. Make sure `%USERPROFILE%\SSH Remote\FreeRemote.cmd` exists with your ssh line and the key file next to it.
4. The app listens on port 8010 on the server. If the server is behind NAT, forward a public port on the provider side to 8010, then pass that public port as `-PublicPort` when you deploy.

### What the script changes in your `.env` copy

Your local `.env` is never modified. Only the copy sent to the server is adjusted.

Forced:

- `DJANGO_DEBUG=0`
- `DJANGO_ALLOWED_HOSTS` is your local value plus the server host from `FreeRemote.cmd`, `localhost` and `127.0.0.1`
- `DJANGO_CSRF_TRUSTED_ORIGINS` gets `http://<host>:<PublicPort>` added when `-PublicPort` is given
- On the server, `PRIVATE_MEDIA_ROOT` is set to `/root/services/printq/shared/private_uploads`

Dropped because they only make sense on your machine: `TEST_STUDENT_EMAIL`, `TEST_STUDENT_PASSWORD`, `PRIVATE_MEDIA_ROOT`, `DB_SSL_REQUIRE`, `DJANGO_SECRET_KEY`. Comments are not sent.

## How to deploy

Double-click `deploy\deploy.cmd`, or run:

    powershell -ExecutionPolicy Bypass -File deploy\deploy.ps1 -PublicPort 8080

Options: `-Port 8010`, `-PublicPort <n>`, `-RemoteCmd <path>`, `-SkipChecks` and `-DryRun`. A dry run does the local checks, builds the archive and prints its file count and size, then prints the ssh and scp commands without running them or contacting the server. Secret values are always shown as `***`.

## Releases and rollback

Each deploy goes into its own folder and the `current` link is switched in one atomic step only after the new release has passed its checks and migrations. If anything fails after the switch, the link goes back to the previous release, PM2 reloads, and the script prints `ROLLED BACK to <label>`. If it fails before the switch, the half-built release is deleted and the running app is not touched.

To roll back by hand later, point `current` at an older folder in `releases/` and reload `printq`.

The limit: database migrations are not reversed. If a bad release already applied a migration, the older code runs against the newer schema, so keep migrations backward compatible when you can.

## Safety rules

- The script only touches the PM2 process named exactly `printq`. It never uses `delete all`, `restart all`, `kill`, `update` or `startup`, and never installs another PM2.
- It only writes under `/root/services/printq`. The one exception is a single `apt-get update` and `apt-get install python3.12-venv`, and only if creating the venv fails.
- It never touches Docker (open-webui on port 3000), vlmcsd (port 1688), the firewall or other services.
- Port guard: if the chosen port is already listening and the listener is not printq's own process, the deploy stops before changing anything.
- `pm2 save` runs only after confirming every app that was online before the deploy is still online. Otherwise it is skipped with a warning.
- If an old `/root/services/printq/app` folder exists it is left alone.

## Useful commands on the server

PM2 is installed through nvm and is not on the PATH for non-interactive ssh, so load it first:

    export NVM_DIR=/root/.nvm; . "$NVM_DIR/nvm.sh"
    pm2 status
    pm2 logs printq
    pm2 describe printq

## Removing PrintQ cleanly

    export NVM_DIR=/root/.nvm; . "$NVM_DIR/nvm.sh"
    pm2 delete printq
    pm2 save
    rm -rf /root/services/printq
