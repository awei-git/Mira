When an automation script silently does nothing, investigate in this order:

1. **Verify the trigger logic exists and fires correctly.**
   Read the "should_run()" or equivalent gate function. Manually trace what it would return given current state. If it returns a valid target, the logic is fine — the failure is downstream.

2. **Check external API calls for rate-limit or quota errors in logs.**
   Search logs for error codes (e.g., 1002, 429, quota exhausted). Count how many attempts were made and whether retries exhausted the wait budget. If all attempts fail at the *first* API call, rate limiting is the prime suspect.

3. **Identify resource contention between parallel jobs.**
   If multiple background processes (e.g., zh/en variants, daily/hourly jobs) share the same API key or rate-limited resource, they will mutually exhaust quota. This looks like intermittent failure or consistent first-call failures during peak windows. Fix: serialize with a global file lock (e.g., `fcntl.flock`) or a shared semaphore around all calls to that API.

4. **Check for slug/filename drift causing false "missing" detection.**
   If the pipeline checks file existence to determine what needs processing, verify the generated filename exactly matches the expected path. A mismatch means the file exists but the script thinks it doesn't — causing repeated re-generation attempts (wasting quota) or false "nothing to do" conclusions.

5. **Identify the cheapest path to unblock.**
   If partial cached work exists (e.g., N/M chunks already done), prioritize the job closest to completion to restore service fastest with minimal API calls.

Key heuristic: if the logic is correct and the job is being selected, but nothing completes — look at the API layer, not the code.