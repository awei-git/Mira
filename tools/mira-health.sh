#!/usr/bin/env bash
set -u

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"

bridge_heartbeat_file="${MIRA_BRIDGE_HEARTBEAT:-$HOME/Library/Mobile Documents/com~apple~CloudDocs/MtJoy/Mira-Bridge/heartbeat.json}"
legacy_heartbeat_file="$repo_root/logs/heartbeat.json"
if [[ -f "$bridge_heartbeat_file" ]]; then
  heartbeat_file="$bridge_heartbeat_file"
elif [[ -f "$legacy_heartbeat_file" ]]; then
  heartbeat_file="$legacy_heartbeat_file"
else
  heartbeat_file="$bridge_heartbeat_file"
fi
tasks_dir="$repo_root/data/tasks"
crash_log="/tmp/mira-crash.log"
audit_integrity_file="$repo_root/data/logs/skill_audit_integrity.json"
agent_label="${MIRA_AGENT_LABEL:-com.angwei.mira-agent}"
since_timestamp="${1:-}"
overall_status=0
heartbeat_fresh=0

stat_mtime() {
  if stat -f %m "$1" >/dev/null 2>&1; then
    stat -f %m "$1"
  else
    stat -c %Y "$1"
  fi
}

now_epoch() {
  date +%s
}

to_epoch() {
  local value="$1"
  local base=""
  local tz=""
  local normalized=""

  if [[ "$value" =~ ^[0-9]+$ ]]; then
    printf '%s\n' "$value"
    return 0
  fi

  if [[ "$value" =~ ^([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})(\.[0-9]+)?(Z|[+-][0-9]{2}:?[0-9]{2})?$ ]]; then
    base="${BASH_REMATCH[1]}"
    tz="${BASH_REMATCH[3]}"

    if [[ "$tz" == "Z" ]]; then
      tz="+0000"
    elif [[ "$tz" =~ ^[+-][0-9]{2}:[0-9]{2}$ ]]; then
      tz="${tz:0:3}${tz:4:2}"
    fi

    if [[ -n "$tz" ]]; then
      normalized="${base}${tz}"
      if date -j -f "%Y-%m-%dT%H:%M:%S%z" "$normalized" +%s >/dev/null 2>&1; then
        date -j -f "%Y-%m-%dT%H:%M:%S%z" "$normalized" +%s
        return 0
      fi
    else
      if date -j -f "%Y-%m-%dT%H:%M:%S" "$base" +%s >/dev/null 2>&1; then
        date -j -f "%Y-%m-%dT%H:%M:%S" "$base" +%s
        return 0
      fi
    fi
  fi

  if date -j -f "%Y-%m-%dT%H:%M:%S%z" "$value" +%s >/dev/null 2>&1; then
    date -j -f "%Y-%m-%dT%H:%M:%S%z" "$value" +%s
    return 0
  fi

  if date -j -f "%Y-%m-%d %H:%M:%S" "$value" +%s >/dev/null 2>&1; then
    date -j -f "%Y-%m-%d %H:%M:%S" "$value" +%s
    return 0
  fi

  if date -d "$value" +%s >/dev/null 2>&1; then
    date -d "$value" +%s
    return 0
  fi

  return 1
}

print_section() {
  printf '\n== %s ==\n' "$1"
}

