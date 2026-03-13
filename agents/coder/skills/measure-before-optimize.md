# Measure Before Optimize

**Tags:** coding, performance, profiling, optimization

## Core Principle
Profile first to locate the actual bottleneck before writing any optimization code — never optimize by intuition.

## Process
1. **Establish a baseline** — Benchmark with realistic data volumes before touching any code. Record wall time, CPU, memory, and I/O. This is your reference point.
2. **Profile, don't guess** — Use a profiler (CPU, memory, I/O, DB query analyzer) to find the *actual* hotspot, not the suspected one. The actual bottleneck is wrong 80% of the time.
3. **Apply 80/20 ruthlessly** — Focus on the top one or two hotspots that consume the majority of time. Optimizing everything is optimizing nothing.
4. **Distinguish complexity from constants** — Fix algorithmic complexity (O(n²) → O(n log n)) before constant-factor tuning. A better algorithm beats a faster implementation of a worse one.
5. **One change at a time** — After each optimization, benchmark against the baseline and record the delta. Never chain multiple changes before measuring.
6. **Know when to stop** — Stop when the performance target is met. Over-optimization creates unmaintainable code with diminishing returns.

## Rules
- "Premature optimization is the root of all evil" (Knuth) — write correct code first, fast code second.
- Document *why* the optimization exists with benchmark numbers — future maintainers need to know if they can remove it.
- Cache invalidation is harder than optimization — prefer algorithmic fixes to caching.
- Database queries are usually the bottleneck — check query plans before optimizing application code.

## Application
- When performance is a concern: write the slow, obvious version first, measure it, then optimize exactly what the profiler shows.
- For refactoring: keep benchmarks in the test suite so regressions are caught automatically.

## Source
Donald Knuth, *The Art of Computer Programming*; SmartBear Profiling Guide; DZone Code Profiling in Performance Engineering
