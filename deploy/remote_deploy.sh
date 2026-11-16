#!/usr/bin/env bash
# Runs ON THE SERVER as root. Installs an uploaded PrintQ release under /root/services/printq and
# manages only the PM2 process named "printq". Safe to re-run. No GitHub access needed.
set -euo pipefail

RELEASE=""
PORT="8010"
while [ $# -gt 0 ]; do
  case "$1" in
    --release) RELEASE="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done
if [ -z "$RELEASE" ]; then
  echo "Usage: remote_deploy.sh --release LABEL [--port N]" >&2
  exit 2
fi
# The label becomes a folder name that later gets rm -rf'd: allow only plain names, never a path.
if ! [[ "$RELEASE" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || [ "$RELEASE" = "." ] || [ "$RELEASE" = ".." ]; then
  echo "Invalid release label: $RELEASE" >&2
  exit 2
fi
if ! [[ "$PORT" =~ ^[0-9]+$ ]] || [ "$PORT" -lt 1024 ] || [ "$PORT" -gt 65535 ]; then
  echo "Invalid port: $PORT" >&2
  exit 2
fi

ROOT="${PRINTQ_ROOT:-/root/services/printq}"
RELEASES=$ROOT/releases
CURRENT=$ROOT/current
INCOMING=$ROOT/incoming
VENV=$ROOT/venv
PY="$VENV/bin/python"
# Everything Python runs through the dedicated venv. System python3 is used only to create the
# venv and for the read-only pm2 jlist JSON helper below.
export PIP_REQUIRE_VIRTUALENV=1
export PIP_DISABLE_PIP_VERSION_CHECK=1
export PYTHONNOUSERSITE=1
SHARED=$ROOT/shared
LOGS=$ROOT/logs
DEPLOY_LOG=$ROOT/deploy.log
REL_DIR=$RELEASES/$RELEASE
PREV_RELEASE=""
SWITCHED=0
mkdir -p "$ROOT" "$LOGS" "$RELEASES" "$INCOMING"

step() {
  local line
  line="$(date '+%Y-%m-%d %H:%M:%S') ==> $*"
  echo "$line"
  echo "$line" >> "$DEPLOY_LOG"
}

load_pm2() {
  export NVM_DIR=/root/.nvm
  # shellcheck disable=SC1091
  if [ -s "$NVM_DIR/nvm.sh" ]; then . "$NVM_DIR/nvm.sh"; fi
}

health_check() {
  local i code
  for i in $(seq 1 20); do
    code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/accounts/login/" || true)
    if [ "$code" = "200" ]; then return 0; fi
    sleep 2
  done
  return 1
}

online_apps() {
  pm2 jlist 2>/dev/null | python3 -c '
import json,sys
raw=sys.stdin.read()
start=raw.find("[")
data=json.loads(raw[start:]) if start>=0 else []
for p in data:
    if p.get("pm2_env",{}).get("status")=="online":
        print(p["name"])
' | sort
}

switch_current() {  # switch_current RELEASE_DIR : atomic symlink swap
  ln -sfn "$1" "$CURRENT.tmp"
  mv -Tf "$CURRENT.tmp" "$CURRENT"
}

on_exit() {
  local rc=$?
  if [ $rc -ne 0 ]; then
    echo
    step "FAILED (exit $rc)"
    if [ "$SWITCHED" = "1" ] && [ -n "$PREV_RELEASE" ] && [ -d "$PREV_RELEASE" ]; then
      step "Rolling back to $(basename "$PREV_RELEASE")"
      (
        set +e
        switch_current "$PREV_RELEASE"
        if pm2 describe printq >/dev/null 2>&1; then
          PRINTQ_PORT=$PORT pm2 reload printq --update-env
        fi
        if health_check; then
          echo "ROLLED BACK to $(basename "$PREV_RELEASE")"
        else
          echo "Rollback finished but the health check still fails."
        fi
        echo "Note: database migrations are NOT reversed by a rollback."
      ) || true
    elif [ "$SWITCHED" = "1" ]; then
      echo "No previous release to roll back to; the new release stays current."
    else
      if [ -d "$REL_DIR" ]; then
        rm -rf "$REL_DIR"
        echo "Removed the half-built release $RELEASE. The running app was not touched."
      fi
    fi
    if [ -f "$LOGS/printq-error.log" ]; then
      echo "---- last 40 lines of printq-error.log ----"
      tail -n 40 "$LOGS/printq-error.log" || true
    fi
  fi
  exit $rc
}
trap on_exit EXIT

# 1 -------------------------------------------------------------------------
step "Load nvm and check pm2"
load_pm2
if ! command -v pm2 >/dev/null 2>&1; then
  echo "pm2 not found (looked under /root/.nvm). Not installing it; fix that first." >&2
  exit 1
fi
if [ -d "$ROOT/app" ]; then
  echo "    note: $ROOT/app exists from an older layout; leaving it alone."
fi

# 2 -------------------------------------------------------------------------
step "Snapshot online pm2 apps"
ONLINE_BEFORE="$(online_apps || true)"
echo "$ONLINE_BEFORE" | sed 's/^/    online: /'

# 3 -------------------------------------------------------------------------
step "Ensure directories and python venv"
mkdir -p "$SHARED/private_uploads" "$LOGS"
SYS_MM="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
NEED_VENV=0
if [ ! -x "$PY" ]; then
  NEED_VENV=1
else
  VENV_MM="$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)"
  if [ -z "$VENV_MM" ] || [ "$VENV_MM" != "$SYS_MM" ]; then
    echo "    existing venv is broken or built for another Python (${VENV_MM:-unknown} vs $SYS_MM); recreating"
    NEED_VENV=1
  fi
fi
if [ "$NEED_VENV" = "1" ]; then
  rm -rf "$VENV"
  if ! python3 -m venv --clear "$VENV" 2>/dev/null; then
    step "venv creation failed, installing python3.12-venv"
    rm -rf "$VENV"
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3.12-venv
    python3 -m venv --clear "$VENV"
  fi
fi
step "Venv isolation check"
"$PY" -c 'import sys; assert sys.prefix != sys.base_prefix, "not a venv"; print(sys.version.split()[0], sys.prefix)'

# 4 -------------------------------------------------------------------------
step "Port guard (port $PORT)"
LISTENERS="$(ss -ltnpH "sport = :$PORT" 2>/dev/null || true)"
if [ -n "$LISTENERS" ]; then
  OWN_PIDS=""
  PM2_PID="$(pm2 pid printq 2>/dev/null | tr -d '[:space:]' || true)"
  if [ -n "$PM2_PID" ] && [ "$PM2_PID" != "0" ]; then
    OWN_PIDS="$PM2_PID"
    for c in $(pgrep -P "$PM2_PID" 2>/dev/null || true); do
      OWN_PIDS="$OWN_PIDS $c $(pgrep -P "$c" 2>/dev/null | tr '\n' ' ' || true)"
    done
  fi
  FOREIGN=0
  for pid in $(echo "$LISTENERS" | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u); do
    case " $OWN_PIDS " in
      *" $pid "*) ;;
      *) FOREIGN=1 ;;
    esac
  done
  if [ "$FOREIGN" = "1" ]; then
    echo "Port $PORT is in use by something other than the printq pm2 process:" >&2
    echo "$LISTENERS" >&2
    echo "Nothing was changed. Pick another port with -Port." >&2
    exit 1
  fi