print_section "Heartbeat"
if [[ -f "$heartbeat_file" ]]; then
  heartbeat_timestamp="$(grep -E '"timestamp"[[:space:]]*:' "$heartbeat_file" | head -1 | sed -E 's/.*"timestamp"[[:space:]]*:[[:space:]]*"([^"]*)".*/\1/')"
  heartbeat_mtime="$(stat_mtime "$heartbeat_file")"
  heartbeat_epoch="$heartbeat_mtime"
  freshness_basis="file mtime"

  if [[ -n "$heartbeat_timestamp" ]]; then
    printf 'timestamp: %s\n' "$heartbeat_timestamp"
    if parsed_heartbeat_epoch="$(to_epoch "$heartbeat_timestamp")"; then
      heartbeat_epoch="$parsed_heartbeat_epoch"
      freshness_basis="timestamp"
    fi
  else
    printf 'timestamp: unavailable\n'
  fi

  age_seconds=$(( $(now_epoch) - heartbeat_epoch ))
  printf 'file: %s\n' "$heartbeat_file"
  printf 'age_seconds: %s\n' "$age_seconds"
  printf 'freshness_basis: %s\n' "$freshness_basis"

  if [[ "$age_seconds" -le 300 ]]; then
    printf 'freshness: ok, heartbeat within 5 minutes\n'
    heartbeat_fresh=1
  else
    printf 'freshness: stale, heartbeat older than 5 minutes\n'
    overall_status=1
  fi
else
  printf 'missing: %s\n' "$heartbeat_file"
  overall_status=1
fi

print_section "Skill Audit Integrity"
if [[ -f "$audit_integrity_file" ]]; then
  audit_status="$(grep -E '"status"[[:space:]]*:' "$audit_integrity_file" | head -1 | sed -E 's/.*"status"[[:space:]]*:[[:space:]]*"([^"]*)".*/\1/')"
  if [[ "$audit_status" == "degraded" ]]; then
    printf 'status: degraded\n'
    printf 'file: %s\n' "$audit_integrity_file"
    overall_status=1
  else
    printf 'status: %s\n' "${audit_status:-unknown}"
    printf 'file: %s\n' "$audit_integrity_file"
  fi
else
  printf 'status: ok\n'
fi

print_section "Processes"
printf "command: ps -ef | grep -E 'mira-agent.sh|task_worker|core.py'\n"
ps_output="$(ps -ef 2>&1)"
ps_status=$?
if [[ "$ps_status" -ne 0 ]]; then
  printf 'ps failed: %s\n' "$ps_output"
  printf 'fallback: launchctl list %s\n' "$agent_label"
  launchd_output="$(launchctl list "$agent_label" 2>&1)"
  launchd_status=$?
  if [[ "$launchd_status" -ne 0 ]]; then
    printf 'launchctl failed: %s\n' "$launchd_output"
    if [[ "$heartbeat_fresh" -eq 1 ]]; then
      printf 'process_check: unavailable, but heartbeat is fresh\n'
    else
      overall_status=1
    fi
  elif printf '%s\n' "$launchd_output" | grep -E '"PID"[[:space:]]*=[[:space:]]*[0-9]+' >/dev/null 2>&1; then
    printf 'launchd: ok, %s has a PID\n' "$agent_label"
  else
    printf 'launchd: %s has no PID\n' "$agent_label"
    printf '%s\n' "$launchd_output"
    overall_status=1
  fi
else
  processes="$(printf '%s\n' "$ps_output" | grep -E 'mira-agent.sh|task_worker|core.py' | grep -v grep || true)"
  if [[ -n "$processes" ]]; then
    printf '%s\n' "$processes"
  else
    printf 'no mira-agent.sh, task_worker, or core.py processes found\n'
    overall_status=1
  fi
fi

print_section "Crash Log"
if [[ -f "$crash_log" ]]; then
  crash_mtime="$(stat_mtime "$crash_log")"
  printf 'file: %s\n' "$crash_log"
  printf 'mtime_epoch: %s\n' "$crash_mtime"
  if [[ "$heartbeat_fresh" -eq 1 && "${heartbeat_epoch:-0}" =~ ^[0-9]+$ && "$crash_mtime" -le "${heartbeat_epoch:-0}" ]]; then
    printf 'recent: none newer than the fresh heartbeat; historical tail suppressed\n'
  else
    tail -20 "$crash_log"
  fi
else
  printf 'missing: %s\n' "$crash_log"
fi

print_section "Recent Task Workspaces"
if [[ -d "$tasks_dir" ]]; then
  ls -ltd "$tasks_dir"/*/ 2>/dev/null | head -20 || printf 'no task workspaces found\n'
else
  printf 'missing: %s\n' "$tasks_dir"
fi

print_section "New Task Workspace Since Timestamp"
if [[ -z "$since_timestamp" ]]; then
  printf 'not checked: pass a timestamp argument to enable this check\n'
elif [[ ! -d "$tasks_dir" ]]; then
  printf 'missing: %s\n' "$tasks_dir"
else
  if since_epoch="$(to_epoch "$since_timestamp")"; then
    newest_epoch=0
    newest_name=""

    for entry in "$tasks_dir"/*/; do
      [[ -e "$entry" ]] || continue
      entry_epoch="$(stat_mtime "$entry")"
      if [[ "$entry_epoch" -gt "$newest_epoch" ]]; then
        newest_epoch="$entry_epoch"
        entry="${entry%/}"
        newest_name="${entry##*/}"
      fi
    done

    if [[ "$newest_epoch" -gt "$since_epoch" ]]; then
      printf 'created: yes\n'
      printf 'newest: %s\n' "$newest_name"
      printf 'newest_mtime_epoch: %s\n' "$newest_epoch"
    else
      printf 'created: no\n'
      printf 'newest_mtime_epoch: %s\n' "$newest_epoch"
    fi
else
    printf 'invalid timestamp: %s\n' "$since_timestamp"
    printf 'accepted formats: epoch seconds, YYYY-MM-DDTHH:MM:SS+0000, YYYY-MM-DD HH:MM:SS\n'
    overall_status=1
  fi
fi

exit "$overall_status"
