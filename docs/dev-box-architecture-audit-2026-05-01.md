# Dev Box Architecture Audit — 2026-05-01

## Current Decision

Local model serving is **oMLX only**. Ollama is legacy and must not be used for Mira runtime paths.

Model weights and Hugging Face/oMLX caches live on:

```text
/Volumes/aw_swap/omlx-cache
```

The internal SSD is for code, configs, secrets, Postgres data, launch agents, and small operational logs. It is not for model weights.

## Applied Cleanup

- Moved active Hugging Face/oMLX cache from:
  - `/Users/angwei/.cache/huggingface`
- To:
  - `/Volumes/aw_swap/omlx-cache/huggingface`
- Replaced the internal path with a symlink:
  - `/Users/angwei/.cache/huggingface -> /Volumes/aw_swap/omlx-cache/huggingface`
- Updated `homebrew.mxcl.omlx` LaunchAgent environment:
  - `HF_HOME=/Volumes/aw_swap/omlx-cache/huggingface`
  - `HF_HUB_CACHE=/Volumes/aw_swap/omlx-cache/huggingface/hub`
  - `XDG_CACHE_HOME=/Volumes/aw_swap/omlx-cache/xdg`
- Removed legacy Ollama model store:
  - `/Volumes/aw_swap/ollama`
- Removed legacy Ollama app-support/log leftovers:
  - `/Users/angwei/Library/Application Support/Ollama`
  - `/Users/angwei/Library/Logs/Homebrew/ollama`

## Verified State

`oMLX` is running from Homebrew:

```text
Label: homebrew.mxcl.omlx
Program: /opt/homebrew/opt/omlx/bin/omlx serve --max-model-memory 20GB
Port: 8800
```

`/v1/models` returns:

```text
Qwen3.5-27B-4bit
gemma-4-31b-it-4bit
nomicai-modernbert-embed-base-4bit
```

Disk pressure after cleanup:

```text
Internal Data volume: ~36 GiB free
/Volumes/aw_swap: ~900 GiB free
```

Live completion smoke test:

```text
model: gemma-4-31b-it-4bit
prompt: Reply with exactly OK.
response: OK
```

Service snapshot:

```text
homebrew.mxcl.omlx: running
homebrew.mxcl.postgresql@17: running
com.angwei.mira-agent: running
com.angwei.mira-web: running, https://127.0.0.1:8384/ returns 200
```

## Drift From Original Design Docs

The original family AI system design documented Ollama as the local model runtime and a 2TB external model SSD. That is no longer the runtime architecture.

Current reality:

- Runtime is `oMLX`, not Ollama.
- Database service is `postgresql@17`, not `postgresql@16`.
- Model disk is `/Volumes/aw_swap`, a 1TB APFS external volume, not a 2TB model disk.
- Media disk is `/Volumes/aw_footage`, exFAT. The original design preferred APFS for external SSDs. This remains a drift item if that disk hosts active write-heavy workflows.
- `/Volumes/aw_swap` is APFS but mounted `noowners`; acceptable for model cache, not ideal for security-sensitive state.

## Canonical Rules Going Forward

1. Do not reintroduce Ollama for Mira runtime.
2. Any local model cache must go under `/Volumes/aw_swap/omlx-cache`.
3. Any script that downloads Hugging Face or MLX models must preserve:
   - `HF_HOME=/Volumes/aw_swap/omlx-cache/huggingface`
   - `HF_HUB_CACHE=/Volumes/aw_swap/omlx-cache/huggingface/hub`
   - `XDG_CACHE_HOME=/Volumes/aw_swap/omlx-cache/xdg`
4. Mira config should keep local model references under `omlx`, with `ollama` names treated only as compatibility aliases.
5. Recovery docs should be updated to say: install oMLX, configure external cache env, then pull/verify models.
6. The internal SSD should keep at least 50 GiB free. If it drops below that, inspect:
   - `~/.cache`
   - `~/Library/Caches`
   - Xcode simulator runtimes
   - Docker images/volumes

## Remaining Infrastructure Risks

- Internal Data volume is still relatively tight at ~36 GiB free.
- Multiple iOS Simulator runtimes appear mounted and nearly full; remove unused runtimes from Xcode if internal free space drops again.
- The v0 original design doc still contains Ollama setup commands by design. Treat that file as historical only.
- `aw_footage` is exFAT; if it is used for active project writes, APFS would be safer.
- Mira runtime logs still show unrelated publishing pipeline warnings and health export lock retries. Those are application-level issues, not oMLX storage drift.
