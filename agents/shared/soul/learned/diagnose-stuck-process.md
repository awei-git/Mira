1. Verify liveness: `ps aux | grep <PID>` — confirm the process is still running and note its start time and CPU/mem usage.

2. Find logs: Check common log directories (~/project/logs/, /tmp/, ~/.local/share/). Look for files matching the process name or task name. Prefer the most recently modified file.

3. Read recent log tail: Read the last 100–200 lines. Look for:
   - Repeated error messages (rate limits, timeouts, auth failures)
   - Progress indicators (e.g., "turn 35/69", "chunk 4/10")
   - Retry/backoff patterns (exponential backoff loops = stuck, not dead)
   - Timestamps to judge last real progress vs. last log entry

4. Classify the stuck state:
   - **Rate limit / quota**: 429s, QPM/RPM errors → process is alive but throttled; may self-recover
   - **Hard error loop**: repeated 4xx/5xx non-429 → likely won't recover without intervention
   - **Hung / deadlock**: no log output, process still in CPU → may need kill
   - **Slow but progressing**: log shows forward movement → just wait

5. Check for cache/checkpoints: Does the process save intermediate results? If yes, killing is safe — work up to last checkpoint is preserved.

6. Recommend:
   - Kill + resume later: rate limit with quota reset window, cache exists
   - Kill immediately: hard error loop, no progress possible
   - Wait: genuine progress being made, or backoff window is short
   - Investigate further: unexpected state, missing logs, zombie process