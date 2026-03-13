# Security Threat Modeling (STRIDE)

**Tags:** coding, security, threat-modeling, owasp, architecture

## Core Principle
Enumerate trust boundaries and attack surfaces at design time using STRIDE, then encode mitigations as tests — shift security left before vulnerabilities are baked in.

## STRIDE Framework
Apply to every component in the system:
- **S**poofing — Can an attacker impersonate a user, service, or data source?
- **T**ampering — Can data be modified in transit or at rest without detection?
- **R**epudiation — Can actions be denied? Are there audit logs?
- **I**nformation Disclosure — Can sensitive data leak through error messages, logs, or side channels?
- **D**enial of Service — Can the system be overwhelmed or made unavailable?
- **E**levation of Privilege — Can a lower-privilege principal gain higher privileges?

## Process
1. **Draw the data-flow diagram** — Identify all actors, processes, data stores, and trust boundaries between them.
2. **Apply STRIDE to each boundary** — A trust boundary crossing is where threats concentrate.
3. **Treat all external input as hostile** — Validate at the boundary, reject anything not explicitly allowed (allowlist over denylist).
4. **Apply the same rules to third-party APIs** — Never trust external services more than user input.
5. **Encode each threat as a test** — Write failing security tests before implementing mitigations.
6. **Secrets hygiene** — No credentials in code; environment variables only; rotate regularly; log access.

## Coding Defaults (apply always, not on request)
- Parameterized queries / prepared statements for all DB access — no string interpolation.
- Output encoding for all user-controlled data rendered in HTML/JSON/XML.
- Authenticate before authorizing; authorize before executing.
- Fail closed, not open: when in doubt, deny.

## Source
OWASP Secure Coding Practices Quick Reference; OWASP API Security Top 10; Microsoft STRIDE threat model
