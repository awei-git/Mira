#!/bin/sh
# box-deploy.sh — generic deploy worker, runs ON the EC2 box as root.
# Never hand-edit per-project values here; the sandbox builds the full
# command line from deploy/projects.yaml (single source of truth).
#
#   box-deploy.sh --project NAME --path /opt/x --url URL --sha256 SUM \
#       [--exclude dir]... [--service unit | --docker-container name
#        --docker-publish 127.0.0.1:80:8080 --docker-volume SRC:DST
#        --docker-env-file FILE] [--overlay rel/patch] [--health-cmd "..."]
#       [--stage] [--tag TAG]
#   box-deploy.sh --rollback --project NAME --path /opt/x --backup FILE.tgz \
#       [--service unit | --docker-container ...]
#
# Rules: data dirs are rsync-excluded (never touched); every deploy backs up
# the old tree first; any failure after backup restores it automatically.
set -eu

DEPLOY_ROOT=/opt/deploy
STAGE=$DEPLOY_ROOT/staging
BACKUPS=$DEPLOY_ROOT/backups
log() { echo "[deploy] $*"; }
die() { echo "[deploy] FATAL: $*" >&2; exit 1; }

PROJECT=""; PATH_=""; URL=""; SUM=""; SERVICE=""; OVERLAY=""; HEALTH=""
DOCKERC=""; DOCKERP=""; DOCKERV=""; DOCKERE=""; STAGE_ONLY=0; TAG="manual"
ROLLBACK=0; BACKUP=""
EXCLUDES=""

while [ $# -gt 0 ]; do
  case "$1" in
    --project) PROJECT="$2"; shift 2;;
    --path) PATH_="$2"; shift 2;;
    --url) URL="$2"; shift 2;;
    --sha256) SUM="$2"; shift 2;;
    --exclude) EXCLUDES="$EXCLUDES --exclude=$2"; shift 2;;
    --service) SERVICE="$2"; shift 2;;
    --overlay) OVERLAY="$2"; shift 2;;
    --health-cmd) HEALTH="$2"; shift 2;;
    --docker-container) DOCKERC="$2"; shift 2;;
    --docker-publish) DOCKERP="$2"; shift 2;;
    --docker-volume) DOCKERV="$2"; shift 2;;
    --docker-env-file) DOCKERE="$2"; shift 2;;
    --stage) STAGE_ONLY=1; shift;;
    --tag) TAG="$2"; shift 2;;
    --rollback) ROLLBACK=1; shift;;
    --backup) BACKUP="$2"; shift 2;;
    *) die "unknown arg $1";;
  esac
done

[ -n "$PROJECT" ] || die "--project required"
[ -n "$PATH_" ] || die "--path required"
mkdir -p "$STAGE" "$BACKUPS"

do_rollback() {
  # $1 = backup tgz
  log "restoring $1 -> $PATH_"
  tar xzf "$1" -C /
  if [ -n "$SERVICE" ]; then systemctl restart "$SERVICE"; fi
  log "rollback done"
}

if [ "$ROLLBACK" = 1 ]; then
  [ -n "$BACKUP" ] || die "--backup required for rollback"
  [ -f "$BACKUP" ] || die "backup not found: $BACKUP"
  do_rollback "$BACKUP"
  exit 0
fi

[ -n "$URL" ] || die "--url required"
[ -n "$SUM" ] || die "--sha256 required"
command -v curl >/dev/null || die "curl missing"
command -v rsync >/dev/null || die "rsync missing"

TS=$(date +%Y%m%d-%H%M%S)
WORK=$STAGE/${PROJECT}-${TS}
mkdir -p "$WORK"
TARBALL=$WORK/dist.tar.gz

log "project=$PROJECT tag=$TAG"
log "downloading dist..."
curl -fsSL --retry 3 -o "$TARBALL" "$URL" || die "download failed"
echo "$SUM  $TARBALL" | sha256sum -c - >/dev/null || die "sha256 mismatch"
mkdir -p "$WORK/new"
tar xzf "$TARBALL" -C "$WORK/new"
log "dist verified: $(find "$WORK/new" -type f | wc -l) files"

if [ "$STAGE_ONLY" = 1 ]; then
  DEST=$DEPLOY_ROOT/staging-test/$PROJECT
  rm -rf "$DEST"; mkdir -p "$DEST"
  # shellcheck disable=SC2086
  rsync -a $EXCLUDES "$WORK/new/" "$DEST/"
  log "STAGE-ONLY: extracted to $DEST (live path untouched)"
  exit 0
fi

[ -d "$PATH_" ] || die "deploy path missing: $PATH_"
BACKUP_FILE=$BACKUPS/${PROJECT}-${TS}.tgz
log "backup -> $BACKUP_FILE"
tar czf "$BACKUP_FILE" -C / "${PATH_#/}" || die "backup failed"

restore_on_fail() {
  log "DEPLOY FAILED: $1 — rolling back"
  do_rollback "$BACKUP_FILE"
  exit 1
}

# shellcheck disable=SC2086
rsync -a --delete $EXCLUDES "$WORK/new/" "$PATH_"/ || restore_on_fail "rsync failed"

if [ -n "$OVERLAY" ]; then
  if [ -f "$WORK/new/$OVERLAY" ]; then
    log "applying overlay $OVERLAY"
    (cd "$PATH_" && patch -p1 --dry-run < "$WORK/new/$OVERLAY" >/dev/null) \
      || restore_on_fail "overlay dry-run failed (already upstream?)"
    (cd "$PATH_" && patch -p1 < "$WORK/new/$OVERLAY") \
      || restore_on_fail "overlay apply failed"
  else
    log "overlay $OVERLAY not in dist, skipping"
  fi
fi

if [ -n "$DOCKERC" ]; then
  command -v docker >/dev/null || restore_on_fail "docker missing"
  log "building image ${DOCKERC}:${TAG}"
  (cd "$PATH_" && docker build -t "${DOCKERC}:${TAG}" .) \
    || restore_on_fail "docker build failed"
  if docker ps -q -f "name=^${DOCKERC}$" | grep -q .; then
    docker stop "$DOCKERC" >/dev/null && docker rm "$DOCKERC" >/dev/null
  fi
  # shellcheck disable=SC2086
  docker run -d --name "$DOCKERC" --restart unless-stopped \
    ${DOCKERP:+-p "$DOCKERP"} ${DOCKERV:+-v "$DOCKERV"} \
    ${DOCKERE:+--env-file "$DOCKERE"} "${DOCKERC}:${TAG}" \
    || restore_on_fail "docker run failed"
  sleep 3
  docker ps -q -f "name=^${DOCKERC}$" | grep -q . || restore_on_fail "container not running"
elif [ -n "$SERVICE" ]; then
  if systemctl is-active --quiet "$SERVICE"; then
    log "restarting $SERVICE"
    systemctl restart "$SERVICE" || restore_on_fail "service restart failed"
    sleep 3
    systemctl is-active --quiet "$SERVICE" || restore_on_fail "service not active after restart"
  else
    log "service $SERVICE not running; leaving stopped (deploy-only)"
  fi
fi

if [ -n "$HEALTH" ]; then
  log "health check"
  sh -c "$HEALTH" || restore_on_fail "health check failed"
fi

log "OK: $PROJECT $TAG deployed; backup=$BACKUP_FILE"
echo "BACKUP=$BACKUP_FILE"
