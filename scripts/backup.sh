#!/bin/bash
# Mira daily backup script
# Backs up: PostgreSQL, config files, soul data, secrets
# Destination: local + NAS (/Volumes/home/backup/mira)
set -euo pipefail

BACKUP_ROOT="/Volumes/home/backup/mira"
DATE=$(date +%Y-%m-%d)
BACKUP_DIR="$BACKUP_ROOT/$DATE"
LOG="$BACKUP_ROOT/backup.log"

mkdir -p "$BACKUP_DIR"

log() { echo "[$(date '+%H:%M:%S')] $1" | tee -a "$LOG"; }

log "=== Backup started: $DATE ==="

# 1. PostgreSQL full dump
log "Dumping PostgreSQL..."
if pg_dump -U ai_admin ai_system | gzip > "$BACKUP_DIR/ai_system.sql.gz" 2>>"$LOG"; then
    log "PostgreSQL dump OK ($(du -h "$BACKUP_DIR/ai_system.sql.gz" | cut -f1))"
else
    log "ERROR: PostgreSQL dump failed"
fi

# 2. Config files
log "Backing up config..."
cp "$HOME/Sandbox/Mira/config.yml" "$BACKUP_DIR/" 2>>"$LOG" || true
cp "$HOME/Sandbox/.config/secrets.yml" "$BACKUP_DIR/secrets.yml" 2>>"$LOG" || true
chmod 600 "$BACKUP_DIR/secrets.yml" 2>/dev/null || true

# 3. Soul data (identity, memory, worldview, interests, scores)
log "Backing up soul..."
SOUL="$HOME/Sandbox/Mira/agents/shared/soul"
mkdir -p "$BACKUP_DIR/soul"
for f in identity.md memory.md worldview.md interests.md skills.md catalog.jsonl scores.json emptiness.json; do
    cp "$SOUL/$f" "$BACKUP_DIR/soul/" 2>/dev/null || true
done

# 4. Agent state
cp "$HOME/Sandbox/Mira/.agent_state.json" "$BACKUP_DIR/" 2>>"$LOG" || true

# 5. Sync to NAS
if [ -d "$NAS_BACKUP" ]; then
    log "Syncing to NAS..."
    rsync -a "$BACKUP_DIR/" "$NAS_BACKUP/$DATE/" 2>>"$LOG"
    # Prune NAS backups older than 90 days
    find "$NAS_BACKUP" -maxdepth 1 -type d -name "20*" -mtime +90 -exec rm -rf {} \; 2>>"$LOG"
    log "NAS sync OK"
else
    log "WARNING: NAS not mounted at $NAS_BACKUP, skipping remote backup"
fi

# 6. Prune local backups older than 30 days
log "Pruning old local backups..."
find "$BACKUP_ROOT" -maxdepth 1 -type d -name "20*" -mtime +30 -exec rm -rf {} \; 2>>"$LOG"

# 7. Summary
TOTAL=$(du -sh "$BACKUP_DIR" | cut -f1)
log "Backup complete: $BACKUP_DIR ($TOTAL)"
log "=== Backup finished ==="