fi

# 5 -------------------------------------------------------------------------
step "Unpack release $RELEASE"
TARBALL="$INCOMING/$RELEASE.tar.gz"
if [ ! -f "$TARBALL" ]; then
  echo "Missing $TARBALL. Upload the release first (deploy.ps1 does this)." >&2
  exit 1
fi
if [ -L "$CURRENT" ] || [ -e "$CURRENT" ]; then
  PREV_RELEASE="$(readlink -f "$CURRENT" || true)"
fi
if [ -n "$PREV_RELEASE" ] && [ "$(readlink -f "$REL_DIR" 2>/dev/null || echo "$REL_DIR")" = "$PREV_RELEASE" ]; then
  echo "Release $RELEASE is the one currently running; refusing to overwrite it. Deploy again for a new label." >&2
  exit 1
fi
rm -rf "$REL_DIR"
mkdir -p "$REL_DIR"
tar -xzf "$TARBALL" -C "$REL_DIR"
rm -f "$TARBALL"
if [ -n "$PREV_RELEASE" ]; then
  echo "    previous release: $(basename "$PREV_RELEASE")"
else
  echo "    first release, nothing to roll back to"
fi
if [ ! -f "$REL_DIR/manage.py" ]; then
  echo "The archive does not look like PrintQ (no manage.py)." >&2
  exit 1
fi

# 6 -------------------------------------------------------------------------
step "Prepare environment file"
ENV_FILE="$SHARED/.env"
PREV_SECRET=""
if [ -f "$ENV_FILE" ]; then
  PREV_SECRET="$(grep -E '^DJANGO_SECRET_KEY=.+' "$ENV_FILE" | tail -n1 | cut -d= -f2- || true)"
