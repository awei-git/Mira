---
activation_trigger: "Apply when designing code at a system boundary where external, untrusted data enters and must be validated before flowing inward."
---

# Defensive Interface Design

**Tags:** coding, architecture, API, robustness

## Core Principle
Validate at system boundaries, trust internal code — concentrate defensive logic at the edges where external data enters.

## Technique
Every system has a trust boundary. Outside the boundary (user input, API responses, file contents, environment variables), data is untrusted and must be validated. Inside the boundary, data has already been validated and can flow freely without redundant checks.

Key rules:
1. **Validate once at entry** — Parse and validate all external data into well-typed internal representations immediately
2. **Fail fast and loud** — Bad input should produce clear errors at the boundary, not mysterious failures deep inside
3. **Make illegal states unrepresentable** — Use types, enums, and data structures that make invalid data impossible to construct
4. **Internal functions trust their callers** — Don't re-validate data that was already validated at the boundary

Anti-patterns to avoid:
- Checking `if x is None` everywhere instead of validating once at the entry point
- Silently coercing bad data instead of rejecting it
- Defensive copying of immutable data
- Try/except around every internal function call

## Application
- Draw the trust boundary for your system explicitly — where does external data enter?
- Write validation/parsing at each boundary point
- Use typed containers (dataclasses, TypedDict, Pydantic) for validated data
- Remove redundant internal validation — it's noise that hides real bugs
