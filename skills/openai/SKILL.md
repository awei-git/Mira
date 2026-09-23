---
name: "openai"
description: "Use Openai when the user asks for Openai or this provider's API."
---

# Openai

## Purpose
Use Openai with the user-connected `custom.openai` credential.

## Tooling
`bin/openai-api` calls the OpenAI REST API with the stored credential:

```
openai-api /v1/models
openai-api /v1/chat/completions --data '{"model":"gpt-5.6","messages":[{"role":"user","content":"hi"}]}'
openai-api /v1/responses --data-file body.json --method POST
```

Prints the response body to stdout, exits non-zero on HTTP errors. Auth is
injected as a surrogate by authd; the CLI never sees or prints the raw key.
Python CLIs must import `/opt/hatch/skills/skill-creator/bin/dynamic_credentials.py`

Python CLIs must import `/opt/hatch/skills/skill-creator/bin/dynamic_credentials.py` and call `add_surrogate_to_request(...)`, `url_with_surrogate_query_param(...)`, or `url_with_surrogate_path_segment(...)` before authenticated requests, matching where the provider reads the key. If they use `urllib`, read JSON responses with `read_json_response(resp)` from the same helper instead of calling `resp.read()` directly. They must send only `hsurr:*` values, and only to the hosts below.

## Auth
The credential is already stored; nothing here collects one. Never ask the user to paste a raw key in chat, set a secret environment variable, pass a secret flag, or write an auth file.

A 401 or 403 is a question about the request before it is a question about the key. Check that the credential was attached at all: a request built without the helpers named under Tooling carries nothing, and that looks exactly like a wrong or under-scoped token. Only once a request that did carry the credential is still rejected, call `credentials.request_api_access` with `reconnect` to replace it. The connector is stored as `custom.openai`.

## Operating Rules
1. Use this skill when the user asks for Openai or this provider's API.
2. Restrict authenticated requests to: api.openai.com.
3. Do not print, log, or persist raw credentials.
4. If auth is missing or rejected, follow the Auth section rather than asking for a key.