fi
if [ -f "$SHARED/.env.incoming" ]; then
  if [ -f "$ENV_FILE" ]; then
    cp -p "$ENV_FILE" "$SHARED/.env.bak-$(date +%Y%m%d-%H%M%S)"
    ls -1t "$SHARED"/.env.bak-* 2>/dev/null | tail -n +4 | xargs -r rm -f
  fi
  mv -f "$SHARED/.env.incoming" "$ENV_FILE"
  echo "    installed new .env from deploy"
fi
if [ ! -f "$ENV_FILE" ]; then
  echo "No $ENV_FILE. Put DATABASE_URL in your local .env and run deploy.ps1 again." >&2
  exit 1
fi
sed -i 's/\r$//' "$ENV_FILE"
if ! grep -qE '^DATABASE_URL=.+' "$ENV_FILE"; then
  echo "DATABASE_URL is missing or empty in the server .env. Set it in your local .env and re-run deploy." >&2
  exit 1
fi
set_kv() {  # set_kv KEY VALUE : replace or append
  grep -vE "^$1=" "$ENV_FILE" > "$ENV_FILE.tmp" || true
  echo "$1=$2" >> "$ENV_FILE.tmp"
  mv -f "$ENV_FILE.tmp" "$ENV_FILE"
}
if ! grep -qE '^DJANGO_SECRET_KEY=.+' "$ENV_FILE"; then
  if [ -n "$PREV_SECRET" ]; then
    set_kv DJANGO_SECRET_KEY "$PREV_SECRET"
    echo "    kept previous DJANGO_SECRET_KEY"
  else
    set_kv DJANGO_SECRET_KEY "$("$PY" -c 'import secrets;print(secrets.token_urlsafe(50))')"
    echo "    generated DJANGO_SECRET_KEY"
  fi
fi
set_kv DJANGO_DEBUG 0
set_kv PRIVATE_MEDIA_ROOT "$SHARED/private_uploads"
chmod 600 "$ENV_FILE"
ln -sfn "$ENV_FILE" "$REL_DIR/.env"

# 7 -------------------------------------------------------------------------
step "Install python dependencies"
"$PY" -m pip install --upgrade pip -q
"$PY" -m pip install -r "$REL_DIR/requirements.txt" -q
"$VENV/bin/gunicorn" --version

# 8 -------------------------------------------------------------------------
step "check, migrate, collectstatic, seed"
cd "$REL_DIR"
"$PY" manage.py check
"$PY" manage.py migrate --noinput
"$PY" manage.py collectstatic --noinput
"$PY" manage.py seed

# 9 -------------------------------------------------------------------------
step "Switch current to $RELEASE and start or reload pm2 app printq"
switch_current "$REL_DIR"
SWITCHED=1
PRINTQ_ROOT=$ROOT PRINTQ_PORT=$PORT pm2 startOrReload "$REL_DIR/deploy/ecosystem.config.js" --only printq --update-env

# 10 ------------------------------------------------------------------------
step "Health check and smoke test"
if ! health_check; then
  echo "App did not return HTTP 200 on /accounts/login/ within 40s." >&2
  exit 1
fi
cd "$(readlink -f "$CURRENT")"
"$PY" scripts/smoke_test.py --base-url "http://127.0.0.1:$PORT"

# 11 ------------------------------------------------------------------------
step "Record success and prune old releases"
echo "$RELEASE" > "$ROOT/last_good_release"
KEEP_CUR="$(readlink -f "$CURRENT")"
KEEP_PREV="${PREV_RELEASE:-}"
COUNT=0
for d in $(ls -1dt "$RELEASES"/*/ 2>/dev/null); do
  d="${d%/}"
  COUNT=$((COUNT + 1))
  if [ "$COUNT" -le 3 ]; then continue; fi
  if [ "$(readlink -f "$d")" = "$KEEP_CUR" ] || [ "$(readlink -f "$d")" = "$KEEP_PREV" ]; then continue; fi
  echo "    pruning $(basename "$d")"
  rm -rf "$d"
done
ONLINE_AFTER="$(online_apps || true)"
MISSING=""
for n in $ONLINE_BEFORE; do
  if ! grep -qx "$n" <<< "$ONLINE_AFTER"; then MISSING="$MISSING $n"; fi
done
if [ -n "$MISSING" ]; then
  echo "WARNING: previously online app(s) not online now:$MISSING. Skipping pm2 save." >&2
else
  pm2 save
fi
echo
echo "Deployed release $RELEASE on port $PORT"
pm2 describe printq | grep -E 'status|uptime|restarts' || true
step "Done"
