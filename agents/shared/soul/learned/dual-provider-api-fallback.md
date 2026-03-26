## Pattern: Dual-Provider API Fallback

### When to use

All four must be true:
- Two providers for the same capability (TTS, LLM inference, image gen, translation, etc.)
- Primary has rate limits, quota caps, or occasional downtime
- Secondary is acceptable quality (may cost more or be slower)
- Call must succeed on the first user-facing attempt — async retry later is not acceptable

### When NOT to use
- Single provider with exponential backoff is sufficient
- Providers return semantically different results (e.g., different translation styles) — this pattern silently swaps, which may confuse users
- Failover requires credential rotation or OAuth re-auth — add an auth refresh step before adopting this pattern
- You have three or more providers — use a priority queue / load balancer instead

### Quick Start

To implement the core pattern in 15 minutes:

1.  **Define Config**: Copy the `config.py` block from **Structure (1)**. Set your primary and fallback provider names, and a conservative `FALLBACK_BUDGET_LIMIT`.
2.  **Create Response Type**: Copy the `CompletionResult` dataclass from **Structure (2)**. Ensure both your provider adapters will return this exact shape.
3.  **Implement Adapters**: Create two functions (`call_primary`, `call_fallback`) following the template in **Structure (3)**. They must wrap your SDK calls and return a `CompletionResult`.
4.  **Build Dispatcher**: Copy the `complete()` function from **Structure (4)**. Map your provider names to your adapter functions in `PROVIDER_MAP`.
5.  **Add Logging**: After calling `complete(prompt)`, immediately log the `result.provider`, `result.latency_ms`, and `result.estimated_cost` as shown in **Structure (5)**.

Now test with `print(complete("Hello"))`. For production, add the **Budget Circuit Breaker (6)** and run the **Implementation Checklist**.

### Structure

**1. Config at file top** — not buried in logic:

```python
# config.py
PRIMARY_PROVIDER = "openai"
FALLBACK_PROVIDER = "anthropic"
FALLBACK_ENABLED = True
MAX_PRIMARY_RETRIES = 1  # keep low — failover IS the retry strategy
PRIMARY_TIMEOUT_S = 5.0  # must be shorter than your SLA so fallback has time
FALLBACK_BUDGET_LIMIT = 50.00  # USD per hour — circuit breaker threshold
```

**2. Unified response type** — both providers must return the same shape:

```python
from dataclasses import dataclass
from typing import Literal

@dataclass
class CompletionResult:
    text: str
    provider: Literal["openai", "anthropic"]
    latency_ms: float
    estimated_cost: float  # track per-request for circuit breaker
```

**3. Provider-specific adapters** — isolate all provider differences here:

```python
def call_openai(prompt: str) -> CompletionResult:
    start = time.monotonic()
    resp = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        timeout=PRIMARY_TIMEOUT_S,
    )
    elapsed = (time.monotonic() - start) * 1000
    return CompletionResult(
        text=resp.choices[0].message.content,
        provider="openai",
        latency_ms=elapsed,
        estimated_cost=resp.usage.total_tokens * 0.000005,  # adjust per model
    )

def call_anthropic(prompt: str) -> CompletionResult:
    start = time.monotonic()
    resp = anthropic_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    elapsed = (time.monotonic() - start) * 1000
    input_cost = resp.usage.input_tokens * 0.000003
    output_cost = resp.usage.output_tokens * 0.000015
    return CompletionResult(
        text=resp.content[0].text,
        provider="anthropic",
        latency_ms=elapsed,
        estimated_cost=input_cost + output_cost,
    )
```

**4. Fallback dispatcher** — the only place that catches rate-limit errors:

```python
from openai import RateLimitError as OpenAIRateLimit
from anthropic import RateLimitError as AnthropicRateLimit

FAILOVER_EXCEPTIONS = (OpenAIRateLimit, AnthropicRateLimit, TimeoutError)

PROVIDER_MAP = {
    "openai": call_openai,
    "anthropic": call_anthropic,
}

def complete(prompt: str) -> CompletionResult:
    providers = [PRIMARY_PROVIDER]
    if FALLBACK_ENABLED and not budget_breaker.is_open(FALLBACK_PROVIDER):
        providers.append(FALLBACK_PROVIDER)

    last_exc = None
    for provider_name in providers:
        try:
            result = PROVIDER_MAP[provider_name](prompt)
            budget_breaker.record(provider_name, result.estimated_cost)
            return result
        except FAILOVER_EXCEPTIONS as e:
            logger.warning(f"{provider_name} failed: {e}, trying next")
            last_exc = e

    raise last_exc  # both failed — let caller handle
```

