#!/bin/bash
# Mira daily backup script
# Backs up: PostgreSQL, config files, soul data, socialmedia state, learned skills
# Destination: NAS (/Volumes/home/backup/mira)
set -uo pipefail  # no -e: we want to continue on individual failures

DATE=$(date +%Y-%m-%d)
LOCAL_LOG="$HOME/Sandbox/Mira/logs/backup.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOCAL_LOG"; }

# Check NAS is mounted before doing anything
BACKUP_ROOT="/Volumes/home/backup/mira"
if [ ! -d "$BACKUP_ROOT" ]; then
    log "ERROR: NAS backup root unavailable ($BACKUP_ROOT), skipping backup"
    exit 0
fi

BACKUP_DIR="$BACKUP_ROOT/$DATE"
mkdir -p "$BACKUP_DIR"

log "=== Backup started: $DATE ==="

# 1. PostgreSQL full dump
log "Dumping PostgreSQL..."
if pg_dump -U ai_admin ai_system 2>>"$LOCAL_LOG" | gzip > "$BACKUP_DIR/ai_system.sql.gz"; then
    log "PostgreSQL dump OK ($(du -h "$BACKUP_DIR/ai_system.sql.gz" | cut -f1))"
else
    log "ERROR: PostgreSQL dump failed"
fi

# 2. Config files
log "Backing up config..."
cp "$HOME/Sandbox/Mira/config.yml" "$BACKUP_DIR/" 2>>"$LOCAL_LOG" || true
if [ -f "$HOME/.config/secrets.yml" ]; then
    cp "$HOME/.config/secrets.yml" "$BACKUP_DIR/secrets.yml" 2>>"$LOCAL_LOG" || true
    chmod 600 "$BACKUP_DIR/secrets.yml" 2>/dev/null || true
fi

# 3. Soul data (core files)
log "Backing up soul..."
SOUL="$HOME/Sandbox/Mira/agents/shared/soul"
mkdir -p "$BACKUP_DIR/soul"
for f in identity.md memory.md worldview.md interests.md skills.md catalog.jsonl scores.json emptiness.json; do
    cp "$SOUL/$f" "$BACKUP_DIR/soul/" 2>/dev/null || true
done

# 4. Learned skills (full directory)
log "Backing up learned skills..."
if [ -d "$SOUL/learned" ]; then
    rsync -a "$SOUL/learned/" "$BACKUP_DIR/soul/learned/" 2>>"$LOCAL_LOG" || true
fi

# 5. Agent state
cp "$HOME/Sandbox/Mira/.agent_state.json" "$BACKUP_DIR/" 2>>"$LOCAL_LOG" || true

# 6. Social media state files
log "Backing up socialmedia state..."
SM="$HOME/Sandbox/Mira/agents/socialmedia"
mkdir -p "$BACKUP_DIR/socialmedia"
for f in growth_state.json notes_state.json notes_queue.json comment_state.json publication_stats.json reply_tracking.json; do
    cp "$SM/$f" "$BACKUP_DIR/socialmedia/" 2>/dev/null || true
done

# 7. iCloud bridge items + archive (lightweight JSON files)
log "Backing up iCloud bridge..."
BRIDGE="$HOME/Library/Mobile Documents/com~apple~CloudDocs/MtJoy/Mira-Bridge/users"
if [ -d "$BRIDGE" ]; then
    mkdir -p "$BACKUP_DIR/bridge"
    for user_dir in "$BRIDGE"/*/; do
        user=$(basename "$user_dir")
        mkdir -p "$BACKUP_DIR/bridge/$user"
        # Items and archive (skip commands — ephemeral)
        [ -d "$user_dir/items" ] && rsync -a "$user_dir/items/" "$BACKUP_DIR/bridge/$user/items/" 2>/dev/null || true
        [ -d "$user_dir/archive" ] && rsync -a "$user_dir/archive/" "$BACKUP_DIR/bridge/$user/archive/" 2>/dev/null || true
        # Manifest and config
        cp "$user_dir/manifest.json" "$BACKUP_DIR/bridge/$user/" 2>/dev/null || true
        cp "$user_dir/config.json" "$BACKUP_DIR/bridge/$user/" 2>/dev/null || true
    done
fi

# 8. Prune local backups older than 30 days
log "Pruning old backups..."
find "$BACKUP_ROOT" -maxdepth 1 -type d -name "20*" -mtime +30 -exec rm -rf {} \; 2>>"$LOCAL_LOG"

# 9. Summary
log "Writing backup manifest..."
python3 "$HOME/Sandbox/Mira/scripts/backup_integrity.py" "$BACKUP_DIR" >>"$LOCAL_LOG" 2>&1 || \
    log "WARNING: backup manifest generation failed"

TOTAL=$(du -sh "$BACKUP_DIR" | cut -f1)
log "Backup complete: $BACKUP_DIR ($TOTAL)"
log "=== Backup finished ==="
