# verify-local-model-service-before-inference

Always confirm Ollama (or any local model server) is running before attempting inference

**Source**: Extracted from task failure (2026-03-27)
**Tags**: ollama, local-llm, infrastructure, debugging

---

## Rule: Verify Local Model Service Before Inference

Before sending any request to a local model endpoint (Ollama, LM Studio, llama.cpp server, etc.), confirm the service is actually running.

**Check steps (in order):**
1. `curl -s http://localhost:11434/api/tags` — Ollama health check; non-empty JSON means it's up
2. If that fails: `pgrep -x ollama` or `ps aux | grep ollama` to see if the process exists
3. If process is absent: start it with `ollama serve` before retrying

**Why empty results happen:**
- Ollama was never started in this session
- The machine was rebooted and Ollama is not configured as a launch daemon
- The service crashed silently

Empty or null responses from a local model are almost always a connectivity issue, not a model issue. Do not retry inference — diagnose the service first.

**Actionable pattern:**
```python
import httpx
try:
    r = httpx.get("http://localhost:11434/api/tags", timeout=2)
    r.raise_for_status()
except Exception:
    raise RuntimeError("Ollama is not running. Start it with: ollama serve")
```

If Ollama needs to run persistently, register it as a launchd service (macOS) or systemd unit (Linux) so it survives reboots.