**5. Log which provider served each request** — silent failover hides cost/quality shifts:

```python
result = complete(prompt)
logger.info(f"provider={result.provider} latency={result.latency_ms:.0f}ms cost=${result.estimated_cost:.4f}")
metrics.increment("completion.provider", tags={"provider": result.provider})
# Alert if fallback rate exceeds 10% — your primary has a problem
```

**6. Budget circuit breaker** — prevents a rate-limit storm on primary from becoming a billing event on secondary:

```python
import time
import threading
from collections import defaultdict

class BudgetCircuitBreaker:
    def __init__(self, window_s: float = 3600):
        self._window_s = window_s
        self._ledger: dict[str, list[tuple[float, float]]] = defaultdict(list)
        self._lock = threading.Lock()

    def record(self, provider: str, cost: float) -> None:
        with self._lock:
            self._ledger[provider].append((time.monotonic(), cost))

    def _spend_in_window(self, provider: str) -> float:
        """Must be called while holding self._lock."""
        cutoff = time.monotonic() - self._window_s
        entries = self._ledger[provider]
        self._ledger[provider] = [(t, c) for t, c in entries if t > cutoff]
        return sum(c for _, c in self._ledger[provider])

    def is_open(self, provider: str) -> bool:
        """True = circuit is open = stop sending traffic."""
        with self._lock:
            spent = self._spend_in_window(provider)
        if spent >= FALLBACK_BUDGET_LIMIT:
            logger.error(
                f"Circuit breaker OPEN for {provider}: ${spent:.2f} in last hour "
                f"(limit ${FALLBACK_BUDGET_LIMIT:.2f})"
            )
            return True
        return False

    def force_close(self, provider: str) -> None:
        """Manual override for ops — clears spend history."""
        with self._lock:
            self._ledger[provider].clear()

budget_breaker = BudgetCircuitBreaker()
```

**7. Verify failover before you need it** — test with injected failures, not real outages:

```python
import pytest
from unittest.mock import patch

def test_failover_on_primary_rate_limit():
    with patch("yourmodule.call_openai", side_effect=OpenAIRateLimit("rate limited", response=None, body=None)):
        result = complete("test prompt")
    assert result.provider == "anthropic"

def test_both_fail_raises():
    with patch("yourmodule.call_openai", side_effect=TimeoutError()), \
         patch("yourmodule.call_anthropic", side_effect=AnthropicRateLimit("rate limited", response=None, body=None)):
        with pytest.raises((TimeoutError, AnthropicRateLimit)):
            complete("test prompt")

def test_circuit_breaker_opens():
    breaker = BudgetCircuitBreaker(window_s=10)
    for _ in range(100):
        breaker.record("anthropic", 1.0)  # $100 total
    assert breaker.is_open("anthropic")  # exceeds $50 limit

def test_circuit_breaker_force_close():
    breaker = BudgetCircuitBreaker(window_s=10)
    for _ in range(100):
        breaker.record("anthropic", 1.0)
    breaker.force_close("anthropic")
    assert not breaker.is_open("anthropic")
```

### Implementation Checklist

Before deploying to production, verify:

1. **✅ Unified response type**: Both provider adapters return identical `CompletionResult` structure
2. **✅ Exception isolation**: Only rate-limit and timeout errors trigger failover (not 400s)
3. **✅ Provider logging**: Every request logs which provider served it with cost and latency
4. **✅ Circuit breaker configured**: `FALLBACK_BUDGET_LIMIT` set based on fallback's higher cost
5. **✅ Timeout calculation**: `PRIMARY_TIMEOUT_S = your_SLA - fallback_p95_latency`
6. **✅ Alert threshold**: Monitoring alerts when fallback usage exceeds 10% for 5 minutes
7. **✅ Failover tests**: Unit tests simulate primary failure and verify fallback activation

### Gotchas
- **Cost asymmetry**: if fallback is 5x the price, set `FALLBACK_BUDGET_LIMIT` based on dollar spend, not request count.
- **Latency asymmetry**: set `PRIMARY_TIMEOUT_S` to `your_SLA - fallback_p95_latency`, so the fallback still completes within SLA.
- **Silent quality drift**: you won't notice worse fallback results unless you track provider per request (step 5). Periodically sample and compare outputs.
- **Don't catch broad exceptions**: only catch rate-limit and timeout. A 400 (bad request) will fail on the fallback too — let it propagate immediately.
- **Thread safety**: the circuit breaker is called from concurrent request handlers. The `threading.Lock` in step 6 prevents race conditions that could blow past your budget limit.