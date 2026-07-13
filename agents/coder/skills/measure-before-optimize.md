---
activation_trigger: "Apply when a measured performance number (latency, throughput, memory) is more than 2x worse than a known target and optimization is being considered."
---

# Measure Before Optimize

**Tags:** coding, performance, profiling, optimization

## Triggers
Activate when ALL of these are true:
- A measured number exists: latency, throughput, memory, or query time — not a feeling
- A target exists or can be derived (SLO, regression baseline, user-facing threshold)
- The gap between measured and target is >2× — smaller gaps are usually not worth the code complexity

Specific activation scenarios:
- `EXPLAIN ANALYZE` shows sequential scan on a table with >10k rows AND the query is in a hot path (called >1/sec or per-request)
- A PR adds a query or network call inside a loop where the collection grows with data volume
- A benchmark shows >20% regression vs. a linked prior commit (not vs. a different machine or config)
- p99 latency exceeds the SLO by >2× and the endpoint handles >100 req/min

Do NOT activate:
- "This feels slow" with no number — ask for a number first
- Startup/init code that runs once per process lifetime
- Bounded small collections (<100 items) with no I/O in the loop
- Code that produces wrong results — correctness first, always
- Gap between measured and target is <2× — the optimization will likely cost more in complexity than it saves

## Step 1: Get a baseline number
Before any change, measure with the same method you'll use to verify the fix.

**Web endpoints:** `curl -o /dev/null -s -w '%{time_total}' <url>` — run 5×, take median. Or pull p50/p99 from APM over a 1-hour window at typical load.
**Python:** `python -m cProfile -s cumtime script.py | head -30` for CPU. `time.perf_counter()` for wall time.
**Node:** `node --prof script.js` then `node --prof-process isolate-*.log`. Async bottlenecks: `clinic flame -- node script.js`.
**DB queries:** `EXPLAIN (ANALYZE, BUFFERS) <query>` (Postgres) or `EXPLAIN FORMAT=JSON <query>` (MySQL). Record actual time, rows, and buffer hits.
**JVM:** `async-profiler` flame graph: `./profiler.sh -d 30 -f out.html <pid>`.

Write the number down. This is your baseline.

## Step 1.5: Set a target with a sanity check
Optimization without a target is infinite work. Set one:

- **Regression?** Target = the prior baseline (linked commit).
- **SLO breach?** Target = the SLO.
- **"Make it faster"?** Ask: faster for whom? Pick the user-visible metric and set target at the perceptible threshold (100ms for UI interactions, 1s for page loads, 30s for batch jobs).

Then check the physical lower bound:
- **Network round trip:** ~0.5ms local, 1-10ms same-region, 50-150ms cross-continent. You cannot beat the speed of light. If your target requires 5 sequential API calls each at 50ms, your floor is 250ms — no code change fixes this. You need to parallelize or reduce calls.
- **Disk I/O:** ~0.1ms SSD random read, ~5ms HDD. Scanning 1GB from SSD takes ~1s minimum.
- **DB query:** A well-indexed point lookup on Postgres returns in 0.1-1ms. A sequential scan of 1M rows takes 100ms+ regardless of query shape. If your query must touch 1M rows, the fix is touching fewer rows, not a faster scan.

If your target is below the physical floor, renegotiate the target or change the architecture (caching, precomputation, async). Don't waste time micro-tuning code that's already near the limit.

## Step 2: Identify the bottleneck type
Read the profiler output. The fix depends on which category, but so does the trap:

| Bottleneck | How to Spot It | Actionable Fix |
|---|---|---|
| **N+1 queries** | Same query repeated N× in trace/logs; `EXPLAIN ANALYZE` shows many similar queries. | **Trap:** Eager-loading everything — trades N+1 SELECTs for one massive JOIN that blows memory.<br>**Fix:** Batch with `IN` clause (batch size ≤1000) first; only JOIN if batch approach is still too slow. |
| **Missing index** | `EXPLAIN` shows Seq Scan with high row estimate; query planner warns about missing index. | **Trap:** Adding index without checking write impact (slows INSERT/UPDATE).<br>**Fix:** On write-heavy tables (>1k writes/sec), benchmark writes too. Composite indexes: most-selective column first; verify usage with `EXPLAIN`. |
| **O(n²) algorithm** | Single function dominates; time grows quadratically with input size in profiler. | **Trap:** Rewriting algorithm when real fix is reducing n.<br>**Fix:** If n comes from unbounded query, add LIMIT first — O(n²) on 50 items is 2500 ops, not worth rewriting. |
| **Unbounded result set** | Memory spikes; query returns 100k+ rows; `EXPLAIN` shows no LIMIT. | **Trap:** Adding LIMIT without ORDER BY — get arbitrary rows each time.<br>**Fix:** Pagination with OFFSET scales badly past page 100; use keyset pagination (`WHERE id > last_seen_id`). |
| **Serialization cost** | JSON/XML parsing as top profiler frame; high CPU in `to_json`/`Marshal` methods. | **Trap:** Switching serializers (JSON→protobuf) before checking payload size.<br>**Fix:** If serializing 10MB of unused data, select fewer fields first, not faster serializer. |
| **External API latency** | Wall time >> CPU time for single call; thread/connection pool exhaustion. | **Trap:** Adding cache before adding timeout.<br>**Fix:** Add timeout first (3× p99 of upstream), then consider caching. |
| **Lock contention** | CPU idle, throughput low, threads WAITING in `park` or `monitorenter`. | **Trap:** Replacing lock with lock-free code.<br>**Fix:** Hold lock for less time — move I/O and computation outside critical section before `ConcurrentHashMap`. |

## Step 2.5: Check your diagnosis before coding
Three misreads that waste optimization effort:

**Inclusive vs. exclusive time.** Most profilers default to *inclusive* time (function + everything it calls). A function at 400ms inclusive but 2ms exclusive is not the bottleneck — something it calls is. Drill into callees before rewriting the caller.

**Latency vs. throughput.** A 50ms function called once per request is fine. Called 200× per request, it's a crisis. Check *call count × per-call cost*. High call count → batch or cache at the call site, don't speed up the callee.

**Profiler overhead distortion.** cProfile adds ~30% overhead per call, inflating trivially-cheap functions called millions of times. If your hotspot is a one-liner in a tight loop, verify with `time.perf_counter()` around the outer loop before rewriting the inner function.

If the hotspot doesn't match the table and survives these checks, post the profiler output and ask before guessing.

## Step 3: Fix one thing, re-measure
Apply the smallest change that addresses the identified bottleneck. Run the exact measurement from Step 1.

- **Target met** → stop. Write before/after numbers in the commit message.
- **Improved but not enough** → re-profile. The next bottleneck is usually a different category. Do not apply a second fix to the same bottleneck.
- **No change or regression** → revert immediately. Your diagnosis was wrong. Re-read Step 2.5.
- **Improved locally but something else got worse** (memory up, write latency up, error rate up) → revert. You moved the cost, not removed it.

## Hard rules
- No optimization without a measured baseline and a defined target.
- Fix algorithmic complexity before constant factors. O(n²) → O(n) beats any micro-tuning.
- If the bottleneck is a DB query, fix the query before adding application-level caching. Caches add invalidation bugs and mask the real problem.
- Document with numbers: `-- Added index: query dropped from 1200ms to 8ms at 500k rows`.
- Stop when the target is met. Every optimization adds maintenance cost — it must earn its complexity.
